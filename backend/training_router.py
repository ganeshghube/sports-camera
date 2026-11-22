"""
Training router — UI-driven ball-tracking model improvement.

Endpoints:
  POST /api/training/capture          upload a JPEG frame
  GET  /api/training/frames           list frames + label status
  GET  /api/training/frames/{n}/image serve frame image
  GET  /api/training/frames/{n}/label get YOLO boxes
  POST /api/training/frames/{n}/label save YOLO boxes
  DELETE /api/training/frames/{n}     delete frame
  POST /api/training/run              start fine-tune training
  GET  /api/training/status           training progress + log
  POST /api/training/deploy           export + deploy best model
  POST /api/training/stop             stop running training
"""
from __future__ import annotations
import json, shutil, subprocess, sys, threading, time
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse, JSONResponse

ROOT       = Path(__file__).resolve().parent.parent
TRAIN_DIR  = ROOT / "data" / "training_frames"
IMAGES_DIR = TRAIN_DIR / "images"
LABELS_DIR = TRAIN_DIR / "labels"
DATASET_DIR= ROOT / "data" / "training_dataset"

for _d in (IMAGES_DIR, LABELS_DIR, DATASET_DIR):
    _d.mkdir(parents=True, exist_ok=True)

# ── shared mutable state ───────────────────────────────────────────────────────
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


def _log(*msgs):
    for m in msgs:
        for line in str(m).splitlines():
            line = line.rstrip()
            if line:
                _state["log"].append(line)
    if len(_state["log"]) > 600:
        _state["log"] = _state["log"][-600:]


