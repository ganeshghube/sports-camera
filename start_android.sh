#!/usr/bin/env bash
# ==============================================================================
# Cricket Live — Plug-and-Play Android Launcher with Hotspot Detection & QR Code
# ==============================================================================
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-8000}"

# 0. Automatic First-Time Install & Deploy Check
# If packages or .env are missing, run setup_android.sh automatically
if [ ! -f ".env" ] || ! python -c "import fastapi, uvicorn, cv2" >/dev/null 2>&1; then
  echo "===================================================================="
  echo "🚀 First-time run detected! Auto-installing dependencies..."
  echo "===================================================================="
  bash tools/setup_android.sh
fi

# 1. Inspect all network interfaces for Hotspot or Wi-Fi
HOTSPOT_IP=""
WIFI_IP=""
OTHER_IPS=()

# Extract all IPv4 addresses excluding loopback (127.0.0.1)
ADDR_LINES=$(ip -4 -o addr show 2>/dev/null | awk '{print $2 "|" $4}' | grep -v '^lo ' || true)
for item in $ADDR_LINES; do
  [ -z "$item" ] && continue
  IFACE="${item%%|*}"
  IP_CIDR="${item##*|}"
  IP_ADDR="${IP_CIDR%%/*}"

  # Check if this is an Android Hotspot / Tethering interface
  if [[ "$IFACE" =~ ^(ap0|wlan1|swlan0|softap0)$ ]] || [[ "$IP_ADDR" =~ ^192\.168\.(43|49)\. ]]; then
    HOTSPOT_IP="$IP_ADDR"
  elif [[ "$IFACE" =~ ^(wlan0)$ ]]; then
    WIFI_IP="$IP_ADDR"
  else
    OTHER_IPS+=("$IP_ADDR")
  fi
done

# 2. If Hotspot is not yet active, offer to open Android Hotspot Settings
if [ -z "${HOTSPOT_IP}" ]; then
  if command -v am >/dev/null 2>&1; then
    echo "💡 [Tip] Turn on your phone's 'Mobile Hotspot' so anyone nearby can connect!"
    # Non-blocking attempt to open Hotspot settings screen on phone
    am start -a android.settings.TETHER_SETTINGS >/dev/null 2>&1 || true
  fi
  # Standard Android default hotspot gateway is 192.168.43.1
  HOTSPOT_IP="192.168.43.1 (default when enabled)"
fi

PRIMARY_EXTERNAL_IP="${WIFI_IP:-${HOTSPOT_IP%% *}}"

echo "===================================================================="
echo "🏏 CRICKET LIVE — ANDROID MOBILE HOTSPOT & CONSOLE"
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
  echo "🌐 HOME/GROUND WI-FI (If phone is connected to existing router):"
  echo "   http://${WIFI_IP}:${PORT}"
fi

echo "===================================================================="
echo "• Camera Source:    Phone Camera (http://127.0.0.1:8080/video)"
echo "• Motor Interface:  ESP32 Serial (USB-OTG / Bluetooth auto-scan)"
echo "• AI Features:      Auto-Zoom, Auto-Track, Continuous Learning"
echo "===================================================================="
echo

# 3. Print QR code in terminal if qrencode is available
QR_TARGET_URL="http://${PRIMARY_EXTERNAL_IP}:${PORT}"
if command -v qrencode >/dev/null 2>&1; then
  echo "📱 SCAN TO CONNECT (Point any phone camera at this QR code):"
  qrencode -t ANSIUTF8 "${QR_TARGET_URL}" 2>/dev/null || true
  echo
fi

# 4. If termux-open-url is available, open local preview after a short delay
if command -v termux-open-url >/dev/null 2>&1; then
  (sleep 2.5 && termux-open-url "http://127.0.0.1:${PORT}/preview.html") &
fi

exec python run.py
