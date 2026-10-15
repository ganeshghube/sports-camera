"""Simple recording/streaming router using the single shared vision camera feed.

The AI/vision worker is the only process that opens the physical camera.
FastAPI proxies its latest JPEG frames for browser preview and FFmpeg consumes
an internal MJPEG endpoint for recording/RTMP output. This avoids USB camera
contention and keeps preview/tracking/streaming on one source.
"""
from __future__ import annotations

import json
import os
import re
import secrets as pysecrets
import shutil
import subprocess
import threading
import time
import inspect
from pathlib import Path
from urllib.parse import urlencode

try:
    from .browser_compositor import BrowserBroadcastRenderer
except ImportError:
    from browser_compositor import BrowserBroadcastRenderer


from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse

DESTS = ("youtube", "facebook", "custom")
YT_INGEST_DEFAULT = "rtmp://a.rtmp.youtube.com/live2"
FB_INGEST_DEFAULT = "rtmps://live-api-s.facebook.com:443/rtmp/"


def _mask(v, keep=4):
    if not v:
        return ""
    return "•" * 8 + v[-keep:] if len(v) > keep else "•" * len(v)


def _redact_url(url):
    return re.sub(r"(//[^:/@]+:)[^@]+@", r"\1***@", url or "")


def build_router(store, require_user, current_user, log, root: Path,
                 camera_status_getter=None, camera_control=None,
                 frame_getter=None, camera_discover=None):
    r = APIRouter()
    rec_dir = Path(os.environ.get("RECORDINGS_DIR", root / "recordings"))
    rec_dir.mkdir(parents=True, exist_ok=True)
    lock = threading.RLock()

    # One place for image/video preview frames. The AI worker owns the actual
    # hardware capture and publishes JPEGs to FastAPI.
    camera_status_getter = camera_status_getter or (lambda: {"running": False, "status": "STOPPED"})
    camera_control = camera_control or (lambda _cmd: None)
    frame_getter = frame_getter or (lambda: None)
    camera_discover = camera_discover or (lambda probe=False: [])

    try:
        from .broadcast_overlay import BroadcastScoreOverlay
    except ImportError:
        from broadcast_overlay import BroadcastScoreOverlay
    broadcast_overlay = BroadcastScoreOverlay(store, root, log)
    # Exact renderer: captures the same HTML/CSS overlay that Live Preview uses.
    # It is intentionally lazy and non-blocking so camera/recording/YouTube never
    # waits for Chromium to start.
    def broadcast_auth_token():
        # The exact browser renderer must see the same live score data as the
        # operator's Live Preview. Give it a server-created session rather than
        # requiring/storing the user's password in Chromium.
        try:
            with store.lock:
                row = store.conn.execute("SELECT username FROM users ORDER BY rowid LIMIT 1").fetchone()
            if not row:
                return None
            return store.new_session(row[0])
        except Exception as exc:
            log(f"Broadcast renderer auth session failed: {exc}")
            return None

    browser_renderer = BrowserBroadcastRenderer(
        root, log, int(os.environ.get("PORT", "8000")),
        settings_getter=lambda: settings(),
        auth_token_getter=broadcast_auth_token,
    )

    def broadcast_frame(frame):
        """Render the same score presentation used by the public overlay into the
        actual video frames consumed by FFmpeg.

        The browser overlay is HTML/CSS, while YouTube only receives encoded video.
        Therefore the broadcast path must render the authoritative score data into
        every outgoing frame.  This compositor intentionally mirrors the public
        score bar/scorecard layout (team colour, score, batsmen, bowler, rates,
        target/free-hit badges and scorecard) rather than drawing a second, simpler
        overlay.
        """
        if not frame or not settings().get("broadcast_overlay", True):
            return frame
        try:
            import cv2
            import numpy as np

            arr = np.frombuffer(frame, dtype=np.uint8)
            img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if img is None:
                return frame
            h, w = img.shape[:2]
            d = store.get("cricket") or {}
            ui = store.get("ui") or {}
            if not isinstance(d, dict):
                d = {}
            if not isinstance(ui, dict):
                ui = {}
            show_bar = ui.get("scorebar", True) is not False
            show_card = ui.get("scorecard", False) is True
            if not show_bar and not show_card:
                return frame

            def team_color(name, fallback):
                n = str(name or "").upper()
                fixed = {
                    "INDIA": (147, 56, 0), "PAKISTAN": (71, 103, 0),
                    "AUSTRALIA": (42, 144, 200), "ENGLAND": (43, 20, 207),
                    "SRI LANKA": (120, 52, 0), "SOUTH AFRICA": (77, 122, 0),
                    "NEW ZEALAND": (42, 42, 42), "WEST INDIES": (65, 0, 123),
                    "BANGLADESH": (78, 106, 0), "ZIMBABWE": (0, 0, 212),
                    "AFGHANISTAN": (160, 50, 0), "IRELAND": (98, 155, 22),
                }
                for key, bgr in fixed.items():
                    if key in n:
                        return bgr
                # Stable colour similar to the frontend's HSL hash, converted to BGR.
                import colorsys
                hv = 0
                for c in n:
                    hv = ord(c) + ((hv << 5) - hv)
                rgb = colorsys.hls_to_rgb((abs(hv) % 360) / 360.0, 0.35, 0.65)
                return tuple(int(x * 255) for x in rgb[::-1]) if n else fallback

            def fit(text, max_chars):
                text = str(text or "—").strip()
                return text if len(text) <= max_chars else text[:max_chars - 1] + "…"

            def txt(text, x, y, size=0.7, color=(255,255,255), thick=2):
                cv2.putText(img, str(text), (int(x), int(y)),
                            cv2.FONT_HERSHEY_SIMPLEX, float(size), color,
                            max(1, int(thick)), cv2.LINE_AA)

            runs = int(d.get("runs") or 0)
            wkts = int(d.get("wkts") or 0)
            balls = int(d.get("balls") or 0)
            overs = f"{balls // 6}.{balls % 6}"
            max_overs = int(d.get("maxOvers") or 20)
            team = fit(d.get("batTeam") or "TEAM", 18)
            bowl_team = fit(d.get("bowlTeam") or "BOWLING", 18)
            bat_c = team_color(team, (176, 86, 26))
            bowl_c = team_color(bowl_team, (0, 103, 71))
            gold = (41, 180, 240)
            white = (245, 245, 245)
            muted = (165, 165, 165)
            dark = (14, 14, 24)
            border = (48, 48, 64)

            # Responsive dimensions. The browser overlay is ~96% of the video width.
            margin = max(12, int(w * 0.02))
            panel_w = min(int(w * 0.96), 1080)
            left = (w - panel_w) // 2
            right = left + panel_w
            bar_h = max(72, min(112, int(h * 0.115)))
            rate_h = max(26, int(bar_h * 0.32))
            bar_top = h - rate_h - bar_h

            target = int(d.get("target") or 0)
            need = max(0, target - runs) if target else 0
            balls_left = max(0, max_overs * 6 - balls)
            crr = (runs / (balls / 6.0)) if balls else 0.0
            rrr = ((target - runs) / (balls_left / 6.0)) if target and balls_left else None

            # Scorecard mirrors the public card: it sits above the score bar when enabled.
            if show_card:
                card_h = max(190, min(270, int(h * 0.30)))
                card_bottom = bar_top - 6
                card_top = max(margin, card_bottom - card_h)
                overlay = img.copy()
                cv2.rectangle(overlay, (left, card_top), (right, card_bottom), dark, -1)
                cv2.addWeighted(overlay, 0.96, img, 0.04, 0, img)
                cv2.rectangle(img, (left, card_top), (right, card_top + 4), gold, -1)
                # Header
                cv2.rectangle(img, (left, card_top + 4), (left + 8, card_top + 62), bat_c, -1)
                txt(team.upper(), left + 24, card_top + 40, 0.95, white, 2)
                txt(f"{max_overs} OVERS MATCH" if max_overs else "T20 MATCH",
                    left + 255, card_top + 39, 0.42, muted, 1)
                txt(f"{runs}/{wkts}", right - 170, card_top + 42, 0.95, gold, 2)
                txt(f"OVERS {overs}", right - 112, card_top + 60, 0.36, muted, 1)
                # Innings/target line
                inn = "2ND INNINGS" if int(d.get("innings") or 1) == 2 else "1ST INNINGS"
                txt(inn, left + 24, card_top + 84, 0.46, muted, 1)
                if target:
                    txt(f"TARGET {target}   NEED {need} OFF {balls_left}   RRR {rrr:.2f}" if rrr is not None
                        else f"TARGET {target}   NEED {need} OFF {balls_left}",
                        left + 190, card_top + 84, 0.43, gold, 1)
                # Batting rows
                p1 = fit(d.get("p1n") or "Bat 1", 24)
                p2 = fit(d.get("p2n") or "Bat 2", 24)
                p1v = f"{int(d.get('p1r') or 0)} ({int(d.get('p1b') or 0)})"
                p2v = f"{int(d.get('p2r') or 0)} ({int(d.get('p2b') or 0)})"
                y1, y2 = card_top + 120, card_top + 150
                if int(d.get("str") or 1) == 1: p1 = "* " + p1
                else: p2 = "* " + p2
                txt(p1, left + 28, y1, 0.56, gold if int(d.get("str") or 1) == 1 else white, 1)
                txt(p1v, left + 270, y1, 0.50, white, 1)
                txt(p2, left + 28, y2, 0.56, gold if int(d.get("str") or 1) == 2 else white, 1)
                txt(p2v, left + 270, y2, 0.50, white, 1)
                # Bowling column
                bowler = fit(d.get("bwn") or "Bowler", 22)
                bwb = int(d.get("bwb") or 0)
                bwl = f"{int(d.get('bwr') or 0)}-{int(d.get('bww') or 0)} ({bwb//6}.{bwb%6})"
                bx = left + max(420, panel_w // 2)
                txt("BOWLER", bx, y1 - 22, 0.36, muted, 1)
                txt(bowler, bx, y1, 0.56, white, 1)
                txt(bwl, bx, y2, 0.50, (160, 215, 120), 1)

            if show_bar:
                # Target / free-hit badges above the bar.
                badge_y = bar_top - 28
                if target:
                    badge_text = f"TARGET {target}  ·  NEED {need} OFF {balls_left}"
                    (tw, th), _ = cv2.getTextSize(badge_text, cv2.FONT_HERSHEY_SIMPLEX, 0.42, 1)
                    cv2.rectangle(img, (left, badge_y - th - 8), (left + tw + 24, badge_y + 4), (12, 130, 190), -1)
                    txt(badge_text, left + 12, badge_y - 3, 0.42, (0, 0, 0), 1)
                if d.get("freeHit"):
                    fx = left + 220 if target else left
                    cv2.rectangle(img, (fx, badge_y - 26), (fx + 145, badge_y + 4), (20, 105, 220), -1)
                    txt("FREE HIT", fx + 12, badge_y - 5, 0.42, white, 1)

                # Main score bar.
                cv2.rectangle(img, (left, bar_top), (right, bar_top + bar_h), dark, -1)
                cv2.rectangle(img, (left, bar_top), (right, bar_top + 3), bat_c, -1)
                team_w = min(250, max(190, int(panel_w * 0.22)))
                bowl_w = min(290, max(230, int(panel_w * 0.28)))
                bat_left = left + team_w
                bowl_left = right - bowl_w
                cv2.rectangle(img, (left, bar_top + 3), (bat_left, bar_top + bar_h), bat_c, -1)
                # dark gradient-ish blocks
                cv2.rectangle(img, (bat_left, bar_top + 3), (bowl_left, bar_top + bar_h), (16, 16, 28), -1)
                cv2.rectangle(img, (bowl_left, bar_top + 3), (right, bar_top + bar_h), (8, 42, 32), -1)
                txt(team.upper(), left + 18, bar_top + 28, 0.45, white, 1)
                txt(f"{runs}/{wkts}", left + 18, bar_top + 68, 1.00, white, 2)
                txt(f"({overs})", left + 138, bar_top + 67, 0.43, muted, 1)

                p1 = fit(d.get("p1n") or "Bat 1", 17)
                p2 = fit(d.get("p2n") or "Bat 2", 17)
                p1v = f"{int(d.get('p1r') or 0)}({int(d.get('p1b') or 0)})"
                p2v = f"{int(d.get('p2r') or 0)}({int(d.get('p2b') or 0)})"
                active = int(d.get("str") or 1)
                txt(("* " if active == 1 else "  ") + p1, bat_left + 18, bar_top + 30,
                    0.47, gold if active == 1 else (185,185,185), 1)
                txt(p1v, bowl_left - 135, bar_top + 30, 0.43, white, 1)
                txt(("* " if active == 2 else "  ") + p2, bat_left + 18, bar_top + 62,
                    0.47, gold if active == 2 else (185,185,185), 1)
                txt(p2v, bowl_left - 135, bar_top + 62, 0.43, white, 1)

                bowler = fit(d.get("bwn") or "Bowler", 18)
                bwb = int(d.get("bwb") or 0)
                bwl = f"{int(d.get('bww') or 0)}-{int(d.get('bwr') or 0)} ({bwb//6}.{bwb%6})"
                txt(bowler, bowl_left + 18, bar_top + 30, 0.48, white, 1)
                txt(bwl, bowl_left + 18, bar_top + 62, 0.48, gold, 1)

                # Rates strip exactly under the score bar.
                rate_top = bar_top + bar_h
                cv2.rectangle(img, (left, rate_top), (right, rate_top + rate_h), (4, 4, 8), -1)
                txt(f"CRR {crr:.2f}", left + 18, rate_top + int(rate_h * .68), 0.40, gold, 1)
                rx = left + 135
                if target:
                    txt(f"RRR {rrr:.2f}" if rrr is not None else "RRR INF", rx, rate_top + int(rate_h * .68), 0.40, (70, 70, 230), 1)
                    rx += 130
                if d.get("inn1Score"):
                    txt(f"{fit(d.get('inn1Bat'), 18)} {d.get('inn1Score')}", rx,
                        rate_top + int(rate_h * .68), 0.38, (200, 200, 215), 1)

            ok, out = cv2.imencode('.jpg', img, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            return out.tobytes() if ok else frame
        except Exception as exc:
            log(f"Broadcast compositor warning: {exc}")
            return frame

    with store.lock:
        store.conn.execute("CREATE TABLE IF NOT EXISTS secrets(k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        store.conn.commit()

    def kv_get(k, default=None):
        with store.lock:
            row = store.conn.execute("SELECT v FROM secrets WHERE k=?", (k,)).fetchone()
        return json.loads(row[0]) if row else default

    def kv_set(k, v):
        with store.lock:
            store.conn.execute(
                "INSERT INTO secrets(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v)),
            )
            store.conn.commit()

    def settings():
        s = kv_get("settings", {}) or {}
        return {
            "test_mode": bool(s.get("test_mode", True)),
            "camera_type": s.get("camera_type", "network"),
            "camera_device": s.get("camera_device", 0),
            "camera_name": s.get("camera_name", ""),
            "rtsp_url": s.get("rtsp_url") or os.environ.get("RTSP_URL", ""),
            "resolution": s.get("resolution", "1920x1080"),
            "fps": int(s.get("fps", 60)),
            "ai_device": s.get("ai_device", os.environ.get("DEVICE", "AUTO")),
            "encode": s.get("encode", "x264"),
            "video_bitrate_k": int(s.get("video_bitrate_k", 4500)),
            "broadcast_overlay": bool(s.get("broadcast_overlay", True)),
            "dest": s.get("dest", {}),
            "yt_meta": s.get("yt_meta", {"title": "Cricket Live", "description": "", "privacy": "unlisted", "category": "17", "latency": "ultraLow"}),
            "youtube_latency": str(s.get("youtube_latency") or os.environ.get("YOUTUBE_LATENCY", "ultraLow")),
            "youtube_resolution": str(s.get("youtube_resolution") or os.environ.get("YOUTUBE_RESOLUTION", "720p")),
            "youtube_fps": int(s.get("youtube_fps", os.environ.get("YOUTUBE_FPS", 30))),
            "youtube_bitrate_k": int(s.get("youtube_bitrate_k", os.environ.get("YOUTUBE_BITRATE_K", 3000))),
            "youtube_audio_bitrate_k": int(s.get("youtube_audio_bitrate_k", os.environ.get("YOUTUBE_AUDIO_BITRATE_K", 128))),
            "youtube_profile": str(s.get("youtube_profile", os.environ.get("YOUTUBE_PROFILE", "performance"))),
            "auto_camera_track": bool(s.get("auto_camera_track", True)),
            "min_track_confidence": float(s.get("min_track_confidence", 0.35)),
            "ai_max_fps": int(s.get("ai_max_fps", 20)),
            "motor_driver": str(s.get("motor_driver", os.environ.get("MOTOR_DRIVER", "none"))),
            "pan_step_pin": int(s.get("pan_step_pin", os.environ.get("PAN_STEP_PIN", 17))),
            "pan_dir_pin": int(s.get("pan_dir_pin", os.environ.get("PAN_DIR_PIN", 27))),
            "tilt_step_pin": int(s.get("tilt_step_pin", os.environ.get("TILT_STEP_PIN", 22))),
            "tilt_dir_pin": int(s.get("tilt_dir_pin", os.environ.get("TILT_DIR_PIN", 23))),
        }

    def public_settings():
        s = settings()
        out_dest = {}
        for name in DESTS:
            c = s["dest"].get(name, {})
            out_dest[name] = {
                "server_url": c.get("server_url", ""),
                "stream_key_hint": _mask(c.get("stream_key", "")),
                "has_stream_key": bool(c.get("stream_key")),
                "username": c.get("username", ""),
                "has_password": bool(c.get("password")),
                "configured": bool(c.get("stream_key") and (c.get("server_url") or name != "custom")),
                "oauth_connected": bool(kv_get(f"oauth_{name}")) if name != "custom" else None,
            }
        return {
            **s,
            "rtsp_url": _redact_url(s["rtsp_url"]),
            "has_rtsp": bool(s["rtsp_url"]),
            "dest": out_dest,
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "camera_status": camera_status_getter(),
            "oauth_available": {
                "youtube": bool(os.environ.get("GOOGLE_CLIENT_ID")),
                "facebook": bool(os.environ.get("FACEBOOK_APP_ID")),
            },
        }

    @r.get("/api/settings")
    def get_settings(user=Depends(require_user)):
        return public_settings()

    @r.put("/api/settings")
    async def put_settings(request: Request, user=Depends(require_user)):
        body = await request.json()
        s = kv_get("settings", {}) or {}
        old_camera_cfg = {k: s.get(k) for k in ("camera_type", "camera_device", "camera_name", "rtsp_url", "resolution", "fps", "ai_device")}
        allowed = (
            "test_mode", "camera_type", "camera_device", "camera_name", "resolution", "fps",
            "ai_device", "encode", "video_bitrate_k", "broadcast_overlay", "yt_meta",
            "youtube_latency", "youtube_resolution", "youtube_fps",
            "youtube_bitrate_k", "youtube_audio_bitrate_k", "youtube_profile",
            "auto_camera_track", "min_track_confidence", "ai_max_fps",
            "motor_driver", "pan_step_pin", "pan_dir_pin", "tilt_step_pin", "tilt_dir_pin"
        )
        for k in allowed:
            if k in body:
                s[k] = body[k]
        if body.get("rtsp_url"):
            s["rtsp_url"] = str(body["rtsp_url"]).strip()
        dest = s.setdefault("dest", {})
        for name, cfg in (body.get("dest") or {}).items():
            if name not in DESTS:
                continue
            cur = dest.setdefault(name, {})
            if "server_url" in cfg:
                cur["server_url"] = str(cfg.get("server_url") or "").strip()
            if "username" in cfg:
                cur["username"] = str(cfg.get("username") or "").strip()
            for secret in ("stream_key", "password"):
                if cfg.get(secret):
                    cur[secret] = str(cfg[secret]).strip()
            if cfg.get("clear_stream_key"):
                cur.pop("stream_key", None)
        kv_set("settings", s)

        runtime = root / "data" / "tracker_config.json"
        runtime.parent.mkdir(parents=True, exist_ok=True)
        existing_runtime = {}
        try:
            if runtime.exists(): existing_runtime = json.loads(runtime.read_text(encoding="utf-8"))
        except Exception:
            existing_runtime = {}
        runtime.write_text(json.dumps({
            "camera_type": s.get("camera_type", "network"),
            "camera_device": s.get("camera_device", 0),
            "camera_name": s.get("camera_name", ""),
            "rtsp_url": s.get("rtsp_url", ""),
            "resolution": s.get("resolution", "1920x1080"),
            "fps": int(s.get("fps", 60)),
            "ai_device": s.get("ai_device", "AUTO"),
            "model_path": existing_runtime.get("model_path") or os.environ.get("MODEL_PATH", "models/best.pt"),
            "auto_camera_track": bool(s.get("auto_camera_track", True)),
            "min_track_confidence": float(s.get("min_track_confidence", 0.35)),
            "ai_max_fps": int(s.get("ai_max_fps", 20)),
            "motor_driver": str(s.get("motor_driver", os.environ.get("MOTOR_DRIVER", "none"))),
            "pan_step_pin": int(s.get("pan_step_pin", os.environ.get("PAN_STEP_PIN", 17))),
            "pan_dir_pin": int(s.get("pan_dir_pin", os.environ.get("PAN_DIR_PIN", 27))),
            "tilt_step_pin": int(s.get("tilt_step_pin", os.environ.get("TILT_STEP_PIN", 22))),
            "tilt_dir_pin": int(s.get("tilt_dir_pin", os.environ.get("TILT_DIR_PIN", 23))),
        }, indent=2), encoding="utf-8")
        new_camera_cfg = {k: s.get(k) for k in ("camera_type", "camera_device", "camera_name", "rtsp_url", "resolution", "fps", "ai_device")}
        if new_camera_cfg != old_camera_cfg:
            try:
                # Only reload the vision worker when a camera/AI setting really
                # changed. Saving stream/YouTube settings must never interrupt
                # an active camera or recording.
                result = camera_control("reload_config")
                if inspect.isawaitable(result):
                    await result
            except Exception as exc:
                log(f"Camera config notification warning: {exc}")
        log("Settings updated")
        return public_settings()

    def ffmpeg_bin():
        p = shutil.which("ffmpeg")
        if not p:
            raise HTTPException(503, "FFmpeg is not installed or not on PATH.")
        return p

    def internal_mjpeg_url():
        return os.environ.get("INTERNAL_CAMERA_MJPEG_URL", "http://127.0.0.1:8000/api/internal/camera.mjpeg")

    def ensure_camera_ready(timeout=8.0):
        status = camera_status_getter() or {}
        if not status.get("running"):
            raise HTTPException(409, "Camera is stopped. Press Start Camera in Live Preview first.")
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = camera_status_getter() or {}
            if st.get("connected"):
                return st
            time.sleep(0.15)
        raise HTTPException(504, "Camera started but no video frames arrived. Check the selected camera.")

    class Job:
        def __init__(self, name, cmd, log_name):
            self.name = name
            self.cmd = cmd
            self.log_name = log_name
            self.status = "STARTING"
            self.error = None
            self.started = time.time()
            self.fps = None
            self.bitrate = None
            self.dropped = 0
            self.frames = 0
            self.speed = None
            self.duplicated = 0
            self.last_progress = time.monotonic()
            self.progress_age_ms = 0
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            self.proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                creationflags=flags,
                cwd=str(root),
            )
            self.tail = []
            threading.Thread(target=self._read_progress, daemon=True).start()
            threading.Thread(target=self._read_err, daemon=True).start()
            threading.Thread(target=self._watchdog, daemon=True).start()
            threading.Thread(target=self._wait, daemon=True).start()

        def _read_progress(self):
            for line in self.proc.stdout:
                if "=" not in line:
                    continue
                k, v = line.strip().split("=", 1)
                self.last_progress = time.monotonic()
                if k == "frame":
                    try:
                        self.frames = int(v)
                        if self.frames and self.status == "STARTING":
                            self.status = "LIVE"
                            if self.name in stream_desired:
                                with stream_restart_lock:
                                    stream_restart_attempts[self.name] = 0
                    except ValueError:
                        pass
                elif k == "fps":
                    self.fps = v
                elif k == "bitrate":
                    self.bitrate = v
                elif k in ("drop_frames", "drop_frames_count"):
                    try: self.dropped = int(float(v))
                    except ValueError: pass
                elif k in ("dup_frames", "dup_frames_count"):
                    try: self.duplicated = int(float(v))
                    except ValueError: pass
                elif k == "speed":
                    self.speed = v

        def _read_err(self):
            for line in self.proc.stderr:
                line = _redact_url(line.strip())
                if line:
                    self.tail = (self.tail + [line])[-10:]

        def _watchdog(self):
            # Treat both a stuck STARTING encoder and a stalled LIVE encoder as
            # recoverable failures. YouTube/RTMP can reject or close a session
            # before FFmpeg emits its first progress frame, so watching only the
            # LIVE state can leave the stream silently dead.
            while self.proc.poll() is None:
                time.sleep(3)
                if self.name not in stream_desired or self.status in ("STOPPING", "STOPPED", "ERROR"):
                    continue
                age = time.monotonic() - self.last_progress
                limit = 12 if self.status == "STARTING" else 15
                if age > limit:
                    log(f"{self.name.title()} encoder has produced no progress for {int(age)}s; restarting encoder")
                    try:
                        self.error = f"FFmpeg stalled for {int(age)}s"
                        self.proc.kill()
                    except Exception:
                        pass
                    return

        def _wait(self):
            code = self.proc.wait()
            intentional = self.status == "STOPPING" or self.name not in stream_desired
            if self.status != "STOPPING":
                self.status = "ERROR" if code else "STOPPED"
                if code:
                    self.error = self.tail[-1] if self.tail else f"FFmpeg exited with code {code}"
                elif self.name in stream_desired:
                    # A clean exit is still unexpected while the operator has
                    # asked this destination to remain live. This commonly
                    # happens when the remote RTMP endpoint closes a session.
                    self.error = "FFmpeg exited while live streaming was still requested"
            else:
                self.status = "STOPPED"
            if not intentional and self.name in stream_desired:
                _schedule_stream_restart(self.name)

        def stop(self):
            if not self.alive():
                return
            self.status = "STOPPING"
            try:
                self.proc.stdin.write("q\n")
                self.proc.stdin.flush()
            except Exception:
                pass
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()

        def alive(self):
            return self.proc.poll() is None

        def info(self):
            duration = max(0, int(time.time() - self.started)) if self.alive() else 0
            return {
                "status": self.status,
                "error": self.error,
                "fps": self.fps,
                "bitrate": self.bitrate,
                "dropped_frames": self.dropped,
                "duplicated_frames": self.duplicated,
                "speed": self.speed,
                "progress_age_ms": int(max(0, (time.monotonic() - self.last_progress) * 1000)),
                "duration_s": duration,
                "error_detail": self.error,
                "log_tail": self.tail[-3:] if self.tail else [],
            }

    streams = {}
    stream_desired = set()
    stream_restart_attempts = {}
    stream_restart_lock = threading.RLock()
    rec = {"job": None, "parts": [], "paused": False, "match": "match", "started": None, "base_stem": None, "part_no": 0, "final_name": None}

    def _schedule_stream_restart(name):
        """Reconnect a live encoder after an unexpected FFmpeg/network failure.

        A manual Stop removes the destination from stream_desired, so this never
        resurrects a stream the operator intentionally stopped. Transient YouTube
        ingest/network failures get bounded exponential backoff instead of leaving
        the broadcast permanently dead.
        """
        with stream_restart_lock:
            attempt = stream_restart_attempts.get(name, 0) + 1
            stream_restart_attempts[name] = attempt
        if attempt > 8:
            log(f"{name.title()} stream restart limit reached; manual restart required")
            return
        delay = min(30, 2 ** min(attempt - 1, 4))
        log(f"{name.title()} stream disconnected unexpectedly; reconnecting in {delay}s (attempt {attempt}/8)")
        def worker():
            if stream_desired.__contains__(name):
                time.sleep(delay)
            if name not in stream_desired:
                return
            try:
                start_dest(name, _from_restart=True)
            except Exception as exc:
                log(f"{name.title()} reconnect failed: {exc}")
                if name in stream_desired:
                    _schedule_stream_restart(name)
        threading.Thread(target=worker, name=f"{name}-reconnect", daemon=True).start()

    def common_input_args():
        # FFmpeg consumes the same JPEG stream shown in Live Preview.
        return [
            "-hide_banner", "-loglevel", "warning", "-fflags", "nobuffer",
            "-f", "mpjpeg", "-i", internal_mjpeg_url()
        ]

    def overlay_filter():
        # Kept for API compatibility. The internal MJPEG feed now carries the
        # composed score directly, so FFmpeg no longer reads mutable text files.
        return None

    def dest_url(name):
        s = settings()
        cfg = s["dest"].get(name, {})
        key = cfg.get("stream_key")
        if not key:
            raise HTTPException(409, f"{name.title()} is not configured. Add its stream key in Setup Camera / Streaming.")
        base = cfg.get("server_url") or {"youtube": YT_INGEST_DEFAULT, "facebook": FB_INGEST_DEFAULT}.get(name)
        if not base:
            raise HTTPException(409, "Custom RTMP server URL is missing.")
        return base.rstrip("/") + "/" + key

    def start_dest(name, _from_restart=False):
        s = settings()
        if s["test_mode"]:
            raise HTTPException(409, "TEST MODE is ON. Turn it OFF in Setup Camera / Streaming to go live.")
        # Validate destination before marking the stream as desired or requiring
        # a running camera so a configuration error can never trigger reconnects.
        # Validate destination before requiring a running camera so configuration
        # errors are reported clearly in the UI.
        if name == "youtube" and not settings()["dest"].get("youtube", {}).get("stream_key") and kv_get("oauth_youtube"):
            _prepare_youtube_destination()
        url = dest_url(name)
        ensure_camera_ready()
        stream_desired.add(name)
        if not _from_restart:
            with stream_restart_lock:
                stream_restart_attempts[name] = 0
        with lock:
            old = streams.get(name)
            if old and old.alive():
                return old.info()
            if name == "youtube":
                fps = max(30, min(60, int(s.get("youtube_fps", 60))))
                yres = str(s.get("youtube_resolution", "1080p"))
                dims = {"720p": (1280, 720), "1080p": (1920, 1080),
                        "1440p": (2560, 1440), "2160p": (3840, 2160)}
                width, height = dims.get(yres, (1920, 1080))
                profile = str(s.get("youtube_profile", "performance"))
                defaults = {"performance": 12000 if yres == "1080p" and fps == 60 else 10000,
                            "balanced": 14000 if yres == "1080p" and fps == 30 else 17000,
                            "quality": 17000}
                video_k = max(3000, min(60000, int(s.get("youtube_bitrate_k", defaults.get(profile, 12000)))))
                audio_k = max(64, min(384, int(s.get("youtube_audio_bitrate_k", 128))))
                preset = "ultrafast"
            else:
                fps = max(15, min(60, int(s.get("fps", 30))))
                width = height = None
                video_k = int(s.get("video_bitrate_k", 4500))
                audio_k = 96
                preset = os.environ.get("STREAM_X264_PRESET", "ultrafast")
            cmd = [ffmpeg_bin()] + ["-hide_banner", "-loglevel", "warning"] + [
                "-fflags", "nobuffer+discardcorrupt", "-flags", "low_delay",
                "-analyzeduration", "200000", "-probesize", "256k", "-thread_queue_size", "8",
                "-use_wallclock_as_timestamps", "1",
                "-f", "mpjpeg", "-i", internal_mjpeg_url(),
                "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-map", "0:v:0", "-map", "1:a:0",
                "-r", str(fps),
                "-c:v", "libx264", "-preset", preset,
                "-tune", "zerolatency",
                "-bf", "0",
                "-threads", "0",
                "-x264-params", "rc-lookahead=0:sync-lookahead=0:scenecut=0",
                "-profile:v", "high" if name == "youtube" and fps <= 60 else "main",
            ]
            if width and height:
                cam = camera_status_getter() or {}
                cw, ch = int(cam.get("frame_width") or 0), int(cam.get("frame_height") or 0)
                if (cw, ch) != (width, height):
                    cmd += ["-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"]
            cmd += [
                "-b:v", f"{video_k}k",
                "-maxrate", f"{video_k}k",
                "-bufsize", f"{max(video_k * 2, video_k)}k",
                "-g", str(fps * 2), "-keyint_min", str(fps),
                "-sc_threshold", "0",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", f"{audio_k}k",
                "-ar", "44100", "-ac", "2",
                "-fps_mode", "cfr",
                "-flush_packets", "1",
                "-muxdelay", "0", "-muxpreload", "0",
                "-flvflags", "no_duration_filesize",
            ]
            # Job reads key=value progress from stdout. Without -progress,
            # FFmpeg's stdout stays empty and the UI incorrectly remains STARTING.
            cmd += ["-progress", "pipe:1", "-nostats", "-f", "flv", url]
            streams[name] = Job(name, cmd, f"{name.title()} stream")
            return streams[name].info()

    @r.get("/api/stream/status")
    def stream_status():
        s = settings()
        out = {}
        for n in DESTS:
            cfg = s["dest"].get(n, {})
            configured = bool(cfg.get("stream_key") and (cfg.get("server_url") or n != "custom"))
            job = streams.get(n)
            out[n] = {"configured": configured, **job.info()} if job and (job.alive() or job.status == "ERROR") else {
                "configured": configured,
                "status": "READY" if configured else "NOT CONFIGURED",
                "error": None, "fps": None, "bitrate": None, "dropped_frames": 0, "duplicated_frames": 0, "speed": None, "progress_age_ms": None, "duration_s": 0, "error_detail": None, "log_tail": [],
            }
            if out[n].get("status") == "LIVE":
                age = out[n].get("progress_age_ms") or 999999
                out[n]["ingest_health"] = "GOOD" if age < 3000 and (out[n].get("speed") in (None, "N/A") or str(out[n].get("speed")).startswith(("0.", "1."))) else "DEGRADED"
            else:
                out[n]["ingest_health"] = "OFFLINE"
        live = any(v["status"] == "LIVE" for v in out.values())
        return {
            "test_mode": s["test_mode"],
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "camera_configured": True,
            "camera": camera_status_getter(),
            "live": live,
            "destinations": out,
            "overlay_in_stream": bool(s.get("broadcast_overlay", True)),
            "exact_live_preview_overlay": bool(browser_renderer.ready),
            "broadcast_renderer_error": browser_renderer.error,
            "pipeline": {
                "camera_to_ai_ms": (camera_status_getter() or {}).get("latency_ms"),
                "ai_inference_ms": (camera_status_getter() or {}).get("inference_ms"),
                "encoder_progress_age_ms": out.get("youtube", {}).get("progress_age_ms"),
                "encoder_fps": out.get("youtube", {}).get("fps"),
                "encoder_speed": out.get("youtube", {}).get("speed"),
                "dropped_frames": out.get("youtube", {}).get("dropped_frames", 0),
                "ingest_health": out.get("youtube", {}).get("ingest_health", "OFFLINE"),
                "measurement_note": "These are Pi-side timings. Exact viewer playback delay inside YouTube is not directly observable from the encoder.",
            },
            "note": "YouTube/RTMP receives the exact Live Preview HTML/CSS score bar and scorecard rendered by Chromium; FFmpeg does not depend on a score text file.",
        }

    @r.post("/api/stream/{dest}/start")
    async def stream_start(dest: str, user=Depends(require_user)):
        if dest == "all":
            results, errors = {}, {}
            for name in DESTS:
                if settings()["dest"].get(name, {}).get("stream_key"):
                    try:
                        results[name] = start_dest(name)
                    except HTTPException as exc:
                        errors[name] = exc.detail
            if not results and not errors:
                raise HTTPException(409, "No streaming destination is configured.")
            return {"started": results, "errors": errors}
        if dest not in DESTS:
            raise HTTPException(404, "Unknown destination")
        return start_dest(dest)

    @r.post("/api/stream/stop")
    def stream_stop(dest: str | None = None, user=Depends(require_user)):
        for n in list(stream_desired):
            if dest in (None, "", "all", n):
                stream_desired.discard(n)
                with stream_restart_lock:
                    stream_restart_attempts[n] = 0
        for n, job in list(streams.items()):
            if dest in (None, "", "all", n) and job.alive():
                job.stop()
        return stream_status()

    # -------------------------------------------------------- recording
    def safe_name(name):
        return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_") or "match"

    def start_recording_part():
        s = settings()
        ensure_camera_ready()
        if not rec.get("base_stem"):
            stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
            rec["base_stem"] = f"{safe_name(rec['match'])}_{stamp}"
        rec["part_no"] = int(rec.get("part_no") or 0) + 1
        suffix = f"_part{rec['part_no']:02d}" if rec["part_no"] > 1 else ""
        path = rec_dir / f"{rec['base_stem']}{suffix}.mp4"
        n = 1
        while path.exists():
            path = rec_dir / f"{rec['base_stem']}{suffix}_{n}.mp4"
            n += 1
        cmd = [ffmpeg_bin()] + common_input_args() + ["-map", "0:v:0"]
        fps = max(15, min(60, int(s.get("fps", 30))))
        cmd += [
            "-r", str(fps),
            "-c:v", "libx264", "-preset", os.environ.get("RECORD_X264_PRESET", "veryfast"),
            "-tune", "zerolatency",
            "-b:v", f"{int(s['video_bitrate_k'])}k", "-maxrate", f"{int(s['video_bitrate_k'])}k",
            "-bufsize", f"{int(s['video_bitrate_k'])}k", "-g", str(fps * 2),
            "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart",
            "-progress", "pipe:1", "-nostats", str(path)
        ]
        rec["job"] = Job("recording", cmd, "Recording")
        rec["parts"].append(path.name)

    def validate_recording_file(path: Path) -> bool:
        if not path.exists() or path.stat().st_size < 1024:
            return False
        probe = shutil.which("ffprobe")
        if not probe:
            return True
        try:
            rr = subprocess.run(
                [probe, "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
                capture_output=True, text=True, timeout=8,
            )
            return rr.returncode == 0 and float((rr.stdout or "0").strip() or 0) > 0
        except Exception:
            return False

    def merge_recording_parts():
        """Create one playable MP4 for the logical match and remove part files."""
        parts = [rec_dir / n for n in rec.get("parts", []) if (rec_dir / n).exists()]
        valid = [p for p in parts if validate_recording_file(p)]
        if not valid:
            return None
        if len(valid) == 1:
            rec["final_name"] = valid[0].name
            return valid[0].name
        stem = rec.get("base_stem") or safe_name(rec.get("match") or "match")
        final = rec_dir / f"{stem}.mp4"
        if final.exists():
            final = rec_dir / f"{stem}_complete.mp4"
        manifest = rec_dir / f".{stem}_concat.txt"
        try:
            manifest.write_text("\n".join("file '" + p.name.replace("'", "'\\''") + "'" for p in valid) + "\n", encoding="utf-8")
            probe = shutil.which("ffmpeg")
            if not probe:
                return valid[-1].name
            cmd = [probe, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                   "-i", str(manifest), "-c", "copy", "-movflags", "+faststart", str(final)]
            rr = subprocess.run(cmd, cwd=str(rec_dir), capture_output=True, text=True, timeout=120)
            if rr.returncode != 0 or not validate_recording_file(final):
                # Re-encode only if stream parameters differ between paused parts.
                rr = subprocess.run([probe, "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
                                     "-i", str(manifest), "-c:v", "libx264", "-preset", "veryfast",
                                     "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(final)],
                                    cwd=str(rec_dir), capture_output=True, text=True, timeout=300)
            if rr.returncode != 0 or not validate_recording_file(final):
                final.unlink(missing_ok=True)
                return valid[-1].name
            for p in valid:
                if p != final:
                    p.unlink(missing_ok=True)
            rec["final_name"] = final.name
            return final.name
        except Exception as exc:
            log(f"Recording merge warning: {exc}")
            return valid[-1].name
        finally:
            manifest.unlink(missing_ok=True)

    def finalize_recording_parts():
        kept = []
        for name in rec.get("parts", []):
            path = rec_dir / name
            if validate_recording_file(path):
                kept.append(name)
            elif path.exists():
                try:
                    path.unlink()
                    log(f"Removed incomplete recording: {path.name}")
                except OSError as exc:
                    log(f"Could not remove incomplete recording {path.name}: {exc}")
        rec["parts"] = kept
        if kept:
            merged = merge_recording_parts()
            rec["parts"] = [merged] if merged else kept

    @r.post("/api/recording/{action}")
    async def recording(action: str, request: Request, user=Depends(require_user)):
        with lock:
            job = rec["job"]
            if action == "start":
                if job and job.alive():
                    return recording_status()
                if rec.get("started") and not rec.get("paused") and rec.get("final_name") is None:
                    # A previous recording stopped unexpectedly; finalize its
                    # valid files before beginning another session.
                    finalize_recording_parts()
                    rec["started"] = None
                try:
                    body = await request.json()
                except Exception:
                    body = {}
                rec.update(match=safe_name(body.get("match_name") or "match"), parts=[], paused=False, started=time.time(), base_stem=None, part_no=0, final_name=None)
                start_recording_part()
            elif action == "pause":
                if not (job and job.alive()):
                    raise HTTPException(409, "Recording is not running.")
                job.stop()
                # Keep the paused part; it will be merged with subsequent parts on Stop.
                rec["parts"] = [n for n in rec.get("parts", []) if validate_recording_file(rec_dir / n)]
                rec["paused"] = True
            elif action == "resume":
                if not rec["paused"]:
                    raise HTTPException(409, "Recording is not paused.")
                rec["paused"] = False
                start_recording_part()
            elif action == "stop":
                if job and job.alive():
                    job.stop()
                finalize_recording_parts()
                rec["paused"] = False
                rec["started"] = None
            else:
                raise HTTPException(404, "Unknown recording action")
        return recording_status()

    @r.get("/api/recording/status")
    def recording_status():
        job = rec["job"]
        if rec["paused"]:
            st = "PAUSED"
        elif job and job.alive():
            st = job.status
        elif job and job.status == "ERROR":
            st = "ERROR"
        else:
            st = "IDLE"
        return {
            "status": st,
            "error": job.error if job and job.status == "ERROR" else None,
            "files": list(rec["parts"]),
            "ffmpeg": bool(shutil.which("ffmpeg")),
            "duration_s": int(time.time() - rec["started"]) if rec["started"] and st in ("STARTING", "LIVE", "PAUSED") else 0,
            "match": rec.get("match"), "final_name": rec.get("final_name"),
        }

    def recording_group_key(name):
        stem = Path(name).stem
        stem = re.sub(r"_part\d{2}(?:_\d+)?$", "", stem, flags=re.I)
        stem = re.sub(r"_complete$", "", stem, flags=re.I)
        return stem

    @r.get("/api/recordings")
    def list_recordings(user=Depends(require_user)):
        files = [p for p in rec_dir.glob("*.mp4") if p.is_file()]
        groups = {}
        for p in files:
            key = recording_group_key(p.name)
            groups.setdefault(key, []).append(p)
        out = []
        for key, items in groups.items():
            items.sort(key=lambda p: p.stat().st_mtime, reverse=True)
            # Prefer a merged/final file when present; otherwise expose the newest part.
            preferred = next((p for p in items if "_part" not in p.stem.lower()), items[0])
            out.append({
                "name": preferred.name,
                "match": key.replace("_", " "),
                "size": sum(p.stat().st_size for p in items),
                "modified": max(p.stat().st_mtime for p in items),
                "parts": [p.name for p in items],
                "merged": len(items) == 1,
            })
        return sorted(out, key=lambda x: x["modified"], reverse=True)

    @r.get("/recordings/{name}")
    def get_recording(name: str, user=Depends(require_user)):
        p = (rec_dir / name).resolve()
        if p.parent != rec_dir.resolve() or not p.is_file():
            raise HTTPException(404, "Recording not found")
        return FileResponse(p)

    @r.delete("/api/recordings/{name}")
    def delete_recording(name: str, user=Depends(require_user)):
        p = (rec_dir / name).resolve()
        if p.parent != rec_dir.resolve() or not p.is_file():
            raise HTTPException(404, "Recording not found")
        # Never delete the file currently being written.
        if any(job and job.alive() and job.name == "recording" for job in [rec["job"]]):
            active = {x for x in rec.get("parts", [])}
            if p.name in active:
                raise HTTPException(409, "This recording is currently active. Stop recording first.")
        try:
            p.unlink()
        except OSError as exc:
            raise HTTPException(500, f"Unable to delete recording: {exc}")
        return {"ok": True, "name": name}

    # -------------------------------------------------------- shared vision camera
    @r.get("/api/cameras")
    def cameras(probe: bool = False, user=Depends(require_user)):
        return {
            "cameras": camera_discover(probe=probe),
            "selected": {
                "type": settings().get("camera_type"),
                "device": settings().get("camera_device"),
                "name": settings().get("camera_name"),
                "rtsp_url": _redact_url(settings().get("rtsp_url", "")),
            },
        }

    @r.get("/api/camera/status")
    def camera_status(user=Depends(require_user)):
        return camera_status_getter()

    @r.post("/api/camera/start")
    async def camera_start(user=Depends(require_user)):
        try:
            result = await camera_control("start_camera")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, f"Unable to start camera: {exc}")
        return result or camera_status_getter()

    @r.post("/api/camera/stop")
    async def camera_stop(user=Depends(require_user)):
        result = await camera_control("stop_camera")
        return result or camera_status_getter()

    @r.post("/api/camera/restart")
    async def camera_restart(user=Depends(require_user)):
        result = await camera_control("restart_camera")
        return result or camera_status_getter()

    @r.get("/api/camera/mjpeg")
    def camera_mjpeg(user=Depends(require_user)):
        if not camera_status_getter().get("running"):
            raise HTTPException(409, "Camera is stopped. Start Camera in Live Preview.")

        def stream():
            last = None
            while True:
                frame = frame_getter()
                if not frame:
                    time.sleep(0.08)
                    continue
                if frame == last:
                    time.sleep(0.02)
                    continue
                last = frame
                yield b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-cache\r\n\r\n" + frame + b"\r\n"
                time.sleep(0.01)

        return StreamingResponse(stream(), media_type="multipart/x-mixed-replace; boundary=frame")

    def _local_only(request: Request):
        host = request.client.host if request.client else ""
        if host not in ("127.0.0.1", "::1", "localhost"):
            raise HTTPException(403, "Local camera feed only")

    @r.get("/api/internal/raw-camera.mjpeg")
    def internal_raw_camera_mjpeg(request: Request):
        """Raw camera feed used as the background by the exact browser renderer."""
        _local_only(request)

        def stream():
            last = None
            while True:
                status = camera_status_getter() or {}
                if not status.get("running"):
                    time.sleep(0.15)
                    continue
                frame = frame_getter()
                if frame and frame != last:
                    last = frame
                    yield b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-cache\r\n\r\n" + frame + b"\r\n"
                else:
                    time.sleep(0.02)

        return StreamingResponse(stream(), media_type="multipart/x-mixed-replace; boundary=frame")

    
    @r.get("/api/internal/camera.mjpeg")
    def internal_camera_mjpeg(request: Request):
        """Feed changed, freshly composed camera frames to FFmpeg."""
        _local_only(request)
        browser_renderer.start()

        def stream():
            last_sent = None

            while True:
                status = camera_status_getter() or {}
                if not status.get("running"):
                    last_sent = None
                    time.sleep(0.15)
                    continue

                raw = frame_getter()
                if not raw:
                    time.sleep(0.02)
                    continue

                exact = browser_renderer.get_frame(None, max_age=1.0)
                if exact is None:
                    exact = broadcast_frame(raw)

                # Do not send the same composed image repeatedly.
                if exact and exact != last_sent:
                    last_sent = exact
                    yield (
                        b"--frame\r\n"
                        b"Content-Type: image/jpeg\r\n"
                        b"Cache-Control: no-cache\r\n\r\n"
                        + exact
                        + b"\r\n"
                    )
                else:
                    # Give the camera and browser renderer time to publish
                    # the next frame instead of busy-looping on stale images.
                    time.sleep(0.01)

        return StreamingResponse(
            stream(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache, no-store"},
        )


    # -------------------------------------------------------- OAuth
    def base_url(request):
        configured = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
        if configured:
            return configured.rsplit("/api/oauth/", 1)[0].rstrip("/")
        return os.environ.get("PUBLIC_URL") or str(request.base_url).rstrip("/")

    def oauth_redirect_uri(provider, request):
        if provider == "youtube":
            explicit = os.environ.get("GOOGLE_OAUTH_REDIRECT_URI", "").strip()
            if explicit:
                return explicit
            return f"{base_url(request)}/api/oauth/youtube/callback"
        return f"{base_url(request)}/api/oauth/{provider}/callback"

    def oauth_cfg(provider):
        if provider == "youtube":
            cid, sec = os.environ.get("GOOGLE_CLIENT_ID"), os.environ.get("GOOGLE_CLIENT_SECRET")
        elif provider == "facebook":
            cid, sec = os.environ.get("FACEBOOK_APP_ID"), os.environ.get("FACEBOOK_APP_SECRET")
        else:
            raise HTTPException(404, "Unknown provider")
        if not cid or not sec:
            raise HTTPException(501, f"{provider.title()} OAuth is not configured in .env")
        return cid, sec

    @r.get("/api/oauth/{provider}/login")
    def oauth_login(provider: str, request: Request, user=Depends(require_user)):
        cid, _ = oauth_cfg(provider)
        state = pysecrets.token_urlsafe(16)
        kv_set(f"oauth_state_{state}", {"provider": provider, "ts": time.time()})
        redirect = oauth_redirect_uri(provider, request)
        if provider == "youtube":
            q = urlencode({
                "client_id": cid, "redirect_uri": redirect, "response_type": "code", "state": state,
                "scope": "https://www.googleapis.com/auth/youtube", "access_type": "offline", "prompt": "consent",
            })
            return {"url": "https://accounts.google.com/o/oauth2/v2/auth?" + q}
        q = urlencode({"client_id": cid, "redirect_uri": redirect, "state": state,
                       "scope": "pages_show_list,pages_manage_posts,publish_video"})
        return {"url": "https://www.facebook.com/v19.0/dialog/oauth?" + q}

    @r.get("/api/oauth/{provider}/callback")
    def oauth_callback(provider: str, request: Request, code: str = "", state: str = "", error: str = "",
                       user=None):
        import httpx
        if error:
            return RedirectResponse(f"/settings.html?oauth={provider}&err={error}")
        st = kv_get(f"oauth_state_{state}")
        if not st or st["provider"] != provider or time.time() - st["ts"] > 600:
            raise HTTPException(400, "Invalid OAuth state")
        cid, sec = oauth_cfg(provider)
        redirect = oauth_redirect_uri(provider, request)
        if provider == "youtube":
            resp = httpx.post("https://oauth2.googleapis.com/token", data={"code": code, "client_id": cid,
                "client_secret": sec, "redirect_uri": redirect, "grant_type": "authorization_code"}, timeout=20)
        else:
            resp = httpx.get("https://graph.facebook.com/v19.0/oauth/access_token", params={"code": code,
                "client_id": cid, "client_secret": sec, "redirect_uri": redirect}, timeout=20)
        if resp.status_code != 200:
            return RedirectResponse(f"/settings.html?oauth={provider}&err=token_exchange_failed")
        kv_set(f"oauth_{provider}", {**resp.json(), "obtained": time.time()})
        return RedirectResponse(f"/settings.html?oauth={provider}&ok=1")

    def _yt_token():
        import httpx
        t = kv_get("oauth_youtube")
        if not t:
            raise HTTPException(409, "YouTube is not connected. Use Setup Camera / Streaming or enter a stream key.")
        if time.time() > t["obtained"] + t.get("expires_in", 3600) - 60:
            if not t.get("refresh_token"):
                raise HTTPException(401, "YouTube authentication expired — reconnect.")
            cid, sec = oauth_cfg("youtube")
            rr = httpx.post("https://oauth2.googleapis.com/token", data={
                "client_id": cid, "client_secret": sec, "refresh_token": t["refresh_token"],
                "grant_type": "refresh_token"}, timeout=20)
            if rr.status_code != 200:
                raise HTTPException(401, "YouTube authentication expired — reconnect.")
            t = {**t, **rr.json(), "obtained": time.time()}
            kv_set("oauth_youtube", t)
        return t["access_token"]

    def _youtube_quality():
        s = settings()
        res = str(s.get("youtube_resolution", "720p"))
        fps = 60 if int(s.get("youtube_fps", 30)) >= 60 else 30
        bitrate = int(s.get("youtube_bitrate_k", 3000))
        audio = int(s.get("youtube_audio_bitrate_k", 128))
        defaults = {
            ("720p", 30): 8000, ("720p", 60): 8000,
            ("1080p", 30): 14000, ("1080p", 60): 17000,
            ("1440p", 30): 21000, ("1440p", 60): 34000,
            ("2160p", 30): 42000, ("2160p", 60): 50000,
        }
        bitrate = max(3000, min(60000, bitrate))
        audio = max(64, min(384, audio))
        return res, fps, bitrate, audio, defaults.get((res, fps), 17000)

    def _youtube_latency():
        s = settings()
        latency = str(s.get("youtube_latency", "ultraLow"))
        if str(s.get("youtube_resolution", "720p")) == "2160p":
            return "normal"
        return latency if latency in {"normal", "low", "ultraLow"} else "normal"

    def _prepare_youtube_destination():
        """Create a YouTube live target automatically after OAuth connection."""
        import httpx
        tok = _yt_token()
        headers = {"Authorization": f"Bearer {tok}"}
        meta = settings()["yt_meta"]
        start = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 60))
        b = httpx.post("https://www.googleapis.com/youtube/v3/liveBroadcasts?part=snippet,status,contentDetails",
            headers=headers, json={"snippet": {"title": meta.get("title") or "Cricket Live",
            "description": meta.get("description", ""), "scheduledStartTime": start},
            "status": {"privacyStatus": meta.get("privacy", "unlisted"), "selfDeclaredMadeForKids": False},
            "contentDetails": {"enableAutoStart": True, "enableAutoStop": False, "enableDvr": True, "recordFromStart": True, "latencyPreference": _youtube_latency()}}, timeout=20)
        if b.status_code != 200:
            raise HTTPException(502, f"YouTube broadcast creation failed: {b.text[:300]}")
        ls = httpx.post("https://www.googleapis.com/youtube/v3/liveStreams?part=snippet,cdn", headers=headers,
            json={"snippet": {"title": "Cricket Live stream"},
                   "cdn": {"frameRate": f"{_youtube_quality()[1]}fps", "ingestionType": "rtmp", "resolution": _youtube_quality()[0]}}, timeout=20)
        if ls.status_code != 200:
            raise HTTPException(502, f"YouTube stream creation failed: {ls.text[:300]}")
        stream_json = ls.json()
        bind = httpx.post("https://www.googleapis.com/youtube/v3/liveBroadcasts/bind",
            params={"id": b.json()["id"], "part": "id", "streamId": stream_json["id"]},
            headers=headers, timeout=20)
        if bind.status_code >= 300:
            raise HTTPException(502, f"YouTube bind failed: {bind.text[:300]}")
        ing = stream_json["cdn"]["ingestionInfo"]
        ingest_url = ing.get("rtmpsIngestionAddress") or ing.get("ingestionAddress")
        cur = kv_get("settings", {}) or {}
        cur.setdefault("dest", {})["youtube"] = {"server_url": ingest_url, "stream_key": ing["streamName"]}
        kv_set("settings", cur)
        return b.json()["id"]

    @r.post("/api/stream/youtube/prepare")
    def yt_prepare(user=Depends(require_user)):
        import httpx
        tok = _yt_token()
        headers = {"Authorization": f"Bearer {tok}"}
        meta = settings()["yt_meta"]
        start = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() + 60))
        b = httpx.post("https://www.googleapis.com/youtube/v3/liveBroadcasts?part=snippet,status,contentDetails",
            headers=headers, json={"snippet": {"title": meta.get("title") or "Cricket Live",
            "description": meta.get("description", ""), "scheduledStartTime": start},
            "status": {"privacyStatus": meta.get("privacy", "unlisted"), "selfDeclaredMadeForKids": False},
            "contentDetails": {"enableAutoStart": True, "enableAutoStop": False, "enableDvr": True, "recordFromStart": True, "latencyPreference": _youtube_latency()}}, timeout=20)
        if b.status_code != 200:
            raise HTTPException(502, f"YouTube API error: {b.text[:500]}")
        s = httpx.post("https://www.googleapis.com/youtube/v3/liveStreams?part=snippet,cdn", headers=headers,
                       json={"snippet": {"title": "Cricket Live stream"},
                              "cdn": {"frameRate": f"{_youtube_quality()[1]}fps", "ingestionType": "rtmp", "resolution": _youtube_quality()[0]}}, timeout=20)
        if s.status_code != 200:
            raise HTTPException(502, f"YouTube API error: {s.text[:500]}")
        sj = s.json()
        bind = httpx.post("https://www.googleapis.com/youtube/v3/liveBroadcasts/bind",
                          params={"id": b.json()["id"], "part": "id", "streamId": sj["id"]},
                          headers=headers, timeout=20)
        if bind.status_code >= 300:
            raise HTTPException(502, f"YouTube bind error: {bind.text[:500]}")
        ing = sj["cdn"]["ingestionInfo"]
        cur = kv_get("settings", {}) or {}
        cur.setdefault("dest", {})["youtube"] = {"server_url": ing.get("rtmpsIngestionAddress") or ing["ingestionAddress"], "stream_key": ing["streamName"]}
        kv_set("settings", cur)
        return {"ok": True, "broadcast_id": b.json()["id"], "watch_url": f"https://youtube.com/watch?v={b.json()['id']}"}

    def shutdown():
        for job in list(streams.values()) + ([rec["job"]] if rec["job"] else []):
            try:
                if job and job.alive():
                    job.stop()
            except Exception:
                pass
        try:
            broadcast_overlay.stop()
        except Exception:
            pass
        try:
            browser_renderer.stop()
        except Exception:
            pass

    r.cleanup = shutdown
    r.get_settings = settings
    return r