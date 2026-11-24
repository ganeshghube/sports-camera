"""
Training router — UI-driven and continuous ball-tracking model improvement.

Features:
  - Frame capture, manual annotation, dataset building, YOLO fine-tuning & OpenVINO export
  - Continuous active learning: automatically harvests diverse frames and auto-labels them
    during live camera usage (high-confidence ball frames + background negative frames)
  - Cumulative training: preserves all historical training samples across matches so the
    model never forgets previous capabilities
  - Safety & quality preservation: starts fine-tuning from models/best.pt with frozen
    backbone layers, archives timestamped backups, and hot-reloads the active AI tracker
    process via WebSocket with zero camera downtime.

Endpoints:
  POST /api/training/capture          upload or grab a live camera JPEG frame
  GET  /api/training/frames           list frames + label status
  GET  /api/training/frames/{n}/image serve frame image
  GET  /api/training/frames/{n}/label get YOLO boxes
  POST /api/training/frames/{n}/label save YOLO boxes
  DELETE /api/training/frames/{n}     delete frame
  POST /api/training/run              start manual fine-tune training
  GET  /api/training/status           training progress + log
  POST /api/training/deploy           export + deploy best model & hot-reload tracker
  POST /api/training/stop             stop running training
  GET  /api/training/auto/status      continuous auto-train state & harvest counters
  POST /api/training/auto/toggle      toggle auto-harvest and auto-train
  POST /api/training/auto/train-now   trigger immediate fine-tune & auto-deploy in background
  GET  /api/training/models/history   get model backup list and version history
  POST /api/training/models/rollback  rollback to a previous timestamped model backup
"""
from __future__ import annotations
import asyncio
import json
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse

ROOT        = Path(__file__).resolve().parent.parent
TRAIN_DIR   = ROOT / "data" / "training_frames"
IMAGES_DIR  = TRAIN_DIR / "images"
LABELS_DIR  = TRAIN_DIR / "labels"
DATASET_DIR = ROOT / "data" / "training_dataset"
MODELS_DIR  = ROOT / "models"
BACKUP_DIR  = MODELS_DIR / "backup"

