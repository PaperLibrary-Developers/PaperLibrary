"""构建便携 Python 运行时（runtime/portable）。

目标：解压即用、无需安装 Python/Node。组成：
  python-3.11.9-embed-amd64.zip（官方 11MB → 解压 21MB）
  + 锁定版本及 SHA-256 的 Windows x64 运行时依赖
  + python311._pth（把 site-packages 与项目根加进 sys.path）

用法：python tools/make_portable.py [--runtime-dir <dir>] [--source-site-packages <dir>] [--force]
幂等：校验通过的 ZIP 可复用。使用 --force 从锁定的依赖重建。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
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
EMBED_SHA256 = "009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b"
WHEEL_LOCK = Path(__file__).with_name("windows-portable-requirements.txt")
PTH = "python311._pth"
SELF_TEST = (
    "import sys; "
    "from importlib.metadata import version; "
    "import sqlite3, ssl, json, hashlib, urllib.request, zipfile, ctypes, http.server; "
    "import pymupdf; from PIL import Image; "
    "from doc2docx import convert; from striprtf.striprtf import rtf_to_text; "
    "from openpyxl import load_workbook; import et_xmlfile; "
    "print('portable-ok', sys.version.split()[0], *(version(name) for name in sys.argv[1:]))"
)


def sha256(path):
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def download_zip(build_dir):
    build_dir.mkdir(parents=True, exist_ok=True)
    target = build_dir / EMBED_NAME
    for cached in dict.fromkeys((target, BUILD / EMBED_NAME)):
        if cached.is_file() and sha256(cached) == EMBED_SHA256:
            return cached
    print("[portable] 下载 %s …" % EMBED_URL)
    with tempfile.TemporaryDirectory(dir=build_dir) as work:
        downloaded = Path(work) / EMBED_NAME
        with urllib.request.urlopen(EMBED_URL, timeout=120) as resp, downloaded.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        if sha256(downloaded) != EMBED_SHA256:
            raise SystemExit("便携 Python ZIP 的 SHA-256 与锁定值不符")
        downloaded.replace(target)
    return target


def install_wheels(target):
    subprocess.run([
        sys.executable, "-m", "pip", "--disable-pip-version-check", "install",
        "--no-deps", "--no-compile", "--only-binary=:all:", "--require-hashes",
        "--platform", "win_amd64", "--implementation", "cp", "--python-version", PY_VERSION,
        "--target", str(target), "-r", str(WHEEL_LOCK),
    ], check=True)


def copy_packages(source: Path, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("pymupdf", "fitz", "PIL", "pillow.libs", "pymupdf.libs",
                 "doc2docx", "striprtf", "openpyxl", "et_xmlfile"):
        src = source / name
        if src.is_dir():
            dst = target / name
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            copied.append(name)
    if "pymupdf" not in copied and "fitz" not in copied:
        raise SystemExit("复制失败：源 site-packages 里没有 pymupdf/fitz")
    # Pillow 12 把扩展模块放在 PIL 包内；老版本可能放在 site-packages 根，按需补
    for pyd in list(source.glob("_imaging*.pyd")) + list(source.glob("_imagingft*.pyd")):
        shutil.copy(pyd, target / pyd.name)


def write_pth(portable):
    (portable / PTH).write_text(
        "python311.zip\n.\nsite-packages\n..\\..\nimport site\n", encoding="utf-8"
    )


def self_test(portable, locked):
    exe = portable / "python.exe"
    env = dict(os.environ, PYTHONUTF8="1")
    packages = [line.split()[0].split("==") for line in WHEEL_LOCK.read_text(encoding="utf-8").splitlines()
                if "==" in line]
    expected = [version for _, version in packages]
    names = [name for name, _ in packages] if locked else []
    out = subprocess.run([str(exe), "-B", "-c", SELF_TEST, *names], capture_output=True, text=True, env=env)
    if out.returncode != 0 or (locked and out.stdout.split() != ["portable-ok", PY_VERSION, *expected]):
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
    parser.add_argument("--runtime-dir", type=Path, default=RUNTIME)
    parser.add_argument("--source-site-packages", default=None)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)
    runtime_dir = args.runtime_dir.resolve()
    portable = runtime_dir / PORTABLE.name

    if portable.exists() and not args.force:
        try:
            self_test(portable, locked=not args.source_site_packages)
            print("[portable] 已存在且自检通过：%s（%.1f MB）" % (portable, tree_size(portable) / 1048576))
            return 0
        except SystemExit:
            print("[portable] 现有运行时自检失败，重建")
    zip_path = download_zip(runtime_dir / "build")
    with tempfile.TemporaryDirectory(prefix="portable-build-", dir=runtime_dir) as work:
        staged = Path(work) / "portable"
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(staged)
        target = staged / "site-packages"
        if args.source_site_packages:
            copy_packages(Path(args.source_site_packages), target)
        else:
            install_wheels(target)
        write_pth(staged)
        self_test(staged, locked=not args.source_site_packages)
        previous = Path(work) / "previous"
        if portable.exists():
            portable.replace(previous)
        try:
            staged.replace(portable)
        except OSError:
            if previous.exists():
                previous.replace(portable)
            raise
    size = tree_size(portable)
    print("[portable] 完成：%s（%.1f MB）" % (portable, size / 1048576))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
