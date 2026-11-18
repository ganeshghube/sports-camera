#!/usr/bin/env python3
"""
Step 1 — Extract frames from your cricket match videos.

Usage:
    python tools/train/1_extract_frames.py --video recordings/match1.mp4 --out training_data/raw_frames
    python tools/train/1_extract_frames.py --video recordings/ --out training_data/raw_frames  # whole folder

Options:
    --every N      Save every Nth frame (default: 10, ~3 fps from 30fps video)
    --max N        Max frames per video (default: 2000)
    --quality N    JPEG quality 1-100 (default: 92)
    --blur N       Skip blurry frames below Laplacian threshold (default: 80)
    --resize WxH   Resize output frames e.g. 1280x720 (default: keep original)
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path

VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".ts", ".mts", ".m4v", ".webm"}


def process_video(video_path: Path, out_dir: Path, every: int, max_frames: int,
                  quality: int, blur_thresh: float, resize: tuple | None):
    try:
        import cv2
    except ImportError:
        sys.exit("cv2 not found — run from the project venv: .venv\\Scripts\\python.exe")

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"  ✗ Cannot open: {video_path}")
        return 0

    total  = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps    = cap.get(cv2.CAP_PROP_FPS) or 25
    stem   = video_path.stem.replace(" ", "_")
    saved  = 0
    fi     = 0

    print(f"  → {video_path.name}  ({total} frames @ {fps:.1f} fps)")

    while saved < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        fi += 1
        if fi % every != 0:
            continue

        # Blur filter — skip frames that are too motion-blurred to annotate
        if blur_thresh > 0:
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            if cv2.Laplacian(gray, cv2.CV_64F).var() < blur_thresh:
                continue

        if resize:
            frame = cv2.resize(frame, resize, interpolation=cv2.INTER_AREA)

        name = f"{stem}_f{fi:07d}.jpg"
        cv2.imwrite(str(out_dir / name), frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
        saved += 1

    cap.release()
    print(f"     saved {saved} frames")
    return saved


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", required=True, help="Video file or folder of videos")
    ap.add_argument("--out",   required=True, help="Output folder for extracted frames")
    ap.add_argument("--every", type=int,   default=10,   help="Save 1 frame every N frames (default 10)")
    ap.add_argument("--max",   type=int,   default=2000, help="Max frames per video (default 2000)")
    ap.add_argument("--quality", type=int, default=92,   help="JPEG quality (default 92)")
    ap.add_argument("--blur",  type=float, default=80.0, help="Blur threshold — skip frames below (default 80)")
    ap.add_argument("--resize", default=None, help="Resize WxH e.g. 1280x720 (default keep original)")
    args = ap.parse_args()

    video_path = Path(args.video)
    out_dir    = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    resize = None
    if args.resize:
        w, h = args.resize.lower().split("x")
        resize = (int(w), int(h))

    videos: list[Path] = []
    if video_path.is_dir():
        for ext in VIDEO_EXTS:
            videos.extend(video_path.rglob(f"*{ext}"))
        videos.extend(video_path.rglob("*" + ext.upper()) for ext in VIDEO_EXTS)
        videos = sorted(set(videos))
    elif video_path.is_file():
        videos = [video_path]
    else:
        sys.exit(f"Not found: {video_path}")

    print(f"Found {len(videos)} video(s) → extracting frames to {out_dir}/")
    total_saved = 0
    for v in videos:
        if v.suffix.lower() in VIDEO_EXTS:
            total_saved += process_video(v, out_dir, args.every, args.max,
                                          args.quality, args.blur, resize)

    print(f"\n✅ Done — {total_saved} frames saved to {out_dir}/")
    print("Next: open the frames folder in a labelling tool (see Step 2 instructions).")


if __name__ == "__main__":
    main()
