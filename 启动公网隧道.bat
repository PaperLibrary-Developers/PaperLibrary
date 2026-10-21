@echo off
chcp 65001 >nul
title 🌍 文献检索系统 — 公网隧道

echo.
echo   ╔══════════════════════════════════════════╗
echo   ║  🌍 公网隧道 — 让文献库随身访问        ║
echo   ╚══════════════════════════════════════════╝
echo.
echo   [1] 🔷 Ngrok 隧道（推荐：免费固定域名）
echo   [2] ☁️  Cloudflare Tunnel（免费无限制）
echo   [0] 退出
echo.

set /p choice="  请选择 [1/2/0]: "

set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%PATH%"
cd /d "%~dp0"

if "%choice%"=="1" (
    python tunnel.py ngrok
) else if "%choice%"=="2" (
    python tunnel.py cloudflare
) else (
    echo   退出
)

pause
