"""包外 Updater Helper 的部署（阶段 2E-1 / P2-12）。

Helper 是单文件 C# 可执行程序，随发布包放在 tools/updater/PaperLibraryUpdater.exe
（**不在包根**），由应用复制到 %LOCALAPPDATA%/PaperLibrary/updater/<version>/ 后从
包外运行。这样安装期间程序目录被整体替换时，正在执行替换的 helper 自身不会被覆盖。

为什么按版本目录部署：
- 运行中的 exe 在 Windows 上不能被覆盖/替换；新 helper 写入新版本目录，旧 helper
  即使仍在运行也不受影响（helper 自身可升级）；
- current.json 指向当前应启动的版本，由启动器/后端读取；
- 旧版本目录按保留策略清理，默认保留当前和上一个版本。

本模块只负责“把包内 helper 安全部署到包外”，不负责启动 helper（P2-10/P2-11 接入
启动器），也不负责安装替换/回滚（P2-13~P2-17）。所有路径与哈希在部署前校验；
部署失败不会留下半个可执行文件（临时文件 + 原子替换）。
"""
from __future__ import annotations

import hashlib
import os
import shutil
import time
from pathlib import Path

from paperlib import version as version_mod
from paperlib.update.store import is_within
from paperlib.util import ensure_dir, read_json, write_json_atomic

HELPER_SCHEMA = 1
PRODUCT = "PaperLibrary"
HELPER_DIR_REL = ("tools", "updater")
HELPER_EXE = "PaperLibraryUpdater.exe"
HELPER_META = "helper.json"
CURRENT_FILE = "current.json"
DEFAULT_KEEP = 2


