"""下载后校验（阶段 2A / P2-03）：大小、SHA-256、清单签名。

顺序（任一失败 → 删除临时包、写 failed、不进入解压/安装）：
1. 重新读取并验签 @manifest.json@（内置公钥；清单来自 P1-02 的签名链）；
2. @package.zip.part@ 存在且字节数 == 清单 @assets.full.size@（若记录过 Content-Length 也一并比对）；
3. 流式计算 SHA-256（1 MB/块，不整读内存）== 清单 @assets.full.sha256@；
4. 全部通过：@os.replace@ 把 .part 改名为 @package.zip@，状态置 @verified@。

失败清理只删本版本的临时包（.part/package.zip），不动 staging/ready 以外的其它版本或用户数据。
"""
from __future__ import annotations

import hashlib
import os

from paperlib.update import manifest as manifest_mod
from paperlib.update import signing
from paperlib.update import store as store_mod

CHUNK_BYTES = 1024 * 1024


class VerifyError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sha256_file(path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cleanup_package(paths) -> None:
    for path in (paths.part, paths.package):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def verify_download(store: store_mod.UpdateStore, version: str, public_key=None,
                    content_length=None) -> dict:
    """校验已下载的 .part；成功时改名为 package.zip 并返回校验信息。"""
    paths = store.paths(version)
    store.update_state(version, phase="verifying", error="", code="")

    def fail(code: str, message: str):
        _cleanup_package(paths)
        store.update_state(version, phase="failed", error=message, code=code)
        raise VerifyError(code, message)

    raw = store.read_manifest(version)
    if not raw:
        fail("manifest-missing", "缺少本次下载使用的清单 manifest.json，无法校验")
    try:
        data = manifest_mod.load_bytes(raw)
    except manifest_mod.ManifestError as exc:
        fail("invalid-manifest", "清单无效：" + str(exc))
    try:
        signing.verify_manifest(data, public_key)
    except signing.ManifestSignatureError as exc:
        fail("signature", "清单验签失败：" + str(exc))

    asset = data["assets"]["full"]
    if data["version"] != version:
        fail("version-mismatch",
             "清单版本 %s 与目标版本 %s 不一致" % (data["version"], version))
    if not paths.part.is_file():
        fail("missing-package", "下载临时文件不存在：%s" % paths.part.name)
    actual_size = paths.part.stat().st_size
    if actual_size != asset["size"]:
        fail("size-mismatch",
             "下载大小不符：实际 %d 字节，清单 %d 字节" % (actual_size, asset["size"]))
    if content_length is not None:
        try:
            declared = int(content_length)
        except (TypeError, ValueError):
            declared = -1
        if declared >= 0 and declared != asset["size"]:
            fail("size-mismatch",
                 "Content-Length %d 与清单大小 %d 不一致" % (declared, asset["size"]))
    state_total = int(store.read_state(version).get("totalBytes") or 0)
    if state_total and state_total != asset["size"]:
        fail("size-mismatch",
             "下载响应总大小 %d 与清单大小 %d 不一致" % (state_total, asset["size"]))

    actual_sha = sha256_file(paths.part)
    if actual_sha != asset["sha256"]:
        fail("sha256-mismatch",
             "SHA-256 不符：实际 %s…，清单 %s…" % (actual_sha[:12], asset["sha256"][:12]))

    try:
        os.replace(paths.part, paths.package)
    except OSError as exc:
        fail("disk-write", "无法把临时包转正：%s" % exc)

    info = {
        "version": str(version),
        "size": actual_size,
        "sha256": actual_sha,
        "packagePath": str(paths.package),
        "manifestSha256": hashlib.sha256(raw).hexdigest(),
    }
    store.update_state(version, phase="verified", downloadedBytes=actual_size,
                       totalBytes=actual_size, sha256=actual_sha, error="", code="")
    return info
