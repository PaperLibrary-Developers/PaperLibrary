"""等服务就绪后打开浏览器（启动器用；无控制台，结果写 data/logs/launcher.log）。

用法：pythonw tools/open_when_ready.py http://127.0.0.1:4321/ [--timeout 25]
退出码：0 已打开（或服务已可用但打开失败也返回 0，日志里有原因）；1 超时/服务未起来。
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "data" / "logs"


def log(message):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_DIR / "launcher.log", "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
    except OSError:
        pass


def health_ok(base, timeout=1.0):
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/health", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:
        return False


def open_browser(url):
    """按可靠性顺序尝试：explorer → os.startfile → webbrowser → cmd start。返回 True/False。

    explorer.exe 会在资源管理器（普通权限）里打开 URL，能避开
    「现有 Edge 实例以提升权限运行」这类冲突。
    """
    if sys.platform == "win32":
        try:
            subprocess.Popen(["explorer.exe", url])
            log("已请求 explorer 打开：" + url)
            return True
        except OSError as exc:
            log("explorer 失败：" + str(exc))
        try:
            os.startfile(url)  # noqa: S606 - 本地固定 URL
            return True
        except OSError as exc:
            log("os.startfile 失败：" + str(exc))
    try:
        import webbrowser

        if webbrowser.open(url, new=2):
            return True
    except Exception as exc:  # noqa: BLE001
        log("webbrowser 失败：" + str(exc))
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url], creationflags=0x08000000)
        return True
    except Exception as exc:  # noqa: BLE001
        log("cmd start 失败：" + str(exc))
    return False


def main(argv=None):
    argv = list(argv or [])
    if not argv:
        print("用法：open_when_ready.py <url> [--timeout 秒]")
        return 2
    url = argv[0]
    timeout = 25.0
    if "--timeout" in argv:
        try:
            timeout = float(argv[argv.index("--timeout") + 1])
        except (IndexError, ValueError):
            pass
    deadline = time.time() + timeout
    while time.time() < deadline:
        if health_ok(url):
            ok = open_browser(url)
            log(("已打开浏览器：" if ok else "服务已就绪但打开浏览器失败：") + url)
            return 0 if ok else 0
        time.sleep(0.3)
    log("等待服务超时（%.0fs）：%s" % (timeout, url))
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
