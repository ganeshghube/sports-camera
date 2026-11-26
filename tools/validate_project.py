"""
Comprehensive validation test for Cricket Live.
Runs within standard sandbox (no network sockets needed).
"""
import sys
import os
import pathlib
import json

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

def test_routes():
    from backend.server import app, system_ping
    
    all_paths = set()
    for r in app.routes:
        if hasattr(r, "path") and r.path:
            all_paths.add(r.path)
        elif hasattr(r, "original_router"):
            for sub in r.original_router.routes:
                if hasattr(sub, "path") and sub.path:
                    all_paths.add(sub.path)
    
    # Check essential endpoints
    assert "/api/system/ping" in all_paths, "Missing /api/system/ping"
    assert "/api/system/restart" in all_paths, "Missing /api/system/restart"
    assert "/api/camera/restart" in all_paths, "Missing /api/camera/restart"
    assert "/api/training/auto/status" in all_paths, "Missing /api/training/auto/status"
    assert "/api/training/models/rollback" in all_paths, "Missing /api/training/models/rollback"
    assert "/api/training/reload-model" in all_paths, "Missing /api/training/reload-model"
    
    # Test ping directly
    p = system_ping()
    assert p["ok"] is True
    assert "version" in p
    print("✓ Backend routes and /api/system/ping verified")

def test_store():
    from backend.server import Store
    import tempfile
    import secrets
    
    tmp = tempfile.mkdtemp()
    db_file = os.path.join(tmp, "val_test.db")
    s = Store(db_file)
    s.ensure_admin()
    
    # Insert user and verify auth
    salt = secrets.token_hex(16)
    s.conn.execute("INSERT INTO users VALUES(?,?,?,?)",
                   ("testuser", salt, s._hash("secret123", salt), "admin"))
    s.conn.commit()
    assert s.verify("testuser", "secret123") is True
    assert s.verify("testuser", "wrong") is False
    
    # Verify CRUD
    s.set("cricket/runs", 10)
    assert s.get("cricket/runs") == 10
    s.update("cricket", {"wkts": 2, "overs": 3.4})
    assert s.get("cricket/wkts") == 2
    assert s.get("cricket/overs") == 3.4
    
    # Sessions
    tok = s.new_session("testuser")
    assert s.session_user(tok) == "testuser"
    s.end_session(tok)
    assert s.session_user(tok) is None
    print("✓ Database store and authentication operations verified")

def test_ball_tracker_zoom_and_scanner():
    from ai.ball_tracker import calculate_zoom, PanTiltController
    
    # Center ball -> min zoom
    z_center = calculate_zoom(640, 360, 1280, 720)
    assert z_center == 1.0, f"Expected 1.0 at center, got {z_center}"
    
    # Edge ball -> higher zoom
    z_edge = calculate_zoom(50, 50, 1280, 720)
    assert z_edge > 1.0, f"Expected > 1.0 near edge, got {z_edge}"
    
    # PanTiltController Linux/Android ports list
    controller = PanTiltController(port="auto", baudrate=115200)
    ports = getattr(controller, "attempted_ports", [])
    assert any("USB" in p for p in ports), f"Missing Linux /dev/ttyUSB ports in candidate list: {ports}"
    assert any("rfcomm" in p for p in ports), f"Missing Bluetooth rfcomm ports in candidate list: {ports}"
    print("✓ AI ball tracker zoom calculation and motor port scanner verified")

def test_frontend_nav_and_zoom():
    app_js = (ROOT / "frontend" / "js" / "app.js").read_text(encoding="utf-8")
    preview_html = (ROOT / "frontend" / "preview.html").read_text(encoding="utf-8")
    system_html = (ROOT / "frontend" / "system.html").read_text(encoding="utf-8")
    app_css = (ROOT / "frontend" / "css" / "app.css").read_text(encoding="utf-8")
    
    # Check NAV has training.html
    assert '["training.html", "🎯", "AI Training", "training"]' in app_js, "training.html not registered as AI Training in app.js NAV"
    assert "CL.restartApp" in app_js, "CL.restartApp missing in app.js"
    
    # Check slow zoom in preview.html (ease = 0.021, maxZoomRate = 0.15, dt slew rate)
    assert "const ease = 0.021;" in preview_html, "preview.html ease not set to 0.021"
    assert "const maxZoomRate = 0.15;" in preview_html, "preview.html maxZoomRate not set to 0.15"
    assert "lastDetectionAt < 1400" in preview_html, "preview.html hold duration not set to 1400ms"
    assert "restartCamBtn" in preview_html, "restartCamBtn missing in preview.html"
    
    # Check system.html buttons
    assert "restartCam" in system_html, "restartCam button missing in system.html"
    assert "reloadTracker" in system_html, "reloadTracker button missing in system.html"
    
    # Check CSS modal
    assert ".cl-restart-overlay" in app_css, "cl-restart-overlay missing in app.css"
    print("✓ Frontend navigation, 50% slower auto-zoom, and restart UI verified")

