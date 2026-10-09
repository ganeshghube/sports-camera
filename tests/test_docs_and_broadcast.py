from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_broadcast_is_score_only():
    broadcast = (ROOT / "frontend" / "broadcast.html").read_text(encoding="utf-8")
    assert '/api/internal/raw-camera.mjpeg' in broadcast
    assert 'index.html?overlay=1' in broadcast
    assert 'tracking marker' not in broadcast.lower()
    assert 'ball marker' not in broadcast.lower()


def test_docs_do_not_advertise_separate_model_updates():
    readme = (ROOT / "README.md").read_text(encoding="utf-8").lower()
    assert 'application updates and model updates are separate' not in readme
    assert 'no separate model-update workflow' in readme
