"""安全拉取更新清单（阶段 1 / P1-02）：只读、不下载安装包。

约束（方案一原文 §4.2 / §5.2 / §9）：
- 只允许 HTTPS，主机必须在允许列表内（清单地址与每一次重定向都校验）；
- 超时 8–10 秒（默认 9s），失败重试 1–2 次（默认 2 次，共 3 次尝试）；
- 不缓存旧结果：请求带 no-cache，每次调用都重新拉取；
- 响应体 ≤ 256 KB；流式读取并硬性截断，避免内存被拉爆；
- 404 视为“发布流程异常”（latest 缺清单），不猜测版本；5xx/超时才重试。
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from paperlib.update import manifest, release, tls

DEFAULT_TIMEOUT = 9.0
DEFAULT_RETRIES = 2
MAX_HOPS = 6
USER_AGENT = "PaperLibrary-update-check/1.0"

# 允许重定向的主机：实测 github.com → release-assets.githubusercontent.com。
# objects.githubusercontent.com 是原文示例，本次未观测到，不加入；见 docs/update-p1-20261116。
ALLOWED_HOSTS = release.ALLOWED_DOWNLOAD_HOSTS


class FetchError(Exception):
    """拉取清单失败；code 供 API 层映射成可诊断的错误码。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = tls.build_opener(_NoRedirect)


def _request_once(url: str, timeout: float):
    request = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    })
    try:
        return _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def _allowed(url: str) -> bool:
    return release.allowed_download_url(url)


def _open_chain(url: str, timeout: float, request_once=_request_once):
    """手动跟随重定向；每一跳都必须在允许主机列表内。返回 (最终 URL, 响应, 状态码)。"""
    current = url
    for _ in range(MAX_HOPS):
        if not _allowed(current):
            raise FetchError("redirect-host", "下载主机不在允许列表（https + %s）：%s"
                             % ("、".join(sorted(ALLOWED_HOSTS)), current))
        try:
            response = request_once(current, timeout)
        except (socket.timeout, TimeoutError):
            raise FetchError("timeout", "连接更新源超时（%.0f 秒）" % timeout)
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise FetchError("timeout", "连接更新源超时（%.0f 秒）" % timeout)
            raise FetchError("network", "无法连接更新源：%s" % (reason or exc))
        except OSError as exc:
            raise FetchError("network", "无法连接更新源：%s" % exc)
        status = getattr(response, "status", None) or getattr(response, "code", 0)
        if status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location") if response.headers else None
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass
            if not location:
                raise FetchError("network", "更新源返回 %d 但没有 Location" % status)
            current = urllib.parse.urljoin(current, location)
            continue
        return current, response, status
    raise FetchError("redirect-host", "重定向次数超过 %d 次：%s" % (MAX_HOPS - 1, url))


def _fetch_once(url: str, timeout: float, request_once=_request_once) -> bytes:
    final_url, response, status = _open_chain(url, timeout, request_once)
    try:
        if status == 404:
            raise FetchError("no-manifest", "更新源缺少 update-manifest.json（发布流程异常，不猜测版本）")
        if status != 200:
            raise FetchError("network", "更新源返回 HTTP %d" % status)
        raw = response.read(manifest.MAX_BYTES + 1)
    except FetchError:
        raise
    except (socket.timeout, TimeoutError):
        raise FetchError("timeout", "读取更新清单超时（%.0f 秒）" % timeout)
    except OSError as exc:
        raise FetchError("network", "读取更新清单失败：%s" % exc)
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass
    if len(raw) > manifest.MAX_BYTES:
        raise FetchError("manifest-too-large", "更新清单超过 %d KB 上限"
                         % (manifest.MAX_BYTES // 1024))
    return raw


def fetch_manifest(url: str = "", timeout: float = DEFAULT_TIMEOUT,
                   retries: int = DEFAULT_RETRIES, sleep=time.sleep,
                   request_once=_request_once) -> bytes:
    """拉取并返回清单原始字节；网络错误/超时按 retries 重试，404/超大/主机不符不重试。"""
    url = url or release.stable_manifest_url()
    if not _allowed(url):
        raise FetchError("bad-url", "更新清单地址必须是 https + 允许主机：%s" % url)
    attempts = max(0, int(retries)) + 1
    last = None
    for attempt in range(attempts):
        try:
            return _fetch_once(url, timeout, request_once)
        except FetchError as exc:
            last = exc
            if exc.code in ("no-manifest", "manifest-too-large", "redirect-host", "bad-url"):
                raise
            if attempt + 1 < attempts:
                sleep(0.3 * (attempt + 1))
    raise last or FetchError("network", "无法连接更新源")
