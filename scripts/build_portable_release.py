# -*- coding: utf-8 -*-
"""build_portable_release.py — 打包「个人文献库」便携分发版

产物：
  桌面\\个人文献库_便携版\\            （可直接运行的目录）
  桌面\\个人文献库_便携版.zip          （发给别人的压缩包）

要点：
  · 内嵌整套 Python 运行时（对方电脑不需要装 Python、不需要联网装依赖）
  · 只带程序本体，不带任何个人文献（papers.db 重新生成空库，images/thumb_cache 不带）
  · 启动器双击即用：自动建桌面快捷方式 + 起服务 + 开浏览器
"""
import os
import shutil
import subprocess
import sys
import time

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
DIST_NAME = "个人文献库_便携版"
DIST = os.path.join(DESKTOP, DIST_NAME)
ZIP_PATH = os.path.join(DESKTOP, DIST_NAME + ".zip")

PY_HOME = r"C:\Program Files\Python311"
PY_SITE = os.path.join(os.path.expanduser("~"), "AppData", "Roaming", "Python", "Python311", "site-packages")

APP_PY_FILES = [
    "app.py", "start_all.py", "file_server.py", "database.py", "importer.py", "library_fs.py",
    "folder_picker.py", "parser.py", "pdf_index.py", "pdf_fetch.py",
    "thumbs.py", "metadata_enricher.py", "citation.py", "ai_engine.py", "caption_matcher.py",
    "comparator.py", "doc_classify.py", "figure_anchor.py", "local_rank.py",
    "search_aggregate.py", "search_sources.py", "title_extract.py", "tunnel.py",
]

# 从 site-packages 里剔除（打包器、构建残留、测试与无用大件）
SITE_EXCLUDE = {
    "PyInstaller", "_pyinstaller_hooks_contrib", "altgraph", "pip", "setuptools",
    "pkg_resources", "_distutils_hack", "distutils-precedence.pth", "wheel",
    "__pycache__", "PyInstaller-", "altgraph-", "pip-", "setuptools-", "wheel-",
    "docx", "arxiv", "pytest", "IPython", "jupyter",
}
# Python 主目录里剔除（文档/静态库/工具/测试，均与运行无关）
PY_EXCLUDE = {"Doc", "NEWS.txt", "include", "libs", "Tools", "test", "idlelib",
              "tkinter", "tcl", "ensurepip", "distutils", "site-packages", "__pycache__"}


def log(msg):
    print(msg, flush=True)


def copy_tree(src, dst, exclude_names=(), exclude_prefixes=()):
    os.makedirs(dst, exist_ok=True)
    for name in os.listdir(src):
        if name in exclude_names:
            continue
        if any(name.startswith(p) for p in exclude_prefixes):
            continue
        s, d = os.path.join(src, name), os.path.join(dst, name)
        try:
            if os.path.isdir(s):
                if os.path.islink(s):
                    continue
                copy_tree(s, d, exclude_names, exclude_prefixes)
            else:
                shutil.copy2(s, d)
        except (PermissionError, OSError) as e:
            log(f"   [跳过] {name}: {e}")


