#!/usr/bin/env python3
"""
Evaluate model accuracy on a test set — run before and after training to measure improvement.

Usage:
    python tools/train/evaluate.py --data training_data/dataset/data.yaml --model models/best.pt
    python tools/train/evaluate.py --data training_data/dataset/data.yaml --model runs/detect/cricket_v2/weights/best.pt

Prints: mAP50, mAP50-95, Precision, Recall for the cricketBall class.
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data",   required=True, help="data.yaml path")
    ap.add_argument("--model",  default=str(ROOT / "models" / "best.pt"))
    ap.add_argument("--imgsz",  type=int, default=640)
    ap.add_argument("--conf",   type=float, default=0.25)
    ap.add_argument("--iou",    type=float, default=0.45)
    ap.add_argument("--device", default=None)
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ultralytics not found")

    device = args.device
    if device is None:
        try:
            import torch
            device = "0" if torch.cuda.is_available() else "cpu"
        except Exception:
            device = "cpu"

    model   = YOLO(args.model)
    metrics = model.val(
        data   = args.data,
        imgsz  = args.imgsz,
        conf   = args.conf,
        iou    = args.iou,
        device = device,
        verbose= True,
    )

    print("\n── Evaluation Results ──────────────────────────────")
    print(f"  mAP50      : {metrics.box.map50:.4f}  (target ≥ 0.80)")
    print(f"  mAP50-95   : {metrics.box.map:.4f}")
    print(f"  Precision  : {metrics.box.mp:.4f}")
    print(f"  Recall     : {metrics.box.mr:.4f}")
    print("────────────────────────────────────────────────────")

    if metrics.box.map50 >= 0.80:
        print("✅ Model looks good — ready to deploy (mAP50 ≥ 0.80)")
    elif metrics.box.map50 >= 0.65:
        print("⚠️  Acceptable — consider more labelled data or more epochs")
    else:
        print("❌  mAP50 < 0.65 — need more data or check label quality")


if __name__ == "__main__":
    main()
