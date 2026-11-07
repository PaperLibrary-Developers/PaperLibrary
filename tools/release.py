"""组装发布包 dist/PaperLibrary-<date>/ 并实测体积（目标 ≤120MB）。

包含：public/ shared/ paperlib/ vendor/ tools/ 启动.cmd 停止.cmd 使用说明.txt runtime/portable/
排除：data/ docs/ test/ .git/ __pycache__/ dist/ runtime/build runtime/*.exe
用法：python tools/release.py [--out dist] [--skip-portable]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INCLUDE = ["public", "shared", "paperlib", "vendor", "tools",
           "启动.exe", "停止.exe", "启动.cmd", "停止.cmd", "启动-调试.cmd",
           "使用说明.txt", "README.md"]
SKIP_DIRS = {"__pycache__", ".git", "node_modules"}
LIMIT_KB = 120 * 1024


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

    # 启动器 exe（无控制台；C# 由 PowerShell Add-Type 编译，失败则回退 .cmd）
    launcher_ps = target / "tools" / "make_launcher.ps1"
    if launcher_ps.is_file():
        try:
            proc = subprocess.run(
                ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(launcher_ps), "-OutDir", str(target)],
                cwd=str(target), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=180,
            )
            if proc.returncode == 0 and (target / "启动.exe").is_file():
                print("[release] 启动器已生成：启动.exe / 停止.exe")
            else:
                print("[release] 启动器编译失败（将只用 .cmd）：" + ((proc.stderr or proc.stdout or "")[-200:]))
        except Exception as exc:  # noqa: BLE001
            print("[release] 启动器编译异常（将只用 .cmd）：" + str(exc))

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
        "sha256": {},
    }
    for key in ("paperlib/routes.py", "paperlib/app.py", "public/index.html", "启动.cmd"):
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
