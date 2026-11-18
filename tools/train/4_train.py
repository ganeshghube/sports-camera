#!/usr/bin/env python3
"""
Step 4 — Fine-tune / retrain the cricket ball detector.

Fine-tunes the EXISTING models/best.pt on your new labelled data.
Starting from the existing weights is MUCH faster than training from scratch
(typically 20-50 epochs vs 300+) and needs far fewer labelled images.

Usage:
    python tools/train/4_train.py --data training_data/dataset/data.yaml

Options:
    --model  Path to starting weights (default: models/best.pt)
    --epochs Number of training epochs (default: 50)
    --imgsz  Image size for training (default: 640)
    --batch  Batch size — lower if GPU OOM (default: 8)
    --device cuda:0 | cpu | mps (default: auto)
    --name   Run name used for output folder (default: cricket_v2)
    --lr0    Initial learning rate (default: 0.001 — lower for fine-tuning)
    --freeze N  Freeze first N layers of backbone (default: 10, fine-tune head only)

Output is saved to runs/detect/<name>/weights/best.pt
"""
from __future__ import annotations
import argparse, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data",   required=True, help="Path to data.yaml")
    ap.add_argument("--model",  default=str(ROOT / "models" / "best.pt"),
                    help="Starting weights (default models/best.pt)")
    ap.add_argument("--epochs", type=int,   default=50,   help="Epochs (default 50)")
    ap.add_argument("--imgsz",  type=int,   default=640,  help="Image size (default 640)")
    ap.add_argument("--batch",  type=int,   default=8,    help="Batch size (default 8)")
    ap.add_argument("--device", default=None,             help="Device: cuda:0 | cpu | mps")
    ap.add_argument("--name",   default="cricket_v2",     help="Run name (default cricket_v2)")
    ap.add_argument("--lr0",    type=float, default=0.001,help="Initial LR (default 0.001)")
    ap.add_argument("--freeze", type=int,   default=10,   help="Freeze N backbone layers (default 10)")
    ap.add_argument("--patience", type=int, default=15,   help="Early-stop patience (default 15)")
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ultralytics not found — run from project venv: .venv\\Scripts\\python.exe")

    model_path = Path(args.model)
    if not model_path.exists():
        sys.exit(f"Model not found: {model_path}")
    data_path = Path(args.data)
    if not data_path.exists():
        sys.exit(f"data.yaml not found: {data_path}")

    device = args.device
    if device is None:
        try:
            import torch
            device = "0" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    print(f"Model  : {model_path}")
    print(f"Data   : {data_path}")
    print(f"Device : {device}")
    print(f"Epochs : {args.epochs}  |  Batch: {args.batch}  |  imgsz: {args.imgsz}")
    print(f"Freeze : first {args.freeze} backbone layers (fine-tuning mode)")
    print()

    model = YOLO(str(model_path))

    results = model.train(
        data       = str(data_path),
        epochs     = args.epochs,
        imgsz      = args.imgsz,
        batch      = args.batch,
        device     = device,
        name       = args.name,
        lr0        = args.lr0,
        lrf        = 0.01,          # final LR = lr0 * lrf
        momentum   = 0.937,
        weight_decay = 0.0005,
        warmup_epochs = 3,
        freeze     = args.freeze,
        patience   = args.patience,
        # Augmentation — tuned for cricket (fast-moving small ball)
        hsv_h      = 0.015,
        hsv_s      = 0.7,
        hsv_v      = 0.4,
        degrees    = 5.0,           # slight rotation (not too much — ball trajectory matters)
        translate  = 0.1,
        scale      = 0.5,
        fliplr     = 0.5,           # horizontal flip OK (symmetric pitch)
        flipud     = 0.0,           # vertical flip — don't do this (sky/ground)
        mosaic     = 0.5,
        mixup      = 0.0,
        copy_paste = 0.0,
        # Save settings
        save       = True,
        save_period = 10,
        project    = str(ROOT / "runs" / "detect"),
        exist_ok   = True,
        verbose    = True,
    )

    best_pt = ROOT / "runs" / "detect" / args.name / "weights" / "best.pt"
    if not best_pt.exists():
        print("⚠️  best.pt not found in expected location — check runs/detect/ folder")
        return

    print(f"\n✅ Training complete!")
    print(f"   Best model: {best_pt}")
    print(f"\nNext: run Step 5 (export.py) to convert to OpenVINO and deploy.")


if __name__ == "__main__":
    main()
