@echo off
setlocal
PowerShell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_windows.ps1" %*
if errorlevel 1 (
  echo.
  echo Windows install failed.
  exit /b 1
)
echo.
echo Windows install complete.
