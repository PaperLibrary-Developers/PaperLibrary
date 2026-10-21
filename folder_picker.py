# -*- coding: utf-8 -*-
"""folder_picker.py — Windows 原生与网页交互式文件夹选择器

为用户提供两种选择文献库总路径的方式：
1. 弹出 Windows 原生文件夹选择框 (通过 tkinter.filedialog.askdirectory 或 PowerShell)
2. 网页端交互式目录树浏览器 (支持盘符切换、面包屑导航、子文件夹进入与确认选择)
"""

import os
import sys
import string
import platform


def get_available_drives():
    """获取 Windows 系统上所有可用的逻辑驱动器盘符 (如 ['C:\\', 'D:\\', 'Y:\\'])"""
    drives = []
    if platform.system() == "Windows":
        # 获取有效盘符
        for letter in string.ascii_uppercase:
            drive_str = f"{letter}:\\"
            if os.path.exists(drive_str):
                drives.append(drive_str)
    else:
        drives.append("/")
    return drives


def pick_folder_native(initial_dir=""):
    """在 Windows 屏幕最前沿弹出原生文件夹浏览窗口。
    
    返回用户选中的绝对路径字符串，若用户点击取消则返回 None。
    """
    initial_dir = initial_dir or os.getcwd()
    if not os.path.isdir(initial_dir):
        initial_dir = os.path.expanduser("~")

    # 优先尝试 tkinter 原生弹窗
    try:
        import tkinter as tk
        from tkinter import filedialog
        
        root = tk.Tk()
        root.withdraw()  # 隐藏主窗口
        root.attributes("-topmost", True)  # 保证置顶在所有窗口前面
        
        selected_path = filedialog.askdirectory(
            parent=root,
            initialdir=initial_dir,
            title="请选择文献库总文件夹（包含所有文献的大文件夹）"
        )
        root.destroy()
        
        if selected_path and os.path.isdir(selected_path):
            return os.path.abspath(selected_path)
        return None
    except Exception as e:
        # 回退：使用 PowerShell 原生 FolderBrowserDialog
        try:
            import subprocess
            ps_script = f'''
            [System.Reflection.Assembly]::LoadWithPartialName("System.windows.forms") | Out-Null
            $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
            $dialog.Description = "请选择文献库总文件夹"
            $dialog.SelectedPath = "{initial_dir.replace('"', '`"')}"
            $dialog.ShowNewFolderButton = $true
            if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {{
                Write-Output $dialog.SelectedPath
            }}
            '''
            res = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_script],
                capture_output=True,
                text=True,
                timeout=120
            )
            out = res.stdout.strip()
            if out and os.path.isdir(out):
                return os.path.abspath(out)
        except Exception:
            pass
    return None


def get_breadcrumbs(current_path: str):
    """根据路径解析出面包屑列表 [{'name': '...', 'path': '...'}]"""
    abs_path = os.path.abspath(current_path)
    parts = []
    curr = abs_path
    
    # 逐级向上提取父目录
    while True:
        parent = os.path.dirname(curr)
        name = os.path.basename(curr)
        if not name:  # 盘符根目录，例如 'Y:\\'
            name = curr
            parts.append({"name": name, "path": curr})
            break
        parts.append({"name": name, "path": curr})
        if parent == curr:
            break
        curr = parent
        
    parts.reverse()
    return parts


def list_subfolders(current_path: str, max_items: int = 50):
    """列出当前路径下的子文件夹，供网页端选择器展示。"""
    if not os.path.isdir(current_path):
        return []
        
    subfolders = []
    try:
        entries = os.scandir(current_path)
        dirs = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False) and not e.name.startswith("."):
                    dirs.append(e)
            except (PermissionError, OSError):
                continue
                
        dirs.sort(key=lambda d: d.name.lower())
        for d in dirs[:max_items]:
            subfolders.append({
                "name": d.name,
                "path": os.path.abspath(d.path)
            })
    except (PermissionError, OSError):
        pass
        
    return subfolders
