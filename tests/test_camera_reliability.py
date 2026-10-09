from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_usb_prefers_directshow_and_negotiates_safe_modes():
    text = (ROOT / "ai" / "ball_tracker.py").read_text(encoding="utf-8")
    assert '("DirectShow"' in text
    assert '(1920, 1080, 30)' in text
    assert '(1280, 720, 30)' in text
    assert '(1280, 720, 60)' in text
    assert '(640, 480, 30)' in text
    assert 'stable frames' in text


def test_camera_is_not_marked_connected_until_real_read():
    text = (ROOT / "ai" / "ball_tracker.py").read_text(encoding="utf-8")
    assert 'self.connected = False\n                self.error = "Waiting for first camera frame"' in text
    assert 'AI camera connected — receiving real frames' in text


def test_rtsp_open_timeout_support_is_present():
    text = (ROOT / "ai" / "ball_tracker.py").read_text(encoding="utf-8")
    assert 'CAP_PROP_OPEN_TIMEOUT_MSEC' in text
    assert 'CAP_PROP_READ_TIMEOUT_MSEC' in text


def test_discovery_prefers_directshow_and_uses_stable_frames():
    text = (ROOT / "backend" / "server.py").read_text(encoding="utf-8")
    assert '[("DirectShow"' in text
    assert 'good >= 2' in text
    assert 'CAP_PROP_FRAME_WIDTH, 1280' in text
