"""Pixel-accurate broadcast compositor.

The public Live Preview renders the score overlay with the real HTML/CSS in
frontend/index.html. A Python/OpenCV reimplementation can only approximate that
presentation. This module therefore renders the *same browser page* headlessly:

    raw camera MJPEG + index.html?overlay=1 iframe -> Chromium screenshot

The resulting JPEG is the exact visual composition used by Live Preview and is
fed directly to FFmpeg/YouTube. If Chromium becomes unavailable, callers can
fall back to their existing compositor/raw frame so streaming is never stopped.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path


class BrowserBroadcastRenderer:
    def __init__(self, root: Path, log, port: int = 8000, settings_getter=None, auth_token_getter=None):
        self.root = Path(root)
        self.log = log
        self.port = int(port)
        self.settings_getter = settings_getter
        self.auth_token_getter = auth_token_getter
        self._lock = threading.RLock()
        self._latest = None
        self._latest_at = 0.0
        self._thread = None
        self._stop = threading.Event()
        self._started = False
        self._ready = False
        self._error = None

    @property
    def ready(self):
        with self._lock:
            return self._ready and self._latest is not None

    @property
    def error(self):
        with self._lock:
            return self._error

    def start(self):
        with self._lock:
            if self._started:
                return
            self._started = True
        self._thread = threading.Thread(target=self._run, name="broadcast-browser", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()

    def get_frame(self, fallback: bytes | None = None, max_age: float = 1.0):
        """Return the newest exact-browser frame, or fallback immediately.

        This method never blocks the camera/FFmpeg path. A stale/dead Chromium
        renderer therefore cannot stop a YouTube stream.
        """
        with self._lock:
            if self._latest is not None and (time.monotonic() - self._latest_at) <= max_age:
                return self._latest
        return fallback

    def _find_browser(self, playwright):
        explicit = os.environ.get("CRICKET_CHROME_PATH", "").strip().strip('"')
        candidates = []
        if explicit:
            candidates.append(explicit)
        if os.name == "nt":
            local = os.environ.get("LOCALAPPDATA", "")
            program = os.environ.get("PROGRAMFILES", r"C:\\Program Files")
            program86 = os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)")
            candidates += [
                str(Path(local) / "Google/Chrome/Application/chrome.exe"),
                str(Path(program) / "Google/Chrome/Application/chrome.exe"),
                str(Path(program86) / "Google/Chrome/Application/chrome.exe"),
                str(Path(local) / "Microsoft/Edge/Application/msedge.exe"),
                str(Path(program) / "Microsoft/Edge/Application/msedge.exe"),
                str(Path(program86) / "Microsoft/Edge/Application/msedge.exe"),
            ]
        else:
            candidates += [
                shutil.which("google-chrome"), shutil.which("chromium"),
                shutil.which("chromium-browser"), shutil.which("microsoft-edge"),
            ]
        candidates = [c for c in candidates if c and Path(c).exists()]
        if candidates:
            return candidates[0]
        try:
            bundled = playwright.chromium.executable_path
            if bundled and Path(bundled).exists():
                return bundled
        except Exception:
            pass
        return None

    def _install_bundled_browser(self):
        try:
            self.log("No Chrome/Edge/Chromium found; installing the Playwright Chromium browser once...")
            subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"], timeout=300)
            return True
        except Exception as exc:
            self.log(f"Playwright Chromium installation failed: {exc}")
            return False

    def _target_viewport(self):
        dims = {"720p": (1280, 720), "1080p": (1920, 1080),
                "1440p": (2560, 1440), "2160p": (3840, 2160)}
        try:
            cfg = self.settings_getter() if self.settings_getter else {}
            return dims.get(str(cfg.get("youtube_resolution", "1080p")), (1280, 720))
        except Exception:
            return (1280, 720)

    def _run(self):
        # Playwright launches its driver through an asyncio subprocess. On
        # Windows that requires ProactorEventLoop; this thread must establish
        # its own Proactor loop because it does not inherit the main thread's
        # loop instance. Without this, Python 3.14 can raise
        # NotImplementedError from loop.subprocess_exec().
        thread_loop = None
        if os.name == "nt":
            try:
                import asyncio
                proactor = getattr(asyncio, "ProactorEventLoop", None)
                if proactor is not None:
                    thread_loop = proactor()
                    asyncio.set_event_loop(thread_loop)
            except Exception as exc:
                self.log(f"Broadcast renderer asyncio setup warning: {exc}")
        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            with self._lock:
                self._error = f"Playwright is not installed: {exc}"
            self.log(f"Exact broadcast renderer unavailable: {self._error}")
            return

        try:
            with sync_playwright() as p:
                executable = self._find_browser(p)
                launch = {
                    "headless": True,
                    "args": [
                        "--disable-gpu",
                        "--disable-dev-shm-usage",
                        "--disable-background-networking",
                        "--disable-background-timer-throttling",
                        "--disable-renderer-backgrounding",
                    ],
                }
                if executable:
                    launch["executable_path"] = executable
                elif os.name != "nt":
                    if self._install_bundled_browser():
                        executable = self._find_browser(p)
                    launch["args"].append("--no-sandbox")
                else:
                    if self._install_bundled_browser():
                        executable = self._find_browser(p)
                    if not executable:
                        raise RuntimeError(
                            "No Chrome/Edge/Chromium executable found. Install Chrome/Edge or run: python -m playwright install chromium"
                        )

                browser = p.chromium.launch(**launch)
                width, height = self._target_viewport()
                context = browser.new_context(
                    viewport={"width": width, "height": height},
                    device_scale_factor=1,
                    color_scheme="dark",
                )
                # Live Preview runs inside an authenticated operator session.
                # The headless broadcast renderer has no browser session of its
                # own, so without this cookie the overlay page can load but its
                # local DB requests receive 401 and redirect to login.html.
                # Create a server-side session for the renderer; this avoids
                # needing the user's password and also works when the password
                # was generated automatically on first run.
                if self.auth_token_getter:
                    try:
                        token = self.auth_token_getter()
                        if token:
                            context.add_cookies([
                                {
                                    "name": "cl_session",
                                    "value": str(token),
                                    "domain": "127.0.0.1",
                                    "path": "/",
                                    "httpOnly": True,
                                    "secure": False,
                                    "sameSite": "Lax",
                                }
                            ])
                            self.log("Broadcast renderer authenticated for score database")
                    except Exception as exc:
                        self.log(f"Broadcast renderer authentication warning: {exc}")
                page = context.new_page()
                page.set_default_timeout(8000)
                url = f"http://127.0.0.1:{self.port}/broadcast.html"
                self.log(f"Exact broadcast renderer opening {url}")
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                # Give the overlay's font/layout and local database listeners a
                # moment to settle before the first frame. The live loop below
                # continues immediately afterwards.
                page.wait_for_timeout(1200)

                with self._lock:
                    self._ready = True
                    self._error = None
                self.log("Exact browser score overlay renderer ready")

                interval = 1.0 / max(10, min(30, int(os.environ.get("BROADCAST_BROWSER_FPS", "30"))))
                current_viewport = (width, height)
                while not self._stop.is_set():
                    started = time.monotonic()
                    try:
                        target_viewport = self._target_viewport()
                        if target_viewport != current_viewport:
                            page.set_viewport_size({"width": target_viewport[0], "height": target_viewport[1]})
                            current_viewport = target_viewport
                            self.log(f"Broadcast renderer output changed to {target_viewport[0]}x{target_viewport[1]}")
                        shot = page.screenshot(type="jpeg", quality=92)
                        with self._lock:
                            self._latest = shot
                            self._latest_at = time.monotonic()
                            self._ready = True
                    except Exception as exc:
                        with self._lock:
                            self._error = str(exc)
                            self._ready = False
                        self.log(f"Exact broadcast screenshot warning: {exc}")
                        # Try to recover the page without taking down FFmpeg.
                        try:
                            page.reload(wait_until="domcontentloaded", timeout=10000)
                            page.wait_for_timeout(500)
                            with self._lock:
                                self._ready = True
                        except Exception:
                            pass
                    remaining = interval - (time.monotonic() - started)
                    if remaining > 0:
                        self._stop.wait(remaining)
                context.close()
                browser.close()
        except Exception as exc:
            with self._lock:
                self._error = str(exc)
                self._ready = False
            self.log(f"Exact broadcast renderer stopped: {exc}")
        finally:
            if thread_loop is not None:
                try:
                    thread_loop.close()
                except Exception:
                    pass
