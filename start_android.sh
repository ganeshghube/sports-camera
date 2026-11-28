#!/usr/bin/env bash
# ==============================================================================
# Cricket Live — Plug-and-Play Android (Fedora / UserLAnd) Launcher
# ==============================================================================
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"
export ANDROID_DATA="${ANDROID_DATA:-/data}"

REQ_CHECK="import fastapi, uvicorn, httpx, dotenv, psutil, websocket, cv2, numpy, ultralytics, serial"

# 0. Start immediately if dependencies exist; otherwise run setup_android.sh
if [ ! -x "${ROOT_DIR}/.venv-android/bin/python" ] || ! "${ROOT_DIR}/.venv-android/bin/python" -c "${REQ_CHECK}" >/dev/null 2>&1 || ! command -v ffmpeg >/dev/null 2>&1; then
  echo "===================================================================="
  echo "🚀 Missing dependencies detected! Auto-installing for Fedora..."
  echo "===================================================================="
  bash tools/setup_android.sh
fi

if [ ! -f ".env" ]; then
  cp .env.example .env 2>/dev/null || touch .env
  grep -q "camera_type=" .env 2>/dev/null && sed -i 's/^camera_type=.*/camera_type=android/' .env || echo "camera_type=android" >> .env
  grep -q "RTSP_URL=" .env 2>/dev/null && sed -i 's|^RTSP_URL=.*|RTSP_URL=http://127.0.0.1:8080/video|' .env || echo "RTSP_URL=http://127.0.0.1:8080/video" >> .env
  grep -q "DEVICE=" .env 2>/dev/null && sed -i 's/^DEVICE=.*/DEVICE=CPU/' .env || echo "DEVICE=CPU" >> .env
  grep -q "SERIAL_PORT=" .env 2>/dev/null && sed -i 's|^SERIAL_PORT=.*|SERIAL_PORT=/dev/ttyUSB0|' .env || echo "SERIAL_PORT=/dev/ttyUSB0" >> .env
fi

# 1. Inspect network interfaces for Hotspot or Wi-Fi
HOTSPOT_IP=""
WIFI_IP=""
ADDR_LINES=$(ip -4 -o addr show 2>/dev/null | awk '{print $2 "|" $4}' | grep -v '^lo ' || true)
for item in $ADDR_LINES; do
  [ -z "$item" ] && continue
  IFACE="${item%%|*}"
  IP_CIDR="${item##*|}"
  IP_ADDR="${IP_CIDR%%/*}"

  if [[ "$IFACE" =~ ^(ap0|wlan1|swlan0|softap0)$ ]] || [[ "$IP_ADDR" =~ ^192\.168\.(43|49)\. ]]; then
    HOTSPOT_IP="$IP_ADDR"
  elif [[ "$IFACE" =~ ^(wlan0)$ ]]; then
    WIFI_IP="$IP_ADDR"
  fi
done

if [ -z "${HOTSPOT_IP}" ]; then
  HOTSPOT_IP="192.168.43.1 (default when enabled)"
fi

PRIMARY_EXTERNAL_IP="${WIFI_IP:-${HOTSPOT_IP%% *}}"

echo "===================================================================="
echo "🏏 CRICKET LIVE — ANDROID FEDORA CONSOLE"
echo "===================================================================="
echo "📱 PHONE LOCAL ACCESS:"
echo "   http://127.0.0.1:${PORT}"
echo
echo "📶 HOTSPOT ACCESS (Anyone connected to your phone's Wi-Fi hotspot):"
echo "   • Main Dashboard:    http://${HOTSPOT_IP}:${PORT}"
echo "   • Scorer (Pavilion): http://${HOTSPOT_IP}:${PORT}/scoring.html"
echo "   • Live Preview HUD:  http://${HOTSPOT_IP}:${PORT}/preview.html"
echo "   • Live Score Overlay:http://${HOTSPOT_IP}:${PORT}/index.html?overlay=1"

if [ -n "${WIFI_IP}" ]; then
  echo
  echo "🌐 HOME/GROUND WI-FI:"
  echo "   http://${WIFI_IP}:${PORT}"
fi

echo "===================================================================="
echo "• Camera Source:    Phone Camera (http://127.0.0.1:8080/video)"
echo "• AI Features:      Auto-Zoom, Auto-Track, Continuous Learning"
echo "===================================================================="
echo

# 2. Print QR code in terminal if qrencode is available
QR_TARGET_URL="http://${PRIMARY_EXTERNAL_IP}:${PORT}"
if command -v qrencode >/dev/null 2>&1; then
  echo "📱 SCAN TO CONNECT (Point any phone camera at this QR code):"
  qrencode -t ANSIUTF8 "${QR_TARGET_URL}" 2>/dev/null || true
  echo
fi

exec "${ROOT_DIR}/.venv-android/bin/python" run.py
