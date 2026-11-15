"""组装发布包 dist/PaperLibrary-<date>/ 并实测体积（目标 ≤120MB）。

包含：public/ shared/ paperlib/ vendor/ tools/ 使用说明.txt runtime/portable/
      （唯一用户入口 PaperLibrary.exe 由 make_launcher.ps1 现场编译，并嵌入 public/paperlibrary.ico）
排除：data/ docs/ test/ .git/ __pycache__/ dist/ runtime/build runtime/*.exe
用法：python tools/release.py [--out dist] [--skip-portable]

启动入口约定：PaperLibrary.exe.config 由 make_launcher.ps1 设为隐藏文件，文件名不可改
（.NET CLR 只读取 <exe 文件名>.config）。这样资源管理器里只会看到一个可见入口，
避免用户在不显示文件扩展名时把配置文件和启动器搞混。打包结束会校验这一点。

打 zip 分发请用 python tools/package_zip.py（Compress-Archive 的目录通配会跳过隐藏文件，
把 PaperLibrary.exe.config 漏在 zip 外）；zip 不保存隐藏属性，启动器首次运行会重新隐藏它。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["public", "shared", "paperlib", "vendor", "tools",
           "使用说明.txt", "README.md", "requirements.txt"]
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
LIMIT_KB = 120 * 1024
LAUNCHER_EXE = "PaperLibrary.exe"
LAUNCHER_CONFIG = "PaperLibrary.exe.config"


def is_hidden(path: Path) -> bool:
    """Windows 隐藏属性；Python 3.5+ 在 Windows 上提供 st_file_attributes。"""
    try:
        attrs = path.stat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attrs & stat.FILE_ATTRIBUTE_HIDDEN)


def tree_size(path: Path) -> int:
    total = 0
    for root, dirs, files in os.walk(path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def copy_tree(src: Path, dst: Path):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".git"))


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dist")
    parser.add_argument("--skip-portable", action="store_true")
    args = parser.parse_args(argv)

    # 精确到秒 + pid：并发打包不会撞同名，避免把另一个正在写的包删掉
    stamp = time.strftime("%Y%m%d-%H%M%S") + "-" + str(os.getpid())
    target = ROOT / args.out / ("PaperLibrary-" + stamp)
    if target.exists():
        raise SystemExit("产物目录已存在（不应发生）：" + str(target))
    target.mkdir(parents=True)
    for name in INCLUDE:
        src = ROOT / name
        if not src.exists():
            continue
        dst = target / name
        if src.is_dir():
            copy_tree(src, dst)
        else:
            shutil.copy2(src, dst)

    portable_src = ROOT / "runtime" / "portable"
    if not args.skip_portable:
        if not portable_src.is_dir():
            raise SystemExit("缺少 runtime/portable；先运行 python tools/make_portable.py")
        shutil.copytree(portable_src, target / "runtime" / "portable")
        # 便携运行时里的项目根路径：release/runtime/portable/../.. = release 根，与 _pth 一致

    # 唯一用户入口：无控制台启动器 PaperLibrary.exe（csc 现场编译，图标取 public/paperlibrary.ico）。
    # 不再保留 .cmd 回退：编译失败必须让打包失败，否则包里会没有任何用户入口。
    launcher_ps = target / "tools" / "make_launcher.ps1"
    if not launcher_ps.is_file():
        print("[release] 缺少启动器脚本：tools/make_launcher.ps1")
        return 1
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(launcher_ps), "-OutDir", str(target)],
            cwd=str(target), capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180,
        )
    except Exception as exc:  # noqa: BLE001
        print("[release] 启动器编译异常：" + str(exc))
        return 1
    launcher = target / LAUNCHER_EXE
    launcher_config = target / LAUNCHER_CONFIG
    if proc.returncode != 0 or not launcher.is_file():
        print("[release] 启动器编译失败：" + ((proc.stderr or proc.stdout or "")[-400:]))
        return 1
    if not launcher_config.is_file():
        print("[release] 缺少启动器配置：" + LAUNCHER_CONFIG)
        return 1
    if not is_hidden(launcher_config):
        print("[release] 启动器配置未隐藏：" + LAUNCHER_CONFIG
              + "（make_launcher.ps1 应设置 Hidden 属性；文件名必须保持 <exe>.config，不能改名）")
        return 1
    print("[release] 启动器已生成：" + LAUNCHER_EXE + "（唯一可见入口，已嵌入图标）")
    print("[release] " + LAUNCHER_CONFIG + " 已隐藏（CLR 仍按 <exe>.config 加载配置）")

    size = tree_size(target)
    breakdown = {
        "runtime": tree_size(target / "runtime") if (target / "runtime").exists() else 0,
        "vendor": tree_size(target / "vendor"),
        "public": tree_size(target / "public"),
        "code": tree_size(target / "paperlib") + tree_size(target / "shared") + tree_size(target / "tools"),
    }
    manifest = {
        "builtAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target": str(target),
        "bytes": size,
        "kb": round(size / 1024),
        "breakdown": {k: round(v / 1048576, 2) for k, v in breakdown.items()},
        "files": sum(len(files) for _, _, files in os.walk(target)),
        "entry": {
            "launcher": LAUNCHER_EXE,
            "launcherConfig": LAUNCHER_CONFIG,
            "launcherConfigHidden": True,
            "note": ".NET CLR 只读取 <exe 文件名>.config；文件名保持原名并设为隐藏，避免与启动器混淆",
        },
        "sha256": {},
    }
    for key in ("paperlib/routes.py", "paperlib/app.py", "public/index.html", LAUNCHER_EXE, LAUNCHER_CONFIG):
        path = target / key
        if path.is_file():
            manifest["sha256"][key] = hashlib.sha256(path.read_bytes()).hexdigest()
    (target / "release-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    print("[release] %s" % target)
    print("[release] 体积 %.1f MB（%d KB），上限 %d KB" % (size / 1048576, round(size / 1024), LIMIT_KB))
    for key, value in manifest["breakdown"].items():
        print("[release]   %-8s %6.1f MB" % (key, value))
    if size > LIMIT_KB * 1024:
        print("[release] 超过 120MB 上限！")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
