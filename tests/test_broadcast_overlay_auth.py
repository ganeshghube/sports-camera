from pathlib import Path


def test_broadcast_renderer_supports_authenticated_score_database():
    text = Path('backend/browser_compositor.py').read_text()
    assert 'auth_token_getter=None' in text
    assert 'context.add_cookies' in text
    assert 'name": "cl_session"' in text
    assert 'Broadcast renderer authenticated for score database' in text


def test_streaming_creates_server_session_for_broadcast_renderer():
    text = Path('backend/streaming.py').read_text()
    assert 'def broadcast_auth_token()' in text
    assert 'store.new_session(row[0])' in text
    assert 'auth_token_getter=broadcast_auth_token' in text


def test_broadcast_still_uses_exact_preview_overlay_page():
    text = Path('frontend/broadcast.html').read_text()
    assert 'src="/index.html?overlay=1"' in text
    assert '/api/internal/raw-camera.mjpeg' in text
