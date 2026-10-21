"""
launcher_gui.py — GUI 启动器
启动 Streamlit 后台服务 + 显示状态窗口，用户双击 .exe 即可使用
"""

import os
import sys
import time
import socket
import signal
import threading
import subprocess
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox

# ── 路径解析（兼容开发模式和 PyInstaller 打包模式） ──

if getattr(sys, 'frozen', False):
    # PyInstaller 打包模式
    APP_DIR = os.path.dirname(sys.executable)
else:
    # 开发模式
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

APP_SCRIPT = os.path.join(APP_DIR, "app.py")
DATA_DIR = os.path.join(APP_DIR, "data")

PORT = 8501
LOCAL_URL = f"http://localhost:{PORT}"


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def find_python():
    """找到可用的 Python 解释器"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 模式下用自身启动的子进程来跑 streamlit
        return sys.executable
    return sys.executable


# ── Streamlit 进程管理 ──

class StreamlitServer:
    def __init__(self):
        self.process = None
        self.running = False

    def start(self):
        """启动 Streamlit 服务"""
        python = find_python()
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        # 构建命令
        cmd = [
            python, "-m", "streamlit", "run", APP_SCRIPT,
            "--server.headless", "true",
            "--server.port", str(PORT),
            "--server.address", "0.0.0.0",
            "--browser.gatherUsageStats", "false",
            "--logger.level", "error",
            "--server.enableCORS", "false",
            "--server.enableXsrfProtection", "false",
        ]

        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.running = True

    def stop(self):
        """停止 Streamlit 服务"""
        if self.process:
            try:
                if sys.platform == "win32":
                    self.process.terminate()
                else:
                    self.process.send_signal(signal.SIGTERM)
                self.process.wait(timeout=5)
            except Exception:
                try:
                    self.process.kill()
                except Exception:
                    pass
        self.running = False

    def wait_ready(self, timeout=30):
        """等待 Streamlit 服务就绪"""
        start = time.time()
        while time.time() - start < timeout:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1)
                s.connect(("localhost", PORT))
                s.close()
                return True
            except Exception:
                time.sleep(0.5)
        return False


# ── GUI 窗口 ──

class LauncherWindow:
    def __init__(self):
        self.server = StreamlitServer()
        self.root = tk.Tk()
        self._setup_ui()

    def _setup_ui(self):
        self.root.title("📚 学术文献管理与图片检索系统")
        self.root.geometry("520x420")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        # 图标
        try:
            if sys.platform == "win32":
                self.root.iconbitmap(default="")
        except Exception:
            pass

        # 主题色
        BG = "#f8f9fa"
        FG = "#1a1a2e"
        ACCENT = "#4a6cf7"

        self.root.configure(bg=BG)

        # ── 标题区域 ──
        title_frame = tk.Frame(self.root, bg=BG)
        title_frame.pack(pady=(30, 5))

        tk.Label(
            title_frame, text="📚", font=("Segoe UI", 36),
            bg=BG
        ).pack()

        tk.Label(
            title_frame,
            text="学术文献管理与图片检索系统",
            font=("Segoe UI", 15, "bold"),
            fg=FG, bg=BG
        ).pack()

        tk.Label(
            title_frame,
            text="Paper Library & Figure-Level Search System v1.0",
            font=("Segoe UI", 9),
            fg="#888", bg=BG
        ).pack(pady=(0, 5))

        # ── 状态指示器 ──
        status_frame = tk.Frame(self.root, bg=BG)
        status_frame.pack(pady=(10, 5))

        self.status_canvas = tk.Canvas(status_frame, width=16, height=16,
                                       bg=BG, highlightthickness=0)
        self.status_canvas.pack(side=tk.LEFT, padx=(0, 8))
        self.status_dot = self.status_canvas.create_oval(2, 2, 14, 14,
                                                         fill="#ccc", outline="")

        self.status_label = tk.Label(
            status_frame, text="正在启动服务...",
            font=("Segoe UI", 10), fg="#666", bg=BG
        )
        self.status_label.pack(side=tk.LEFT)

        # ── URL 区域 ──
        url_frame = tk.Frame(self.root, bg=BG)
        url_frame.pack(pady=(5, 10))

        tk.Label(url_frame, text="本地访问:", font=("Segoe UI", 9),
                 fg="#888", bg=BG).pack()
        self.url_label = tk.Label(
            url_frame, text=LOCAL_URL,
            font=("Segoe UI", 12, "bold"), fg=ACCENT, bg=BG,
            cursor="hand2"
        )
        self.url_label.pack()
        self.url_label.bind("<Button-1>", lambda e: webbrowser.open(LOCAL_URL))

        lan_ip = get_local_ip()
        if lan_ip:
            lan_url = f"http://{lan_ip}:{PORT}"
            tk.Label(url_frame, text=f"局域网: {lan_url}",
                     font=("Segoe UI", 9), fg="#888", bg=BG).pack()

        # ── 分隔线 ──
        sep = tk.Frame(self.root, height=1, bg="#e0e0e0")
        sep.pack(fill=tk.X, padx=40, pady=(10, 15))

        # ── 信息区域 ──
        info_frame = tk.Frame(self.root, bg=BG)
        info_frame.pack(pady=(0, 15))

        info_text = (
            "• 文献 PDF 放入程序所在目录的对应文件夹\n"
            "• 打开浏览器后在侧边栏点击「更新文献库」\n"
            "• 配置 API Key 后点「AI 总结全部」启用智能检索"
        )
        tk.Label(
            info_frame, text=info_text,
            font=("Segoe UI", 9), fg="#666", bg=BG,
            justify=tk.LEFT
        ).pack()

        # ── 按钮 ──
        btn_frame = tk.Frame(self.root, bg=BG)
        btn_frame.pack(pady=(5, 10))

        self.open_btn = tk.Button(
            btn_frame, text="🌐 打开浏览器",
            font=("Segoe UI", 10, "bold"),
            bg=ACCENT, fg="white",
            activebackground="#3b5de7", activeforeground="white",
            relief=tk.FLAT,
            padx=24, pady=8,
            cursor="hand2",
            command=self._open_browser
        )
        self.open_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = tk.Button(
            btn_frame, text="⏹ 停止服务",
            font=("Segoe UI", 10),
            bg="#e74c3c", fg="white",
            activebackground="#c0392b", activeforeground="white",
            relief=tk.FLAT,
            padx=24, pady=8,
            cursor="hand2",
            command=self._on_close
        )
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        # ── 底部 ──
        tk.Label(
            self.root, text="关闭此窗口即停止服务",
            font=("Segoe UI", 8), fg="#bbb", bg=BG
        ).pack(side=tk.BOTTOM, pady=(0, 15))

        # ── 启动服务 ──
        self.root.after(500, self._start_server)

    def _start_server(self):
        """后台启动 Streamlit"""
        def run():
            self.server.start()
            ready = self.server.wait_ready(timeout=45)

            self.root.after(0, lambda: self._on_ready(ready))

        t = threading.Thread(target=run, daemon=True)
        t.start()

    def _on_ready(self, success):
        """服务就绪回调"""
        if success:
            self.status_canvas.itemconfig(self.status_dot, fill="#2ecc71")
            self.status_label.config(text="✅ 服务运行中", fg="#2ecc71")
            # 自动打开浏览器
            webbrowser.open(LOCAL_URL)
        else:
            self.status_canvas.itemconfig(self.status_dot, fill="#e74c3c")
            self.status_label.config(text="❌ 启动失败", fg="#e74c3c")
            messagebox.showerror("启动失败",
                                 "无法启动服务，请检查:\n"
                                 "1. Python 环境是否正常\n"
                                 "2. 端口 8501 是否被占用")

    def _open_browser(self):
        webbrowser.open(LOCAL_URL)

    def _on_close(self):
        """关闭窗口时停止服务"""
        if self.server.running:
            self.status_label.config(text="正在停止服务...")
            self.root.update()
            self.server.stop()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


# ── 主入口 ──

def main():
    # 确保 data 目录存在
    os.makedirs(DATA_DIR, exist_ok=True)

    app = LauncherWindow()
    app.run()


if __name__ == "__main__":
    main()