def main():
    t0 = time.time()
    if os.path.isdir(DIST):
        log(f"[1/8] 清理旧目录 {DIST}")
        shutil.rmtree(DIST, ignore_errors=True)
    if os.path.exists(ZIP_PATH):
        os.remove(ZIP_PATH)

    runtime = os.path.join(DIST, "runtime")
    app = os.path.join(DIST, "app")

    # ── 1. Python 运行时 ──
    log("[2/8] 复制 Python 运行时（约 130MB）…")
    copy_tree(PY_HOME, runtime, exclude_names=PY_EXCLUDE)
    # 补回 python3.dll 这类前缀误伤（只有 python3.dll / python311.dll 需要）
    for f in ("python3.dll", "python311.dll", "python.exe", "pythonw.exe",
              "vcruntime140.dll", "vcruntime140_1.dll"):
        src = os.path.join(PY_HOME, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(runtime, f))

    # ── 2. 第三方依赖 ──
    log("[3/8] 复制第三方依赖（约 460MB，稍慢）…")
    site_dst = os.path.join(runtime, "Lib", "site-packages")
    copy_tree(PY_SITE, site_dst, exclude_names=SITE_EXCLUDE,
              exclude_prefixes=("PyInstaller-", "altgraph-", "pip-", "setuptools-",
                                "wheel-", "_pyinstaller"))

    # ── 3. 程序源码 ──
    log("[4/8] 复制程序源码…")
    os.makedirs(app, exist_ok=True)
    for f in APP_PY_FILES:
        src = os.path.join(APP_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(app, f))
        else:
            log(f"   [缺失] {f}")

    # 配置与静态资源
    shutil.copytree(os.path.join(APP_DIR, ".streamlit"), os.path.join(app, ".streamlit"))
    for f in ("app_icon.ico", "arondex_icon.png", "requirements.txt"):
        src = os.path.join(APP_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(app, f))

    # PDF.js 阅读器
    shutil.copytree(os.path.join(APP_DIR, "data", "pdfjs"), os.path.join(app, "data", "pdfjs"))
    os.makedirs(os.path.join(app, "data", "images"), exist_ok=True)

    # 空文献存放处
    papers_dir = os.path.join(app, "【文献存放处】")
    os.makedirs(papers_dir, exist_ok=True)
    with open(os.path.join(papers_dir, "把 PDF 放进这个文件夹.txt"), "w", encoding="utf-8") as f:
        f.write("把你的论文 PDF 复制到这个文件夹里（可以自己建子文件夹当分类）。\n"
                "然后回到网页，点左侧边栏「🔄 扫描刷新文献库」，全部自动入库。\n")

    # ── 4. 生成全新的空数据库 ──
    log("[5/8] 生成空白数据库…")
    subprocess.run([sys.executable, "-c", (
        "import sys, os;sys.path.insert(0, r'%s');"
        "import database; database.init_db(); print('   空库已建立')" % app
    )], check=True, capture_output=True)

    # ── 5. 启动器 ──
    log("[6/8] 写入启动器与说明…")
    write_launchers(DIST)

    # ── 6. 体积统计 ──
    total = sum(os.path.getsize(os.path.join(r, f))
                for r, _, fs in os.walk(DIST) for f in fs)
    log(f"[7/8] 目录体积：{total / 1024 / 1024:.0f} MB")

    # ── 7. 压缩 ──
    log("[8/8] 压缩为 zip（约 1-3 分钟）…")
    subprocess.run([sys.executable, "-c", (
        "import shutil,os;"
        "shutil.make_archive(r'%s', 'zip', r'%s', r'%s')"
        % (ZIP_PATH[:-4], DESKTOP, DIST_NAME)
    )], check=True)
    zsize = os.path.getsize(ZIP_PATH) / 1024 / 1024
    log("")
    log("=" * 62)
    log(f"✅ 打包完成  用时 {time.time() - t0:.0f} 秒")
    log(f"   目录：{DIST}  ({total / 1024 / 1024:.0f} MB)")
    log(f"   压缩包：{ZIP_PATH}  ({zsize:.0f} MB)")
    log("=" * 62)


def write_launchers(dist):
    app = os.path.join(dist, "app")

    bat = r'''@echo off
chcp 65001 >nul
title 个人文献库 — 正在启动，请稍候…
cd /d "%~dp0"
set "HERE=%~dp0"
set "PYTHONHOME=%HERE%runtime"
set "PYTHONPATH=%HERE%app;%HERE%runtime\Lib\site-packages"
set "PYTHONNOUSERSITE=1"
set "PYTHONIOENCODING=utf-8"

echo.
echo   ============================================================
echo      个人文献库  正在启动，请稍候…
echo   ============================================================
echo.
echo   [1/3] 检查运行环境…
if not exist "%HERE%runtime\python.exe" (
    echo.
    echo   启动失败：找不到内置运行环境 runtime\python.exe
    echo   请确认压缩包已完整解压（不要直接在压缩包里双击运行）
    echo.
    pause
    exit /b 1
)

echo   [2/3] 正在启动服务（首次启动约需 20～40 秒）…
echo   [3/3] 启动完成后浏览器会自动打开…
echo.
"%HERE%runtime\python.exe" "%HERE%app\start_all.py"
set "RC=%errorlevel%"

if not "%RC%"=="0" (
    echo.
    echo   ------------------------------------------------------------
    echo   启动失败（错误码 %RC%）
    echo   请把本窗口截图发给提供软件的人
    echo   ------------------------------------------------------------
    echo.
    pause
    exit /b %RC%
)

echo.
echo   已启动完成！
echo   浏览器若没有自动打开，请手动访问： http://localhost:8501
echo   本窗口 5 秒后自动关闭，桌面已生成「个人文献库」图标。
echo.
timeout /t 5 >nul
exit /b 0
'''
    with open(os.path.join(dist, "启动个人文献库.bat"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(bat)

    vbs = ('Set WshShell = CreateObject("WScript.Shell")\r\n'
           'WshShell.Run """%~dp0启动个人文献库.bat""", 1, False\r\n')
    with open(os.path.join(dist, "启动个人文献库.vbs"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write('Set fso = CreateObject("Scripting.FileSystemObject")\r\n'
                'here = fso.GetParentFolderName(WScript.ScriptFullName)\r\n'
                'Set WshShell = CreateObject("WScript.Shell")\r\n'
                'WshShell.CurrentDirectory = here\r\n'
                'WshShell.Run """" & here & "\\启动个人文献库.bat""", 1, False\r\n')

    stop = r'''@echo off
chcp 65001 >nul
title 退出个人文献库
echo.
echo   正在关闭个人文献库服务…
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8501" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8502" ^| findstr "LISTENING"') do taskkill /F /PID %%a >nul 2>&1
echo   已关闭。文献数据仍保存在 app\data 目录中，不会丢失。
timeout /t 3 >nul
'''
    with open(os.path.join(dist, "退出个人文献库.bat"), "w", encoding="utf-8", newline="\r\n") as f:
        f.write(stop)


if __name__ == "__main__":
    main()
