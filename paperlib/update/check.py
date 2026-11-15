"""GET /api/update/check 的只读检查逻辑（阶段 1）。

只做：拉清单 → 结构校验 → Ed25519 验签 → arch/Windows 版本检查 → semver 比较。
不做：下载安装包、校验文件哈希、解压、安装、回滚（阶段 2）。

结果约定：
- 200 且 ok=true：available/reason 表示“有无更新”；
  reason: newer / current / older（拒绝降级）/ unsupported-win / unsupported-arch；
- 拉取/清单/签名失败抛 FetchError 或 CheckError（API 层映射为 502 + code）；
- 当前 version.json 损坏抛 VersionError（API 层映射为 500）。
"""
from __future__ import annotations

import os
import platform
import re
import time

from paperlib import version as version_mod
from paperlib.update import fetch as fetch_mod
from paperlib.update import keys, manifest, signing

_VERSION_TEXT = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


class CheckError(Exception):
    """清单无效/签名失败/版本不可比等，code 供 API 层返回可诊断错误。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def local_windows_version() -> str:
    """本机 Windows 版本（如 10.0.22631）；非 Windows 返回空串（开发/测试环境不做拦截）。"""
    if os.name != "nt":
        return ""
    try:
        raw = platform.version() or ""
    except (OSError, ValueError):
        return ""
    match = _VERSION_TEXT.match(raw)
    return match.group(0) if match else ""


def local_arch() -> str:
    machine = (platform.machine() or "").lower()
    if machine in ("amd64", "x86_64", "x64"):
        return "x64"
    return machine or "unknown"


def version_tuple(value: str):
    try:
        return version_mod.semver(value)
    except version_mod.VersionError:
        return None


def compare_versions(latest: str, current: str) -> int:
    """semver 比较：>0 表示 latest 更新；不支持降级时由调用方处理。"""
    left, right = version_tuple(latest), version_tuple(current)
    if left is None:
        raise CheckError("invalid-version", "清单 version 不是合法 semver：%r" % (latest,))
    if right is None:
        raise CheckError("invalid-version", "当前 version 不是合法 semver：%r" % (current,))
    return (left > right) - (left < right)


class UpdateChecker:
    """一次检查的完整流程；依赖可注入，便于离线测试与后续阶段复用。"""

    def __init__(self, root, fetch_manifest=None, public_key=None,
                 platform_version=None, arch=None, now=None, manifest_url=""):
        self.root = root
        self.manifest_url = manifest_url
        self._fetch = fetch_manifest
        self.public_key = public_key if public_key is not None else keys.public_key()
        self.platform_version = platform_version
        self.arch = arch
        self.now = now or time.time

    def _checked_at(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.now()))

    def check(self) -> dict:
        current = version_mod.load(self.root)  # VersionError 由 API 层转 500
        raw = self._fetch() if self._fetch is not None else fetch_mod.fetch_manifest(self.manifest_url)
        return self._evaluate(current, raw)

    def _evaluate(self, current: dict, raw: bytes) -> dict:
        try:
            data = manifest.load_bytes(raw)
        except manifest.ManifestError as exc:
            raise CheckError("invalid-manifest", "更新清单无效：" + str(exc))
        try:
            signing.verify_manifest(data, self.public_key)
        except signing.ManifestSignatureError as exc:
            raise CheckError("signature", str(exc))

        latest = {
            "version": data["version"],
            "tag": data["tag"],
            "publishedAt": data["publishedAt"],
            "size": data["assets"]["full"]["size"],
            "notesUrl": data["releaseNotesUrl"],
        }
        result = {
            "ok": True,
            "current": {"version": current["version"], "tag": current["tag"]},
            "latest": latest,
            "checkedAt": self._checked_at(),
        }

        arch = self.arch if self.arch is not None else local_arch()
        if arch != manifest.ARCH:
            result.update({"available": False, "reason": "unsupported-arch",
                           "message": "当前架构 %s，发布包仅支持 %s" % (arch, manifest.ARCH)})
            return result

        local_win = self.platform_version if self.platform_version is not None else local_windows_version()
        minimum = data["minWindows"]
        if local_win and version_tuple(local_win) is not None and version_tuple(minimum) is not None \
                and version_tuple(local_win) < version_tuple(minimum):
            result.update({"available": False, "reason": "unsupported-win",
                           "message": "当前 Windows 版本 %s 低于最低要求 %s" % (local_win, minimum)})
            return result

        order = compare_versions(data["version"], current["version"])
        if order > 0:
            result.update({"available": True, "reason": "newer",
                           "message": "发现新版本 %s" % data["tag"]})
        elif order == 0:
            result.update({"available": False, "reason": "current", "message": "已是最新版本"})
        else:
            result.update({"available": False, "reason": "older",
                           "message": "更新源版本 %s 低于当前 %s，已忽略（不降级）"
                                      % (data["tag"], current["tag"])})
        return result
