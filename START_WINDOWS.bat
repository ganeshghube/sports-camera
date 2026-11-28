@echo off
setlocal
cd /d "%~dp0"
if defined VIRTUAL_ENV if exist "%VIRTUAL_ENV%\Scripts\python.exe" (
  "%VIRTUAL_ENV%\Scripts\python.exe" run.py
  exit /b %ERRORLEVEL%
)
if not exist ".venv\Scripts\python.exe" (
  echo Virtual environment not found.
  echo Run: bash tools/setup_gitbash.sh
  pause
  exit /b 1
)
".venv\Scripts\python.exe" run.py
