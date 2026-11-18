#!/usr/bin/env python3
"""
Step 2b — Auto-label frames using the EXISTING model (pre-annotation).

This runs the current best.pt over your extracted frames and saves YOLO .txt
label files alongside each image. You then correct mistakes in a labelling tool
(LabelImg / Label Studio / Roboflow) instead of drawing every box from scratch.
This can cut annotation time by 60-80%.

Usage:
    python tools/train/2_auto_label.py --frames training_data/raw_frames
                                       --model  models/best.pt
                                       --out    training_data/auto_labels

Options:
    --conf N    Confidence threshold (default 0.20 — keep low to catch faint balls)
    --device    cpu | 0 | cuda:0  (default auto)
    --dry-run   Show counts only, don't write files
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", required=True, help="Folder of extracted frames")
    ap.add_argument("--model",  default="models/best.pt", help="YOLO .pt model (default models/best.pt)")
    ap.add_argument("--out",    required=True, help="Output folder for label .txt files")
    ap.add_argument("--conf",   type=float, default=0.20, help="Confidence threshold (default 0.20)")
    ap.add_argument("--device", default=None, help="Device: cpu | 0 | cuda:0 (default auto)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    try:
        from ultralytics import YOLO
    except ImportError:
        sys.exit("ultralytics not found — run from project venv")

    frames_dir = Path(args.frames)
    out_dir    = Path(args.out)
    if not args.dry_run:
        out_dir.mkdir(parents=True, exist_ok=True)

    images = sorted(p for p in frames_dir.rglob("*") if p.suffix.lower() in IMG_EXTS)
    if not images:
        sys.exit(f"No images found in {frames_dir}")

    print(f"Loading model: {args.model}")
    model  = YOLO(args.model)
    device = args.device or ("0" if _has_cuda() else "cpu")

    labeled = skipped = 0
    batch = 32

    for i in range(0, len(images), batch):
        chunk = images[i:i + batch]
        results = model.predict(
            [str(p) for p in chunk],
            conf=args.conf,
            device=device,
            verbose=False,
            imgsz=640,
        )
        for img_path, result in zip(chunk, results):
            boxes = result.boxes
            if boxes is None or len(boxes) == 0:
                skipped += 1
                continue

            if args.dry_run:
                labeled += 1
                continue

            # YOLO label format: class cx cy w h  (all normalised 0-1)
            h, w = result.orig_shape
            lines = []
            for box in boxes:
                cls  = int(box.cls[0])
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                cx = ((x1 + x2) / 2) / w
                cy = ((y1 + y2) / 2) / h
                bw = (x2 - x1) / w
                bh = (y2 - y1) / h
                lines.append(f"{cls} {cx:.6f} {cy:.6f} {bw:.6f} {bh:.6f}")

            label_file = out_dir / (img_path.stem + ".txt")
            label_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
            labeled += 1

        done = min(i + batch, len(images))
        print(f"  {done}/{len(images)}  labeled={labeled}  no-detection={skipped}", end="\r")

    print(f"\n✅ Done — {labeled}/{len(images)} frames have detections, {skipped} empty")
    if not args.dry_run:
        print(f"Label files written to: {out_dir}/")
    print("\nNext steps:")
    print("  1. Copy label files next to their images (same filename, .txt extension)")
    print("  2. Open in LabelImg or Roboflow to review/correct the boxes")
    print("  3. Run Step 3 (build_dataset.py) to create the train/val split")


def _has_cuda():
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


if __name__ == "__main__":
    main()
