@echo off
title PaperLibrary
cd /d "%~dp0"
"C:\Program Files\Python311\python.exe" start_all.py
echo.
if errorlevel 1 (
    echo Startup failed. See message above.
)
pause
