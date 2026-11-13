@echo off
chcp 65001 >nul
title PaperLibrary v1.3 Nexus - Stop
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONDONTWRITEBYTECODE=1

if exist "runtime\portable\python.exe" (set "PY=runtime\portable\python.exe") else (set "PY=python")

"%PY%" -m paperlib --stop
if errorlevel 1 (
  echo.
  echo Stop failed: the service may not be running on port 4321.
  pause
)
exit /b 0
