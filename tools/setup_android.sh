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

py_has() {
  python -c "import $1" >/dev/null 2>&1
}

pip_install() {
  python -m pip install --no-cache-dir "$@"
}

pip_install_nobi() {
  python -m pip install --no-cache-dir --no-build-isolation "$@"
}

pip_install_smart() {
  pip_install "$@" || pip_install_nobi "$@"
}

# 1. Check if running inside Termux or Android
IS_TERMUX=0
if [ -d "/data/data/com.termux" ] || [ -n "${TERMUX_VERSION:-}" ] || [ -n "${ANDROID_ROOT:-}" ]; then
  IS_TERMUX=1
else
  echo "⚠️  Note: This script is optimized for Android Termux."
  echo "    Continuing installation for Linux/ARM64..."
fi

# 2. Update Termux repositories and install system + prebuilt Python packages
echo "📦 Step 1/5: Installing required system & prebuilt ARM64 packages..."
if command -v pkg >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  pkg update -y || true
  pkg upgrade -y -o Dpkg::Options::="--force-confold" || true

  # Enable Termux extra repos that provide prebuilt OpenCV, PyTorch, SciPy, etc.
  pkg install -y x11-repo tur-repo >/dev/null 2>&1 || true
  pkg update -y || true

  # Core runtime tools, FFmpeg, QR generator, and native build toolchain
  pkg install -y \
    python python-pip ffmpeg git qrencode termux-tools iproute2 curl wget \
    clang make cmake ninja pkg-config binutils rust maturin cython patchelf ndk-sysroot \
    libjpeg-turbo libpng libwebp libtiff freetype littlecms openblas \
    libxml2 libxslt zlib openssl libffi || true

  # Prebuilt Termux Python packages avoid fragile source builds on Bionic libc
  for pkg_name in \
    python-setuptools \
    python-wheel \
    python-numpy \
    opencv-python \
    python-opencv \
    python-pillow \
    python-torch \
    python-torchvision \
    python-scipy \
    python-matplotlib \
    python-psutil \
    python-cryptography \
    python-yaml \
    python-lxml \
    python-pydantic-core \
    python-pydantic \
    python-fastapi; do
    pkg install -y "$pkg_name" >/dev/null 2>&1 || true
  done
elif command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update -y || true
  sudo apt-get install -y \
    python3 python3-pip python3-dev python3-venv \
    ffmpeg libjpeg-dev zlib1g-dev libgl1 libglib2.0-0 \
    libopenblas-dev build-essential git qrencode
fi

# 3. Prepare Python & native build environment variables for Termux
echo "🐍 Step 2/5: Preparing Python environment..."
if [ "$IS_TERMUX" -eq 1 ]; then
  export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-24}"
  export MATHLIB="m"
  export LDFLAGS="${LDFLAGS:-} -lm"
  export CFLAGS="${CFLAGS:-} -Wno-incompatible-function-pointer-types"
  if command -v rustc >/dev/null 2>&1; then
    RUST_HOST="$(rustc -vV 2>/dev/null | awk '/host:/ {print $2}' || true)"
    [ -n "$RUST_HOST" ] && export CARGO_BUILD_TARGET="${CARGO_BUILD_TARGET:-$RUST_HOST}"
  fi
fi

if ! python -m pip --version >/dev/null 2>&1; then
  python -m ensurepip --upgrade || true
fi
pip_install --upgrade pip setuptools wheel || true

# 4. Install Python dependencies (using requirements-android.txt with Termux fallbacks)
echo "📥 Step 3/5: Installing Cricket Live Android dependencies..."
if [ -f "requirements-android.txt" ] && (pip_install -r requirements-android.txt || pip_install_nobi -r requirements-android.txt); then
  echo "✅ Installed all packages from requirements-android.txt"
