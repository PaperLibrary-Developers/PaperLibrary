@echo off
title Stop PaperLibrary
:: Kill streamlit and file_server on ports 8501/8502
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8502" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo PaperLibrary stopped.
timeout /t 2 >nul
