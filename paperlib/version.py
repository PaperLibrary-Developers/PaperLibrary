"""产品版本唯一来源：包根 `version.json`。

版本号只维护这一处：仓库根的 `version.json` 供源码运行；发布时
`tools/release.py` 复制同一份并补上 `builtAt` / `commit`，启动器、
Python 后端、Web 前端都从这份文件读，不再各自硬编码。

字段（方案一原文 §3.1）：
    product   产品名，固定 "PaperLibrary"
    version   语义化版本，如 "1.6.0"（更新比较、排序用这个）
    tag       发布标签，如 "v1.6"（1.6.0）或 "v1.7.1"（1.7.1）：
              patch=0 用 v<major>.<minor>（兼容既有 Release），patch>0 用完整 v<major>.<minor>.<patch>
    builtAt   构建时间，UTC ISO8601（发布包里必须存在）
    commit    构建来源提交（发布包里必须存在；无 git 时写 "unknown"）

读取约定：源码运行允许缺省 builtAt/commit；发布包必须齐全，由
`tools/release.py` 写入、`tools/version_check.py` 校验。
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

PRODUCT = "PaperLibrary"
VERSION_FILE = "version.json"
FIELDS = ("product", "version", "tag", "builtAt", "commit")

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_TAG = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_BUILT_AT = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_COMMIT = re.compile(r"^(?:[0-9a-f]{7,40}|unknown)$")


class VersionError(ValueError):
    """version.json 缺失、损坏或与约定不符。"""


def version_path(root) -> Path:
    return Path(root) / VERSION_FILE


def semver(version: str) -> tuple[int, int, int]:
    """把 `x.y.z` 拆成整数三元组；非法值抛 VersionError（不猜默认版本）。"""
    match = _SEMVER.match(str(version or "").strip())
    if not match:
        raise VersionError("version 必须是 x.y.z 形式的语义化版本（当前：%r）" % (version,))
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def ui_label(version: str) -> str:
    """界面/发布标签：patch=0 用 v<major>.<minor>（兼容既有 v1.6），
    patch>0 用完整 v<major>.<minor>.<patch>（如 v1.7.1），保证同一个 tag 不重复用于多个补丁版本。
    """
    major, minor, patch = semver(version)
    if patch:
        return "v%d.%d.%d" % (major, minor, patch)
    return "v%d.%d" % (major, minor)


def validate(data, require_build: bool = False) -> dict:
    """校验并归一化 version.json；返回固定字段顺序的 dict。"""
    if not isinstance(data, dict):
        raise VersionError("version.json 顶层必须是对象")
    missing = [key for key in ("product", "version", "tag") if key not in data]
    if missing:
        raise VersionError("version.json 缺少字段：" + "、".join(missing))
    for key in FIELDS:
        if key in data and not isinstance(data[key], str):
            raise VersionError("version.json 字段 %s 必须是字符串" % key)
    product = data["product"].strip()
    if product != PRODUCT:
        raise VersionError("product 必须是 %s（当前：%r）" % (PRODUCT, product))
    version = data["version"].strip()
    semver(version)  # 失败时抛出说明 x.y.z 约定的 VersionError
    tag = data["tag"].strip()
    expected = ui_label(version)
    if tag != expected:
        raise VersionError("tag 必须等于 %s（当前：%r）" % (expected, tag))
    built_at = str(data.get("builtAt") or "").strip()
    commit = str(data.get("commit") or "").strip()
    if require_build:
        if not built_at:
            raise VersionError("发布包 version.json 缺少 builtAt")
        if not commit:
            raise VersionError("发布包 version.json 缺少 commit")
    if built_at and not _BUILT_AT.match(built_at):
        raise VersionError("builtAt 必须是 UTC ISO8601（如 2026-12-01T00:00:00Z）：%r" % built_at)
    if commit and not _COMMIT.match(commit):
        raise VersionError("commit 必须是 7–40 位小写十六进制（或 unknown）：%r" % commit)
    return {"product": product, "version": version, "tag": tag,
            "builtAt": built_at, "commit": commit}


def load(root, require_build: bool = False) -> dict:
    """读包根 version.json；缺失/损坏时抛 VersionError（调用方给出可读错误）。"""
    path = version_path(root)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise VersionError("包根缺少 %s：%s" % (VERSION_FILE, path))
    except OSError as exc:
        raise VersionError("无法读取 %s：%s" % (path, exc))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise VersionError("%s 不是合法 JSON：%s" % (path, exc))
    return validate(data, require_build=require_build)


def current_commit(root) -> str:
    """构建来源提交；不在 git 仓库里时如实写 unknown，不伪造哈希。"""
    try:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(root),
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if proc.returncode != 0:
        return "unknown"
    commit = (proc.stdout or "").strip().lower()
    return commit if _COMMIT.match(commit) else "unknown"


def build_record(root, built_at: str = "", commit: str = "") -> dict:
    """发布包 version.json：仓库版本 + 构建时间 + 来源提交。"""
    record = load(root)
    record["builtAt"] = built_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record["commit"] = commit or current_commit(root)
    return validate(record, require_build=True)


def dumps(record: dict) -> str:
    """固定字段顺序、UTF-8、末尾换行（便于发布产物逐字节复现与比对）。"""
    return json.dumps({key: record[key] for key in FIELDS}, ensure_ascii=False, indent=2) + "\n"
