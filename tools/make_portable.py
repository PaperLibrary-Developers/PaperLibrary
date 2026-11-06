"""构建便携 Python 运行时（runtime/portable）。

目标：解压即用、无需安装 Python/Node。组成：
  python-3.11.9-embed-amd64.zip（官方 11MB → 解压 21MB）
  + PyMuPDF（53MB，cp311，从本机 Python 3.11 环境复制）
  + Pillow（15MB，cp311）
  + python311._pth（把 site-packages 与项目根加进 sys.path）

用法：python tools/make_portable.py [--source-site-packages <dir>] [--force]
幂等：zip 已存在不重复下载；portable 已存在且自检通过则跳过（--force 重建）。
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNTIME = ROOT / "runtime"
BUILD = RUNTIME / "build"
PORTABLE = RUNTIME / "portable"
PY_VERSION = "3.11.9"
EMBED_NAME = "python-%s-embed-amd64.zip" % PY_VERSION
EMBED_URL = "https://www.python.org/ftp/python/%s/%s" % (PY_VERSION, EMBED_NAME)
PTH = "python311._pth"
SELF_TEST = (
    "import sys; "
    "import sqlite3, ssl, json, hashlib, urllib.request, zipfile, ctypes, http.server; "
    "import pymupdf; from PIL import Image; "
    "print('portable-ok', sys.version.split()[0], pymupdf.__version__, Image.__version__)"
)


def default_site_packages():
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Python311" / "Lib" / "site-packages",
        ROOT.parent / "PaperLibrary" / "runtime" / "Lib" / "site-packages",
    ]
    for path in candidates:
        if (path / "pymupdf").is_dir() or (path / "fitz").is_dir():
            return path
    raise SystemExit("找不到含 PyMuPDF 的 Python 3.11 site-packages，请用 --source-site-packages 指定")


def download_zip():
    BUILD.mkdir(parents=True, exist_ok=True)
    target = BUILD / EMBED_NAME
    if target.is_file() and target.stat().st_size > 10_000_000:
        return target
    print("[portable] 下载 %s …" % EMBED_URL)
    with urllib.request.urlopen(EMBED_URL, timeout=120) as resp, open(target, "wb") as fh:
        shutil.copyfileobj(resp, fh)
    return target


def extract(zip_path):
    if PORTABLE.exists():
        shutil.rmtree(PORTABLE)
    PORTABLE.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(PORTABLE)


def copy_packages(source: Path):
    target = PORTABLE / "site-packages"
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("pymupdf", "fitz", "PIL"):
        src = source / name
        if src.is_dir():
            dst = target / name
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            copied.append(name)
    if "pymupdf" not in copied and "fitz" not in copied:
        raise SystemExit("复制失败：源 site-packages 里没有 pymupdf/fitz")
    # Pillow 12 把扩展模块放在 PIL 包内；老版本可能放在 site-packages 根，按需补
    for pyd in list(source.glob("_imaging*.pyd")) + list(source.glob("_imagingft*.pyd")):
        shutil.copy(pyd, target / pyd.name)
    return copied


def write_pth():
    (PORTABLE / PTH).write_text(
        "python311.zip\n.\nsite-packages\n..\\..\nimport site\n", encoding="utf-8"
    )


def self_test():
    exe = PORTABLE / "python.exe"
    env = dict(os.environ, PYTHONUTF8="1")
    out = subprocess.run([str(exe), "-c", SELF_TEST], capture_output=True, text=True, env=env)
    if out.returncode != 0:
        print(out.stdout)
        print(out.stderr)
        raise SystemExit("便携运行时自检失败")
    print("[portable] 自检通过：" + out.stdout.strip())


def tree_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-site-packages", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if PORTABLE.exists() and not args.force:
        try:
            self_test()
            print("[portable] 已存在且自检通过：%s（%.1f MB）" % (PORTABLE, tree_size(PORTABLE) / 1048576))
            return 0
        except SystemExit:
            print("[portable] 现有运行时自检失败，重建")
    source = Path(args.source_site_packages) if args.source_site_packages else default_site_packages()
    zip_path = download_zip()
    extract(zip_path)
    copied = copy_packages(source)
    write_pth()
    self_test()
    size = tree_size(PORTABLE)
    print("[portable] 完成：%s（%.1f MB；复制 %s）" % (PORTABLE, size / 1048576, ", ".join(copied)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
