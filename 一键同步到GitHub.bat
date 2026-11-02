@echo off
chcp 65001 >nul
title 同步更新至 GitHub 私人仓库

echo ====================================================
echo           正在同步本地代码至 GitHub 私人仓库
echo ====================================================
echo.

cd /d "%~dp0"

echo [1/3] 正在检查文件改动...
git status -s

echo.
set /p commit_msg="请输入本次更新说明（直接按回车将默认填入自动提交）: "
if "%commit_msg%"=="" set commit_msg=自动同步更新: %date% %time%

echo.
echo [2/3] 正在添加文件并提交本地版本...
git add .
git commit -m "%commit_msg%"

echo.
echo [3/3] 正在推送到 GitHub...
git push origin main

if %errorlevel% equ 0 (
    echo.
    echo ====================================================
    echo               同步成功！已推送至 GitHub
    echo ====================================================
) else (
    echo.
    echo ====================================================
    echo         推送失败，请检查网络或 GitHub 连接状态
    echo ====================================================
)

echo.
pause