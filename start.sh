#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ -f ".venv/Scripts/python.exe" ]; then
  exec ".venv/Scripts/python.exe" run.py
elif [ -f ".venv/bin/python" ]; then
  exec ".venv/bin/python" run.py
else
  echo "Virtual environment not found. Run tools/setup_gitbash.sh first."
  exit 1
fi
