"""Release 资产命名、稳定地址与发布包校验（阶段 0 / P0-04）。

方案一原文 §3.2：
- 每个公开 Release 至少附带 完整包 zip + update-manifest.json（可选 SHA256SUMS/更新说明）；
- 客户端只用稳定地址 <repo>/releases/latest/download/update-manifest.json，不调用 api.github.com；
- 资产下载本身不消耗 API 限额。

2026-11-15 实测：仓库规范名是 PaperLibrary-Developers/PaperLibrary（旧地址 301）；
资产重定向到 release-assets.githubusercontent.com（原文示例 objects.githubusercontent.com 未观测到）。
最终允许主机列表仍是待确认项，下载实现（P2-02）前复核。
"""
from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

REPO = "PaperLibrary-Developers/PaperLibrary"
LAUNCHER = "PaperLibrary.exe"
VERSION_FILE = "version.json"
MANIFEST_NAME = "update-manifest.json"
TAG_RE = re.compile(r"^v(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\.(0|[1-9]\d*))?$")

# 资产 URL 允许的主机（清单里固定 github.com）；下载重定向实测落到 release-assets。
ALLOWED_DOWNLOAD_HOSTS = frozenset({"github.com", "release-assets.githubusercontent.com"})


def check_tag(tag: str) -> str:
    tag = str(tag or "").strip()
    if not TAG_RE.match(tag):
        raise ValueError("tag 必须是 v<major>.<minor> 形式（当前：%r）" % (tag,))
    return tag


def asset_name(tag: str) -> str:
    """完整包资产命名：PaperLibrary.<tag>.Nexus.zip（如 PaperLibrary.v1.6.Nexus.zip）。"""
    return "PaperLibrary.%s.Nexus.zip" % check_tag(tag)


def release_asset_url(tag: str, name: str = "", repo: str = REPO) -> str:
    tag = check_tag(tag)
    return "https://github.com/%s/releases/download/%s/%s" % (repo, tag, name or asset_name(tag))


def stable_manifest_url(repo: str = REPO) -> str:
    return "https://github.com/%s/releases/latest/download/%s" % (repo, MANIFEST_NAME)


def stable_asset_url(name: str, repo: str = REPO) -> str:
    return "https://github.com/%s/releases/latest/download/%s" % (repo, name)


def allowed_download_url(url: str) -> bool:
    """下载/重定向的目标是否在允许列表内（只允许 https + 固定主机）。"""
    try:
        parts = urlsplit(str(url or ""))
    except ValueError:
        return False
    return (parts.scheme == "https" and (parts.hostname or "").lower() in ALLOWED_DOWNLOAD_HOSTS)


def check_zip(path, expected_version: str = "", require_version_file: bool = False):
    """校验完整包 zip 的发布约束；返回 (problems, warnings)。

    P0-04 只强制：可正常读取、无顶层 data/、无绝对路径/.. 条目、含 PaperLibrary.exe；
    version.json 缺失或版本不符：新包按 problem，历史资产可作为 warning（require_version_file=False）。
    """
    problems: list[str] = []
    warnings: list[str] = []
    path = Path(path)
    if not path.is_file():
        return ["完整包不存在：" + str(path)], warnings
    try:
        with zipfile.ZipFile(path) as zf:
            bad = zf.testzip()
            if bad is not None:
                problems.append("zip 损坏（第一个坏条目：%s）" % bad)
            names = zf.namelist()
            version_text = None
            if VERSION_FILE in names:
                try:
                    version_text = zf.read(VERSION_FILE).decode("utf-8-sig")
                except (KeyError, OSError, UnicodeDecodeError) as exc:
                    problems.append("无法读取 zip 内 version.json：%s" % exc)
    except (zipfile.BadZipFile, OSError) as exc:
        return ["不是合法 zip：%s" % exc], warnings

    if not names:
        problems.append("zip 是空的")
        return problems, warnings
    data_entries = [name for name in names
                    if name == "data" or name.startswith("data/") or name.startswith("data\\")]
    if data_entries:
        problems.append("zip 含用户数据目录 data/（不得分发）：" + "、".join(data_entries[:5]))
    if LAUNCHER not in names:
        problems.append("zip 根目录缺少启动入口：" + LAUNCHER)
    unsafe = []
    for name in names:
        normalized = name.replace("\\", "/")
        if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in normalized.split("/"):
            unsafe.append(name)
    if unsafe:
        problems.append("zip 含绝对路径/上跳条目：" + "、".join(unsafe[:5]))

    if version_text is None:
        message = "zip 根目录缺少 %s（历史资产可能没有；新构建包必须包含）" % VERSION_FILE
        (problems if require_version_file else warnings).append(message)
    elif expected_version:
        try:
            packaged = json.loads(version_text)
            packaged_version = str(packaged.get("version") or "")
        except ValueError:
            problems.append("zip 内 version.json 不是合法 JSON")
        else:
            if packaged_version != expected_version:
                problems.append("zip 内 version.json 的 version=%r 与清单 version=%r 不一致"
                                % (packaged_version, expected_version))
    return problems, warnings
