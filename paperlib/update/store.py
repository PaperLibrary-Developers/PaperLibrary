"""更新目录与状态文件（阶段 2A / P2-01）。

目录结构（相对于数据目录；数据目录移到外部目录时结构不变）：

    <data>/updates/
      <version>/                     ← 每个目标版本一个目录，互不串包
        package.zip.part             ← 下载中的临时文件（支持续传）
        package.zip                  ← 下载完成并通过校验的完整包
        manifest.json                ← 本次检查/下载使用的已签名清单（原始字节）
        staging/                     ← 解压暂存区（P2-04；等价于原文的 staging/<version>/）
        download-state.json          ← 下载/校验/解压状态（原子写）
        ready.json                   ← staging 通过全部安全校验后才写入（P2-04）

设计约定：
- 版本号必须是 x.y.z（复用 paperlib.version.semver），并校验最终路径位于 updates/ 内，
  防止版本字符串被当成路径注入；
- 同一版本重新下载先 reset（清 part/zip/staging/ready/state），不会把旧包当新包；
- 切换到新版本时 prepare() 会清理其它版本目录（阶段 2A 没有 apply/回滚语义；
  阶段 2 若需要保留旧版本产物，应在该阶段显式调整本策略）；
- 状态文件只记录状态，不写用户数据；所有写操作走 util.write_json_atomic（临时文件 + 原子替换）。
"""
from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from paperlib import version as version_mod
from paperlib.util import ensure_dir, read_json, write_json_atomic

STATE_SCHEMA = 1
PHASES = ("idle", "checking", "downloading", "verifying", "verified", "extracting", "ready", "failed", "cancelled")


class UpdateStoreError(ValueError):
    """版本号非法或目标路径越出 updates/ 目录。"""


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def is_within(base: Path, path: Path) -> bool:
    """path 解析后是否位于 base 之内（Windows 大小写不敏感由 os.path.commonpath 处理）。"""
    base = Path(base).resolve()
    path = Path(path).resolve()
    try:
        return path == base or base in path.parents
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class UpdatePaths:
    version: str
    root: Path
    part: Path
    package: Path
    manifest: Path
    staging: Path
    state: Path
    ready: Path


def default_state(version: str) -> dict:
    return {
        "schema": STATE_SCHEMA,
        "version": version,
        "phase": "idle",
        "downloadedBytes": 0,
        "totalBytes": 0,
        "resumedFrom": 0,
        "sourceUrl": "",
        "sha256": "",
        "startedAt": "",
        "updatedAt": "",
        "error": "",
        "code": "",
    }


