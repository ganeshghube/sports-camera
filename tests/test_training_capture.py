"""Focused tests for the UI training-frame capture path."""
import pytest
from fastapi import HTTPException
from pathlib import Path

from backend import training_router


def test_training_page_provides_the_shell_mount_point():
    page = Path(__file__).parents[1] / "frontend" / "training.html"
    text = page.read_text(encoding="utf-8")

    assert '<div id="page">' in text
    assert text.index('<div id="page">') < text.index('<script src="js/app.js"></script>')


def test_capture_uses_the_existing_live_camera_frame():
    frame = b"\xff\xd8shared-camera-frame\xff\xd9"

    assert training_router._capture_bytes(b"", lambda: frame) == frame


def test_capture_reports_when_the_camera_has_no_frame():
    with pytest.raises(HTTPException, match="not delivering live frames") as error:
        training_router._capture_bytes(b"", lambda: None)

    assert error.value.status_code == 409


def test_capture_rejects_a_non_jpeg_frame():
    with pytest.raises(HTTPException, match="valid JPEG") as error:
        training_router._capture_bytes(b"not an image", lambda: None)

    assert error.value.status_code == 400