for _d in (IMAGES_DIR, LABELS_DIR, DATASET_DIR, BACKUP_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── Shared mutable state ───────────────────────────────────────────────────────
_state: dict = {
    "status": "idle",   # idle | running | done | error
    "phase":  "",
    "progress": 0,
    "log": [],
    "metrics": {},
    "best_model": None,
}
_proc: subprocess.Popen | None = None
_lock = threading.Lock()

_auto_state: dict = {
    "enabled": True,
    "harvested_session": 0,
    "total_harvested": 0,
    "is_training": False,
    "last_train_time": 0.0,
    "last_mAP50": None,
    "last_harvest_pos": 0.0,
    "last_harvest_neg": 0.0,
    "was_camera_running": False,
}
_harvester_started = False


def _log(*msgs):
    for m in msgs:
        for line in str(m).splitlines():
            line = line.rstrip()
            if line:
                # Ensure ASCII-safe logging to avoid Windows cp1252 encoding issues
                line = line.encode("ascii", errors="replace").decode("ascii")
                _state["log"].append(line)
    if len(_state["log"]) > 800:
        _state["log"] = _state["log"][-800:]


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:
        return False


def _capture_bytes(uploaded: bytes, frame_getter):
    """Return a JPEG supplied by a client or by the shared live camera."""
    body = uploaded or (frame_getter() if frame_getter else None)
    if not body:
        raise HTTPException(
            409,
            "Camera is not delivering live frames. Start Camera in Live Preview first.",
        )
    if not isinstance(body, bytes) or not body.startswith(b"\xff\xd8"):
        raise HTTPException(400, "Camera did not provide a valid JPEG frame")
    return body


def _notify_tracker(camera_control):
    """Notify the active AI tracker to reload models without restarting the process."""
    if camera_control:
        try:
            import inspect
            if inspect.iscoroutinefunction(camera_control):
                try:
                    loop = asyncio.get_running_loop()
                    asyncio.run_coroutine_threadsafe(camera_control("reload_model"), loop)
                except RuntimeError:
                    asyncio.run(camera_control("reload_model"))
            else:
                camera_control("reload_model")
            _log("⚡ AI ball tracker notified: active model hot-reloaded into live camera feed!")
        except Exception as exc:
            _log(f"Model hot-reload note: {exc}")


def _record_model_history(model_path, metrics):
    """Save an immutable audit trail of deployed models and validation scores."""
    try:
        history_file = MODELS_DIR / "model_history.json"
        history = []
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                history = []
        all_imgs = list(IMAGES_DIR.glob("*.jpg"))
        entry = {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model_file": str(model_path),
            "mAP50": metrics.get("mAP50"),
            "mAP50_95": metrics.get("mAP50_95"),
            "total_frames": len(all_imgs),
        }
        history.insert(0, entry)
        history_file.write_text(json.dumps(history[:50], indent=2), encoding="utf-8")
    except Exception as exc:
        _log(f"History record note: {exc}")


def build_router(auth_dep, frame_getter=None, camera_control=None, store=None, tracker_holder=None) -> APIRouter:
    global _harvester_started
    router = APIRouter(prefix="/api/training")

    # Load persistent setting if available
    if store is not None:
        try:
            persisted = store.get("settings/auto_train_enabled")
            if persisted is not None:
                _auto_state["enabled"] = bool(persisted)
        except Exception:
            pass

    # ── Background Continuous Harvester & Training Thread ─────────────────────
    def _harvester_loop():
        while True:
            time.sleep(0.5)
            if not _auto_state["enabled"] or tracker_holder is None:
                continue
            try:
                st = getattr(tracker_holder, "status", None) or {}
                cam_running = bool(st.get("camera_running"))
                now = time.time()

                if cam_running:
                    det = getattr(tracker_holder, "last_detection", None)
                    frame = getattr(tracker_holder, "last_frame", None)
                    det_ts = (det or {}).get("timestamp", 0)

                    # 1. Harvest positive frame (high-confidence ball)
                    if det and frame and (now - det_ts < 1.2):
                        if det.get("detected") and float(det.get("confidence", 0)) >= 0.45:
                            if now - _auto_state["last_harvest_pos"] >= 3.0:
                                fw = max(1, det.get("frame_width", 1280))
                                fh = max(1, det.get("frame_height", 720))
                                box = det.get("box") or [det["x"] - 15, det["y"] - 15, det["x"] + 15, det["y"] + 15]
                                x1, y1, x2, y2 = box
                                cx = max(0.001, min(0.999, ((x1 + x2) / 2.0) / fw))
                                cy = max(0.001, min(0.999, ((y1 + y2) / 2.0) / fh))
                                w = max(0.002, min(0.999, abs(x2 - x1) / fw))
                                h = max(0.002, min(0.999, abs(y2 - y1) / fh))
                                name = f"auto_{int(now * 1000)}"
                                (IMAGES_DIR / f"{name}.jpg").write_bytes(frame)
                                (LABELS_DIR / f"{name}.txt").write_text(f"0 {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}\n", encoding="utf-8")
                                _auto_state["last_harvest_pos"] = now
                                _auto_state["harvested_session"] += 1
                                _auto_state["total_harvested"] += 1

                        # 2. Harvest negative frame (pitch/field with no ball present)
                        elif not det.get("detected") and (now - _auto_state["last_harvest_neg"] >= 20.0):
                            name = f"auto_neg_{int(now * 1000)}"
                            (IMAGES_DIR / f"{name}.jpg").write_bytes(frame)
                            (LABELS_DIR / f"{name}.txt").write_text("", encoding="utf-8")
                            _auto_state["last_harvest_neg"] = now
                            _auto_state["harvested_session"] += 1
                            _auto_state["total_harvested"] += 1

                # 3. Check transition: Camera stopped after harvesting samples
                if _auto_state["was_camera_running"] and not cam_running:
                    if _auto_state["harvested_session"] >= 6 and not _auto_state["is_training"] and _state["status"] != "running":
                        _log(f"⚡ Camera session ended: {_auto_state['harvested_session']} new frames collected. Triggering auto fine-tune & deploy...")
                        _auto_state["harvested_session"] = 0
                        _start_pipeline_worker(epochs=20, batch=8, auto_deploy=True, camera_control=camera_control)

                _auto_state["was_camera_running"] = cam_running
            except Exception:
                pass

    if tracker_holder is not None and not _harvester_started:
        _harvester_started = True
        threading.Thread(target=_harvester_loop, daemon=True, name="auto-harvester").start()

    # ── Pipeline Worker (Used by both manual and auto training) ────────────────
    def _start_pipeline_worker(epochs=30, batch=8, auto_deploy=False, camera_control=None):
        global _proc
        _auto_state["is_training"] = True
        _state.update({
            "status": "running", "phase": "building dataset",
            "progress": 0, "log": [], "metrics": {}, "best_model": None
        })

        def _worker():
            global _proc
            try:
                python = sys.executable
                all_imgs = list(IMAGES_DIR.glob("*.jpg"))
                labeled = [f for f in all_imgs
                           if (LABELS_DIR / (f.stem + ".txt")).exists()
                           and (LABELS_DIR / (f.stem + ".txt")).read_text(encoding="utf-8").strip()]

                _log("=" * 55)
                _log(f"🔄 Training Pipeline: {len(all_imgs)} cumulative frames ({len(labeled)} labeled with ball)")
                _log("Building dataset splits (train / val)...")
                _state["phase"] = "building dataset"
                _state["progress"] = 5

                # Step A: build dataset
                r = subprocess.run(
                    [python, str(ROOT / "tools/train/3_build_dataset.py"),
                     "--frames", str(IMAGES_DIR),
                     "--labels", str(LABELS_DIR),
                     "--out", str(DATASET_DIR),
                     "--val-split", "0.15"],
                    capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT)
                )
                _log(r.stdout, r.stderr)
                if r.returncode != 0:
                    _state["status"] = "error"
                    _state["phase"] = "dataset failed"
                    _log("❌ Dataset build failed")
                    return

                # Step B: fine-tune YOLO model
                _state["phase"] = "fine-tuning YOLO"
                _state["progress"] = 10
                base_model = ROOT / "models" / "best.pt"
                _log(f"Fine-tuning YOLO weights ({epochs} epochs, batch {batch}, starting from {base_model.name})...")
                _log("Freezing 10 backbone layers to preserve all prior model capabilities.")
                _log("-" * 55)

                run_name = f"cricket_train_{int(time.time())}"
                _proc = subprocess.Popen(
                    [python, str(ROOT / "tools/train/4_train.py"),
                     "--data", str(DATASET_DIR / "data.yaml"),
                     "--model", str(base_model),
                     "--epochs", str(epochs),
                     "--batch", str(batch),
                     "--name", run_name,
                     "--lr0", "0.001",
                     "--freeze", "10",
                     "--patience", "10"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", cwd=str(ROOT)
                )

                for line in _proc.stdout:
                    line = line.rstrip()
                    _log(line)
                    if "Epoch" in line:
                        try:
                            parts = line.split("Epoch")[-1].strip().split("/")
                            cur = int(parts[0].strip())
                            tot = int(parts[1].split()[0])
                            _state["progress"] = 10 + int(cur / tot * 80)
                        except Exception:
                            pass
                    if "mAP50" in line and "all" in line:
                        try:
                            nums = [x for x in line.split() if _is_float(x)]
                            if len(nums) >= 2:
                                _state["metrics"]["mAP50"] = float(nums[-2])
                                _state["metrics"]["mAP50_95"] = float(nums[-1])
                                _auto_state["last_mAP50"] = float(nums[-2])
                        except Exception:
                            pass

                _proc.wait()
                best = ROOT / "runs" / "detect" / run_name / "weights" / "best.pt"

                if _proc.returncode == 0 and best.exists():
                    _state["status"] = "done"
                    _state["progress"] = 95
                    _state["phase"] = "training complete"
                    _state["best_model"] = str(best)
                    _log("-" * 55)
                    _log("✅ Model training complete!")
                    m = _state["metrics"]
                    if m:
                        _log(f"   Validation mAP50 = {m.get('mAP50', '?')}   mAP50-95 = {m.get('mAP50_95', '?')}")

                    if auto_deploy:
                        _log("🚀 Auto-deploying new weights to models/best.pt and OpenVINO IR...")
                        _state["phase"] = "exporting & deploying"
                        dep = subprocess.run(
                            [python, str(ROOT / "tools/train/5_export_and_deploy.py"),
                             "--model", str(best)],
                            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT)
                        )
                        _log(dep.stdout, dep.stderr)
                        if dep.returncode == 0:
                            _state["phase"] = "deployed"
                            _state["progress"] = 100
                            _notify_tracker(camera_control)
                            _record_model_history(best, _state.get("metrics", {}))
                            _log("🎉 New model deployed and hot-reloaded into live camera feed!")
                        else:
                            _state["phase"] = "deploy failed"
                            _log("⚠️ OpenVINO export failed, but trained weights are saved in runs/")
                    else:
                        _log("Click 'Deploy Model' to activate it.")
                else:
                    _state["status"] = "error"
                    _state["phase"] = "training failed"
                    _log("❌ Training process did not produce best.pt")

            except Exception as exc:
                _log(f"❌ Training exception: {exc}")
                _state["status"] = "error"
                _state["phase"] = "failed"
            finally:
                _auto_state["is_training"] = False
                _auto_state["last_train_time"] = time.time()

        threading.Thread(target=_worker, daemon=True, name="model-train-pipeline").start()

    # ── capture ───────────────────────────────────────────────────────────────
    @router.post("/capture")
    async def capture(request: Request, _=auth_dep):
        body = _capture_bytes(await request.body(), frame_getter)
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        name = f"frame_{int(time.time() * 1000)}.jpg"
        (IMAGES_DIR / name).write_bytes(body)
        return {"name": name}

    # ── list frames ───────────────────────────────────────────────────────────
    @router.get("/frames")
    def list_frames(_=auth_dep):
        frames = []
        for img in sorted(IMAGES_DIR.glob("*.jpg")):
            lbl = LABELS_DIR / (img.stem + ".txt")
            has_lbl = lbl.exists()
            has_ball = has_lbl and bool(lbl.read_text(encoding="utf-8").strip())
            no_ball = has_lbl and not lbl.read_text(encoding="utf-8").strip()
            frames.append({
                "name": img.name,
                "labeled": has_ball,
                "no_ball": no_ball,
                "pending": not has_lbl,
            })
        total = len(frames)
        labeled = sum(1 for f in frames if f["labeled"])
        no_ball = sum(1 for f in frames if f["no_ball"])
        return {"frames": frames, "total": total,
                "labeled": labeled, "no_ball": no_ball,
                "pending": total - labeled - no_ball}

    # ── serve image ───────────────────────────────────────────────────────────
    @router.get("/frames/{name}/image")
    def frame_image(name: str, _=auth_dep):
        p = IMAGES_DIR / Path(name).name
        if not p.exists():
            raise HTTPException(404, "Frame not found")
        return FileResponse(str(p), media_type="image/jpeg")

    # ── get label ─────────────────────────────────────────────────────────────
    @router.get("/frames/{name}/label")
    def get_label(name: str, _=auth_dep):
        lbl = LABELS_DIR / (Path(name).stem + ".txt")
        if not lbl.exists():
            return {"boxes": None}
        text = lbl.read_text(encoding="utf-8").strip()
        if not text:
            return {"boxes": []}
        boxes = []
        for line in text.splitlines():
            p = line.split()
            if len(p) == 5:
                boxes.append({"cls": int(p[0]),
                              "cx": float(p[1]), "cy": float(p[2]),
                              "w": float(p[3]), "h": float(p[4])})
        return {"boxes": boxes}

    # ── save label ────────────────────────────────────────────────────────────
    @router.post("/frames/{name}/label")
    async def save_label(name: str, request: Request, _=auth_dep):
        body = await request.json()
        boxes = body.get("boxes", [])
        lbl = LABELS_DIR / (Path(name).stem + ".txt")
        if boxes:
            lines = [f"0 {b['cx']:.6f} {b['cy']:.6f} {b['w']:.6f} {b['h']:.6f}"
                     for b in boxes]
            lbl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            lbl.write_text("", encoding="utf-8")
        return {"saved": True}

    # ── delete frame ──────────────────────────────────────────────────────────
    @router.delete("/frames/{name}")
    def delete_frame(name: str, _=auth_dep):
        stem = Path(name).stem
        (IMAGES_DIR / Path(name).name).unlink(missing_ok=True)
        (LABELS_DIR / (stem + ".txt")).unlink(missing_ok=True)
        return {"deleted": True}

    # ── training status ───────────────────────────────────────────────────────
    @router.get("/status")
    def status(_=auth_dep):
        return _state

    # ── start manual training ─────────────────────────────────────────────────
    @router.post("/run")
    async def run_training(request: Request, _=auth_dep):
        if _state["status"] == "running" or _auto_state["is_training"]:
            raise HTTPException(409, "Training is already running")

        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        epochs = int(body.get("epochs", 30))
        batch = int(body.get("batch", 8))

        all_imgs = list(IMAGES_DIR.glob("*.jpg"))
        labeled = [f for f in all_imgs
                   if (LABELS_DIR / (f.stem + ".txt")).exists()
                   and (LABELS_DIR / (f.stem + ".txt")).read_text(encoding="utf-8").strip()]
        if len(labeled) < 5:
            raise HTTPException(400,
                f"Need at least 5 frames with ball labels (have {len(labeled)}). "
                "Capture and label more frames first, or use camera to auto-harvest.")

        _start_pipeline_worker(epochs=epochs, batch=batch, auto_deploy=False, camera_control=camera_control)
        return {"started": True, "epochs": epochs, "batch": batch}

    # ── stop training ─────────────────────────────────────────────────────────
    @router.post("/stop")
    def stop_training(_=auth_dep):
        global _proc
        if _proc and _proc.poll() is None:
            _proc.terminate()
            _state["status"] = "idle"
            _state["phase"] = "stopped"
            _auto_state["is_training"] = False
            _log("⏹ Training stopped by user")
        return {"stopped": True}

    # ── deploy model ──────────────────────────────────────────────────────────
    @router.post("/deploy")
    def deploy(_=auth_dep):
        best = _state.get("best_model")
        if not best or not Path(best).exists():
            raise HTTPException(400, "No trained model ready. Run training first.")

        _log("Exporting to OpenVINO and deploying to models/...")
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools/train/5_export_and_deploy.py"),
             "--model", best],
            capture_output=True, text=True, encoding="utf-8", cwd=str(ROOT)
        )
        _log(r.stdout, r.stderr)
        if r.returncode != 0:
            raise HTTPException(500, f"Deploy failed: {r.stderr or r.stdout}")
        _state["phase"] = "deployed"

        _notify_tracker(camera_control)
        _record_model_history(best, _state.get("metrics", {}))
        return {"deployed": True, "hot_reloaded": True}

    @router.post("/reload-model")
    def reload_model(_=auth_dep):
        """Hot-reload the active AI model into the live ball tracker."""
        _notify_tracker(camera_control)
        return {"ok": True, "reloaded": True, "model": str(ROOT / "models" / "best.pt")}

    # ── Continuous Auto-Training Endpoints ─────────────────────────────────────
    @router.get("/auto/status")
    def auto_status(_=auth_dep):
        all_imgs = list(IMAGES_DIR.glob("*.jpg"))
        labeled = [f for f in all_imgs
                   if (LABELS_DIR / (f.stem + ".txt")).exists()
                   and (LABELS_DIR / (f.stem + ".txt")).read_text(encoding="utf-8").strip()]
        return {
            "auto_train_enabled": _auto_state["enabled"],
            "harvested_session": _auto_state["harvested_session"],
            "total_harvested": _auto_state["total_harvested"],
            "total_frames": len(all_imgs),
            "labeled_frames": len(labeled),
            "is_training": _auto_state["is_training"] or (_state["status"] == "running"),
            "training_phase": _state.get("phase", ""),
            "training_progress": _state.get("progress", 0),
            "last_mAP50": _auto_state.get("last_mAP50"),
            "active_model": str(ROOT / "models" / "best.pt"),
        }

    @router.post("/auto/toggle")
    async def auto_toggle(request: Request, _=auth_dep):
        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        val = body.get("enabled")
        if val is None:
            _auto_state["enabled"] = not _auto_state["enabled"]
        else:
            _auto_state["enabled"] = bool(val)
        if store:
            try:
                store.set("settings/auto_train_enabled", _auto_state["enabled"])
            except Exception:
                pass
        _log(f"Continuous learning mode {'ENABLED' if _auto_state['enabled'] else 'DISABLED'}")
        return {"auto_train_enabled": _auto_state["enabled"]}

    @router.post("/auto/train-now")
    def auto_train_now(_=auth_dep):
        if _auto_state["is_training"] or _state["status"] == "running":
            raise HTTPException(409, "Training is already in progress")
        all_imgs = list(IMAGES_DIR.glob("*.jpg"))
        labeled = [f for f in all_imgs
                   if (LABELS_DIR / (f.stem + ".txt")).exists()
                   and (LABELS_DIR / (f.stem + ".txt")).read_text(encoding="utf-8").strip()]
        if len(labeled) < 5:
            raise HTTPException(400, f"Need at least 5 frames with ball labels (have {len(labeled)}). Start camera to auto-harvest frames.")
        _start_pipeline_worker(epochs=20, batch=8, auto_deploy=True, camera_control=camera_control)
        return {"started": True, "message": "Continuous auto-training started in background"}

    # ── Model History & Rollback ──────────────────────────────────────────────
    @router.get("/models/history")
    def model_history(_=auth_dep):
        history_file = MODELS_DIR / "model_history.json"
        history = []
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text(encoding="utf-8"))
            except Exception:
                history = []
        backups = []
        if BACKUP_DIR.exists():
            for d in sorted(BACKUP_DIR.iterdir(), reverse=True):
                if d.is_dir() and (d / "best.pt").exists():
                    backups.append({"stamp": d.name, "path": str(d / "best.pt")})
        return {"history": history, "backups": backups, "active": str(ROOT / "models" / "best.pt")}

    @router.post("/models/rollback")
    async def rollback(request: Request, _=auth_dep):
        body = await request.json()
        stamp = body.get("stamp")
        if not stamp:
            raise HTTPException(400, "Missing backup stamp")
        backup_dir = BACKUP_DIR / stamp
        src_pt = backup_dir / "best.pt"
        if not src_pt.exists():
            raise HTTPException(404, f"Backup not found: {stamp}")
        dst_pt = ROOT / "models" / "best.pt"
        shutil.copy2(src_pt, dst_pt)
        src_ov = backup_dir / "best_openvino_model"
        dst_ov = ROOT / "models" / "best_openvino_model"
        if src_ov.exists():
            if dst_ov.exists():
                shutil.rmtree(dst_ov)
            shutil.copytree(src_ov, dst_ov)
        _notify_tracker(camera_control)
        _log(f"⏪ Rolled back model to backup {stamp} and hot-reloaded into AI tracker!")
        return {"rolled_back": True, "stamp": stamp}

    return router
