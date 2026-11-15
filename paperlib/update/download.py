"""更新包下载（阶段 2A / P2-02）：HTTPS + 白名单、流式写入、续传、取消、磁盘预检。

约束（方案一原文 §5.2）：
- 只允许 HTTPS；清单里的主机与每一次重定向都必须在允许列表内（沿用阶段 1 实测白名单：
  github.com、release-assets.githubusercontent.com）。
- 流式写入 @package.zip.part@（1 MB/块），不把整包读进内存；进度回调 + 状态落盘。
- 支持 Range 续传：已有 .part 时发 Range，206 且 Content-Range 起点匹配才继续；
  服务器返回 200（不支持 Range）或 416 时从头重下。
- 下载前预检磁盘空间：zip + 暂存解压 + 旧程序备份 + 安全余量（按 zip 体积估算，偏保守）。
- 用户可取消（threading.Event）：取消即删（方案 B）——删除该版本的 .part/清单/状态/暂存，
  不保留可用于续传的残包；不启动任何子进程。
- 下载成功只代表字节已落盘；大小/SHA-256/签名校验在 P2-03，解压在 P2-04。
"""
from __future__ import annotations

import socket
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from paperlib.update import release, store as store_mod

CHUNK_BYTES = 1024 * 1024
DEFAULT_TIMEOUT = 20.0
MAX_HOPS = 6
# 空间估算：zip + 暂存解压（≈2×zip）+ 旧程序备份（≈2×zip）+ 256 MB 余量
STAGING_FACTOR = 2
BACKUP_FACTOR = 2
SPACE_MARGIN = 256 * 1024 * 1024
USER_AGENT = "PaperLibrary-update-download/1.0"


class DownloadError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class DownloadCancelled(DownloadError):
    def __init__(self, message: str = "下载已取消"):
        super().__init__("cancelled", message)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def required_space(size: int) -> int:
    """估算整包更新需要的可用空间（偏保守；含解压/备份/余量）。"""
    size = max(0, int(size))
    return size * (1 + STAGING_FACTOR + BACKUP_FACTOR) + SPACE_MARGIN


def check_disk_space(path, required: int, free_bytes=None) -> dict:
    """下载前预检；返回 {required, free}；不足时抛 DownloadError(disk-full)（不发起网络请求）。"""
    target = Path(path)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DownloadError("disk-full", "无法创建更新目录：%s" % exc)
    try:
        usage = shutil.disk_usage(str(target))
    except OSError as exc:
        raise DownloadError("disk-full", "无法读取磁盘剩余空间：%s" % exc)
    free = usage.free if free_bytes is None else int(free_bytes)
    if free < required:
        raise DownloadError("disk-full",
                            "磁盘空间不足：本次更新预计需要约 %.0f MB，当前可用约 %.0f MB；"
                            "请清理磁盘后重试（未开始下载）"
                            % (required / 1048576, free / 1048576))
    return {"required": required, "free": free}


def _request_once(url: str, headers: dict, timeout: float):
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        **headers,
    })
    try:
        return _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def _open_stream(url: str, headers: dict, timeout: float, allowed):
    """手动跟随重定向并逐跳校验主机；返回 (最终 URL, 响应, 状态码)。"""
    current = url
    for _ in range(MAX_HOPS):
        if not allowed(current):
            raise DownloadError("redirect-host", "下载主机不在允许列表：%s" % current)
        try:
            response = _request_once(current, headers, timeout)
        except (socket.timeout, TimeoutError):
            raise DownloadError("timeout", "连接下载源超时（%.0f 秒）" % timeout)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise DownloadError("timeout", "连接下载源超时（%.0f 秒）" % timeout)
            raise DownloadError("network", "无法连接下载源：%s" % (reason or exc))
        except OSError as exc:
            raise DownloadError("network", "无法连接下载源：%s" % exc)
        status = getattr(response, "status", None) or getattr(response, "code", 0)
        if status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location") if response.headers else None
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass
            if not location:
                raise DownloadError("network", "下载源返回 %d 但没有 Location" % status)
            current = urllib.parse.urljoin(current, location)
            continue
        return current, response, status
    raise DownloadError("redirect-host", "重定向次数超过 %d 次" % (MAX_HOPS - 1))