else
  echo "⚠️  Full requirements-android.txt pip install hit a native build issue; installing with Termux fallbacks..."

  # Install pydantic / fastapi safely without letting pip rebuild maturin from source
  if ! py_has pydantic_core; then
    pip_install_nobi maturin >/dev/null 2>&1 || true
    pip_install_nobi pydantic-core || pip_install_nobi "pydantic-core<2.30" || true
  fi
  if ! py_has fastapi; then
    if ! pip_install_smart fastapi; then
      if py_has pydantic_core && py_has pydantic; then
        echo "ℹ️  Installing fastapi (--no-deps) with Termux prebuilt pydantic..."
        pip_install starlette typing-extensions annotated-types anyio idna sniffio
        pip_install --no-deps fastapi
      else
        echo "❌ Failed to install fastapi/pydantic."
        exit 1
      fi
    fi
  fi

  # Core web/backend dependencies
  pip_install_smart httpx python-dotenv "websocket-client>=1.8,<2" pyserial
  pip_install_smart "uvicorn[standard]" || pip_install_smart uvicorn websockets
  py_has psutil || pip_install_smart "psutil>=5.9"

  # Numerical & vision dependencies (prefer Termux prebuilt packages if present)
  py_has numpy || pip_install_smart numpy
  if ! py_has cv2; then
    pip_install_smart opencv-python || pip_install_smart opencv-python-headless
  fi

  # Ultralytics YOLO + its dependencies (avoid recompiling Termux-prebuilt torch/cv2)
  py_has PIL || pip_install_smart pillow || true
  py_has yaml || pip_install_smart pyyaml || true
  pip_install_smart requests tqdm py-cpuinfo ultralytics-thop || true
  if ! py_has ultralytics; then
    if ! pip_install_smart ultralytics; then
      if py_has torch && py_has cv2 && py_has numpy; then
        echo "ℹ️  Installing ultralytics (--no-deps) using Termux prebuilt torch/cv2/numpy..."
        pip_install --no-deps ultralytics
      else
        echo "❌ Failed to install ultralytics and its native dependencies."
        exit 1
      fi
    fi
  fi
fi

# Ensure WebSocket support for Uvicorn is present
py_has websockets || pip_install websockets || true

# 5. Create default Android .env if not exists
echo "⚙️  Step 4/5: Configuring Android environment settings..."
if [ ! -f ".env" ]; then
  cp .env.example .env 2>/dev/null || touch .env
  # Set Android specific defaults
  grep -q "camera_type=" .env 2>/dev/null && sed -i 's/^camera_type=.*/camera_type=android/' .env || echo "camera_type=android" >> .env
  grep -q "RTSP_URL=" .env 2>/dev/null && sed -i 's|^RTSP_URL=.*|RTSP_URL=http://127.0.0.1:8080/video|' .env || echo "RTSP_URL=http://127.0.0.1:8080/video" >> .env
  grep -q "DEVICE=" .env 2>/dev/null && sed -i 's/^DEVICE=.*/DEVICE=CPU/' .env || echo "DEVICE=CPU" >> .env
  grep -q "SERIAL_PORT=" .env 2>/dev/null && sed -i 's|^SERIAL_PORT=.*|SERIAL_PORT=/dev/ttyUSB0|' .env || echo "SERIAL_PORT=/dev/ttyUSB0" >> .env
  echo "Created .env with Android phone camera defaults (http://127.0.0.1:8080/video)."
fi

# Make startup script executable
chmod +x start_android.sh 2>/dev/null || true

# 6. Verify all required Python modules and system tools
echo "🔍 Step 5/5: Verifying installation..."
MISSING_ITEMS=()
for mod in fastapi uvicorn websockets httpx dotenv psutil websocket cv2 numpy ultralytics serial; do
  if ! py_has "$mod"; then
    MISSING_ITEMS+=("python:$mod")
  fi
done
for bin_name in ffmpeg ffprobe qrencode; do
  if ! command -v "$bin_name" >/dev/null 2>&1; then
    MISSING_ITEMS+=("bin:$bin_name")
  fi
done

if [ "${#MISSING_ITEMS[@]}" -gt 0 ]; then
  echo "❌ Missing dependencies after setup: ${MISSING_ITEMS[*]}"
  echo "   Please check the errors above and re-run: bash tools/setup_android.sh"
  exit 1
fi

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
