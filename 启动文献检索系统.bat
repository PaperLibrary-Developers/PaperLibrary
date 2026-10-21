@echo off
chcp 65001 >nul
title 📚 学术文献管理与图片检索系统

echo.
echo   ╔══════════════════════════════════════════╗
echo   ║  📚 学术文献管理与图片级检索系统  v1.0  ║
echo   ╚══════════════════════════════════════════╝
echo.
echo   正在启动服务...

:: 激活 Python 路径
set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%PATH%"

:: 切换到项目目录
cd /d "%~dp0"

:: 检查 Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   ❌ 未找到 Python，请先安装 Python 3.11+
    pause
    exit /b 1
)

:: 检查依赖
python -c "import streamlit" >nul 2>&1
if %errorlevel% neq 0 (
    echo   ⚠ 正在安装依赖...
    pip install -r requirements.txt
)

echo   ✅ 服务启动中，浏览器即将打开...
echo   📍 地址: http://localhost:8501
echo   🛑 关闭此窗口即可停止服务
echo.
echo   ─────────────────────────────────────────
echo.

:: 启动本地文件服务器（用于浏览器查看 PDF）
start "" /B python file_server.py
timeout /t 1 /nobreak >nul

:: 延迟 2 秒后自动打开浏览器
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://localhost:8501"

:: 启动 Streamlit
streamlit run app.py --server.headless true --server.port 8501 --browser.gatherUsageStats false
