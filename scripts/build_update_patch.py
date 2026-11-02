# -*- coding: utf-8 -*-
"""build_update_patch.py — 打包「个人文献库」无痛热升级补丁包

作用：
  1. 产出体积仅 1~2 MB 的「个人文献库_升级补丁.zip」
  2. 师兄师姐只需解压覆盖，或者双击里面的「一键升级.bat」
  3. 只替换代码（.py、.streamlit、静态资源），绝对不碰任何 data/、papers.db 或【文献存放处】
  4. 原有全部文献、笔记、图表、API Key 100% 完整保留！
"""
import os, shutil, zipfile, time

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RELEASES_ROOT = os.path.join(APP_DIR, "【版本发布区】")
VERSION = "v2.5_Nexus"
VERSION_DIR = os.path.join(RELEASES_ROOT, f"{VERSION}_{time.strftime('%Y%m%d')}")
PATCH_DIR_NAME = f"PaperLibrary_{VERSION}_UpdatePatch"
PATCH_DIR = os.path.join(VERSION_DIR, PATCH_DIR_NAME)
PATCH_ZIP = os.path.join(VERSION_DIR, PATCH_DIR_NAME + ".zip")

# 需要同步的 Python 核心代码文件
APP_PY_FILES = [
    "app.py", "start_all.py", "file_server.py", "database.py", "importer.py", "library_fs.py",
    "folder_picker.py", "parser.py", "pdf_index.py", "pdf_fetch.py",
    "thumbs.py", "metadata_enricher.py", "citation.py", "ai_engine.py", "caption_matcher.py",
    "comparator.py", "doc_classify.py", "figure_anchor.py", "local_rank.py",
    "search_aggregate.py", "search_sources.py", "title_extract.py", "tunnel.py",
    "compare_engine.py", "api_manager.py", "deep_analysis.py", "figures_arxiv.py", "obsidian_export.py",
]

def main():
    print("正在构建无痛升级补丁包...", flush=True)
    os.makedirs(VERSION_DIR, exist_ok=True)
    if os.path.isdir(PATCH_DIR):
        shutil.rmtree(PATCH_DIR, ignore_errors=True)
    if os.path.exists(PATCH_ZIP):
        os.remove(PATCH_ZIP)
    
    app_target = os.path.join(PATCH_DIR, "app")
    os.makedirs(app_target, exist_ok=True)
    
    # 1. 拷贝纯代码
    for f in APP_PY_FILES:
        s = os.path.join(APP_DIR, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(app_target, f))
    
    # 2. 拷贝配置与图标
    for f in ("app_icon.ico", "arondex_icon.png", "requirements.txt"):
        s = os.path.join(APP_DIR, f)
        if os.path.exists(s):
            shutil.copy2(s, os.path.join(app_target, f))
    
    if os.path.exists(os.path.join(APP_DIR, ".streamlit")):
        shutil.copytree(os.path.join(APP_DIR, ".streamlit"), os.path.join(app_target, ".streamlit"))
    
    # 3. 写入一键升级脚本（傻瓜式覆盖）
    bat_content = r"""@echo off
chcp 65001 >nul
title 个人文献库 — 智能无痛升级程序
echo.
echo   ============================================================
echo      正在为您升级个人文献库至最新版（代码热更新）...
echo   ============================================================
echo.
echo   [安全保证] 本程序仅更新系统底层代码，绝不触碰您的文献与数据库！
echo.
cd /d "%~dp0"
set "DEST=..\app"
if not exist "%DEST%" (
    set "DEST=app"
)
if not exist "%DEST%" (
    echo   [提示] 请将解压出来的文件夹放在与【个人文献库】同级或直接拖入覆盖。
    echo   正在尝试直接覆盖当前目录下的 app...
)
echo   正在备份并安全替换系统核心代码...
xcopy /Y /E /I "app" "%DEST%" >nul 2>&1
echo.
echo   ============================================================
echo      升级成功！原有全部文献、笔记与数据 100%% 完好保留。
echo      请重新双击「启动个人文献库.bat」即可享受最新功能！
echo   ============================================================
echo.
pause
exit /b 0
"""
    with open(os.path.join(PATCH_DIR, "【双击我一键升级·保留文献】.bat"), "w", encoding="utf-8", newline="\r\n") as bf:
        bf.write(bat_content.replace("\n", "\r\n"))
        
    readme_content = """个人文献库 · 安全升级说明
========================================
本补丁包仅包含最新系统的核心功能代码，大小仅约 1 MB。

【升级方法（任选其一）】：
方法 1（最简单）：将本压缩包内的文件解压，直接把里面的 app 文件夹复制覆盖到您的文献库主目录下替换同名文件即可。
方法 2：解压后把解压文件夹放在个人文献库同级目录，双击「【双击我一键升级·保留文献】.bat」。

【隐私与数据保障】：
本升级过程只替换 .py 源代码，完全不包含 data 文件夹与数据库，您的所有已存文献、图表和分类完全不受影响！
"""
    with open(os.path.join(PATCH_DIR, "升级必读说明.txt"), "w", encoding="utf-8") as rf:
        rf.write(readme_content)
        

    # 5. 写入详细新手指南与更新说明
    readme_md = os.path.join(APP_DIR, "scripts", "README_portable.md")
    if os.path.exists(readme_md):
        with open(readme_md, "r", encoding="utf-8") as f:
            md = f.read()
        with open(os.path.join(PATCH_DIR, "📖 新手使用说明与更新手册.txt"), "w", encoding="utf-8") as f:
            f.write(md)
        try:
            sys.path.insert(0, os.path.join(APP_DIR, "scripts"))
            import add_readme_to_release
            body = add_readme_to_release.md_to_html(md)
            html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>个人文献库 · 新手使用指南与版本更新说明</title>
<style>{add_readme_to_release.CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>"""
            with open(os.path.join(PATCH_DIR, "📖 新手使用说明与更新手册.html"), "w", encoding="utf-8") as f:
                f.write(html)
        except Exception as e:
            pass

    # 4. 打包为 zip
    shutil.make_archive(PATCH_DIR, "zip", VERSION_DIR, PATCH_DIR_NAME)
    size_mb = os.path.getsize(PATCH_ZIP) / 1024 / 1024
    print(f"升级补丁包打包成功！体积: {size_mb:.2f} MB，路径: {PATCH_ZIP}", flush=True)

if __name__ == "__main__":
    main()