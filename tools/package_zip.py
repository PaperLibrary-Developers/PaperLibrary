"""把 release.py 生成的发布包目录打成可分发的 zip（显式包含隐藏文件）。

为什么单独一个脚本：资源管理器 / Compress-Archive 用目录通配打包时会跳过带 Hidden
属性的文件，而 PaperLibrary.exe.config 恰恰是隐藏的 .NET 配置（.NET CLR 只读取
<exe 文件名>.config）。一旦漏掉它，内置窗口的 supportedRuntime 与 DPI 设置会静默丢失，
所以这里显式遍历目录、包含隐藏文件，并在写完后校验两个入口文件的条目都存在。

跳过规则只作用于「顶层」：顶层的 data/ docs/ test/ 属于用户数据与工程材料，不打包；
但 paperlib/data/ 是源码包，必须保留（只按名字全局跳过 data 会把服务依赖打掉）。
__pycache__ / *.pyc / .git / node_modules 在任何层级都跳过。

zip 格式不保存 Windows 文件属性：解压后 PaperLibrary.exe.config 会重新可见。
启动器每次启动都会把它重新设为隐藏（tools/make_launcher.ps1 里的 EnsureConfigHidden），
所以用户第一次双击后根目录就会恢复成「只有一个可见入口」。

用法：
    python tools/package_zip.py --src dist/PaperLibrary-<stamp>
    python tools/package_zip.py --src <包目录> --out dist/PaperLibrary.<tag>.Nexus.zip
"""
from __future__ import annotations

import argparse
import hashlib
import os
import zipfile
from pathlib import Path

# 顶层目录才跳过：用户数据 / 工程材料 / 测试
TOP_SKIP_DIRS = {"data", "docs", "test"}
# 任何层级都跳过：缓存与版本库元数据
ANY_SKIP_DIRS = {".git", "__pycache__", "node_modules"}
SKIP_SUFFIX = (".pyc", ".pyo")
LAUNCHER_EXE = "PaperLibrary.exe"
LAUNCHER_CONFIG = "PaperLibrary.exe.config"


def iter_files(root: Path):
    root = Path(root).resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        dirnames[:] = [
            d for d in dirnames
            if d not in ANY_SKIP_DIRS
            and not (current == root and d in TOP_SKIP_DIRS)
        ]
        dirnames.sort()
        for name in sorted(filenames):
            if name.endswith(SKIP_SUFFIX):
                continue
            yield (Path(dirpath) / name).relative_to(root)


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True, help="release.py 生成的包目录")
    parser.add_argument("--out", default="", help="输出 zip 路径（默认与包目录同名 .zip）")
    args = parser.parse_args(argv)

    src = Path(args.src).resolve()
    if not src.is_dir():
        print("[package] 源目录不存在：" + str(src))
        return 1
    out = Path(args.out).resolve() if args.out else src.parent / (src.name + ".zip")
    if out.exists():
        out.unlink()

    entries = []
    total = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in iter_files(src):
            path = src / rel
            zf.write(path, rel.as_posix())
            entries.append(rel.as_posix())
            try:
                total += path.stat().st_size
            except OSError:
                pass

    names = set(entries)
    checks = {
        LAUNCHER_EXE: LAUNCHER_EXE in names,
        LAUNCHER_CONFIG: LAUNCHER_CONFIG in names,
        # 产品版本的唯一来源，必须随包分发（启动器/后端/前端都读它）
        "version.json": "version.json" in names,
        # 源码包 paperlib/data 必须进 zip，否则解压后服务起不来
        "paperlib/data/__init__.py": "paperlib/data/__init__.py" in names,
        "paperlib/data/store.py": "paperlib/data/store.py" in names,
        "public/index.html": "public/index.html" in names,
        "runtime/portable/python.exe": "runtime/portable/python.exe" in names,
        # 启动器从包内脚本和元数据部署助手，再从包外运行。
        "tools/deploy_updater.py": "tools/deploy_updater.py" in names,
        "tools/updater/PaperLibraryUpdater.exe": "tools/updater/PaperLibraryUpdater.exe" in names,
        "tools/updater/helper.json": "tools/updater/helper.json" in names,
    }
    failed = [k for k, ok in checks.items() if not ok]
    if failed:
        out.unlink(missing_ok=True)
        print("[package] zip 缺少关键条目：" + "、".join(failed))
        return 1

    digest = hashlib.sha256(out.read_bytes()).hexdigest()
    print("[package] " + str(out))
    print("[package] %d 个文件，原始 %.1f MB，zip %.1f MB"
          % (len(entries), total / 1048576, out.stat().st_size / 1048576))
    print("[package] sha256 " + digest)
    print("[package] 校验通过：入口文件、paperlib/data 源码、前端与便携运行时都在")
    print("[package] 解压后启动器会自动把 " + LAUNCHER_CONFIG + " 重新设为隐藏")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
