"""
launcher.py — 一键启动器
  双击 .bat 或运行 python launcher.py 即可启动
  支持:
    - 本地模式 (localhost only)
    - 局域网模式 (同一 WiFi 下其他设备可访问)
    - 外网穿透模式 (通过 ngrok 生成临时公网 URL)

用法:
  python launcher.py                  # 本地模式
  python launcher.py --lan            # 局域网模式
  python launcher.py --ngrok          # 外网穿透 (需先安装 ngrok)
"""

import os
import sys
import time
import socket
import subprocess
import threading
import webbrowser

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8501


def get_local_ip() -> str:
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def start_streamlit(bind_all: bool = False):
    """启动 Streamlit 服务"""
    os.chdir(PROJECT_ROOT)

    host = "0.0.0.0" if bind_all else "localhost"
    cmd = [
        sys.executable, "-m", "streamlit", "run", "app.py",
        "--server.headless", "true",
        "--server.port", str(PORT),
        "--server.address", host,
        "--browser.gatherUsageStats", "false",
    ]

    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="文献检索系统启动器")
    parser.add_argument("--lan", action="store_true", help="局域网模式（同一 WiFi 下其他设备可访问）")
    parser.add_argument("--ngrok", action="store_true", help="通过 ngrok 生成公网 URL")
    args = parser.parse_args()

    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║   📚 学术文献管理与图片级检索系统  v1.0     ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()

    bind_all = args.lan or args.ngrok

    # 启动服务
    print("  ⏳ 正在启动服务...")
    proc = start_streamlit(bind_all=bind_all)

    # 等待服务就绪
    time.sleep(3)

    local_url = f"http://localhost:{PORT}"
    lan_url = f"http://{get_local_ip()}:{PORT}"

    print(f"  ✅ 服务已启动！")
    print()
    print(f"  📍 本地访问:    {local_url}")
    if bind_all:
        print(f"  🌐 局域网访问:  {lan_url}")
    print()
    print(f"  🛑 按 Ctrl+C 停止服务")
    print(f"  ─────────────────────────────────────────")

    # 打开浏览器
    webbrowser.open(local_url)

    # 启动 ngrok（如果指定）
    ngrok_proc = None
    if args.ngrok:
        try:
            ngrok_proc = subprocess.Popen(
                ["ngrok", "http", str(PORT), "--log=stdout"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            time.sleep(2)
            # 获取 ngrok 公网 URL
            import urllib.request, json
            try:
                resp = urllib.request.urlopen("http://localhost:4040/api/tunnels")
                tunnels = json.loads(resp.read())
                public_url = tunnels["tunnels"][0]["public_url"]
                print(f"  🌍 公网访问:    {public_url}")
                print(f"     (关闭此窗口后公网链接失效)")
            except Exception:
                print(f"  ⚠ ngrok 已启动，请查看 http://localhost:4040 获取公网 URL")
        except FileNotFoundError:
            print(f"  ⚠ 未安装 ngrok，跳过公网穿透")
            print(f"     安装方法: https://ngrok.com/download")

    print()

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  🛑 正在停止服务...")
        proc.terminate()
        if ngrok_proc:
            ngrok_proc.terminate()
        print("  👋 再见！")


if __name__ == "__main__":
    main()
