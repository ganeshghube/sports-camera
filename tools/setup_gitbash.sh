#!/usr/bin/env bash
set -e
cd "$(dirname "$0")/.."
echo "Creating Python 3.11 virtual environment..."
rm -rf .venv
if command -v py >/dev/null 2>&1; then
  py -3.11 -m venv .venv
else
  python3.11 -m venv .venv
fi
if [ -f ".venv/Scripts/activate" ]; then
  source .venv/Scripts/activate
else
  source .venv/bin/activate
fi
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install -r ai/requirements.txt
[ -f .env ] || cp .env.example .env
python -c "from backend.system import SystemManager; print('SystemManager import OK')"
echo
echo "Setup complete."
echo "Start everything with: bash start.sh"
