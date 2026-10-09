from pathlib import Path


def test_ffmpeg_watchdog_covers_starting_and_live_states():
    text = Path('backend/streaming.py').read_text()
    assert 'limit = 12 if self.status == "STARTING" else 15' in text
    assert 'FFmpeg stalled for' in text


def test_requested_stream_restarts_after_clean_unexpected_ffmpeg_exit():
    text = Path('backend/streaming.py').read_text()
    assert 'FFmpeg exited while live streaming was still requested' in text
    assert 'if not intentional and self.name in stream_desired:' in text


def test_mjpeg_input_has_startup_buffer_tolerance():
    text = Path('backend/streaming.py').read_text()
    assert '"-probesize", "256k"' in text
    assert '"-thread_queue_size", "8"' in text
