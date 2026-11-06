@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

if exist "runtime\portable\python.exe" (
  set "PY=runtime\portable\python.exe"
) else (
  set "PY=python"
)

"%PY%" -m paperlib --port 4321 %*
if errorlevel 1 (
  echo.
  echo 启动失败。请确认端口 4321 未被占用，或查看上方错误信息。
  pause
)
endlocal
