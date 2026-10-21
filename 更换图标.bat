@echo off
chcp 65001 >nul
title PaperLibrary - Set Icon
cd /d "%~dp0"

set "PY=C:\Program Files\Python311\python.exe"
if not exist "%PY%" set "PY=python"

rem Pass a dragged-in image as the argument, or open a file picker.
"%PY%" set_icon.py %*

if errorlevel 1 pause
