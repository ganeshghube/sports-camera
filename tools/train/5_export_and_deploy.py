#!/usr/bin/env python3
"""
Step 5 — Deploy trained model to models/best.pt.

Backs up the old model and copies the freshly trained best.pt into models/.

Usage:
    python tools/train/5_export_and_deploy.py --model runs/detect/cricket_v2/weights/best.pt

Options:
    --imgsz  Image size (unused, kept for CLI compatibility)
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

    src_pt = Path(args.model)
    if not src_pt.exists():
        sys.exit(f"Not found: {src_pt}")

    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. Backup existing model ───────────────────────────────────────────
    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = MODELS_DIR / "backup" / stamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        for f in MODELS_DIR.iterdir():
            if f.is_file():
                shutil.copy2(f, backup_dir / f.name)
        print(f"✅ Old model backed up to: {backup_dir}/")

    # ── 2. Copy .pt to models/ ────────────────────────────────────────────
    dst_pt = MODELS_DIR / "best.pt"
    shutil.copy2(src_pt, dst_pt)
    print(f"Copied {src_pt.name} → {dst_pt}")

    print(f"\n🚀 New model deployed to models/")
    print("   Restart or reload the AI ball tracker in the web UI to use it.")
    print(f"   Old model is at: {MODELS_DIR / 'backup'}/")


if __name__ == "__main__":
    main()