class UpdateStore:
    """data/updates 的目录与状态管理；不负责网络与解压。"""

    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.updates_dir = self.data_dir / "updates"

    # ---------- 路径 ----------

    def version_dir(self, version: str) -> Path:
        value = str(version or "").strip()
        try:
            version_mod.semver(value)
        except version_mod.VersionError as exc:
            raise UpdateStoreError("更新版本号不合法：" + str(exc))
        path = (self.updates_dir / value)
        if not is_within(self.updates_dir, path):
            raise UpdateStoreError("更新目录越界：" + str(path))
        return path

    def paths(self, version: str) -> UpdatePaths:
        root = self.version_dir(version)
        return UpdatePaths(
            version=str(version).strip(),
            root=root,
            part=root / "package.zip.part",
            package=root / "package.zip",
            manifest=root / "manifest.json",
            staging=root / "staging",
            state=root / "download-state.json",
            ready=root / "ready.json",
        )

    def ensure(self, version: str) -> UpdatePaths:
        paths = self.paths(version)
        ensure_dir(paths.root)
        return paths

    # ---------- 生命周期 ----------

    def reset(self, version: str) -> UpdatePaths:
        """清掉该版本的下载/暂存产物（保留版本目录本身）。"""
        paths = self.paths(version)
        for path in (paths.part, paths.package, paths.manifest, paths.state, paths.ready):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        if paths.staging.exists():
            shutil.rmtree(paths.staging, ignore_errors=True)
        return paths

    def prune_other_versions(self, keep_version: str) -> list[str]:
        """删除其它版本的更新目录（阶段 2A 策略：只保留当前目标版本）。"""
        keep = self.version_dir(keep_version).name
        removed: list[str] = []
        if not self.updates_dir.is_dir():
            return removed
        for child in sorted(self.updates_dir.iterdir()):
            if not child.is_dir() or child.name == keep:
                continue
            shutil.rmtree(child, ignore_errors=True)
            removed.append(child.name)
        return removed

    def prepare(self, version: str, prune: bool = True) -> UpdatePaths:
        """开始一个新版本的下载：清理该版本旧产物，并按策略清理其它版本。"""
        removed = self.prune_other_versions(version) if prune else []
        paths = self.reset(version)
        ensure_dir(paths.root)
        state = default_state(paths.version)
        state["phase"] = "downloading"
        state["startedAt"] = now_iso()
        state["updatedAt"] = state["startedAt"]
        self.write_state(paths.version, state)
        return paths

    # ---------- 状态文件 ----------

    def write_state(self, version: str, state: dict) -> dict:
        if not isinstance(state, dict):
            raise UpdateStoreError("下载状态必须是对象")
        phase = state.get("phase")
        if phase not in PHASES:
            raise UpdateStoreError("未知下载阶段：%r" % (phase,))
        merged = default_state(str(version).strip())
        merged.update({key: value for key, value in state.items() if key != "schema"})
        merged["schema"] = STATE_SCHEMA
        merged["version"] = str(version).strip()
        merged["updatedAt"] = merged.get("updatedAt") or now_iso()
        paths = self.paths(version)
        write_json_atomic(paths.state, merged)
        return merged

    def update_state(self, version: str, **patch) -> dict:
        state = self.read_state(version)
        state.update(patch)
        state["updatedAt"] = now_iso()
        return self.write_state(version, state)

    def read_state(self, version: str) -> dict:
        paths = self.paths(version)
        data = read_json(paths.state, None)
        if not isinstance(data, dict) or data.get("version") != str(version).strip():
            return default_state(str(version).strip())
        merged = default_state(str(version).strip())
        merged.update({key: value for key, value in data.items() if key != "schema"})
        merged["schema"] = STATE_SCHEMA
        return merged

    # ---------- 清单与 ready 标记 ----------

    def write_manifest(self, version: str, raw: bytes) -> Path:
        paths = self.ensure(version)
        paths.manifest.write_bytes(bytes(raw))
        return paths.manifest

    def read_manifest(self, version: str) -> bytes:
        paths = self.paths(version)
        try:
            return paths.manifest.read_bytes()
        except OSError:
            return b""

    def write_ready(self, version: str, info: dict) -> dict:
        payload = dict(info or {})
        payload.update({
            "schema": STATE_SCHEMA,
            "version": str(version).strip(),
            "createdAt": payload.get("createdAt") or now_iso(),
        })
        paths = self.ensure(version)
        write_json_atomic(paths.ready, payload)
        return payload

    def read_ready(self, version: str) -> dict:
        data = read_json(self.paths(version).ready, None)
        return data if isinstance(data, dict) else {}

    def clear_ready(self, version: str) -> None:
        try:
            self.paths(version).ready.unlink(missing_ok=True)
        except OSError:
            pass

    # ---------- apply.json（安装标记，供启动器在退出时读取） ----------

    @property
    def apply_path(self) -> Path:
        return self.updates_dir / "apply.json"

    def read_apply(self) -> dict:
        data = read_json(self.apply_path, None)
        return data if isinstance(data, dict) else {}

    def write_apply(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise UpdateStoreError("apply.json 内容必须是对象")
        merged = dict(payload)
        merged["schema"] = STATE_SCHEMA
        merged["createdAt"] = merged.get("createdAt") or now_iso()
        ensure_dir(self.updates_dir)
        write_json_atomic(self.apply_path, merged)
        return merged

    def clear_apply(self) -> None:
        try:
            self.apply_path.unlink(missing_ok=True)
        except OSError:
            pass

    def package_size(self, version: str) -> int:
        try:
            return self.paths(version).package.stat().st_size
        except OSError:
            return 0
