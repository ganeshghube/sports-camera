#!/usr/bin/env python3
"""
Step 5 — Export new model to OpenVINO and deploy to models/.

Converts the freshly trained best.pt → OpenVINO IR (.xml/.bin)
then backs up the old model and copies the new one into models/.

Usage:
    python tools/train/5_export_and_deploy.py --model runs/detect/cricket_v2/weights/best.pt

Options:
    --imgsz  Image size for export (default: 640 — must match training)
    --backup Keep old model in models/backup/ (default: True)
    --no-backup  Skip backup
"""
from __future__ import annotations
import argparse, shutil, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODELS_DIR = ROOT / "models"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model",  required=True, help="Path to trained best.pt")
    ap.add_argument("--imgsz",  type=int, default=640)
    ap.add_argument("--no-backup", action="store_true")
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ultralytics not found — run from project venv")

    src_pt = Path(args.model)
    if not src_pt.exists():
        sys.exit(f"Not found: {src_pt}")

    # ── 1. Backup existing model ───────────────────────────────────────────
    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = MODELS_DIR / "backup" / stamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f in MODELS_DIR.iterdir():
            if f.is_file():
                shutil.copy2(f, backup_dir / f.name)
        old_ov = MODELS_DIR / "best_openvino_model"
        if old_ov.exists():
            shutil.copytree(old_ov, backup_dir / "best_openvino_model", dirs_exist_ok=True)
        print(f"✅ Old model backed up to: {backup_dir}/")

    # ── 2. Copy .pt to models/ (always works on all platforms incl. Android) ──
    dst_pt = MODELS_DIR / "best.pt"
    shutil.copy2(src_pt, dst_pt)
    print(f"Copied {src_pt.name} → {dst_pt}")

    # ── 3. Export .pt → OpenVINO (only when OpenVINO is available) ───────────
    try:
        import openvino  # noqa: F401
    except ImportError:
        print("ℹ️  OpenVINO is not installed (e.g. Android/ARM64); deployed PyTorch best.pt only.")
        print(f"\n🚀 New model deployed to models/")
        print("   Restart or reload the AI ball tracker in the web UI to use it.")
        print(f"   Old model is at: {MODELS_DIR / 'backup'}/")
        return

    print(f"\nExporting {src_pt.name} → OpenVINO (imgsz={args.imgsz}) …")
    model = YOLO(str(src_pt))
    export_path = model.export(format="openvino", imgsz=args.imgsz, half=False)
    print(f"OpenVINO export saved to: {export_path}")

    # ── 4. Copy OpenVINO folder to models/best_openvino_model/ ────────────
    ov_src = Path(str(export_path))   # e.g. runs/detect/cricket_v2/weights/best_openvino_model
    if not ov_src.exists():
        # Ultralytics sometimes uses different naming
        ov_src = src_pt.parent / (src_pt.stem + "_openvino_model")
    if ov_src.exists() and ov_src.is_dir():
        dst_ov = MODELS_DIR / "best_openvino_model"
        if dst_ov.exists():
            shutil.rmtree(dst_ov)
        shutil.copytree(ov_src, dst_ov)
        print(f"Copied OpenVINO model → {dst_ov}/")
    else:
        print(f"⚠️  OpenVINO folder not found at expected path: {ov_src}")
        print("   The .pt file was deployed; restart the tracker to use it.")
        print("   Re-run the export manually if you need OpenVINO speed.")
        return

    print(f"\n🚀 New model deployed to models/")
    print("   Restart the AI ball tracker in the web UI to use it.")
    print(f"   Old model is at: {MODELS_DIR / 'backup'}/")


if __name__ == "__main__":
    main()