def build_router(auth_dep) -> APIRouter:
    router = APIRouter(prefix="/api/training")

    # ── capture ───────────────────────────────────────────────────────────────
    @router.post("/capture")
    async def capture(request: Request, _=auth_dep):
        body = await request.body()
        if not body:
            raise HTTPException(400, "No image data")
        name = f"frame_{int(time.time()*1000)}.jpg"
        (IMAGES_DIR / name).write_bytes(body)
        return {"name": name}

    # ── list frames ───────────────────────────────────────────────────────────
    @router.get("/frames")
    def list_frames(_=auth_dep):
        frames = []
        for img in sorted(IMAGES_DIR.glob("*.jpg")):
            lbl = LABELS_DIR / (img.stem + ".txt")
            has_lbl   = lbl.exists()
            has_ball  = has_lbl and bool(lbl.read_text().strip())
            no_ball   = has_lbl and not lbl.read_text().strip()
            frames.append({
                "name":    img.name,
                "labeled": has_ball,
                "no_ball": no_ball,
                "pending": not has_lbl,
            })
        total   = len(frames)
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
            raise HTTPException(404)
        return FileResponse(str(p), media_type="image/jpeg")

    # ── get label ─────────────────────────────────────────────────────────────
    @router.get("/frames/{name}/label")
    def get_label(name: str, _=auth_dep):
        lbl = LABELS_DIR / (Path(name).stem + ".txt")
        if not lbl.exists():
            return {"boxes": None}   # None = not yet labeled
        text = lbl.read_text().strip()
        if not text:
            return {"boxes": []}     # [] = labeled as "no ball"
        boxes = []
        for line in text.splitlines():
            p = line.split()
            if len(p) == 5:
                boxes.append({"cls": int(p[0]),
                               "cx": float(p[1]), "cy": float(p[2]),
                               "w":  float(p[3]), "h":  float(p[4])})
        return {"boxes": boxes}

    # ── save label ────────────────────────────────────────────────────────────
    @router.post("/frames/{name}/label")
    async def save_label(name: str, request: Request, _=auth_dep):
        body  = await request.json()
        boxes = body.get("boxes", [])
        lbl   = LABELS_DIR / (Path(name).stem + ".txt")
        if boxes:
            lines = [f"0 {b['cx']:.6f} {b['cy']:.6f} {b['w']:.6f} {b['h']:.6f}"
                     for b in boxes]
            lbl.write_text("\n".join(lines) + "\n", encoding="utf-8")
        else:
            lbl.write_text("", encoding="utf-8")   # negative sample
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

    # ── start training ────────────────────────────────────────────────────────
    @router.post("/run")
    async def run_training(request: Request, _=auth_dep):
        global _proc
        if _state["status"] == "running":
            raise HTTPException(409, "Training is already running")

        body   = await request.json() if request.headers.get("content-type","").startswith("application/json") else {}
        epochs = int(body.get("epochs", 30))
        batch  = int(body.get("batch",  8))

        # Validate: need enough labelled frames
        all_imgs  = list(IMAGES_DIR.glob("*.jpg"))
        labeled   = [f for f in all_imgs
                     if (LABELS_DIR / (f.stem + ".txt")).exists()
                     and (LABELS_DIR / (f.stem + ".txt")).read_text().strip()]
        if len(labeled) < 5:
            raise HTTPException(400,
                f"Need at least 5 frames with ball labels (have {len(labeled)}). "
                "Capture and label more frames first.")

        _state.update({"status": "running", "phase": "building dataset",
                        "progress": 0, "log": [], "metrics": {}, "best_model": None})

        def _run():
            global _proc
            try:
                python = sys.executable

                # ── Step A: build dataset ──────────────────────────────────
                _log("=" * 50)
                _log(f"Dataset: {len(all_imgs)} frames total, {len(labeled)} with ball")
                _log("Building train/val split...")
                _state["phase"] = "building dataset"

                r = subprocess.run(
                    [python, str(ROOT / "tools/train/3_build_dataset.py"),
                     "--frames", str(IMAGES_DIR),
                     "--labels", str(LABELS_DIR),
                     "--out",    str(DATASET_DIR),
                     "--val-split", "0.15"],
                    capture_output=True, text=True, cwd=str(ROOT)
                )
                _log(r.stdout, r.stderr)
                if r.returncode != 0:
                    _state["status"] = "error"
                    _log("❌ Dataset build failed")
                    return

                # ── Step B: train ──────────────────────────────────────────
                _state["phase"]    = "training"
                _state["progress"] = 5
                _log(f"Starting fine-tune ({epochs} epochs, batch {batch})...")
                _log("This may take 10-60 minutes depending on your GPU/CPU.")
                _log("-" * 50)

                run_name = f"cricket_ui_{int(time.time())}"
                _proc = subprocess.Popen(
                    [python, str(ROOT / "tools/train/4_train.py"),
                     "--data",    str(DATASET_DIR / "data.yaml"),
                     "--epochs",  str(epochs),
                     "--batch",   str(batch),
                     "--name",    run_name,
                     "--lr0",     "0.001",
                     "--freeze",  "10",
                     "--patience","10"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, cwd=str(ROOT)
                )

                for line in _proc.stdout:
                    line = line.rstrip()
                    _log(line)
                    # Parse epoch progress  e.g. "Epoch 3/30"
                    if "Epoch" in line:
                        try:
                            parts = line.split("Epoch")[-1].strip().split("/")
                            cur   = int(parts[0].strip())
                            tot   = int(parts[1].split()[0])
                            _state["progress"] = 5 + int(cur / tot * 90)
                        except Exception:
                            pass
                    # Parse mAP from validation line
                    if "mAP50" in line and "all" in line:
                        try:
                            nums = [x for x in line.split() if _is_float(x)]
                            if len(nums) >= 2:
                                _state["metrics"]["mAP50"]    = float(nums[-2])
                                _state["metrics"]["mAP50_95"] = float(nums[-1])
                        except Exception:
                            pass

                _proc.wait()
                best = ROOT / "runs" / "detect" / run_name / "weights" / "best.pt"

                if _proc.returncode == 0 and best.exists():
                    _state["status"]     = "done"
                    _state["progress"]   = 100
                    _state["phase"]      = "complete"
                    _state["best_model"] = str(best)
                    _log("-" * 50)
                    _log("✅ Training complete!")
                    m = _state["metrics"]
                    if m:
                        _log(f"   mAP50 = {m.get('mAP50', '?')}   mAP50-95 = {m.get('mAP50_95', '?')}")
                    _log("Click 'Deploy Model' to activate it.")
                else:
                    _state["status"] = "error"
                    _state["phase"]  = "failed"
                    _log("❌ Training process failed — check log above")

            except Exception as exc:
                _log(f"❌ Exception: {exc}")
                _state["status"] = "error"
                _state["phase"]  = "failed"

        threading.Thread(target=_run, daemon=True, name="ui-training").start()
        return {"started": True, "epochs": epochs, "batch": batch}

    # ── stop training ─────────────────────────────────────────────────────────
    @router.post("/stop")
    def stop_training(_=auth_dep):
        global _proc
        if _proc and _proc.poll() is None:
            _proc.terminate()
            _state["status"] = "idle"
            _state["phase"]  = "stopped"
            _log("⏹ Training stopped by user")
        return {"stopped": True}

    # ── deploy model ──────────────────────────────────────────────────────────
    @router.post("/deploy")
    def deploy(_=auth_dep):
        best = _state.get("best_model")
        if not best or not Path(best).exists():
            raise HTTPException(400, "No trained model ready. Run training first.")

        _log("Exporting to OpenVINO and deploying...")
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools/train/5_export_and_deploy.py"),
             "--model", best],
            capture_output=True, text=True, cwd=str(ROOT)
        )
        _log(r.stdout, r.stderr)
        if r.returncode != 0:
            raise HTTPException(500, f"Deploy failed: {r.stderr or r.stdout}")
        _state["phase"] = "deployed"
        return {"deployed": True}

    return router


def _is_float(s: str) -> bool:
    try:
        float(s); return True
    except Exception:
        return False
