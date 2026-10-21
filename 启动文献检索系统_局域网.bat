@echo off
chcp 65001 >nul
title 📚 文献检索系统 — 局域网模式

echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║  📚 学术文献检索系统 — 局域网共享模式      ║
echo   ║  同一 WiFi 下的手机/平板/电脑均可访问       ║
echo   ╚══════════════════════════════════════════════╝
echo.

set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%PATH%"
cd /d "%~dp0"

python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   ❌ 未找到 Python
    pause
    exit /b 1
)

:: 获取本机 IP
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
    set "LOCAL_IP=%%a"
    set "LOCAL_IP=!LOCAL_IP: =!"
)

echo   📍 本机访问:    http://localhost:8501
echo   🌐 局域网访问:  http://%LOCAL_IP%:8501
echo.
echo   ⚠ 请确保其他设备与本机连接同一 WiFi
echo   🛑 关闭此窗口即可停止服务
echo   ─────────────────────────────────────────
echo.

start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8501"

streamlit run app.py --server.headless true --server.port 8501 --server.address 0.0.0.0 --browser.gatherUsageStats false