def _content_length(headers) -> int:
    try:
        return int(headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return 0


def _parse_content_range(value: str):
    """解析 bytes start-end/total；解析失败返回 None。"""
    try:
        unit, spec = str(value or "").strip().split(" ", 1)
        if unit.lower() != "bytes":
            return None
        span, total = spec.split("/", 1)
        start, _end = span.split("-", 1)
        return int(start), int(total)
    except (ValueError, AttributeError):
        return None


def download_package(store: store_mod.UpdateStore, version: str, asset: dict,
                     cancel_event=None, progress=None, timeout: float = DEFAULT_TIMEOUT,
                     allowed=None, free_bytes=None, chunk_bytes: int = CHUNK_BYTES) -> dict:
    """下载完整包到 data/updates/<version>/package.zip.part（可续传/取消）。

    返回 {version, downloadedBytes, totalBytes, resumedFrom, partPath, sourceUrl}。
    失败抛 DownloadError；取消抛 DownloadCancelled；状态文件同步记录 phase/error/code。
    """
    if not isinstance(asset, dict):
        raise DownloadError("bad-asset", "资产信息必须是对象")
    url = str(asset.get("url") or "")
    expected = int(asset.get("size") or 0)
    allowed = allowed or release.allowed_download_url
    if not allowed(url):
        raise DownloadError("bad-url", "下载地址必须是 https + 允许主机：%s" % url)

    paths = store.ensure(version)
    try:
        check_disk_space(store.data_dir, required_space(expected), free_bytes=free_bytes)
    except DownloadError as exc:
        store.update_state(version, phase="failed", error=exc.message, code=exc.code)
        raise

    offset = 0
    if paths.part.is_file():
        try:
            offset = paths.part.stat().st_size
        except OSError:
            offset = 0
        if expected and offset > expected:
            # 本地 .part 比目标还大：不可信，从头开始
            try:
                paths.part.unlink()
            except OSError:
                pass
            offset = 0
    store.update_state(version, phase="downloading", sourceUrl=url,
                       totalBytes=expected, downloadedBytes=offset,
                       resumedFrom=offset, error="", code="")

    def cancelled() -> bool:
        return bool(cancel_event is not None and cancel_event.is_set())

    for attempt in (0, 1):
        if cancelled():
            store.discard(version)  # 方案 B：取消即删，不保留续传残包
            raise DownloadCancelled()
        headers = {"Range": "bytes=%d-" % offset} if offset > 0 else {}
        response = None
        try:
            final_url, response, status = _open_stream(url, headers, timeout, allowed)
            if status == 416 and offset > 0:
                # .part 与远端不一致：丢弃后从头下载一次
                try:
                    paths.part.unlink()
                except OSError:
                    pass
                offset = 0
                store.update_state(version, phase="downloading", downloadedBytes=0,
                                   resumedFrom=0)
                try:
                    response.close()
                except Exception:  # noqa: BLE001
                    pass
                continue
            if status == 200:
                # 服务器不支持 Range（或本来就从 0 开始）：从头写
                offset = 0
                total = _content_length(response.headers) or expected
                mode = "wb"
            elif status == 206:
                parsed = _parse_content_range(response.headers.get("Content-Range", ""))
                total = expected or (parsed[1] if parsed else 0)
                if parsed is None or parsed[0] != offset:
                    # Content-Range 与本地 .part 不匹配：不信任，从头重下
                    try:
                        response.close()
                    except Exception:  # noqa: BLE001
                        pass
                    try:
                        paths.part.unlink()
                    except OSError:
                        pass
                    offset = 0
                    if attempt == 0:
                        continue
                    raise DownloadError("resume-mismatch", "续传区间不匹配，请重试")
                mode = "ab"
            else:
                raise DownloadError("network", "下载源返回 HTTP %d" % status)

            store.update_state(version, phase="downloading", sourceUrl=final_url,
                               totalBytes=total, downloadedBytes=offset,
                               resumedFrom=offset)
            written = offset
            last_state = written
            with open(paths.part, mode) as fh:
                while True:
                    if cancelled():
                        raise DownloadCancelled()
                    try:
                        chunk = response.read(chunk_bytes)
                    except (socket.timeout, TimeoutError):
                        raise DownloadError("timeout", "下载数据超时（%.0f 秒）" % timeout)
                    except OSError as exc:
                        raise DownloadError("network", "下载中断：%s" % exc)
                    if not chunk:
                        break
                    written += len(chunk)
                    if expected and written > expected:
                        raise DownloadError("size-mismatch",
                                            "下载字节数超过清单大小（%.0f > %d）" % (written, expected))
                    fh.write(chunk)
                    if progress:
                        try:
                            progress(written, total)
                        except Exception:  # noqa: BLE001
                            pass
                    if written - last_state >= CHUNK_BYTES:
                        last_state = written
                        store.update_state(version, downloadedBytes=written,
                                           totalBytes=total, resumedFrom=offset)
            if total and written < total:
                raise DownloadError("network", "连接提前结束：已下载 %d / %d 字节" % (written, total))
            store.update_state(version, phase="downloading", downloadedBytes=written,
                               totalBytes=total, resumedFrom=offset, error="", code="")
            return {
                "version": str(version),
                "downloadedBytes": written,
                "totalBytes": total,
                "resumedFrom": offset,
                "partPath": str(paths.part),
                "sourceUrl": final_url,
            }
        except DownloadCancelled:
            store.discard(version)  # 方案 B：取消即删
            raise
        except DownloadError as exc:
            store.update_state(version, phase="failed", error=exc.message, code=exc.code)
            raise
        except OSError as exc:
            message = "下载写入失败：%s" % exc
            store.update_state(version, phase="failed", error=message, code="disk-write")
            raise DownloadError("disk-write", message)
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:  # noqa: BLE001
                    pass
    raise DownloadError("network", "下载失败（重试后仍未完成）")
