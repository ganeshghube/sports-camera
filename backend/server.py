"""
Cricket Live backend (FastAPI + SQLite).

Replaces Firebase Realtime Database with a local store that has the same
path/tree semantics, so the existing admin/overlay code keeps working:

  GET    /api/db/{path}     read value at path
  PUT    /api/db/{path}     set (replace) value        [auth]
  PATCH  /api/db/{path}     update (merge) children    [auth]
  POST   /api/db/{path}     push (new generated key)   [auth]
  DELETE /api/db/{path}     remove                     [auth]
  WS     /ws/db             subscribe to paths (live updates)

Auth:    POST /api/login, POST /api/logout, GET /api/me
Tracker: WS /ws/ball-tracking (AI publishes)  <->  WS /ws/ball-tracking-ui (UI)
         GET /api/ball-tracking/status
The tracker only reports detections; nothing here changes the score.
"""

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from fastapi import (Cookie, Depends, FastAPI, HTTPException, Request,
                     Response, WebSocket, WebSocketDisconnect)
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

try:
    from backend.system import SystemManager
except ImportError:
    from system import SystemManager

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("DB_PATH", ROOT / "data" / "cricket.db"))
FRONTEND = ROOT / "frontend"
SESSION_TTL = 60 * 60 * 12
COOKIE = "cl_session"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ------------------------------------------------------------------ storage
class Store:
    """Firebase-style JSON tree persisted in SQLite (one row per top-level key)."""

    def __init__(self, path):
        self.lock = threading.RLock()
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users(
                username TEXT PRIMARY KEY, salt TEXT NOT NULL, hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'admin');
            CREATE TABLE IF NOT EXISTS sessions(
                token TEXT PRIMARY KEY, username TEXT NOT NULL, expires REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
                kind TEXT NOT NULL, data TEXT NOT NULL);
        """)
        self.conn.commit()
        self.tree = {}
        for k, v in self.conn.execute("SELECT key, value FROM nodes"):
            self.tree[k] = json.loads(v)

    @staticmethod
    def parts(path):
        return [p for p in path.strip("/").split("/") if p]

    def get(self, path):
        with self.lock:
            node = self.tree
            for p in self.parts(path):
                if isinstance(node, dict) and p in node:
                    node = node[p]
                elif isinstance(node, list):
                    try:
                        node = node[int(p)]
                    except (ValueError, IndexError):
                        return None
                else:
                    return None
            return node

    def _persist(self, top):
        if top in self.tree:
            self.conn.execute(
                "INSERT INTO nodes(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (top, json.dumps(self.tree[top])))
        else:
            self.conn.execute("DELETE FROM nodes WHERE key=?", (top,))
        self.conn.commit()

    def set(self, path, value):
        ps = self.parts(path)
        if not ps:
            raise ValueError("cannot replace root")
        with self.lock:
            node = self.tree
            for p in ps[:-1]:
                nxt = node.get(p)
                if not isinstance(nxt, dict):
                    nxt = {}
                    node[p] = nxt
                node = nxt
            if value is None:
                node.pop(ps[-1], None)
                self._prune(ps)
            else:
                node[ps[-1]] = value
            self._persist(ps[0])

    def _prune(self, ps):
        # remove empty parent dicts like Firebase does
        for i in range(len(ps) - 1, 0, -1):
            parent = self.get("/".join(ps[:i]))
            if isinstance(parent, dict) and not parent:
                grand = self.get("/".join(ps[:i - 1])) if i > 1 else self.tree
                if isinstance(grand, dict):
                    grand.pop(ps[i - 1], None)
            else:
                break

    def update(self, path, patch):
        with self.lock:
            for k, v in patch.items():
                self.set(f"{path.strip('/')}/{k}" if path.strip("/") else k, v)

    def push(self, path, value):
        key = f"-{int(time.time() * 1000):x}{secrets.token_hex(3)}"
        self.set(f"{path.strip('/')}/{key}", value)
        return key

    # ---- auth
    @staticmethod
    def _hash(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode(),
                                   bytes.fromhex(salt), 200_000).hex()

    def ensure_admin(self):
        with self.lock:
            if self.conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                return
            user = os.environ.get("ADMIN_USERNAME", "admin")
            pw = os.environ.get("ADMIN_PASSWORD")
            generated = pw is None
            if generated:
                pw = secrets.token_urlsafe(9)
            salt = secrets.token_hex(16)
            self.conn.execute("INSERT INTO users VALUES(?,?,?,?)",
                              (user, salt, self._hash(pw, salt), "admin"))
            self.conn.commit()
            if generated:
                log(f"FIRST RUN: created user '{user}' with password: {pw}  "
                    f"(change via ADMIN_PASSWORD in .env before first run)")
            else:
                log(f"Created user '{user}' from ADMIN_PASSWORD")

    def verify(self, username, password):
        with self.lock:
            row = self.conn.execute(
                "SELECT salt,hash FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            self._hash(password, "00" * 16)  # equalise timing
            return False
        return hmac.compare_digest(self._hash(password, row[0]), row[1])

    def new_session(self, username):
        tok = secrets.token_urlsafe(32)
        with self.lock:
            self.conn.execute("INSERT INTO sessions VALUES(?,?,?)",
                              (tok, username, time.time() + SESSION_TTL))
            self.conn.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
            self.conn.commit()
        return tok

    def session_user(self, tok):
        if not tok:
            return None
        with self.lock:
            row = self.conn.execute(
                "SELECT username,expires FROM sessions WHERE token=?", (tok,)).fetchone()
        if not row or row[1] < time.time():
            return None
        return row[0]

    def end_session(self, tok):
        with self.lock:
            self.conn.execute("DELETE FROM sessions WHERE token=?", (tok,))
            self.conn.commit()

    def log_event(self, kind, data):
        with self.lock:
            self.conn.execute("INSERT INTO events(ts,kind,data) VALUES(?,?,?)",
                              (time.time(), kind, json.dumps(data)))
            self.conn.commit()


store = Store(DB_PATH)
store.ensure_admin()
app = FastAPI(title="Cricket Live")
system_manager = SystemManager(ROOT, store, log)


# ------------------------------------------------------------------ auth
def current_user(cl_session: str = Cookie(default=None)):
    return store.session_user(cl_session)


def require_user(user=Depends(current_user)):
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


_fails = {}


@app.post("/api/login")
async def login(request: Request, response: Response):
    body = await request.json()
    ip = request.client.host if request.client else "?"
    n, ts = _fails.get(ip, (0, 0))
    if n >= 5 and time.time() - ts < 60:
        raise HTTPException(429, "Too many attempts. Wait a minute.")
    u, p = str(body.get("username", "")), str(body.get("password", ""))
    if not u or not p:
        raise HTTPException(400, "Username and password are required")
    if not store.verify(u, p):
        _fails[ip] = (n + 1, time.time())
        raise HTTPException(401, "Invalid username or password")
    _fails.pop(ip, None)
    tok = store.new_session(u)
    response.set_cookie(COOKIE, tok, httponly=True, samesite="lax",
                        max_age=SESSION_TTL)
    return {"ok": True, "username": u}


@app.post("/api/logout")
def logout(response: Response, cl_session: str = Cookie(default=None)):
    if cl_session:
        store.end_session(cl_session)
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return {"authenticated": bool(user), "username": user}


# ------------------------------------------------------------------ live DB
class Hub:
    def __init__(self):
        self.subs = {}  # websocket -> set(paths)

    async def notify(self, changed_path):
        cp = Store.parts(changed_path)
        for ws, paths in list(self.subs.items()):
            for sp in paths:
                s = Store.parts(sp)
                n = min(len(s), len(cp))
                if s[:n] == cp[:n]:  # related (ancestor/descendant/same)
                    try:
                        await ws.send_json({"type": "value", "path": sp,
                                            "value": store.get(sp)})
                    except Exception:
                        self.subs.pop(ws, None)
                        break


hub = Hub()


async def _body(request: Request):
    raw = await request.body()
    if not raw.strip():
        return None
    try:
        return json.loads(raw)
    except ValueError:
        raise HTTPException(400, "Invalid JSON body")


@app.get("/api/db/{path:path}")
def db_get(path: str):
    return JSONResponse(store.get(path))


@app.put("/api/db/{path:path}")
async def db_set(path: str, request: Request, user=Depends(require_user)):
    store.set(path, await _body(request))
    await hub.notify(path)
    return {"ok": True}


@app.patch("/api/db/{path:path}")
async def db_update(path: str, request: Request, user=Depends(require_user)):
    patch = await _body(request)
    if not isinstance(patch, dict):
        raise HTTPException(400, "update needs an object")
    store.update(path, patch)
    await hub.notify(path)
    return {"ok": True}


@app.post("/api/db/{path:path}")
async def db_push(path: str, request: Request, user=Depends(require_user)):
    key = store.push(path, await _body(request))
    await hub.notify(path)
    return {"key": key}


@app.delete("/api/db/{path:path}")
async def db_remove(path: str, user=Depends(require_user)):
    store.set(path, None)
    await hub.notify(path)
    return {"ok": True}


@app.websocket("/ws/db")
async def ws_db(ws: WebSocket):
    await ws.accept()
    hub.subs[ws] = set()
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "sub":
                hub.subs[ws].add(msg["path"])
                await ws.send_json({"type": "value", "path": msg["path"],
                                    "value": store.get(msg["path"])})
            elif msg.get("type") == "unsub":
                hub.subs[ws].discard(msg["path"])
    except (WebSocketDisconnect, Exception):
        hub.subs.pop(ws, None)


# ------------------------------------------------------------------ ball tracking bridge
class Tracking:
    def __init__(self):
        self.tracker = None
        self.uis = set()
        self.frame_uis = set()
        self.status = {
            "type": "status", "camera": "OFFLINE", "model": "OFFLINE",
            "ai_connected": False, "tracking": False, "camera_running": False,
            "motor": "STOPPED", "detected": False,
        }
        self.last_detection = None
        self.last_seen = 0.0
        self.last_frame = None
        self.last_frame_time = 0.0

tr = Tracking()


def _is_local(ws: WebSocket):
    return ws.client and ws.client.host in ("127.0.0.1", "::1", "localhost")


async def _broadcast(msg):
    for ui in list(tr.uis):
        try:
            await ui.send_json(msg)
        except Exception:
            tr.uis.discard(ui)


async def _send_tracker_command(command: str):
    ws = tr.tracker
    if ws is None:
        raise HTTPException(409, "Vision service is not running. Start the application with: python run.py")
    try:
        await ws.send_json({"command": command})
    except Exception as exc:
        tr.tracker = None
        raise HTTPException(503, f"Vision service is unavailable: {exc}")
    return tr.status


def _camera_status():
    st = dict(tr.status)
    st["ai_connected"] = tr.tracker is not None
    st["last_detection"] = tr.last_detection
    st["seconds_since_message"] = (time.time() - tr.last_seen) if tr.last_seen else None
    return {
        "running": bool(st.get("camera_running")),
        "connected": st.get("camera") == "CONNECTED",
        "status": st.get("camera") or "OFFLINE",
        "error": st.get("camera_error"),
        **st,
    }


def _frame_getter():
    return tr.last_frame


def _discover_cameras(probe=False):
    # RTSP/network is the normal production path. Avoid opening USB device
    # indexes unless the operator explicitly presses Discover; this prevents
    # repeated DSHOW/MSMF/obsensor warnings and unnecessary camera contention.
    if not probe:
        return []
    # Discovery is only called from the Settings page. It may briefly probe
    # device indexes, but the active camera is still owned exclusively by the
    # vision worker, so discovery cannot create a second capture loop.
    import platform as _platform
    import glob as _glob
    import cv2 as _cv2
    found = []
    # OpenCV emits noisy backend/index warnings for every nonexistent Windows
    # camera index. Discovery is a UI helper, so suppress those transient
    # warnings while probing; real errors are still returned by the API.
    _old_log_level = None
    try:
        _old_log_level = _cv2.getLogLevel()
        if hasattr(_cv2, 'setLogLevel'):
            _cv2.setLogLevel(_cv2.LOG_LEVEL_ERROR)
    except Exception:
        pass
    if _platform.system() == "Windows":
        # DirectShow is generally the most reliable OpenCV path for USB webcams
        # on Windows. Media Foundation remains a fallback for newer devices.
        backends = [("DirectShow", getattr(_cv2, "CAP_DSHOW", _cv2.CAP_ANY)),
                    ("Media Foundation", getattr(_cv2, "CAP_MSMF", _cv2.CAP_ANY)),
                    ("OpenCV default", _cv2.CAP_ANY)]
        seen = set()
        for idx in range(6):
            for backend_name, backend in backends:
                cap = None
                try:
                    cap = _cv2.VideoCapture(idx, backend)
                    if cap.isOpened():
                        try:
                            cap.set(_cv2.CAP_PROP_BUFFERSIZE, 1)
                            cap.set(_cv2.CAP_PROP_FRAME_WIDTH, 1280)
                            cap.set(_cv2.CAP_PROP_FRAME_HEIGHT, 720)
                            cap.set(_cv2.CAP_PROP_FPS, 30)
                        except Exception:
                            pass
                        good = 0
                        frame = None
                        for _ in range(4):
                            ok, candidate = cap.read()
                            if ok and candidate is not None and getattr(candidate, "size", 0):
                                frame = candidate
                                good += 1
                            if good >= 2:
                                break
                        if good >= 2 and idx not in seen:
                            seen.add(idx)
                            found.append({"type":"usb","device":idx,"name":f"USB Camera {idx}","backend":backend_name,
                                          "width":int(frame.shape[1]),"height":int(frame.shape[0])})
                            break
                except Exception:
                    pass
                finally:
                    try:
                        if cap is not None: cap.release()
                    except Exception: pass
    else:
        for path in sorted(_glob.glob("/dev/video*")):
            found.append({"type": "usb", "device": path, "name": path})
    # A configured RTSP/Wi-Fi source is also shown without opening it here.
    try:
        from urllib.parse import urlparse
        s = kv = None
        # Read from the persisted settings tree if available.
        row = store.conn.execute("SELECT value FROM nodes WHERE key=?", ("settings",)).fetchone()
        if row:
            import json as _json
            kv = _json.loads(row[0]) or {}
        rtsp = (kv or {}).get("rtsp_url") or os.environ.get("RTSP_URL", "")
        if rtsp:
            found.append({"type": "network", "device": rtsp, "name": "Configured Wi-Fi / RTSP Camera"})
    except Exception:
        pass
    try:
        if _old_log_level is not None and hasattr(_cv2, 'setLogLevel'):
            _cv2.setLogLevel(_old_log_level)
    except Exception:
        pass
    return found


@app.websocket("/ws/ball-tracking")
async def ws_tracker(ws: WebSocket):
    if not _is_local(ws):
        await ws.close(code=1008)
        return
    await ws.accept()
    if tr.tracker is not None:
        try:
            await tr.tracker.close()
        except Exception:
            pass
    tr.tracker = ws
    tr.status = {**tr.status, "ai_connected": True}
    log("Ball tracker connected")
    await _broadcast(tr.status)
    try:
        while True:
            raw = await ws.receive()
            if raw.get("type") == "websocket.disconnect":
                break
            tr.last_seen = time.time()
            if raw.get("bytes") is not None:
                tr.last_frame = raw["bytes"]
                tr.last_frame_time = time.time()
                # Preview is now polled as MJPEG; binary forwarding is retained
                # for backwards compatibility with existing tracker UIs.
                for ui in list(tr.frame_uis):
                    try:
                        await ui.send_bytes(tr.last_frame)
                    except Exception:
                        tr.frame_uis.discard(ui)
                continue
            try:
                msg = json.loads(raw.get("text") or "{}")
            except Exception:
                continue
            if msg.get("type") == "status":
                tr.status = {**tr.status, **msg, "ai_connected": True}
                await _broadcast(tr.status)
            elif msg.get("type") == "ball_detection":
                tr.last_detection = msg
                tr.status = {**tr.status, **msg, "ai_connected": True}
                await _broadcast(msg)
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        if tr.tracker is ws:
            tr.tracker = None
            tr.status = {**tr.status, "ai_connected": False, "camera": "OFFLINE", "camera_running": False, "tracking": False}
            await _broadcast(tr.status)
        log("Ball tracker disconnected")


@app.websocket("/ws/ball-tracking-ui")
async def ws_tracker_ui(ws: WebSocket):
    await ws.accept()
    tr.uis.add(ws)
    await ws.send_json(_camera_status())
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("subscribe") == "frames":
                tr.frame_uis.add(ws)
                continue
            cmd = msg.get("command")
            if cmd in ("start_tracking", "stop_tracking", "start_camera", "stop_camera", "restart_camera", "reload_model", "reload_config"):
                if not store.session_user(ws.cookies.get(COOKIE)):
                    await ws.send_json({"type": "error", "error": "Not authenticated"})
                    continue
                if tr.tracker is None:
                    await ws.send_json({"type": "error", "error": "AI tracker not running"})
                    continue
                try:
                    await tr.tracker.send_json({"command": cmd})
                except Exception as exc:
                    await ws.send_json({"type": "error", "error": f"Command failed: {exc}"})
    except (WebSocketDisconnect, Exception):
        pass
    finally:
        tr.uis.discard(ws)
        tr.frame_uis.discard(ws)


@app.get("/api/ball-tracking/status")
def tracking_status():
    return _camera_status()

@app.get("/api/system/metrics")
def system_metrics(user=Depends(require_user)):
    out = {"cpu_percent": None, "memory_percent": None, "cpu_temp_c": None, "load_1m": None}
    try:
        import psutil
        out["cpu_percent"] = round(float(psutil.cpu_percent(interval=0.15)), 1)
        out["memory_percent"] = round(float(psutil.virtual_memory().percent), 1)
        vals = [x.current for group in psutil.sensors_temperatures().values() for x in group if getattr(x, "current", None) is not None]
        if vals: out["cpu_temp_c"] = round(float(max(vals)), 1)
    except Exception:
        pass
    try:
        out["load_1m"] = round(float(os.getloadavg()[0]), 2)
    except Exception:
        pass
    if out["cpu_temp_c"] is None:
        try:
            out["cpu_temp_c"] = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000.0, 1)
        except Exception: pass
    return out


@app.get("/api/health")
def health():
    return {"ok": True, "db": str(DB_PATH)}


@app.get("/api/system/info")
def system_info(user=Depends(require_user)):
    return {
        "version": system_manager.current_version(),
        "update": system_manager.check_update(),
    }


@app.get("/api/system/update/check")
def system_update_check(user=Depends(require_user)):
    return system_manager.check_update()


@app.post("/api/system/update/install")
async def system_update_install(request: Request, user=Depends(require_user)):
    body = await _body(request) or {}
    result = system_manager.install_update(body.get("package_url"), body.get("sha256"))
    if not result.get("ok"):
        raise HTTPException(409, result.get("error", "Update failed"))
    return result


@app.post("/api/system/restart")
def system_restart(user=Depends(require_user)):
    # Return the acknowledgement before the supervisor restarts the process.
    def _restart():
        time.sleep(0.5)
        try:
            stream_router.cleanup()
        except Exception:
            pass
        system_manager.restart_application()
    threading.Thread(target=_restart, daemon=True).start()
    return {"ok": True, "message": "Application restart scheduled"}


@app.post("/api/system/factory-reset")
def system_factory_reset(user=Depends(require_user)):
    try:
        stream_router.cleanup()
    except Exception:
        pass
    result = system_manager.factory_reset()
    threading.Thread(target=lambda: (time.sleep(1.0), system_manager.restart_application()), daemon=True).start()
    return result


try:
    from backend import streaming  # noqa: E402
except ImportError:  # started as `python backend/server.py`
    import streaming  # noqa: E402

stream_router = streaming.build_router(store, require_user, current_user, log, ROOT, camera_status_getter=_camera_status, camera_control=_send_tracker_command, frame_getter=_frame_getter, camera_discover=_discover_cameras)
app.include_router(stream_router)
import atexit  # noqa: E402
atexit.register(stream_router.cleanup)   # never leave ffmpeg processes running


# ------------------------------------------------------------------ page gate
PUBLIC_PAGES = {"/login.html", "/index.html", "/broadcast.html"}   # index.html = public overlay (OBS/PRISM browser source)


@app.middleware("http")
async def page_gate(request: Request, call_next):
    p = request.url.path
    if p == "/":
        return RedirectResponse("/dashboard.html")
    if p.endswith(".html") and p not in PUBLIC_PAGES:
        if not store.session_user(request.cookies.get(COOKIE)):
            return RedirectResponse("/login.html?next=" + p)
    return await call_next(request)


# ------------------------------------------------------------------ static
FRONTEND.mkdir(exist_ok=True)
app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn
    host = os.environ.get("HOST", "0.0.0.0")
    if host in ("127.0.0.1", "localhost"):
        log("HOST is localhost; overriding to 0.0.0.0 for LAN access")
        host = "0.0.0.0"
    port = int(os.environ.get("PORT", "8000"))
    log(f"Cricket Live on http://{host}:{port}  (DB: {DB_PATH})")
    uvicorn.run(app, host=host, port=port, log_level="warning")
