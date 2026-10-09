"""Single-command launcher for Cricket Live.

Run:
    python run.py

Starts FastAPI and the AI tracker as supervised sibling processes.
The AI process remains idle until Ball Tracking -> Start Tracking is pressed.
"""
from __future__ import annotations

import os
import signal
import subprocess
import asyncio
import sys
import time
from pathlib import Path


CORE_IMPORTS = ("fastapi", "uvicorn", "httpx", "dotenv", "playwright", "psutil", "websocket")
AI_IMPORTS = ("cv2", "numpy", "openvino", "ultralytics", "serial")

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
os.environ.setdefault("HOST", "0.0.0.0")
os.environ.setdefault("PORT", "8000")
os.environ.setdefault("BACKEND_WS_URL", "ws://127.0.0.1:8000/ws/ball-tracking")

tracker = None


def _missing_imports(names):
    missing = []
    for name in names:
        try:
            __import__(name)
        except Exception:
            missing.append(name)
    return missing


def _ensure_pip():
    """Make sure the active interpreter has pip before installing dependencies."""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "--version"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        print("[launcher] pip is missing; bootstrapping pip in this Python environment...", flush=True)
        try:
            subprocess.check_call([sys.executable, "-m", "ensurepip", "--upgrade"])
            return True
        except Exception as exc:
            print(f"[launcher] Could not bootstrap pip: {exc}", flush=True)
            return False


def ensure_project_dependencies():
    """Self-heal an incomplete .venv before importing the application.

    This is intentionally run before importing FastAPI/Uvicorn. A common
    Windows/Git Bash failure mode is an existing .venv with no packages, which
    otherwise produces `ModuleNotFoundError: fastapi` and stops the launcher.
    """
    missing_core = _missing_imports(CORE_IMPORTS)
    missing_ai = _missing_imports(AI_IMPORTS)
    if not missing_core and not missing_ai:
        return

    print("[launcher] Missing Python packages detected:",
          ", ".join(missing_core + missing_ai), flush=True)
    if not _ensure_pip():
        print("[launcher] Please recreate the virtual environment and install requirements.txt.", flush=True)
        raise SystemExit(2)

    requirements = []
    if missing_core:
        requirements.append(ROOT / "requirements.txt")
    if missing_ai:
        requirements.append(ROOT / "ai" / "requirements.txt")

    for req in requirements:
        print(f"[launcher] Installing project dependencies from {req.relative_to(ROOT)}...", flush=True)
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req)])
        except Exception as exc:
            print(f"[launcher] Dependency installation failed: {exc}", flush=True)
            print("[launcher] Try: python -m pip install -r requirements.txt && python -m pip install -r ai/requirements.txt", flush=True)
            raise SystemExit(2)

    still_missing = _missing_imports(CORE_IMPORTS + AI_IMPORTS)
    if still_missing:
        print("[launcher] Dependencies are still missing after installation:",
              ", ".join(still_missing), flush=True)
        raise SystemExit(2)
    print("[launcher] Python dependencies ready.", flush=True)


def ensure_browser_renderer_dependencies():
    """Make the exact broadcast renderer usable on a fresh user venv.

    The full dependency bootstrap above normally installs Playwright. This
    helper remains as a defensive fallback for environments where an import
    failed after the initial dependency check.
    """
    try:
        import playwright.sync_api  # noqa: F401
        return
    except Exception:
        pass
    print("[launcher] Playwright is missing; installing it for the broadcast renderer...", flush=True)
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "playwright>=1.50,<2"])
    except Exception as exc:
        print(f"[launcher] Playwright installation failed: {exc}", flush=True)
        raise SystemExit(2)



def stop_tracker(*_):
    global tracker
    if tracker is not None and tracker.poll() is None:
        try:
            tracker.terminate()
            tracker.wait(timeout=3)
        except Exception:
            try:
                tracker.kill()
            except Exception:
                pass
    tracker = None


def main():
    global tracker
    # Do not replace Windows\' default asyncio loop. Python/Playwright require
    # the Windows Proactor loop for subprocess-backed browser drivers.
    # In particular, forcing SelectorEventLoop here makes Playwright fail with
    # NotImplementedError inside browser_compositor.py.
    ensure_project_dependencies()
    ensure_browser_renderer_dependencies()
    # Start backend in the same Python process via uvicorn; tracker is separate.
    from backend.server import app
    import uvicorn

    tracker = subprocess.Popen(
        [sys.executable, str(ROOT / "ai" / "ball_tracker.py")],
        cwd=str(ROOT),
        env=os.environ.copy(),
    )
    signal.signal(signal.SIGINT, stop_tracker)
    signal.signal(signal.SIGTERM, stop_tracker)
    print("[launcher] Cricket Live server + AI tracker starting...", flush=True)
    try:
        uvicorn.run(app, host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")),
                    log_level="warning")
    finally:
        stop_tracker()


if __name__ == "__main__":
    main()
