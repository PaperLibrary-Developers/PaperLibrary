@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1

if exist "runtime\portable\python.exe" (
  set "PY=runtime\portable\python.exe"
) else (
  set "PY=python"
)

"%PY%" -m paperlib --stop
endlocal
