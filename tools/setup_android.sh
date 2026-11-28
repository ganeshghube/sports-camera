#!/usr/bin/env bash
# ==============================================================================
# Cricket Live — One-Command Automated Setup for Android (Fedora / UserLAnd)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "===================================================================="
echo "🏏 Cricket Live — Android (Fedora ARM64) Setup"
echo "===================================================================="
echo

SUDO=""
if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
  SUDO="sudo"
fi

# 1. Install Fedora system packages
echo "📦 Step 1/4: Installing Fedora system packages..."
if command -v dnf >/dev/null 2>&1; then
  $SUDO dnf install -y \
    python3 python3-pip python3-devel \
    mesa-libGL glib2 libSM libXext libXrender libgomp \
    libjpeg-turbo-devel zlib-devel git qrencode iproute || true

  if ! command -v ffmpeg >/dev/null 2>&1; then
    $SUDO dnf install -y ffmpeg-free || {
      $SUDO dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" || true
      $SUDO dnf install -y ffmpeg || true
    }
  fi
else
  echo "⚠️  dnf not found; ensure python3, ffmpeg, libGL, glib2, and qrencode are installed."
fi

# 2. Setup Python virtual environment & install Android requirements
echo "🐍 Step 2/4: Creating .venv-android and installing Python dependencies..."
if [ ! -d ".venv-android" ]; then
  python3 -m venv .venv-android
fi

.venv-android/bin/python -m pip install --upgrade pip setuptools wheel
.venv-android/bin/python -m pip install -r requirements-android.txt

# 3. Verify installation
echo "🔍 Step 3/4: Verifying installed Python modules & binaries..."
.venv-android/bin/python -c "import fastapi, uvicorn, websockets, httpx, dotenv, psutil, websocket, cv2, numpy, torch, ultralytics, serial; print('✅ All Python dependencies verified!')"
command -v ffmpeg >/dev/null 2>&1 && echo "✅ FFmpeg verified!" || echo "⚠️  FFmpeg not found on PATH."

# 4. Create default Android .env if not exists
echo "⚙️  Step 4/4: Configuring Android environment settings..."
if [ ! -f ".env" ]; then
  cp .env.example .env 2>/dev/null || touch .env
  grep -q "camera_type=" .env 2>/dev/null && sed -i 's/^camera_type=.*/camera_type=android/' .env || echo "camera_type=android" >> .env
  grep -q "RTSP_URL=" .env 2>/dev/null && sed -i 's|^RTSP_URL=.*|RTSP_URL=http://127.0.0.1:8080/video|' .env || echo "RTSP_URL=http://127.0.0.1:8080/video" >> .env
  grep -q "DEVICE=" .env 2>/dev/null && sed -i 's/^DEVICE=.*/DEVICE=CPU/' .env || echo "DEVICE=CPU" >> .env
  grep -q "SERIAL_PORT=" .env 2>/dev/null && sed -i 's|^SERIAL_PORT=.*|SERIAL_PORT=/dev/ttyUSB0|' .env || echo "SERIAL_PORT=/dev/ttyUSB0" >> .env
  grep -q "INPUT_SIZE=" .env 2>/dev/null && sed -i 's/^INPUT_SIZE=.*/INPUT_SIZE=416/' .env || echo "INPUT_SIZE=416" >> .env
  grep -q "PREVIEW_WIDTH=" .env 2>/dev/null && sed -i 's/^PREVIEW_WIDTH=.*/PREVIEW_WIDTH=640/' .env || echo "PREVIEW_WIDTH=640" >> .env
  grep -q "PREVIEW_FPS=" .env 2>/dev/null && sed -i 's/^PREVIEW_FPS=.*/PREVIEW_FPS=15/' .env || echo "PREVIEW_FPS=15" >> .env
  grep -q "PREVIEW_QUALITY=" .env 2>/dev/null && sed -i 's/^PREVIEW_QUALITY=.*/PREVIEW_QUALITY=60/' .env || echo "PREVIEW_QUALITY=60" >> .env
  echo "Created .env with Android phone camera defaults (http://127.0.0.1:8080/video)."
fi

chmod +x start_android.sh 2>/dev/null || true

echo
echo "===================================================================="
echo "✅ Setup Complete! Cricket Live is ready on Android (Fedora)."
echo "===================================================================="
echo
echo "📱 To use the phone's built-in camera:"
echo "   1. Install 'IP Webcam' from Google Play / F-Droid."
echo "   2. In IP Webcam, tap 'Start server' at the bottom."
echo "   3. Run:  bash start_android.sh"
echo "   4. Open your phone browser:  http://127.0.0.1:8000"
echo "===================================================================="
