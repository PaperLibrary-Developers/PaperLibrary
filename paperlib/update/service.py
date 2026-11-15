"""更新流程服务（阶段 2B / P2-05 起）：检查 → 下载 → 校验 → staging 的单任务编排。

约束：
- 同一时间只允许一个下载任务：重复 POST /api/update/download 返回当前状态，不会并发重复下载；
- 下载前先做一次完整检查（拉取并验签清单），拿到“可信清单”后再启动后台线程；
  不信任客户端传入的 URL/版本，也不重复请求清单；
- 进度/阶段/错误写入 data/updates/<version>/download-state.json；status 端点读内存快照（带锁），
  与后台线程的写入无竞态；
- 失败可重试：再次调用 download 会复用同一版本的 .part（阶段 2A 的 Range 续传）；
- 服务重启时若发现上次停在 downloading/verifying/extracting，标记 failed/interrupted（.part 保留可续传）；
- 已有该版本可用 staging（ready.json + staging 目录）时，download 直接返回 ready，不重复下载。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path

from paperlib import version as version_mod
from paperlib.update import check as check_mod
from paperlib.update import download as download_mod
from paperlib.update import manifest as manifest_mod
from paperlib.update import signing
from paperlib.update import staging as staging_mod
from paperlib.update import store as store_mod
from paperlib.update import verify as verify_mod

ACTIVE_PHASES = ("checking", "downloading", "verifying", "extracting")


class UpdateServiceError(Exception):
    """可预期的业务错误；status 供 API 层映射 HTTP 状态码。"""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class NoUpdateError(UpdateServiceError):
    def __init__(self, code: str = "no-update", message: str = "当前已是最新版本，没有可下载的更新"):
        super().__init__(code, message, status=400)


class UpdateService:
    def __init__(self, root, data_dir, fetch_manifest=None, public_key=None,
                 downloader=None, verifier=None, extractor=None, now=None):
        self.root = Path(root)
        self.data_dir = Path(data_dir)
        self.store = store_mod.UpdateStore(self.data_dir)
        self.public_key = public_key
        self.checker = check_mod.UpdateChecker(
            self.root, fetch_manifest=fetch_manifest, public_key=public_key)
        self._downloader = downloader or download_mod.download_package
        self._verifier = verifier or verify_mod.verify_download
        self._extractor = extractor or staging_mod.extract_package
        self._now = now or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))

        self._lock = threading.RLock()
        self._thread = None
        self._active = False
        self._phase = "idle"
        self._version = ""
        self._state = store_mod.default_state("")
        self._ready: dict = {}
        self._last_check_at = ""
        self._cancel_event = None
        self._load_persisted()

    # ---------- 持久化状态恢复 ----------

    def _load_persisted(self) -> None:
        try:
            dirs = [child for child in self.store.updates_dir.iterdir() if child.is_dir()]
        except OSError:
            return
        ready_dirs = []
        state_dirs = []
        for child in dirs:
            try:
                version_mod.semver(child.name)
            except version_mod.VersionError:
                continue
            if (child / "ready.json").is_file() and (child / "staging").is_dir():
                ready_dirs.append(child)
            if (child / "download-state.json").is_file():
                state_dirs.append(child)
        if ready_dirs:
            latest = max(ready_dirs, key=lambda path: path.stat().st_mtime)
            ready = self.store.read_ready(latest.name)
            if ready:
                self._version = latest.name
                self._phase = "ready"
                self._ready = ready
                self._state = self.store.read_state(latest.name)
                return
        if state_dirs:
            latest = max(state_dirs, key=lambda path: path.stat().st_mtime)
            state = self.store.read_state(latest.name)
            self._version = latest.name
            self._state = state
            phase = state.get("phase") or "idle"
            if phase in ACTIVE_PHASES:
                state = self.store.update_state(
                    latest.name, phase="failed", code="interrupted",
                    error="服务重启，下载/校验/解压已中断；重新下载会从已下载部分续传")
                phase = "failed"
            self._state = state
            self._phase = phase

    # ---------- 检查 ----------

    def check(self) -> dict:
        return self.checker.check()

    # ---------- 状态 ----------

    def _snapshot_locked(self, started=None) -> dict:
        state = dict(self._state or {})
        ready = dict(self._ready or {})
        phase = "ready" if ready else (state.get("phase") or self._phase or "idle")
        running = bool(self._active or (self._thread is not None and self._thread.is_alive()))
        version = self._version or ready.get("version") or ""
        done = int(state.get("downloadedBytes") or 0)
        total = int(state.get("totalBytes") or 0)
        percent = 0
        if total > 0:
            percent = int(round(done * 100.0 / total))
        percent = max(0, min(100, percent))
        error = "" if ready else str(state.get("error") or "")
        code = "" if ready else str(state.get("code") or "")
        if phase not in ("failed", "cancelled"):
            error, code = "", ""
        snapshot = {
            "ok": True,
            "phase": phase,
            "running": running,
            "version": version,
            "progress": {
                "downloadedBytes": done,
                "totalBytes": total,
                "percent": percent,
            },
            "ready": bool(ready),
            "stagingVersion": version if ready else "",
            "stagingPath": str(ready.get("stagingPath") or "") if ready else "",
            "error": error,
            "code": code,
            "updatedAt": str(state.get("updatedAt") or ""),
            "lastCheckAt": self._last_check_at,
        }
        if started is not None:
            snapshot["started"] = bool(started)
        return snapshot

    def snapshot(self) -> dict:
        with self._lock:
            return self._snapshot_locked()

    # ---------- 下载 ----------

    def start_download(self) -> dict:
        with self._lock:
            if self._active or (self._thread is not None and self._thread.is_alive()):
                return self._snapshot_locked(started=False)
            self._active = True
            self._phase = "checking"
            checking = dict(self._state or store_mod.default_state(self._version))
            checking.update({"phase": "checking", "error": "", "code": ""})
            self._state = checking

        try:
            result, data, raw = self.checker.check_with_manifest()
        except Exception:
            with self._lock:
                self._active = False
                self._phase = "idle"
                self._state = store_mod.default_state(self._version)
            raise
        self._last_check_at = self._now()

        if not result.get("available"):
            with self._lock:
                self._active = False
                self._phase = "idle"
                self._state = store_mod.default_state(self._version)
            raise NoUpdateError(result.get("reason") or "no-update",
                                result.get("message") or "当前已是最新版本，没有可下载的更新")

        version = str(data["version"])
        asset = data["assets"]["full"]
        with self._lock:
            if self._active is False:  # 检查期间被其它调用抢先启动/停止（防御）
                return self._snapshot_locked(started=False)
            paths = self.store.paths(version)
            ready = self.store.read_ready(version)
            if ready and paths.staging.is_dir():
                self._ready = ready
                self._version = version
                self._phase = "ready"
                self._state = self.store.read_state(version)
                self._active = False
                return self._snapshot_locked(started=False)

            # 开始新的下载前清掉旧的安装标记，避免启动器读到过期的 apply.json
            self.store.clear_apply()
            same_version_part = (self._version == version and paths.part.is_file())
            if same_version_part:
                self.store.ensure(version)
                self.store.update_state(version, phase="downloading", error="", code="")
            else:
                self.store.prepare(version)
            self.store.write_manifest(version, raw)
            self._version = version
            self._phase = "downloading"
            self._state = self.store.read_state(version)
            self._cancel_event = threading.Event()
            cancel_event = self._cancel_event
            self._thread = threading.Thread(target=self._run_pipeline,
                                            args=(version, asset, cancel_event),
                                            name="paperlib-update-download", daemon=True)
            self._thread.start()
            return self._snapshot_locked(started=True)

    def _run_pipeline(self, version: str, asset: dict, cancel_event=None) -> None:
        try:
            with self._lock:
                self._phase = "downloading"
                self._state = self.store.read_state(version)
            self._downloader(self.store, version, asset, cancel_event=cancel_event)
            with self._lock:
                self._phase = "verifying"
                self._state = self.store.read_state(version)
            self._verifier(self.store, version, public_key=self.public_key)
            with self._lock:
                self._phase = "extracting"
                self._state = self.store.read_state(version)
            ready = self._extractor(self.store, version, public_key=self.public_key)
            with self._lock:
                self._ready = ready
                self._phase = "ready"
                self._state = self.store.read_state(version)
        except download_mod.DownloadCancelled:
            # 方案 B：取消即删；文件已由下载层/这里删除，状态只保留在内存（重启后回到 idle）
            self.store.discard(version)
            state = store_mod.default_state(version)
            state.update({"phase": "cancelled", "code": "cancelled",
                          "error": "已取消下载，临时文件已清理"})
            with self._lock:
                self._phase = "cancelled"
                self._state = state
                self._ready = {}
        except Exception as exc:  # noqa: BLE001 - 失败原因优先由各阶段写入，缺省再兜底
            code = str(getattr(exc, "code", "error") or "error")
            message = str(getattr(exc, "message", "") or exc)
            try:
                state = self.store.update_state(version, phase="failed",
                                                code=code, error=message)
                if not state.get("error"):
                    state = self.store.update_state(version, phase="failed",
                                                    code=code, error=message or "更新失败")
            except Exception:  # noqa: BLE001
                state = self.store.read_state(version)
            with self._lock:
                self._phase = "failed"
                self._state = state
        finally:
            with self._lock:
                self._active = False
                self._thread = None

    # ---------- 应用（写 apply.json，供启动器退出时安装；本步不执行替换） ----------

    def _discover_ready(self):
        """从磁盘找一份可用的 staging（服务重启后内存快照为空时）。"""
        try:
            dirs = [child for child in self.store.updates_dir.iterdir() if child.is_dir()]
        except OSError:
            return "", {}
        candidates = []
        for child in dirs:
            try:
                version_mod.semver(child.name)
            except version_mod.VersionError:
                continue
            ready = self.store.read_ready(child.name)
            if ready and (child / "staging").is_dir():
                candidates.append((child.stat().st_mtime, child.name, ready))
        if not candidates:
            return "", {}
        _mtime, version, ready = max(candidates, key=lambda item: item[0])
        return version, ready

    def cancel_download(self, timeout: float = 30.0) -> dict:
        """取消当前下载（方案 B：取消即删）；返回取消后的状态快照。"""
        with self._lock:
            event = self._cancel_event
            thread = self._thread
            active = self._active
            if event is None or not active:
                return self._snapshot_locked()
            event.set()
        if thread is not None:
            thread.join(timeout)
        return self.snapshot()

    def apply(self) -> dict:
        """确认 staging 完整后写 data/updates/apply.json（幂等）。"""
        with self._lock:
            if self._active or (self._thread is not None and self._thread.is_alive()):
                raise UpdateServiceError("busy", "更新任务正在进行，请稍后再应用", 409)
            version = str((self._ready or {}).get("version") or "")
            ready_disk = self.store.read_ready(version) if version else {}
        if not version or not ready_disk:
            version, ready_disk = self._discover_ready()
        if not version or not ready_disk:
            raise UpdateServiceError("staging-missing",
                                     "还没有已准备好的更新（staging 未完成）", 409)

        paths = self.store.paths(version)
        if not paths.staging.is_dir():
            raise UpdateServiceError("staging-incomplete", "staging 目录不存在", 409)
        exe = paths.staging / staging_mod.LAUNCHER
        version_file = paths.staging / staging_mod.VERSION_FILE
        if not exe.is_file() or not version_file.is_file():
            raise UpdateServiceError("staging-incomplete",
                                     "staging 缺少 PaperLibrary.exe 或 version.json", 409)
        try:
            packaged = json.loads(version_file.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise UpdateServiceError("staging-incomplete",
                                     "staging 内 version.json 无法解析：%s" % exc, 409)
        packaged_version = str(packaged.get("version") or "")
        if packaged_version != version:
            raise UpdateServiceError("version-mismatch",
                                     "staging 版本 %r 与目标版本 %r 不一致"
                                     % (packaged_version, version), 409)

        raw = self.store.read_manifest(version)
        if not raw:
            raise UpdateServiceError("staging-incomplete", "缺少本次下载的清单，无法确认版本", 409)
        try:
            data = manifest_mod.load_bytes(raw)
            signing.verify_manifest(data, self.public_key)
        except (manifest_mod.ManifestError, signing.ManifestSignatureError) as exc:
            raise UpdateServiceError("staging-incomplete", "清单校验失败：" + str(exc), 409)
        if str(data["version"]) != version:
            raise UpdateServiceError("version-mismatch",
                                     "清单版本 %s 与 staging 版本 %s 不一致"
                                     % (data["version"], version), 409)

        current = version_mod.load(self.root)  # VersionError → 500
        if current["version"] == version:
            raise UpdateServiceError("version-same", "staging 版本与当前版本相同，无需安装", 409)
        if version_mod.semver(version) < version_mod.semver(current["version"]):
            raise UpdateServiceError("downgrade", "拒绝降级：staging 版本低于当前版本", 409)

        backups_dir = self.store.updates_dir / "backups"
        backup_dir = backups_dir / ("app-" + current["version"])
        def relative(path: Path) -> str:
            try:
                return path.relative_to(self.store.data_dir).as_posix()
            except ValueError:
                return path.name

        state = self.store.read_state(version)
        payload = {
            "schema": store_mod.STATE_SCHEMA,
            "version": version,
            "tag": data["tag"],
            "fromVersion": current["version"],
            "fromTag": current["tag"],
            "stagingPath": str(paths.staging.resolve()),
            "stagingRel": relative(paths.staging),
            "backupPath": str(backup_dir.resolve()),
            "backupRel": relative(backup_dir),
            "packageSha256": str(ready_disk.get("packageSha256") or state.get("sha256") or ""),
            "manifestSha256": str(ready_disk.get("manifestSha256")
                                  or hashlib.sha256(raw).hexdigest()),
            "files": int(ready_disk.get("files") or 0),
            "bytes": int(ready_disk.get("bytes") or 0),
        }
        existing = self.store.read_apply()
        core = ("schema", "version", "tag", "fromVersion", "fromTag", "stagingPath",
                "stagingRel", "backupPath", "backupRel", "packageSha256", "manifestSha256")
        if existing and all(existing.get(key) == payload[key] for key in core):
            return {"ok": True, "applied": True, "idempotent": True, "apply": existing}
        written = self.store.write_apply(payload)
        return {"ok": True, "applied": True, "idempotent": False, "apply": written}

    def wait_idle(self, timeout: float = 120.0) -> bool:
        """等待后台任务结束（测试/运维用）；返回是否在超时前结束。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self._lock:
                thread = self._thread
                active = self._active
            if not active and thread is None:
                return True
            time.sleep(0.02)
        return False
