@echo off
chcp 65001 >nul
title PaperLibrary v1.0 Nexus
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

if exist "runtime\portable\python.exe" (set "PY=runtime\portable\python.exe") else (set "PY=python")
if exist "runtime\portable\pythonw.exe" (set "PYW=runtime\portable\pythonw.exe") else (set "PYW=pythonw")

rem Already running: just open the browser.
"%PY%" "tools\check_running.py" "http://127.0.0.1:4321/api/health" >nul 2>&1
if not errorlevel 1 (
  start "" "http://127.0.0.1:4321/"
  exit /b 0
)

rem Start server in background (pythonw = no console window), then wait and open browser.
start "" "%PYW%" -m paperlib --port 4321 --no-open
"%PYW%" "tools\open_when_ready.py" "http://127.0.0.1:4321/" --timeout 25
if errorlevel 1 (
  echo.
  echo Startup timed out. See data\logs\launcher.log
  echo Or run this in a console to see the error:
  echo   runtime\portable\python.exe -m paperlib --port 4321 --no-open
  pause
)
exit /b 0
