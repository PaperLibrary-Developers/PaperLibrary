"""
start_all.py — Reliable one-click launcher for PaperLibrary.
Starts file_server.py + Streamlit as fully detached background
processes (no console window needed), opens the browser, exits.
Double-click start.bat or this file to use.
"""
import os
import sys
import time
import socket
import subprocess
import webbrowser

APP_DIR = os.path.dirname(os.path.abspath(__file__))
PORT_STREAMLIT = 8501
PORT_FILE = 8502

DETACHED = 0x00000008 | 0x08000000  # DETACHED_PROCESS | CREATE_NO_WINDOW


def port_up(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(0.5)
    try:
        ok = s.connect_ex(("127.0.0.1", port)) == 0
    finally:
        s.close()
    return ok


def ensure_desktop_shortcut():
    """在 Windows 桌面自动建立或更新『个人文献库』快捷方式"""
    if sys.platform != "win32":
        return
    try:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        if not os.path.isdir(desktop):
            onedrive_desktop = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
            if os.path.isdir(onedrive_desktop):
                desktop = onedrive_desktop
            else:
                return

        lnk_path = os.path.join(desktop, "个人文献库.lnk")
        # 便携版布局：启动器在 app/ 的上一级目录；开发版则与 app.py 同级
        base_dirs = [APP_DIR, os.path.dirname(APP_DIR)]
        candidates = []
        for d in base_dirs:
            candidates.extend([
                os.path.join(d, "启动个人文献库.vbs"),
                os.path.join(d, "启动个人文献库.bat"),
                os.path.join(d, "start.bat"),
            ])
        target = next((c for c in candidates if os.path.exists(c)), "")
        if not target:
            return

        icon_path = os.path.join(APP_DIR, "app_icon.ico")
        ps_cmd = (
            f"$ws = New-Object -ComObject WScript.Shell\n"
            f"$sc = $ws.CreateShortcut('{lnk_path}')\n"
            f"$sc.TargetPath = '{target}'\n"
            f"$sc.WorkingDirectory = '{os.path.dirname(target)}'\n"
            f"$sc.Description = '个人文献库 — 智能文献研读与管理系统'\n"
            f"if (Test-Path '{icon_path}') {{ $sc.IconLocation = '{icon_path},0' }}\n"
            f"$sc.Save()\n"
        )
        import base64
        b64 = base64.b64encode(ps_cmd.encode("utf-16-le")).decode("ascii")
        subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", b64],
                       capture_output=True, timeout=15)
        print("[OK] 桌面快捷方式已建立: 个人文献库.lnk")
    except Exception as e:
        print(f"[提示] 桌面快捷方式处理跳过: {e}")


def ensure_default_papers_dir():
    """便携版首次运行：确保存在默认文献存放目录，避免扫描根目录落到程序目录"""
    d = os.path.join(APP_DIR, "【文献存放处】")
    try:
        os.makedirs(d, exist_ok=True)
        readme = os.path.join(d, "把 PDF 放进这个文件夹.txt")
        if not os.path.exists(readme):
            with open(readme, "w", encoding="utf-8") as f:
                f.write("把你的论文 PDF 文件复制到这个文件夹（可以建子文件夹当作分类）。\n"
                        "然后回到网页，点左侧边栏的「🔄 扫描刷新文献库」即可全部入库。\n")
    except Exception:
        pass


def main():
    os.chdir(APP_DIR)
    ensure_default_papers_dir()
    ensure_desktop_shortcut()

    # Already running? Just open the browser.
    if port_up(PORT_STREAMLIT):
        print("[OK] Service already running on port %d" % PORT_STREAMLIT)
        webbrowser.open("http://localhost:%d" % PORT_STREAMLIT)
        return 0

    # 1) Start standalone file server (survives independently; app.py also
    #    runs an in-process backup thread — redundancy keeps PDF viewing alive)
    if not port_up(PORT_FILE):
        try:
            subprocess.Popen(
                [sys.executable, "file_server.py"],
                cwd=APP_DIR,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=DETACHED,
            )
            print("[OK] File server starting (port %d)" % PORT_FILE)
        except Exception as e:
            print("[WARN] File server failed: %s" % e)

    # 2) Start Streamlit (app.py starts the file server in-process on port 8502)
    subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "app.py",
         "--server.headless", "true",
         "--server.port", str(PORT_STREAMLIT),
         "--browser.gatherUsageStats", "false",
         "--logger.level", "error"],
        cwd=APP_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=DETACHED,
    )
    print("[OK] Streamlit starting (port %d)" % PORT_STREAMLIT)

    # 3) Wait for it to come up (max 30s)
    print("Waiting for service...")
    for _ in range(60):
        if port_up(PORT_STREAMLIT):
            # Also wait a moment for the in-process file server to bind
            for _ in range(10):
                if port_up(PORT_FILE):
                    break
                time.sleep(0.3)
            print("[OK] Ready! Opening browser...")
            webbrowser.open("http://localhost:%d" % PORT_STREAMLIT)
            print("URL: http://localhost:%d" % PORT_STREAMLIT)
            print("To stop the service, double-click stop.bat")
            return 0
        time.sleep(0.5)

    print("[ERROR] Streamlit did not start in 30 seconds.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
