@echo off
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"

if not exist "%PY%" (
  echo Virtual environment not found. Run install_windows.bat first.
  exit /b 1
)

set "PYTHONPATH=%ROOT%src"
"%PY%" -m alogger_player %*