def test_android_scripts():
    setup_sh = (ROOT / "tools" / "setup_android.sh").read_text(encoding="utf-8")
    start_sh = (ROOT / "start_android.sh").read_text(encoding="utf-8")
    req_android = (ROOT / "requirements-android.txt").read_text(encoding="utf-8")
    
    # Check setup script
    assert "requirements-android.txt" in setup_sh, "setup_android.sh missing requirements-android.txt reference"
    assert "qrencode" in setup_sh, "qrencode missing in setup_android.sh"
    
    # Check start script
    assert "HOTSPOT_IP" in start_sh, "start_android.sh missing HOTSPOT_IP detection"
    assert "qrencode" in start_sh, "start_android.sh missing QR code generation"
    assert "setup_android.sh" in start_sh, "start_android.sh missing auto-installer check"
    
    # Check Android dependencies (no openvino package)
    active_pkgs = [l.strip().lower() for l in req_android.splitlines() if l.strip() and not l.strip().startswith("#")]
    assert not any("openvino" in p for p in active_pkgs), "openvino should not be an active dependency in requirements-android.txt"
    assert any("ultralytics" in p for p in active_pkgs), "ultralytics missing in requirements-android.txt"
    print("✓ Android setup, launcher, and dependency configurations verified")

def test_broadcast_auto_zoom():
    import time
    broadcast_html = (ROOT / "frontend" / "broadcast.html").read_text(encoding="utf-8")
    assert "broadcast-viewport" in broadcast_html, "broadcast-viewport missing in broadcast.html"
    assert "ws/ball-tracking-ui" in broadcast_html, "WebSocket listener missing in broadcast.html"
    assert "ease = 0.021" in broadcast_html, "ease = 0.021 missing in broadcast.html"
    assert "maxZoomRate = 0.15" in broadcast_html, "maxZoomRate = 0.15 missing in broadcast.html"
    assert "lastDetectionAt < 1400" in broadcast_html, "lastDetectionAt < 1400 missing in broadcast.html"
    assert "targetZoom" in broadcast_html, "targetZoom logic missing in broadcast.html"
    assert "index.html?overlay=1" in broadcast_html, "scoreOverlay missing in broadcast.html"

    # Test Python fallback broadcast_frame with auto-zoom
    import cv2
    import numpy as np
    from backend import streaming

    dummy_img = np.zeros((360, 640, 3), dtype=np.uint8)
    dummy_img[100:260, 200:440] = (255, 255, 255)
    ok, frame_bytes = cv2.imencode(".jpg", dummy_img)
    assert ok

    class MockTracker:
        def __init__(self):
            self.status = {"auto_zoom": True, "camera_running": True}
            self.last_detection = {
                "detected": True,
                "timestamp": time.time(),
                "zoom_factor": 2.5,
                "x": 320, "y": 180,
                "frame_width": 640, "frame_height": 360
            }

    mock_tr = MockTracker()
    from backend.server import store, require_user, current_user, log
    router = streaming.build_router(
        store, require_user, current_user, log, ROOT,
        tracker_holder=mock_tr
    )

    # Test direct execution of broadcast_frame with auto-zoom
    out_frame = router.broadcast_frame(frame_bytes)
    assert out_frame is not None and len(out_frame) > 0, "broadcast_frame failed to return frame"
    dec = cv2.imdecode(np.frombuffer(out_frame, dtype=np.uint8), cv2.IMREAD_COLOR)
    assert dec is not None and dec.shape[0] == 360 and dec.shape[1] == 640
    print("✓ YouTube broadcast auto-zoom renderer and OpenCV fallback verified")

if __name__ == "__main__":
    print(f"--- Running Cricket Live Full Validation (Python {sys.version.split()[0]}) ---")
    test_routes()
    test_store()
    test_ball_tracker_zoom_and_scanner()
    test_frontend_nav_and_zoom()
    test_android_scripts()
    test_broadcast_auto_zoom()
    print("\n🎉 ALL TESTS AND VALIDATIONS PASSED CLEANLY!")
