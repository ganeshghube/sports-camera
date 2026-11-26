#!/usr/bin/env bash
# ==============================================================================
# Cricket Live — One-Command Automated Setup for Android (Termux)
# Uses proot-distro (Ubuntu ARM64) inside Termux so all Python AI/CV packages
# install as prebuilt manylinux aarch64 wheels with zero source compilation.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "===================================================================="
echo "🏏 Cricket Live — Android (Termux ARM64) Setup"
echo "===================================================================="
echo

IS_TERMUX=0
if [ -d "/data/data/com.termux" ] || [ -n "${TERMUX_VERSION:-}" ] || [ -n "${ANDROID_ROOT:-}" ]; then
  IS_TERMUX=1
fi

if [ "$IS_TERMUX" -eq 1 ] && command -v pkg >/dev/null 2>&1; then
  # --------------------------------------------------------------------------
  # Termux Path: Install proot-distro + Ubuntu ARM64 for prebuilt PyPI wheels
  # --------------------------------------------------------------------------
  echo "📦 Step 1/5: Installing Termux host tools (proot-distro, qrencode)..."
  export DEBIAN_FRONTEND=noninteractive
  pkg update -y || true
  pkg upgrade -y -o Dpkg::Options::="--force-confold" || true
  pkg install -y proot-distro qrencode termux-tools iproute2

  echo "🐧 Step 2/5: Setting up Ubuntu ARM64 runtime inside Termux..."
  if [ ! -d "${PREFIX:-/data/data/com.termux/files/usr}/var/lib/proot-distro/installed-rootfs/ubuntu" ]; then
    proot-distro install ubuntu
  else
    echo "   Ubuntu rootfs already installed."
  fi

  echo "📥 Step 3/5: Installing Python, FFmpeg & Cricket Live dependencies (prebuilt ARM64 wheels)..."
  proot-distro login ubuntu --shared-tmp --bind "${ROOT_DIR}:${ROOT_DIR}" -- bash -e <<EOF
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-pip python3-venv ffmpeg libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 libgomp1 git

cd "${ROOT_DIR}"
if [ ! -d ".venv-android" ]; then
  python3 -m venv .venv-android
fi

.venv-android/bin/python -m pip install --upgrade pip setuptools wheel
.venv-android/bin/python -m pip install -r requirements-android.txt

echo "🔍 Verifying installed Python modules & binaries..."
.venv-android/bin/python -c "import fastapi, uvicorn, websockets, httpx, dotenv, psutil, websocket, cv2, numpy, torch, ultralytics, serial; print('✅ All Python dependencies verified!')"
command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1 && echo "✅ FFmpeg & FFprobe verified!"
EOF

else
  # --------------------------------------------------------------------------
  # Generic Linux/ARM64 fallback (UserLAnd Fedora/Ubuntu/Debian, etc.)
  # --------------------------------------------------------------------------
  echo "📦 Step 1/5: Installing system packages..."
  SUDO=""
  if [ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1; then
    SUDO="sudo"
  fi
  if command -v apt-get >/dev/null 2>&1; then
    $SUDO apt-get update -y || true
    $SUDO apt-get install -y python3 python3-pip python3-dev python3-venv ffmpeg libjpeg-dev zlib1g-dev libgl1 libglib2.0-0 git qrencode iproute2 || true
  elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-pip python3-devel mesa-libGL glib2 libjpeg-turbo-devel zlib-devel git qrencode iproute || true
    if ! command -v ffmpeg >/dev/null 2>&1; then
      $SUDO dnf install -y ffmpeg-free || {
        $SUDO dnf install -y "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-$(rpm -E %fedora).noarch.rpm" || true
        $SUDO dnf install -y ffmpeg || true
      }
    fi
  fi

  echo "📥 Step 2-3/5: Installing Cricket Live Android dependencies..."
  if [ ! -d ".venv-android" ]; then
    python3 -m venv .venv-android
  fi
  .venv-android/bin/python -m pip install --upgrade pip setuptools wheel
  .venv-android/bin/python -m pip install -r requirements-android.txt
fi

# 4. Create default Android .env if not exists
echo "⚙️  Step 4/5: Configuring Android environment settings..."
if [ ! -f ".env" ]; then
  cp .env.example .env 2>/dev/null || touch .env
  grep -q "camera_type=" .env 2>/dev/null && sed -i 's/^camera_type=.*/camera_type=android/' .env || echo "camera_type=android" >> .env
  grep -q "RTSP_URL=" .env 2>/dev/null && sed -i 's|^RTSP_URL=.*|RTSP_URL=http://127.0.0.1:8080/video|' .env || echo "RTSP_URL=http://127.0.0.1:8080/video" >> .env
  grep -q "DEVICE=" .env 2>/dev/null && sed -i 's/^DEVICE=.*/DEVICE=CPU/' .env || echo "DEVICE=CPU" >> .env
  grep -q "SERIAL_PORT=" .env 2>/dev/null && sed -i 's|^SERIAL_PORT=.*|SERIAL_PORT=/dev/ttyUSB0|' .env || echo "SERIAL_PORT=/dev/ttyUSB0" >> .env
  echo "Created .env with Android phone camera defaults (http://127.0.0.1:8080/video)."
fi

chmod +x start_android.sh 2>/dev/null || true

echo "🔍 Step 5/5: Finalizing setup..."
echo
echo "===================================================================="
echo "✅ Setup Complete! Cricket Live is ready on Android."
echo "===================================================================="
echo
echo "📱 To use the phone's built-in camera:"
echo "   1. Install 'IP Webcam' from Google Play / F-Droid."
echo "   2. In IP Webcam, tap 'Start server' at the bottom."
echo "   3. Run:  bash start_android.sh"
echo "   4. Open your phone browser:  http://127.0.0.1:8000"
echo
echo "🔌 To attach an ESP32 pan/tilt motor:"
echo "   - Plug ESP32 into phone via USB-OTG adapter (/dev/ttyUSB0 auto-detected)"
echo "   - OR pair ESP32 over Bluetooth (/dev/rfcomm0 auto-detected)"
echo "===================================================================="
