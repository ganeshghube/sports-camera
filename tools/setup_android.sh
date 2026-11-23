#!/usr/bin/env bash
# ==============================================================================
# Cricket Live — One-Command Automated Setup for Android (Termux)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${ROOT_DIR}"

echo "===================================================================="
echo "🏏 Cricket Live — Android (Termux ARM64) Setup"
echo "===================================================================="
echo

# 1. Check if running inside Termux or Android
if [ ! -d "/data/data/com.termux" ] && [ -z "${TERMUX_VERSION:-}" ] && [ -z "${ANDROID_ROOT:-}" ]; then
  echo "⚠️  Note: This script is optimized for Android Termux."
  echo "    Continuing installation for Linux/ARM64..."
fi

# 2. Update Termux repositories and install system packages
echo "📦 Step 1/4: Installing required system packages..."
if command -v pkg >/dev/null 2>&1; then
  pkg update -y || true
  pkg install -y python clang libjpeg-turbo libpng ffmpeg git rust binutils make qrencode termux-tools
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y || true
  sudo apt-get install -y python3 python3-pip python3-dev ffmpeg libjpeg-dev zlib1g-dev libgl1 libglib2.0-0 git qrencode
fi

# 3. Setup Python Virtual Environment or User packages
echo "🐍 Step 2/4: Preparing Python environment..."
python -m pip install --upgrade pip setuptools wheel

echo "📥 Step 3/4: Installing Cricket Live Android dependencies..."
if [ -f "requirements-android.txt" ]; then
  python -m pip install -r requirements-android.txt
else
  python -m pip install fastapi "uvicorn[standard]" httpx python-dotenv psutil "websocket-client>=1.8,<2" opencv-python numpy ultralytics pyserial
fi

# 4. Create default Android .env if not exists
echo "⚙️  Step 4/4: Configuring Android environment settings..."
if [ ! -f ".env" ]; then
  cp .env.example .env 2>/dev/null || touch .env
  # Set Android specific defaults
  grep -q "camera_type=" .env 2>/dev/null && sed -i 's/^camera_type=.*/camera_type=android/' .env || echo "camera_type=android" >> .env
  grep -q "RTSP_URL=" .env 2>/dev/null && sed -i 's|^RTSP_URL=.*|RTSP_URL=http://127.0.0.1:8080/video|' .env || echo "RTSP_URL=http://127.0.0.1:8080/video" >> .env
  grep -q "DEVICE=" .env 2>/dev/null && sed -i 's/^DEVICE=.*/DEVICE=CPU/' .env || echo "DEVICE=CPU" >> .env
  grep -q "SERIAL_PORT=" .env 2>/dev/null && sed -i 's|^SERIAL_PORT=.*|SERIAL_PORT=/dev/ttyUSB0|' .env || echo "SERIAL_PORT=/dev/ttyUSB0" >> .env
  echo "Created .env with Android phone camera defaults (http://127.0.0.1:8080/video)."
fi

# 5. Make startup script executable
chmod +x start_android.sh 2>/dev/null || true

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
