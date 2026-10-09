#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== Cricket Live Raspberry Pi setup =="
if ! command -v apt-get >/dev/null 2>&1; then
  echo "This helper expects Raspberry Pi OS / Debian with apt-get."
  exit 1
fi

sudo apt-get update
sudo apt-get install -y python3-venv python3-dev python3-picamera2 ffmpeg libgl1 libglib2.0-0

if [ ! -d .venv ]; then
  python3 -m venv --system-site-packages .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-rpi.txt

echo
echo "Checking for a Chromium executable..."
for c in chromium chromium-browser google-chrome; do
  if command -v "$c" >/dev/null 2>&1; then
    echo "Found browser: $(command -v "$c")"
    break
  fi
done

echo
echo "Configure .env:"
echo "  camera_type=rpi"
echo "  MOTOR_DRIVER=gpio"
echo "  RPI_CAMERA_BACKEND=auto"
echo "  YOUTUBE_LATENCY=ultraLow"
echo "  GITHUB_REPO=YOUR_ORG/YOUR_REPO"
echo
echo "For a Pi CSI camera, Picamera2 is preferred and V4L2 is the fallback."
echo "Run: ./start.sh"
