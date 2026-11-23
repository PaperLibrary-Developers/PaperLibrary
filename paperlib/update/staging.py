"""解压到 staging 与安全校验（阶段 2A / P2-04）。

安全边界（原文 §5.3 / §9）：
- 拒绝绝对路径、盘符、UNC、@..@ 与路径穿越；拒绝符号链接（Unix mode 位）；
- 限制文件数 / 单文件大小 / 解压总量 / 压缩比（zip bomb）；
- 包内不得含顶层 @data/@；必须含 @PaperLibrary.exe@ 与 @version.json@，且 version 与清单一致；
- 解压到本版本 @staging/@（干净目录），先校验全部条目再写盘；写入时再次做路径包含检查；
- 全部通过后写 @ready.json@ 并置 @phase=ready@；任一失败删除 staging（及旧 ready）并置 failed。

只在 staging 内操作：不覆盖当前程序目录、不碰用户数据。
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

from paperlib.update import manifest as manifest_mod
from paperlib.update import signing
from paperlib.update import store as store_mod

LAUNCHER = "PaperLibrary.exe"
VERSION_FILE = "version.json"
COPY_CHUNK = 1024 * 1024
DRIVE_RE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ZipLimits:
    max_files: int = 5000
    max_file_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 1024 * 1024 * 1024
    max_compression_ratio: int = 200
    ratio_check_min_bytes: int = 20 * 1024 * 1024


DEFAULT_LIMITS = ZipLimits()


class StagingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _normalize(name: str) -> str:
    return str(name or "").replace("\\", "/")


def _unsafe_reason(name: str) -> str:
    raw = _normalize(name)
    if not raw:
        return "空条目名"
    if raw.startswith("/") or raw.startswith("//"):
        return "绝对路径"
    if DRIVE_RE.match(raw):
        return "盘符路径"
    parts = [part for part in raw.split("/") if part not in ("", ".",)]
    if any(part == ".." for part in raw.split("/")):
        return "路径上跳（..）"
    if any(part == ".." for part in parts):
        return "路径上跳（..）"
    return ""


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return (mode & 0o170000) == 0o120000


def inspect_entries(infolist, limits: ZipLimits = DEFAULT_LIMITS) -> dict:
    """在写盘前检查条目；返回 {files, dirs, totalBytes, compressedBytes}。"""
    files = 0
    dirs = 0
    total = 0
    compressed = 0
    for info in infolist:
        name = info.filename
        reason = _unsafe_reason(name)
        if reason:
            raise StagingError("unsafe-path", "zip 条目路径不安全（%s）：%r" % (reason, name))
        if _is_symlink(info):
            raise StagingError("symlink", "zip 含符号链接，拒绝解压：%r" % (name,))
        if info.is_dir():
            dirs += 1
            continue
        files += 1
        total += max(0, int(info.file_size))
        compressed += max(0, int(info.compress_size))
        if files > limits.max_files:
            raise StagingError("too-many-files", "zip 文件数超过上限 %d" % limits.max_files)
        if info.file_size > limits.max_file_bytes:
            raise StagingError("too-large", "zip 单文件超过上限：%s（%d 字节）"
                               % (name, info.file_size))
        if total > limits.max_total_bytes:
            raise StagingError("too-large", "zip 解压总量超过上限 %d 字节"
                               % limits.max_total_bytes)
        if total >= limits.ratio_check_min_bytes and compressed > 0:
            ratio = total / float(compressed)
            if ratio > limits.max_compression_ratio:
                raise StagingError("zip-bomb",
                                   "zip 压缩比异常（%.0f:1，上限 %d:1）"
                                   % (ratio, limits.max_compression_ratio))
    return {"files": files, "dirs": dirs, "totalBytes": total, "compressedBytes": compressed}


def _safe_target(staging: Path, name: str) -> Path:
    rel = _normalize(name).lstrip("/")
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    target = staging.joinpath(*parts)
    base = staging.resolve()
    resolved = target.resolve()
    if resolved != base and base not in resolved.parents:
        raise StagingError("unsafe-path", "解压目标越界：%r" % (name,))
    return target


def _cleanup(store: store_mod.UpdateStore, paths) -> None:
    shutil.rmtree(paths.staging, ignore_errors=True)
    store.clear_ready(paths.version)


def extract_package(store: store_mod.UpdateStore, version: str, public_key=None,
                    limits: ZipLimits = DEFAULT_LIMITS) -> dict:
    """把已校验的 package.zip 解压到 staging 并做全部安全校验，成功写 ready.json。"""
    paths = store.paths(version)
    store.update_state(version, phase="extracting", error="", code="")

    def fail(code: str, message: str):
        _cleanup(store, paths)
        store.update_state(version, phase="failed", error=message, code=code)
        raise StagingError(code, message)

    raw = store.read_manifest(version)
    if not raw:
        fail("manifest-missing", "缺少本次下载使用的清单，无法校验 staging")
    try:
        data = manifest_mod.load_bytes(raw)
    except manifest_mod.ManifestError as exc:
        fail("invalid-manifest", "清单无效：" + str(exc))
    try:
        signing.verify_manifest(data, public_key)
    except signing.ManifestSignatureError as exc:
        fail("signature", "清单验签失败：" + str(exc))
    if data["version"] != version:
        fail("version-mismatch", "清单版本 %s 与目标版本 %s 不一致"
             % (data["version"], version))
    if not paths.package.is_file():
        fail("missing-package", "缺少已校验的 package.zip")
    if paths.package.stat().st_size != data["assets"]["full"]["size"]:
        fail("size-mismatch", "package.zip 大小与清单不一致")

    # 干净 staging：重试不会把旧文件混进来
    _cleanup(store, paths)
    try:
        paths.staging.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fail("disk-write", "无法创建 staging 目录：%s" % exc)

    try:
        with zipfile.ZipFile(paths.package) as zf:
            infos = zf.infolist()
            try:
                stats = inspect_entries(infos, limits)
            except StagingError as exc:
                fail(exc.code, exc.message)
            names = {_normalize(info.filename) for info in infos}
            if any(name == "data" or name.startswith("data/") for name in names):
                fail("data-directory", "完整包含顶层 data/（用户数据不得随包分发）")
            if LAUNCHER not in names:
                fail("missing-exe", "完整包根目录缺少 %s" % LAUNCHER)
            if VERSION_FILE not in names:
                fail("missing-version", "完整包根目录缺少 %s" % VERSION_FILE)

            written_total = 0
            for info in infos:
                target = _safe_target(paths.staging, info.filename)
                if info.is_dir():
                    try:
                        target.mkdir(parents=True, exist_ok=True)
                    except OSError as exc:
                        fail("disk-write", "创建目录失败：%s" % exc)
                    continue
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as source, open(target, "wb") as dest:
                        while True:
                            chunk = source.read(COPY_CHUNK)
                            if not chunk:
                                break
                            written_total += len(chunk)
                            if written_total > limits.max_total_bytes:
                                fail("too-large", "解压总量超过上限 %d 字节"
                                     % limits.max_total_bytes)
                            dest.write(chunk)
                except StagingError as exc:
                    fail(exc.code, exc.message)
                except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
                    fail("extract-failed", "解压失败：%s（%r）" % (exc, info.filename))

            # 包内 version.json 必须存在且与清单一致
            version_path = paths.staging / VERSION_FILE
            try:
                packaged = json.loads(version_path.read_text(encoding="utf-8-sig"))
            except (OSError, ValueError) as exc:
                fail("invalid-version", "包内 version.json 无法解析：%s" % exc)
            packaged_version = str(packaged.get("version") or "")
            if packaged_version != data["version"]:
                fail("version-mismatch",
                     "包内 version.json 版本 %r 与清单 %r 不一致"
                     % (packaged_version, data["version"]))
            if not (paths.staging / LAUNCHER).is_file():
                fail("missing-exe", "staging 中 %s 不是文件" % LAUNCHER)
    except zipfile.BadZipFile as exc:
        fail("invalid-zip", "完整包不是合法 zip：%s" % exc)

    ready = store.write_ready(version, {
        "tag": data["tag"],
        "stagingPath": str(paths.staging.resolve()),
        "files": stats["files"],
        "bytes": written_total,
        "manifestSha256": hashlib.sha256(raw).hexdigest(),
        "packageSha256": store.read_state(version).get("sha256", ""),
    })
    store.update_state(version, phase="ready", downloadedBytes=written_total,
                       totalBytes=written_total, error="", code="")
    return ready
