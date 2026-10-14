#!/usr/bin/env bash
set -e
rm -rf .venv
py -3.14 -m venv .venv
source .venv/Scripts/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install -r ai/requirements.txt
[ -f .env ] || cp .env.example .env
python -c "from backend.system import SystemManager; print('SystemManager import OK')"
ffmpeg -version
ffprobe -version
python -c "from backend.system import SystemManager; print('SystemManager OK')" && \
python -c "import fastapi, uvicorn, cv2, numpy; print('Python dependencies OK')" && \
cd "$(dirname "$0")"
if [ -f ".venv/Scripts/python.exe" ]; then
  exec ".venv/Scripts/python.exe" run.py
elif [ -f ".venv/bin/python" ]; then
  exec ".venv/bin/python" run.py
else
  echo "Virtual environment not found. Run tools/setup_gitbash.sh first."
  exit 1
fi
