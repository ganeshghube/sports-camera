"""Backend tests: run with  python -m pytest tests -q   (or python tests/test_backend.py)"""
import os
import sys
import tempfile

tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(tmp, "t.db")
os.environ["ADMIN_USERNAME"] = "admin"
os.environ["ADMIN_PASSWORD"] = "testpass123"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastapi.testclient import TestClient  # noqa: E402
from backend import server  # noqa: E402

c = TestClient(server.app)


def test_auth_and_crud():
    assert c.get("/api/me").json()["authenticated"] is False
    assert c.put("/api/db/cricket", json={"runs": 1}).status_code == 401  # write needs login
    assert c.post("/api/login", json={"username": "admin", "password": "bad"}).status_code == 401
    r = c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    assert r.status_code == 200 and "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/api/me").json()["authenticated"] is True

    assert c.put("/api/db/cricket", json={"runs": 0, "wkts": 0, "p1n": "A"}).status_code == 200
    assert c.patch("/api/db/cricket", json={"runs": 4, "recentBalls": [4]}).status_code == 200
    d = c.get("/api/db/cricket").json()
    assert d["runs"] == 4 and d["wkts"] == 0 and d["recentBalls"] == [4]
    assert c.get("/api/db/cricket/p1n").json() == "A"
    assert c.get("/api/db/nothing/here").json() is None

    k1 = c.post("/api/db/undoStack", json={"n": 1}).json()["key"]
    k2 = c.post("/api/db/undoStack", json={"n": 2}).json()["key"]
    assert k1 < k2 and len(c.get("/api/db/undoStack").json()) == 2
    c.delete(f"/api/db/undoStack/{k2}")
    assert list(c.get("/api/db/undoStack").json()) == [k1]
    c.put("/api/db/undoStack", json=None)
    assert c.get("/api/db/undoStack").json() is None


def test_live_updates():
    c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    with c.websocket_connect("/ws/db") as ws:
        ws.send_json({"type": "sub", "path": "ui"})
        assert ws.receive_json()["value"] is None
        c.patch("/api/db/ui", json={"scorebar": True})
        m = ws.receive_json()
        assert m["path"] == "ui" and m["value"] == {"scorebar": True}


def test_persistence_across_restart():
    c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    c.put("/api/db/squads", json={"teamA": "X"})
    s2 = server.Store(server.DB_PATH)
    assert s2.get("squads/teamA") == "X"


def test_tracker_hub():
    # tracker must be local; TestClient host is 'testclient' -> rejected
    try:
        with c.websocket_connect("/ws/ball-tracking"):
            pass
        assert False, "non-local tracker should be rejected"
    except Exception:
        pass
    assert c.get("/api/ball-tracking/status").json()["ai_connected"] is False
    with c.websocket_connect("/ws/ball-tracking-ui") as ui:
        assert ui.receive_json()["type"] == "status"


def test_page_gate_and_public_overlay():
    anon = TestClient(server.app, follow_redirects=False)
    assert anon.get("/dashboard.html").status_code in (302, 307)      # needs login
    assert anon.get("/admin.html").status_code in (302, 307)
    assert anon.get("/login.html").status_code == 200
    assert anon.get("/index.html").status_code == 200                  # public overlay (browser source)
    assert anon.get("/").headers["location"] == "/dashboard.html"
    assert anon.get("/api/settings").status_code == 401


def test_settings_secrets_never_returned():
    c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    r = c.put("/api/settings", json={
        "rtsp_url": "rtsp://user:hunter2@10.0.0.5/live",
        "dest": {"custom": {"server_url": "rtmps://example.com/live", "stream_key": "SUPERSECRETKEY1234"}}})
    assert r.status_code == 200
    txt = r.text + c.get("/api/settings").text
    assert "hunter2" not in txt and "SUPERSECRETKEY" not in txt
    s = c.get("/api/settings").json()
    assert s["dest"]["custom"]["configured"] is True and s["dest"]["custom"]["stream_key_hint"].endswith("1234")
    assert "SUPERSECRETKEY" not in c.get("/api/db/").text


def test_stream_is_honest_and_test_mode_blocks(monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    st = c.get("/api/stream/status").json()
    assert st["live"] is False and st["destinations"]["youtube"]["status"] == "NOT CONFIGURED"
    assert st["destinations"]["custom"]["status"] == "READY"
    c.put("/api/settings", json={"test_mode": True})
    r = c.post("/api/stream/custom/start")
    assert r.status_code == 409 and "TEST MODE" in r.json()["detail"]
    c.put("/api/settings", json={"test_mode": False})
    r = c.post("/api/stream/youtube/start")
    assert r.status_code == 409 and "not configured" in r.json()["detail"].lower()
    assert c.get("/api/stream/status").json()["live"] is False
    c.put("/api/settings", json={"test_mode": True})
    assert c.get("/api/oauth/youtube/login").status_code == 501       # no Google client configured


def test_recording_requires_source():
    c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    assert c.get("/api/recording/status").json()["status"] == "IDLE"
    c2 = TestClient(server.app)
    assert c2.post("/api/recording/start").status_code == 401


def test_tracker_commands_need_login_and_a_running_ai():
    with c.websocket_connect("/ws/ball-tracking-ui") as ui:
        assert ui.receive_json()["ai_connected"] is False
        ui.send_json({"subscribe": "frames"})
        ui.send_json({"command": "start_tracking"})          # logged in, but no AI process connected
        assert ui.receive_json()["error"] == "AI tracker not running"


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("PASS", name)


def test_camera_configuration_and_system_pages():
    c.post("/api/login", json={"username": "admin", "password": "testpass123"})
    r = c.put("/api/settings", json={
        "camera_type": "usb", "camera_device": 0, "camera_name": "USB Camera 0",
        "resolution": "1280x720", "fps": 30, "ai_device": "AUTO"
    })
    assert r.status_code == 200
    s = c.get("/api/settings").json()
    assert s["camera_type"] == "usb" and str(s["camera_device"]) == "0"
    assert c.get("/api/camera/status").status_code == 200
    assert c.get("/api/cameras").status_code == 200
    assert c.get("/system.html", follow_redirects=False).status_code == 200
    info = c.get("/api/system/info").json()
    assert "version" in info and "update" in info


def test_public_scorebar_page():
    anon = TestClient(server.app, follow_redirects=False)
    r = anon.get("/index.html?scorebar=1")
    assert r.status_code == 200
    assert "scorebar-only" in r.text



def test_exact_broadcast_renderer_page_and_stream_status():
    anon = TestClient(server.app, follow_redirects=False)
    r = anon.get('/broadcast.html')
    assert r.status_code == 200
    assert '/api/internal/raw-camera.mjpeg' in r.text
    assert 'index.html?overlay=1' in r.text
    c.post('/api/login', json={'username': 'admin', 'password': 'testpass123'})
    st = c.get('/api/stream/status').json()
    assert 'exact_live_preview_overlay' in st
    assert 'broadcast_renderer_error' in st
