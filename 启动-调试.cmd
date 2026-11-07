@echo off
chcp 65001 >nul
title PaperLibrary v1.0 Nexus - Debug
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

if exist "runtime\portable\python.exe" (set "PY=runtime\portable\python.exe") else (set "PY=python")

echo Starting in foreground (Ctrl+C to stop)...
"%PY%" -m paperlib --port 4321
echo.
echo Server exited with code %errorlevel%.
pause
endlocal
