from pathlib import Path


def test_launcher_bootstraps_dependencies_before_fastapi_import():
    text = Path('run.py').read_text()
    assert 'def ensure_project_dependencies()' in text
    assert 'active interpreter' in text
    assert 'ensurepip' in text
    assert 'requirements.txt' in text
    assert 'ai" / "requirements.txt' in text
    assert 'ensure_project_dependencies()' in text
    assert text.index('ensure_project_dependencies()') < text.index('from backend.server import app')


def test_launcher_checks_core_and_ai_imports():
    text = Path('run.py').read_text()
    for name in ('fastapi', 'uvicorn', 'cv2', 'numpy', 'openvino', 'ultralytics'):
        assert f'"{name}"' in text