class HelperError(ValueError):
    """helper 包/部署元数据不合法；code 供调用方与测试断言。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def updater_home(env=None) -> Path:
    """包外 helper 根目录；测试可用 PAPERLIB_UPDATER_HOME 覆盖。"""
    env = os.environ if env is None else env
    override = str(env.get("PAPERLIB_UPDATER_HOME") or "").strip()
    if override:
        return Path(override)
    local = str(env.get("LOCALAPPDATA") or "").strip()
    if not local:
        profile = str(env.get("USERPROFILE") or "").strip()
        if profile:
            local = str(Path(profile) / "AppData" / "Local")
    if not local:
        raise HelperError("no-local-appdata", "无法确定 %LOCALAPPDATA%，不能部署更新助手")
    return Path(local) / "PaperLibrary" / "updater"


def package_helper_dir(package_root) -> Path:
    return Path(package_root).joinpath(*HELPER_DIR_REL)


def read_package_helper(package_root) -> dict:
    """校验并返回包内 helper 的信息（版本、tag、字节数、sha256、绝对路径）。

    拒绝：缺少 exe/helper.json、schema/product/version/tag 不符、大小或 SHA-256 与
    helper.json 不一致、helper 位于包根（必须位于 tools/updater/）。
    """
    root = Path(package_root)
    if not root.is_dir():
        raise HelperError("package-missing", "发布包目录不存在：%s" % root)

    helper_dir = package_helper_dir(root)
    exe_path = helper_dir / HELPER_EXE
    meta_path = helper_dir / HELPER_META
    if not exe_path.is_file():
        raise HelperError("helper-missing", "发布包缺少更新助手：%s" % exe_path)
    if not meta_path.is_file():
        raise HelperError("helper-meta-missing", "发布包缺少更新助手元数据：%s" % meta_path)

    meta = read_json(meta_path, None)
    if not isinstance(meta, dict):
        raise HelperError("helper-meta-invalid", "helper.json 不是合法 JSON 对象：%s" % meta_path)
    if meta.get("schema") != HELPER_SCHEMA:
        raise HelperError("helper-meta-invalid", "helper.json schema 必须是 %d" % HELPER_SCHEMA)
    if meta.get("product") != PRODUCT:
        raise HelperError("helper-meta-invalid", "helper.json product 必须是 %s" % PRODUCT)
    version = str(meta.get("version") or "").strip()
    try:
        version_mod.semver(version)
    except version_mod.VersionError as exc:
        raise HelperError("helper-invalid-version", "helper.json version 不合法：%s" % exc)
    tag = str(meta.get("tag") or "").strip()
    expected_tag = version_mod.ui_label(version)
    if tag != expected_tag:
        raise HelperError("helper-meta-invalid",
                          "helper.json tag 必须是 %s（当前：%r）" % (expected_tag, tag))
    if meta.get("exe") != HELPER_EXE:
        raise HelperError("helper-meta-invalid", "helper.json exe 必须是 %s" % HELPER_EXE)
    expected_sha = str(meta.get("sha256") or "").strip().lower()
    if len(expected_sha) != 64 or any(ch not in "0123456789abcdef" for ch in expected_sha):
        raise HelperError("helper-meta-invalid", "helper.json sha256 必须是 64 位十六进制")
    expected_bytes = meta.get("bytes")
    if not isinstance(expected_bytes, int) or expected_bytes <= 0:
        raise HelperError("helper-meta-invalid", "helper.json bytes 必须是正整数")

    if (root / HELPER_EXE).exists():
        raise HelperError("helper-in-package-root",
                          "更新助手不得位于包根：%s" % (root / HELPER_EXE))

    actual_bytes = exe_path.stat().st_size
    if actual_bytes != expected_bytes:
        raise HelperError("helper-size",
                          "更新助手大小与 helper.json 不一致：%d != %d"
                          % (actual_bytes, expected_bytes))
    actual_sha = sha256_file(exe_path)
    if actual_sha != expected_sha:
        raise HelperError("helper-sha256",
                          "更新助手 SHA-256 与 helper.json 不一致：%s != %s"
                          % (actual_sha, expected_sha))
    return {
        "version": version,
        "tag": tag,
        "bytes": actual_bytes,
        "sha256": actual_sha,
        "path": str(exe_path),
        "meta": meta,
        "packageRoot": str(root),
    }


def read_current(home=None) -> dict:
    """读 current.json；缺失/损坏/字段不符时返回空 dict（不抛错）。"""
    home = Path(home) if home is not None else updater_home()
    data = read_json(home / CURRENT_FILE, None)
    if not isinstance(data, dict) or data.get("schema") != HELPER_SCHEMA:
        return {}
    if data.get("product") != PRODUCT:
        return {}
    version = str(data.get("version") or "").strip()
    try:
        version_mod.semver(version)
    except version_mod.VersionError:
        return {}
    exe_rel = data.get("exe")
    if not isinstance(exe_rel, str) or not exe_rel.strip() or Path(exe_rel).anchor or ".." in exe_rel:
        return {}
    return data


def deployed_helper(home=None) -> dict:
    """当前可用的包外 helper；指向的文件不存在或哈希不符时返回空 dict。"""
    home = Path(home) if home is not None else updater_home()
    data = read_current(home)
    if not data:
        return {}
    path = _verified_helper_path(home, data)
    if path is None:
        return {}
    return dict(data, helperPath=str(path))


def _verified_helper_path(home: Path, record: dict) -> Path | None:
    path = home / record["exe"]
    if path.name == HELPER_EXE and is_within(home, path) and path.is_file():
        try:
            if sha256_file(path) == record.get("sha256"):
                return path
        except OSError:
            pass
    return None


def deploy(package_root, home=None, keep: int = DEFAULT_KEEP, *, preserve_newer: bool = False) -> dict:
    """把包内 helper 部署到包外版本目录，并原子更新 current.json。

    幂等：当前版本与哈希一致时不再复制，返回 deployed=False。
    升级：新版本写入 <home>/<version>/，旧版本继续保留（previousVersion），
    运行中的旧 helper 不受影响。
    preserve_newer：旧版桌面程序启动时保留已通过校验的新版 helper。
    """
    source = read_package_helper(package_root)
    home = Path(home) if home is not None else updater_home()
    ensure_dir(home)

    version = source["version"]
    current = read_current(home)
    if current:
        current_version = current["version"]
        same = current_version == version and current.get("sha256") == source["sha256"]
        newer = preserve_newer and version_mod.semver(current_version) > version_mod.semver(version)
        existing = _verified_helper_path(home, current) if same or newer else None
        if existing is not None:
            return {
                "deployed": False,
                "reason": "newer-current" if newer else "current",
                "version": current_version,
                "tag": version_mod.ui_label(current_version),
                "sha256": current["sha256"],
                "bytes": existing.stat().st_size,
                "helperPath": str(existing),
                "updaterHome": str(home),
                "previousVersion": str(current.get("previousVersion") or ""),
            }

    target_dir = home / version
    if not is_within(home, target_dir):
        raise HelperError("unsafe-version", "helper 版本目录越界：%s" % target_dir)
    ensure_dir(target_dir)

    target_exe = target_dir / HELPER_EXE
    temp_exe = target_dir / (HELPER_EXE + ".tmp-" + str(os.getpid()))
    try:
        shutil.copyfile(source["path"], temp_exe)
        if temp_exe.stat().st_size != source["bytes"] or sha256_file(temp_exe) != source["sha256"]:
            raise HelperError("helper-copy-verify", "复制后的 helper 校验失败：%s" % temp_exe)
        os.replace(temp_exe, target_exe)
    finally:
        try:
            temp_exe.unlink(missing_ok=True)
        except OSError:
            pass

    meta_path = target_dir / HELPER_META
    meta_temp = target_dir / (HELPER_META + ".tmp-" + str(os.getpid()))
    try:
        shutil.copyfile(package_helper_dir(package_root) / HELPER_META, meta_temp)
        os.replace(meta_temp, meta_path)
    finally:
        try:
            meta_temp.unlink(missing_ok=True)
        except OSError:
            pass

    previous = str(current.get("version") or "") if current else ""
    record = {
        "schema": HELPER_SCHEMA,
        "product": PRODUCT,
        "version": version,
        "tag": source["tag"],
        "exe": version + "/" + HELPER_EXE,
        "meta": version + "/" + HELPER_META,
        "sha256": source["sha256"],
        "bytes": source["bytes"],
        "deployedAt": _now(),
        "previousVersion": previous,
        "source": "/".join(HELPER_DIR_REL),
    }
    write_json_atomic(home / CURRENT_FILE, record)
    _prune_versions(home, keep, protected=[version, previous])
    return {
        "deployed": True,
        "reason": "deployed",
        "version": version,
        "tag": source["tag"],
        "sha256": source["sha256"],
        "bytes": source["bytes"],
        "helperPath": str(target_exe),
        "updaterHome": str(home),
        "previousVersion": previous,
    }


def _prune_versions(home: Path, keep: int, protected) -> None:
    """只保留最近的 keep 个版本目录；当前版本与上一版本始终保留。"""
    try:
        children = list(home.iterdir())
    except OSError:
        return
    versions = []
    for child in children:
        if not child.is_dir():
            continue
        try:
            version_mod.semver(child.name)
        except version_mod.VersionError:
            continue
        versions.append(child)
    versions.sort(key=lambda path: version_mod.semver(path.name), reverse=True)

    keep_names = []
    for name in protected:
        if name and name not in keep_names:
            keep_names.append(name)
    limit = max(1, int(keep))
    for child in versions:
        if len(keep_names) >= limit:
            break
        if child.name not in keep_names:
            keep_names.append(child.name)
    for child in versions:
        if child.name in keep_names:
            continue
        shutil.rmtree(child, ignore_errors=True)
