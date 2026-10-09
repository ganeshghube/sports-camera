from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
def test_windows_camera_reader_has_backend_fallbacks_and_auto_mode():
    text=(ROOT/'ai'/'ball_tracker.py').read_text(); assert 'CAP_MSMF' in text; assert 'CAP_DSHOW' in text; assert 'CAP_ANY' in text; assert 'USB camera selected' in text
def test_camera_discovery_checks_real_frames():
    text=(ROOT/'backend'/'server.py').read_text(); assert 'CAP_MSMF' in text; assert 'test_frame' not in text or 'frame is not None' in text
def test_websocket_client_is_declared():
    assert 'websocket-client' in (ROOT/'requirements.txt').read_text()

def test_rtsp_capture_requests_short_tcp_timeouts():
    text=(ROOT/'ai'/'ball_tracker.py').read_text()
    assert 'rtsp_transport;tcp|stimeout;5000000|rw_timeout;5000000' in text
    assert 'CAP_PROP_OPEN_TIMEOUT_MSEC' in text
    assert 'CAP_PROP_READ_TIMEOUT_MSEC' in text
    assert 'RTSP camera opened but produced no frames within 5.5s' in text

def test_windows_discovery_suppresses_transient_opencv_probe_noise():
    text=(ROOT/'backend'/'server.py').read_text()
    assert '_cv2.setLogLevel(_cv2.LOG_LEVEL_ERROR)' in text
    assert '_cv2.setLogLevel(_old_log_level)' in text
