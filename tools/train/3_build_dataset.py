#!/usr/bin/env python3
"""
Step 3 — Build a YOLO dataset folder from labelled images.

Expects:
    training_data/
        raw_frames/   ← images (.jpg/.png)
        auto_labels/  ← matching .txt label files (corrected in LabelImg/Roboflow)

Produces:
    training_data/dataset/
        images/train/   images/val/
        labels/train/   labels/val/
        data.yaml       ← ready for yolo train data=...

Usage:
    python tools/train/3_build_dataset.py --frames training_data/raw_frames
                                          --labels training_data/auto_labels
                                          --out    training_data/dataset
                                          --val-split 0.15
"""
from __future__ import annotations
import argparse, random, shutil, sys
from pathlib import Path

IMG_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
CLASSES   = ["cricketBall"]   # must match your label class IDs


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames",    required=True, help="Folder of extracted frames")
    ap.add_argument("--labels",    required=True, help="Folder of corrected .txt label files")
    ap.add_argument("--out",       required=True, help="Output dataset folder")
    ap.add_argument("--val-split", type=float, default=0.15, help="Fraction for validation (default 0.15)")
    ap.add_argument("--seed",      type=int,   default=42)
    ap.add_argument("--only-labeled", action="store_true",
                    help="Skip images that have no matching label file (default: include as negatives)")
    args = ap.parse_args()

    frames_dir = Path(args.frames)
    labels_dir = Path(args.labels)
    out_dir    = Path(args.out)

    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    images = sorted(p for p in frames_dir.rglob("*") if p.suffix.lower() in IMG_EXTS)
    if not images:
        sys.exit(f"No images found in {frames_dir}")

    # Pair images with labels (label may be missing = negative / no-ball frame)
    pairs: list[tuple[Path, Path | None]] = []
    for img in images:
        lbl = labels_dir / (img.stem + ".txt")
        if lbl.exists():
            pairs.append((img, lbl))
        elif not args.only_labeled:
            pairs.append((img, None))   # hard-negative: teaches "no ball" here

    random.seed(args.seed)
    random.shuffle(pairs)

    n_val   = max(1, int(len(pairs) * args.val_split))
    n_train = len(pairs) - n_val
    splits  = {"train": pairs[:n_train], "val": pairs[n_val:]}

    counts = {}
    for split, ps in splits.items():
        img_dir = out_dir / "images" / split
        lbl_dir = out_dir / "labels" / split
        with_ball = no_ball = 0
        for img_path, lbl_path in ps:
            shutil.copy2(img_path, img_dir / img_path.name)
            if lbl_path:
                shutil.copy2(lbl_path, lbl_dir / (img_path.stem + ".txt"))
                with_ball += 1
            else:
                # Empty label file = negative example
                (lbl_dir / (img_path.stem + ".txt")).write_text("", encoding="utf-8")
                no_ball += 1
        counts[split] = (with_ball, no_ball)
        print(f"  {split:5s}  {with_ball} with ball  +  {no_ball} negatives  = {with_ball+no_ball} total")

    # Write data.yaml
    yaml_path = out_dir / "data.yaml"
    yaml_path.write_text(
        f"path: {out_dir.resolve().as_posix()}\n"
        f"train: images/train\n"
        f"val:   images/val\n"
        f"nc: {len(CLASSES)}\n"
        f"names: {CLASSES}\n",
        encoding="utf-8",
    )
    print(f"\nSUCCESS Dataset ready at: {out_dir}/")
    print(f"   data.yaml: {yaml_path}")
    print(f"\nNext: run Step 4 (train.py) to fine-tune the model.")


if __name__ == "__main__":
    main()
