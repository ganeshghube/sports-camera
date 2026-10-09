"""Application system administration: versions, updates, restart and factory reset."""
from __future__ import annotations

import hashlib
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path


class SystemManager:
    def __init__(self, root: Path, store, log):
        self.root = Path(root)
        self.store = store
        self.log = log
        self.version_file = self.root / "VERSION"
        if not self.version_file.exists():
            self.version_file.write_text("2.1.0\n", encoding="utf-8")
        self.update_dir = self.root / "updates"
        self.update_dir.mkdir(parents=True, exist_ok=True)

    def current_version(self) -> str:
        try:
            return self.version_file.read_text(encoding="utf-8").strip()
        except Exception:
            return "unknown"

    def _manifest_url(self):
        return os.environ.get("UPDATE_MANIFEST_URL", "").strip()

    def _github_repo(self):
        return os.environ.get("GITHUB_REPO", "").strip().strip("/")

    def _github_headers(self):
        h = {"Accept": "application/vnd.github+json", "User-Agent": "Cricket-Live-Updater/2.6"}
        token = os.environ.get("GITHUB_TOKEN", "").strip()
        if token:
            h["Authorization"] = f"Bearer {token}"
        return h

    @staticmethod
    def _version_key(v):
        v = str(v or "").strip().lstrip("vV")
        nums = re.findall(r"\d+", v)
        return tuple(int(x) for x in nums[:4]) if nums else (0,)

    def _github_update(self, current):
        import urllib.request as _ur
        repo = self._github_repo()
        if not repo:
            return None
        api = f"https://api.github.com/repos/{repo}/releases/latest"
        req = _ur.Request(api, headers=self._github_headers())
        with _ur.urlopen(req, timeout=15) as r:
            release = json.loads(r.read().decode("utf-8"))
        tag = str(release.get("tag_name") or release.get("name") or current)
        available = tag.lstrip("vV")
        assets = release.get("assets") or []
        requested = os.environ.get("GITHUB_UPDATE_ASSET", "").strip()
        package = None
        for asset in assets:
            name = asset.get("name", "")
            if requested and name == requested:
                package = asset; break
            if not requested and name.lower().endswith(".zip") and "cricket-live" in name.lower():
                package = asset
        if package is None and assets:
            package = next((a for a in assets if a.get("name","").lower().endswith(".zip")), None)
        if not package:
            raise RuntimeError("Latest GitHub release has no ZIP update asset.")
        digest = ""
        digest_asset = next((a for a in assets if a.get("name","").lower() == package.get("name","").lower()+".sha256"), None)
        if digest_asset:
            req2 = _ur.Request(digest_asset["browser_download_url"], headers=self._github_headers())
            with _ur.urlopen(req2, timeout=15) as r:
                digest = r.read().decode("utf-8").strip().split()[0]
        return {
            "ok": True, "current_version": current, "available_version": available,
            "update_available": self._version_key(available) > self._version_key(current),
            "configured": True, "source": "github",
            "release_notes": release.get("body", "") or "",
            "package_url": package.get("browser_download_url", ""),
            "sha256": digest,
            "release_url": release.get("html_url", ""),
        }


    def check_update(self):
        current = self.current_version()
        url = self._manifest_url()
        try:
            if url:
                with urllib.request.urlopen(url, timeout=15) as r:
                    data = json.loads(r.read().decode("utf-8"))
                available = str(data.get("version", current))
                return {
                    "ok": True, "current_version": current, "available_version": available,
                    "update_available": self._version_key(available) > self._version_key(current),
                    "configured": True, "source": "manifest",
                    "release_notes": data.get("release_notes", ""),
                    "package_url": data.get("package_url", ""),
                    "sha256": data.get("sha256", ""),
                }
            gh = self._github_update(current)
            if gh:
                return gh
            return {
                "ok": True, "current_version": current, "available_version": current,
                "update_available": False, "configured": False,
                "message": "Set GITHUB_REPO=owner/repository or UPDATE_MANIFEST_URL to enable updates."
            }
        except Exception as exc:
            return {"ok": False, "current_version": current, "available_version": None,
                    "update_available": False, "configured": True, "error": str(exc)}

    def install_update(self, package_url: str | None = None, expected_sha256: str | None = None):
        package_url = package_url or ""
        if not package_url:
            chk = self.check_update()
            if not chk.get("ok") or not chk.get("update_available"):
                return {"ok": False, "error": "No update package is available."}
            package_url = chk.get("package_url", "")
            expected_sha256 = expected_sha256 or chk.get("sha256")
        if not package_url:
            return {"ok": False, "error": "Update package URL is missing."}

        tmp = self.update_dir / f"package-{int(time.time())}.zip"
        try:
            with urllib.request.urlopen(package_url, timeout=60) as r, open(tmp, "wb") as f:
                shutil.copyfileobj(r, f)
            digest = hashlib.sha256(tmp.read_bytes()).hexdigest()
            if expected_sha256 and digest.lower() != expected_sha256.lower():
                tmp.unlink(missing_ok=True)
                return {"ok": False, "error": "SHA-256 validation failed.", "sha256": digest}

            # Validate zip before installing.
            with zipfile.ZipFile(tmp) as z:
                bad = z.testzip()
                if bad:
                    raise RuntimeError(f"Corrupt update archive: {bad}")
                names = z.namelist()
                if not names:
                    raise RuntimeError("Empty update archive")

            backup = self.update_dir / f"backup-{int(time.time())}"
            backup.mkdir(parents=True, exist_ok=True)
            # Preserve runtime directories and database. Only replace application source.
            preserve = {"data", "recordings", "updates", ".venv", "__pycache__"}
            # Extract to staging and copy files over atomically at file level.
            staging = Path(tempfile.mkdtemp(prefix="staging-", dir=self.update_dir))
            try:
                with zipfile.ZipFile(tmp) as z:
                    z.extractall(staging)
                source = staging
                # Accept either root-level project or a single nested directory.
                children = [p for p in staging.iterdir() if p.name not in preserve]
                if len(children) == 1 and children[0].is_dir():
                    source = children[0]
                for src in source.rglob("*"):
                    rel = src.relative_to(source)
                    if any(part in preserve for part in rel.parts):
                        continue
                    dst = self.root / rel
                    if src.is_dir():
                        dst.mkdir(parents=True, exist_ok=True)
                        continue
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    if dst.exists():
                        shutil.copy2(dst, backup / rel)
                    shutil.copy2(src, dst)
            finally:
                shutil.rmtree(staging, ignore_errors=True)
            tmp.unlink(missing_ok=True)
            self.log("Software update installed successfully")
            return {"ok": True, "version": self.current_version(), "restart_required": True}
        except Exception as exc:
            self.log(f"Software update failed: {exc}")
            tmp.unlink(missing_ok=True)
            return {"ok": False, "error": str(exc)}

    def restart_application(self):
        self.log("Application restart requested")
        # Development-friendly restart: terminate current process and let run.py/service supervise it.
        cmd = [sys.executable] + sys.argv
        if os.name == "nt":
            flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
            subprocess.Popen(cmd, cwd=str(self.root), creationflags=flags)
        else:
            subprocess.Popen(cmd, cwd=str(self.root), start_new_session=True)
        os._exit(0)

    def factory_reset(self):
        self.log("Factory reset started")
        with self.store.lock:
            # Reset application nodes while preserving auth user accounts.
            for key in list(self.store.tree.keys()):
                if key not in {"system"}:
                    self.store.tree.pop(key, None)
                    self.store.conn.execute("DELETE FROM nodes WHERE key=?", (key,))
            self.store.tree["system"] = {"factory_reset_at": time.time()}
            self.store._persist("system")
            self.store.conn.execute("DELETE FROM sessions")
            self.store.conn.execute("DELETE FROM secrets")
            self.store.conn.commit()
            self.store.ensure_admin()
        tracker_cfg = self.root / "data" / "tracker_config.json"
        try:
            tracker_cfg.unlink(missing_ok=True)
        except Exception:
            pass
        for name in ("recordings", "updates"):
            p = self.root / name
            if p.exists() and name == "recordings":
                for f in p.glob("*"):
                    try:
                        if f.is_file() or f.is_symlink():
                            f.unlink()
                        elif f.is_dir():
                            shutil.rmtree(f)
                    except Exception:
                        pass
        self.log("Factory reset complete")
        return {"ok": True, "message": "Factory reset complete", "restart_required": True}
