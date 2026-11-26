#!/usr/bin/env bash
set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${ROOT_DIR}"

VENV_PY=""
if [ -f ".venv/Scripts/python.exe" ]; then
  VENV_PY=".venv/Scripts/python.exe"
elif [ -f ".venv/bin/python" ]; then
  VENV_PY=".venv/bin/python"
fi

REQ_CHECK="import fastapi, uvicorn, httpx, dotenv, playwright, psutil, websocket, cv2, numpy, ultralytics, serial"

if [ -z "${VENV_PY}" ] || ! "${VENV_PY}" -c "${REQ_CHECK}" >/dev/null 2>&1; then
  echo "===================================================================="
  echo "🚀 Virtual environment or dependencies missing. Installing..."
  echo "===================================================================="
  if [ -z "${VENV_PY}" ]; then
    if command -v py >/dev/null 2>&1; then
      py -3.14 -m venv .venv 2>/dev/null || py -3 -m venv .venv 2>/dev/null || py -m venv .venv
    elif command -v python3 >/dev/null 2>&1; then
      python3 -m venv .venv
    else
      python -m venv .venv
    fi
    if [ -f ".venv/Scripts/python.exe" ]; then
      VENV_PY=".venv/Scripts/python.exe"
    elif [ -f ".venv/bin/python" ]; then
      VENV_PY=".venv/bin/python"
    else
      echo "Virtual environment creation failed."
      exit 1
    fi
  fi

  "${VENV_PY}" -m pip install --upgrade pip setuptools wheel
  "${VENV_PY}" -m pip install -r requirements.txt
  "${VENV_PY}" -m pip install -r ai/requirements.txt
  echo "✅ Dependencies installed."
fi

[ -f .env ] || cp .env.example .env

exec "${VENV_PY}" run.py
