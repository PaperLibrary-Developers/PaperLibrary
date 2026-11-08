@echo off
chcp 65001 >nul
title PaperLibrary v1.2 Nexus
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONDONTWRITEBYTECODE=1

rem ---------------------------------------------------------------
rem Keep this file ASCII-only: cmd.exe parses batch files with the
rem OEM code page, so UTF-8 CJK comments corrupt the script.
rem
rem Pick an interpreter that really has PyMuPDF. The python/pythonw
rem found on PATH may belong to another tool (e.g. an IDE bundle)
rem without fitz; starting with it dies at "import fitz" in
rem paperlib/legacy/figures.py within a second, so the browser keeps
rem showing a stale page and it looks like the edit did nothing.
rem ---------------------------------------------------------------
set "PY="
set "PYW="
call :probe "runtime\portable\python.exe"
if not defined PY call :probe "C:\Program Files\Python311\python.exe"
if not defined PY call :probe "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PY call :probe "python"
if not defined PY (
  echo.
  echo No Python with PyMuPDF found. Install dependencies first:
  echo   pip install -r requirements.txt
  pause
  exit /b 1
)

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
  echo   "%PY%" -m paperlib --port 4321 --no-open
  pause
)
exit /b 0

rem ---- probe one candidate; adopt the first one that can import fitz ----
:probe
if /i not "%~1"=="python" if not exist "%~1" goto :eof
"%~1" -c "import fitz" >nul 2>&1
if errorlevel 1 goto :eof
set "PY=%~1"
set "PYW=%~1"
set "PYW=%PYW:python.exe=pythonw.exe%"
goto :eof
