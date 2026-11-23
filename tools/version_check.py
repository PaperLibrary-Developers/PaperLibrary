"""版本一致性检查：包根 `version.json` 是唯一来源。

用法：
    python tools/version_check.py                                  # 检查仓库
    python tools/version_check.py --package dist/PaperLibrary-xxx  # 额外检查发布包
    python tools/version_check.py --quiet

检查项：
1. `version.json` 合法：product=PaperLibrary、version=x.y.z、tag=v<major>.<minor>；
   发布包里还必须带 builtAt/commit。
2. 受控源码、前端与用户文档里不再硬编码当前版本号（tag 或 x.y.z）——版本只写
   `version.json` 一处，启动器/后端/前端都运行时读取。
3. `--package` 时：包内 `version.json` 与 `release-manifest.json` 的
   product/version/tag/commit 逐字段一致。

退出码：0 = 全部通过；1 = 有问题（逐条打印原因）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperlib.version import VersionError, load  # noqa: E402

# 曾经硬编码过版本、现在改为运行时读取的文件（相对包根）
SOURCE_FILES = (
    "README.md",
    "使用说明.txt",
    "requirements.txt",
    "public/index.html",
    "public/styles.css",
    "public/js/app.mjs",
    "public/js/api.mjs",
    "public/js/views/guide.mjs",
    "paperlib/app.py",
    "paperlib/api/system.py",
    "tools/make_launcher.ps1",
    "tools/release.py",
)


def check_version_file(root, require_build: bool = False):
    """读并校验 version.json；返回 (info, problems)。"""
    try:
        info = load(root, require_build=require_build)
    except VersionError as exc:
        return {}, ["version.json 无效：" + str(exc)]
    return info, []


def check_sources(root, info):
    """扫描受控文件里是否还硬编码当前版本号。"""
    tag = info["tag"]
    version = info["version"]
    problems = []
    for rel in SOURCE_FILES:
        path = Path(root) / rel
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError as exc:
            problems.append("%s 读取失败：%s" % (rel, exc))
            continue
        for needle in (tag, version):
            if needle in text:
                problems.append("%s 仍硬编码版本号 %s（应改为读 version.json）" % (rel, needle))
    return problems


def check_package(package, info):
    """发布包内：version.json 必须带 builtAt/commit，release-manifest.json 必须对应。"""
    package = Path(package)
    problems = []
    packaged, errors = check_version_file(package, require_build=True)
    problems.extend("包内 " + item for item in errors)
    if packaged:
        # 产品的 identity 必须一致；commit/builtAt 是构建期才有的字段，
        # 仓库里的 version.json 不写，所以只在仓库也有 commit 时才比对。
        for key in ("product", "version", "tag"):
            if packaged[key] != info[key]:
                problems.append("包内 version.json 的 %s=%r 与仓库 %r 不一致"
                                % (key, packaged[key], info[key]))
        if info["commit"] and packaged["commit"] != info["commit"]:
            problems.append("包内 version.json 的 commit=%r 与仓库 %r 不一致"
                            % (packaged["commit"], info["commit"]))
    manifest_path = package / "release-manifest.json"
    if not manifest_path.is_file():
        problems.append("包内缺少 release-manifest.json")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            problems.append("release-manifest.json 无法解析：%s" % exc)
            manifest = {}
        for key in ("product", "version", "tag"):
            if manifest.get(key) != info[key]:
                problems.append("release-manifest.json 的 %s=%r 与 version.json %r 不一致"
                                % (key, manifest.get(key), info[key]))
        if packaged:
            for key in ("builtAt", "commit"):
                if manifest.get(key) != packaged.get(key):
                    problems.append("release-manifest.json 的 %s=%r 与包内 version.json %r 不一致"
                                    % (key, manifest.get(key), packaged.get(key)))
    return problems


def run(root=ROOT, package=None):
    """返回 (problems, info)；problems 为空即通过。供测试与发布脚本复用。"""
    root = Path(root)
    info, problems = check_version_file(root)
    if problems:
        return problems, info
    problems.extend(check_sources(root, info))
    if package:
        problems.extend(check_package(package, info))
    return problems, info


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", default="", help="额外检查发布包目录")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    problems, info = run(ROOT, package=args.package or None)
    if problems:
        print("[version] 版本一致性检查未通过：")
        for item in problems:
            print("[version]   - " + item)
        return 1
    if not args.quiet:
        print("[version] version.json：%s %s (%s)" % (info["product"], info["version"], info["tag"]))
        print("[version] 源码/前端/文档无硬编码版本；发布包与 manifest 一致")
        print("[version] 检查通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
