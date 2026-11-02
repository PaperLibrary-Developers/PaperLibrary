@echo off
chcp 65001 >nul
title 📦 构建文献检索系统安装包

echo.
echo   ╔══════════════════════════════════════════════╗
echo   ║     📦 文献检索系统 — 打包构建工具          ║
echo   ╚══════════════════════════════════════════════╝
echo.

set "PATH=C:\Program Files\Python311;C:\Program Files\Python311\Scripts;%APPDATA%\Python\Python311\Scripts;%PATH%"
cd /d "%~dp0"

:: 检查 PyInstaller
python -c "import PyInstaller" >nul 2>&1
if %errorlevel% neq 0 (
    echo   ⚠ PyInstaller 未安装，正在安装...
    pip install pyinstaller
    echo.
)

echo   [1/3] 清理旧的构建文件...
if exist build rmdir /s /q build
if exist dist\PaperLibrary rmdir /s /q dist\PaperLibrary
echo         ✅ 完成
echo.

echo   [2/3] 运行 PyInstaller 打包...
echo         这可能需要 5-10 分钟，请耐心等待...
echo.
python -m PyInstaller paper_library.spec --clean --noconfirm --log-level WARN 2>&1
echo.
echo         ✅ 打包完成
echo.

echo   [3/3] 检查输出...
if exist "dist\PaperLibrary\PaperLibrary.exe" (
    echo.
    echo   ╔══════════════════════════════════════════════╗
    echo   ║  ✅ 构建成功！                              ║
    echo   ╠══════════════════════════════════════════════╣

    :: 计算大小
    for /f "tokens=3" %%a in ('dir /s /-c dist\PaperLibrary 2^>nul ^| findstr "个文件"') do set SIZE=%%a
    for /f %%i in ('powershell -command "(Get-ChildItem -Recurse 'dist\PaperLibrary' | Measure-Object -Property Length -Sum).Sum / 1MB"') do set SIZE_MB=%%i

    echo   ║  输出目录: dist\PaperLibrary\               ║
    echo   ║  主程序:   PaperLibrary.exe                 ║
    echo   ║  体积约:   %SIZE_MB% MB                      ║
    echo   ╠══════════════════════════════════════════════╣
    echo   ║  使用方法:                                   ║
    echo   ║  1. 将 dist\PaperLibrary\ 复制到目标电脑    ║
    echo   ║  2. 将 PDF 放入对应文件夹                   ║
    echo   ║  3. 双击 PaperLibrary.exe 启动              ║
    echo   ╚══════════════════════════════════════════════╝
    echo.
    echo   💡 如需制作安装包 (Setup.exe)，需额外安装 Inno Setup
    echo      然后运行: iscc setup.iss
) else (
    echo.
    echo   ❌ 构建失败，请检查错误信息
    echo.
    echo   💡 常见问题：
    echo   1. 磁盘空间不足（需要 2-3GB 临时空间）
    echo   2. 杀毒软件拦截 PyInstaller
    echo   3. 尝试以管理员身份运行
)

echo.
pause
