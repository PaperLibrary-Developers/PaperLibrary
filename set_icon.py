# -*- coding: utf-8 -*-
"""set_icon.py — 一键更换 PaperLibrary 快捷方式图标

用法（三选一）：
  1. 把图片直接拖到这个 .py 文件上（或用 set_icon.bat）
  2. 双击运行，弹出文件选择框
  3. 命令行：python set_icon.py "D:\\图片.png"

会自动把图片转成多尺寸 .ico 并更新桌面快捷方式，保持原有启动目标不变。
"""
import base64
import os
import subprocess
import sys

from PIL import Image

HOME = os.path.expanduser("~")
DESKTOP = os.path.join(HOME, "Desktop")
LNK = os.path.join(DESKTOP, "PaperLibrary.lnk")
ICON_OUT = os.path.join(HOME, "paperlib_icon.ico")
SIZES = [256, 128, 64, 48, 32, 16]
DEFAULT_TARGET = r"y:\私人\PAPER\paper-library-app\start.bat"
DEFAULT_WORKDIR = r"y:\私人\PAPER\paper-library-app"


def _ps(script: str) -> str:
    """执行 PowerShell（用 base64/UTF-16LE 传参，绕开中文编码问题）"""
    b64 = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    r = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", b64],
        capture_output=True, timeout=60,
    )
    return r.stdout.decode("utf-8", errors="replace")


def read_shortcut():
    """读取现快捷方式的目标与工作目录（原样保留，避免破坏启动）"""
    tmp = os.path.join(os.environ.get("TEMP", "."), "_lnk_read.txt")
    script = (
        f"$ws = New-Object -ComObject WScript.Shell\n"
        f"$sc = $ws.CreateShortcut('{LNK}')\n"
        f"'TARGET=' + $sc.TargetPath | Out-File -Encoding UTF8 '{tmp}'\n"
        f"'WORKDIR=' + $sc.WorkingDirectory | Out-File -Append -Encoding UTF8 '{tmp}'\n"
        f"'DESC=' + $sc.Description | Out-File -Append -Encoding UTF8 '{tmp}'\n"
    )
    _ps(script)
    target, workdir, desc = DEFAULT_TARGET, DEFAULT_WORKDIR, "PaperLibrary"
    try:
        with open(tmp, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith("TARGET=") and len(line) > 7:
                    target = line[7:]
                elif line.startswith("WORKDIR=") and len(line) > 8:
                    workdir = line[8:]
                elif line.startswith("DESC=") and len(line) > 5:
                    desc = line[5:]
    except Exception:
        pass
    return target, workdir, desc


def write_shortcut(target, workdir, desc):
    script = (
        f"$ws = New-Object -ComObject WScript.Shell\n"
        f"$sc = $ws.CreateShortcut('{LNK}')\n"
        f"$sc.TargetPath = '{target}'\n"
        f"$sc.WorkingDirectory = '{workdir}'\n"
        f"$sc.IconLocation = '{ICON_OUT},0'\n"
        f"$sc.Description = '{desc}'\n"
        f"$sc.WindowStyle = 1\n"
        f"$sc.Save()\n"
    )
    _ps(script)


def to_square(img: Image.Image) -> Image.Image:
    """补透明边成正方形，避免图标被拉伸变形（不裁掉任何内容）"""
    w, h = img.size
    if w == h:
        return img
    side = max(w, h)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - w) // 2, (side - h) // 2), img)
    return canvas


def convert(src_path: str) -> None:
    img = Image.open(src_path)
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    img = to_square(img)

    frames = [img.resize((s, s), Image.LANCZOS) for s in SIZES]
    frames[0].save(ICON_OUT, format="ICO",
                   sizes=[(s, s) for s in SIZES],
                   append_images=frames[1:])

    target, workdir, desc = read_shortcut()
    write_shortcut(target, workdir, desc)

    # 刷新图标缓存，让新图标立刻显示
    try:
        subprocess.run(["ie4uinit.exe", "-show"], capture_output=True, timeout=20)
    except Exception:
        pass


def pick_file():
    """没有参数时弹出文件选择框"""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="选择要作为图标的图片",
        filetypes=[("图片文件", "*.png *.jpg *.jpeg *.bmp *.gif *.webp *.ico"),
                   ("所有文件", "*.*")],
    )
    root.destroy()
    return path


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else ""
    if not src or not os.path.isfile(src):
        src = pick_file()
    if not src:
        print("已取消，没有选择图片。")
        return 1

    try:
        convert(src)
    except Exception as e:
        _popup(f"设置图标失败：\n{e}", error=True)
        print(f"失败: {e}")
        return 1

    _popup("图标已更换 ✅\n\n去桌面看看 PaperLibrary 快捷方式。\n"
           "（若还是旧图标，按 F5 刷新桌面，或重启一次资源管理器）")
    print(f"图标已更新: {src} -> {ICON_OUT}")
    return 0


def _popup(msg, error=False):
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        (messagebox.showerror if error else messagebox.showinfo)(
            "PaperLibrary", msg)
        root.destroy()
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(main())
