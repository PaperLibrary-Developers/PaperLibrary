"""
tunnel.py — 公网隧道管理
支持 ngrok（推荐免费固定域名）和 Cloudflare Tunnel（无限制免费）

用法:
  python tunnel.py              # 交互式选择隧道
  python tunnel.py ngrok        # 启动 ngrok 隧道
  python tunnel.py cloudflare   # 启动 Cloudflare Tunnel
  python tunnel.py setup        # 首次配置向导
"""

import os
import sys
import json
import time
import socket
import shutil
import subprocess
import threading

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
PORT = 8501
CONFIG_FILE = os.path.join(PROJECT_ROOT, ".tunnel_config.json")


# ═══════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════

def which(cmd: str) -> bool:
    """检查命令是否存在"""
    return shutil.which(cmd) is not None


def get_tunnel_url() -> str:
    """从配置文件读取当前隧道 URL"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("last_url", "")
        except Exception:
            pass
    return ""


# ═══════════════════════════════════════════
# Ngrok 隧道
# ═══════════════════════════════════════════

def check_ngrok() -> tuple[bool, str]:
    """检查 ngrok 安装状态"""
    if which("ngrok"):
        return True, "已安装"
    # 也检查常见安装路径
    for p in [
        os.path.expanduser("~/ngrok/ngrok.exe"),
        os.path.expanduser("~/AppData/Local/ngrok/ngrok.exe"),
        "C:\\ngrok\\ngrok.exe",
        "/usr/local/bin/ngrok",
    ]:
        if os.path.exists(p):
            return False, f"已安装但不在 PATH 中: {p}"
    return False, "未安装"


def setup_ngrok():
    """配置 ngrok（首次使用）"""
    print()
    print("=" * 60)
    print("  🔧 Ngrok 配置向导")
    print("=" * 60)
    print()
    print("  📋 你需要：")
    print("  1. 一个 ngrok 免费账号 → https://ngrok.com/signup")
    print("  2. 下载 ngrok → https://ngrok.com/download")
    print("  3. 运行: ngrok config add-authtoken <你的token>")
    print()
    print("  ✨ ngrok 免费版现已包含：")
    print("     ✅ 1 个固定域名（URL 永久不变）")
    print("     ✅ 无限隧道时长（无 2 小时限制）")
    print("     ✅ 1GB/月流量")
    print()
    print("  💡 获取固定域名：")
    print("     https://dashboard.ngrok.com/cloud-edge/domains")
    print("     创建免费域名后，将域名填入下方")
    print()

    static_domain = input("  请输入你的 ngrok 固定域名（留空则使用随机域名）: ").strip()

    config = load_config()
    config["ngrok_static_domain"] = static_domain
    save_config(config)

    print()
    print(f"  ✅ 配置已保存！")
    if static_domain:
        print(f"  🌍 你的固定域名: https://{static_domain}")
    print()
    print("  现在可以运行: python tunnel.py ngrok")
    print()


def start_ngrok():
    """启动 ngrok 隧道"""
    ok, msg = check_ngrok()
    if not ok:
        print(f"  ❌ ngrok {msg}")
        print()
        print("  安装步骤:")
        print("  1. 访问 https://ngrok.com/download 下载 Windows 版")
        print("  2. 解压 ngrok.exe 到 C:\\Windows\\System32\\ 或本项目目录")
        print("  3. 运行 ngrok config add-authtoken <你的token>")
        print("  4. 重新运行: python tunnel.py ngrok")
        return

    print("  ⏳ 正在启动 ngrok 隧道...")

    config = load_config()
    static_domain = config.get("ngrok_static_domain", "")

    cmd = ["ngrok", "http", str(PORT), "--log=stdout"]
    if static_domain:
        cmd += ["--domain", static_domain]
        print(f"  🌍 固定域名: https://{static_domain}")
    else:
        print(f"  🌍 随机域名模式（建议设置固定域名: python tunnel.py setup）")

    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 等待 ngrok 启动并获取 URL
    time.sleep(3)
    try:
        import urllib.request
        resp = urllib.request.urlopen("http://localhost:4040/api/tunnels")
        tunnels = json.loads(resp.read())
        public_url = tunnels["tunnels"][0]["public_url"]

        # 保存到配置
        config["last_url"] = public_url
        config["last_tunnel"] = "ngrok"
        save_config(config)

        print(f"\n  {'=' * 50}")
        print(f"  ✅ 隧道已建立！")
        print(f"  🌍 公网地址: {public_url}")
        print(f"  📱 任何设备均可通过此 URL 访问")
        print(f"  🛑 按 Ctrl+C 停止")
        print(f"  {'=' * 50}\n")
    except Exception:
        print(f"  ⚠ ngrok 已启动，查看 http://localhost:4040 获取 URL")
        print(f"  🛑 按 Ctrl+C 停止\n")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  🛑 正在停止 ngrok...")
        proc.terminate()
        print("  👋 隧道已关闭")


# ═══════════════════════════════════════════
# Cloudflare Tunnel (cloudflared)
# ═══════════════════════════════════════════

def check_cloudflared() -> tuple[bool, str]:
    """检查 cloudflared 安装状态"""
    if which("cloudflared"):
        return True, "已安装"
    return False, "未安装"


def setup_cloudflared():
    """配置 Cloudflare Tunnel（首次使用）"""
    print()
    print("=" * 60)
    print("  🔧 Cloudflare Tunnel 配置向导")
    print("=" * 60)
    print()
    print("  📋 Cloudflare Tunnel 优势：")
    print("     ✅ 完全免费，无流量限制")
    print("     ✅ 连接稳定，无时长限制")
    print("     ✅ 适合长期 7×24 使用")
    print()
    print("  快速隧道模式（无需域名）:")
    print("     cloudflared tunnel --url http://localhost:8501")
    print("     → 生成随机 URL (每次重启会变)")
    print()
    print("  固定域名模式（需要域名托管在 Cloudflare）:")
    print("     1. 在 Cloudflare 添加你的域名")
    print("     2. cloudflared tunnel login")
    print("     3. cloudflared tunnel create paper-library")
    print("     4. 配置 DNS 记录指向 tunnel")
    print("     → 永久固定 URL")
    print()

    input("按 Enter 返回主菜单...")


def start_cloudflared():
    """启动 Cloudflare 快速隧道"""
    ok, msg = check_cloudflared()
    if not ok:
        print(f"  ❌ cloudflared {msg}")
        print()
        print("  安装步骤 (Windows):")
        print("  1. 下载: https://github.com/cloudflare/cloudflared/releases")
        print("     选择 cloudflared-windows-amd64.exe")
        print("  2. 重命名为 cloudflared.exe")
        print("  3. 放到 C:\\Windows\\System32\\ 或本项目目录")
        print("  4. 重新运行: python tunnel.py cloudflare")
        return

    print("  ⏳ 正在启动 Cloudflare Tunnel...")

    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )

    # 从输出中提取 URL
    url_found = None
    start_time = time.time()

    while not url_found and time.time() - start_time < 30:
        line = proc.stdout.readline()
        if not line:
            time.sleep(0.5)
            continue
        # cloudflared 输出格式: "... https://xxx.trycloudflare.com"
        if "trycloudflare.com" in line:
            import re
            match = re.search(r'https://[a-z0-9-]+\.trycloudflare\.com', line)
            if match:
                url_found = match.group(0)
                break

    if url_found:
        config = load_config()
        config["last_url"] = url_found
        config["last_tunnel"] = "cloudflare"
        save_config(config)

        print(f"\n  {'=' * 50}")
        print(f"  ✅ Cloudflare Tunnel 已建立！")
        print(f"  🌍 公网地址: {url_found}")
        print(f"  💡 免费、无流量限制、适合长期使用")
        print(f"  ⚠ 重启后 URL 会变化")
        print(f"  🛑 按 Ctrl+C 停止")
        print(f"  {'=' * 50}\n")
    else:
        print("  ⚠ 未能获取 URL，cloudflared 可能已在后台运行")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\n  🛑 正在停止 Cloudflare Tunnel...")
        proc.terminate()
        print("  👋 隧道已关闭")


# ═══════════════════════════════════════════
# 配置文件
# ═══════════════════════════════════════════

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(config: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════
# 交互式菜单
# ═══════════════════════════════════════════

def interactive_menu():
    """交互式隧道选择"""
    print()
    print("=" * 60)
    print("  🌍 文献检索系统 — 公网隧道管理")
    print("=" * 60)
    print()
    print("  请选择隧道方案：")
    print()
    print("  [1] 🔷 Ngrok 隧道")
    ngrok_ok, ngrok_msg = check_ngrok()
    print(f"      状态: {ngrok_msg}")
    print(f"      特点: 免费固定域名、1GB/月流量、适合日常使用")
    print()
    print("  [2] ☁️  Cloudflare Tunnel")
    cf_ok, cf_msg = check_cloudflared()
    print(f"      状态: {cf_msg}")
    print(f"      特点: 完全免费、无流量限制、适合 7×24 长期运行")
    print()
    print("  [3] ⚙️  首次配置向导")
    print("  [0] 退出")
    print()

    choice = input("  请输入选项 [1/2/3/0]: ").strip()

    if choice == "1":
        if not ngrok_ok:
            print("\n  ❌ ngrok 未安装，请选择 [3] 进入配置向导\n")
            interactive_menu()
        else:
            start_ngrok()
    elif choice == "2":
        if not cf_ok:
            print("\n  ❌ cloudflared 未安装")
            print("  快速安装: choco install cloudflared")
            print("  或下载: https://github.com/cloudflare/cloudflared/releases\n")
            interactive_menu()
        else:
            start_cloudflared()
    elif choice == "3":
        print()
        print("  你要配置哪个隧道？")
        print("  [1] ngrok")
        print("  [2] Cloudflare Tunnel")
        sub = input("  选择 [1/2]: ").strip()
        if sub == "1":
            setup_ngrok()
        elif sub == "2":
            setup_cloudflared()
        interactive_menu()
    elif choice == "0":
        print("  👋 再见！")
    else:
        interactive_menu()


# ═══════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════

def print_banner():
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║     🌍 文献检索系统 — 公网隧道管理          ║")
    print("  ╚══════════════════════════════════════════════╝")


if __name__ == "__main__":
    print_banner()

    if len(sys.argv) > 1:
        cmd = sys.argv[1]
        if cmd == "ngrok":
            start_ngrok()
        elif cmd == "cloudflare":
            start_cloudflared()
        elif cmd == "setup":
            setup_ngrok()
        else:
            print(f"  未知命令: {cmd}")
            print("  用法: python tunnel.py [ngrok|cloudflare|setup]")
    else:
        interactive_menu()
