"""HTTP 入口（对应 Node 的 server/http/server.js）：只做分发与访问日志。

  /api/*                → 路由表
  /vendor/pdfjs|katex/* → 可选依赖资源（离线内置）
  /shared/*             → 共享内核
  其余                  → public/ 静态文件（HTML 里的 css/js 引用换成带版本号的 URL）
"""
from __future__ import annotations

import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from paperlib.util import read_body, read_json_body


class Ctx:
    """一次请求的上下文：路径参数、查询串、请求体与响应写入口。"""

    def __init__(self, handler, method, url, router):
        self.handler = handler
        self.method = method
        self.url = url
        split = urlsplit(url)
        self.pathname = unquote(split.path)
        raw_query = parse_qs(split.query, keep_blank_values=True)
        self._query = {k: v[0] for k, v in raw_query.items()}
        self._query_all = raw_query
        self.headers = handler.headers
        self.params = {}
        self.route_name = ""
        self.services = router.services
        self.started = False
        self.sent_status = None
        self._headers_sent = False
        self._dfr = None

    # ---------- 请求 ----------
    def query(self, name, default=""):
        value = self._query.get(name)
        return default if value is None else value

    def query_all(self, name):
        return list(self._query_all.get(name) or [])

    def has_query(self, name) -> bool:
        return name in self._query

    def read_body(self, limit: int = 40 * 1024 * 1024) -> bytes:
        return read_body(self.handler, limit)

    def read_json(self, limit=None):
        return read_json_body(self.handler, limit)

    # ---------- 响应 ----------
    def _send_headers(self, status, headers):
        handler = self.handler
        handler.send_response(status)
        for key, value in headers.items():
            handler.send_header(key, value)
        if not any(k.lower() in ("content-length", "transfer-encoding") for k in headers):
            handler.send_header("Connection", "close")
        handler.end_headers()
        self._headers_sent = True
        self.started = True
        self.sent_status = status

    def json(self, status, data):
        import json as _json

        body = _json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Content-Length": str(len(body)),
            "Cache-Control": "no-store",
        }
        self._send_headers(status, headers)
        if self.method == "HEAD":
            return
        self.handler.wfile.write(body)

    def fail(self, status, message, extra=None):
        body = {"ok": False, "error": message}
        if extra:
            body.update(extra)
        return self.json(status, body)

    def text(self, status, text, content_type="text/plain; charset=utf-8"):
        body = str(text).encode("utf-8")
        self._send_headers(status, {"Content-Type": content_type, "Content-Length": str(len(body))})
        if self.method == "HEAD":
            return
        self.handler.wfile.write(body)

    def send_bytes(self, status, data: bytes, content_type="application/octet-stream", headers=None):
        merged = {"Content-Type": content_type, "Content-Length": str(len(data))}
        if headers:
            merged.update(headers)
        self._send_headers(status, merged)
        if self.method == "HEAD":
            return
        self.handler.wfile.write(data)

    # ---------- 流式 ----------
    def start_stream(self, status, headers):
        """SSE/长响应用：不写 Content-Length，直接按块写。"""
        self._send_headers(status, headers)
        self.handler.wfile.flush()

    def write_chunk(self, data: bytes):
        self.handler.wfile.write(data)
        self.handler.wfile.flush()

    def end_stream(self):
        try:
            self.handler.wfile.flush()
        except Exception:
            pass

    def send_file(self, abs_path, inline=None, download=None, cache=None, content_type=None):
        """支持 Range / ETag 的文件响应（对应 Node 的 sendFile）。"""
        import os
        from email.utils import formatdate

        from paperlib.util import mime_type

        path = str(abs_path)
        try:
            st = os.stat(path)
        except OSError:
            return self.fail(404, "文件不存在：" + os.path.basename(path))
        if not os.path.isfile(path):
            return self.fail(404, "不是文件")
        ctype = content_type or mime_type(path)
        etag = '"' + format(int(st.st_size), "x") + "-" + format(int(round(st.st_mtime * 1000)), "x") + '"'
        headers = {
            "Content-Type": ctype,
            "Accept-Ranges": "bytes",
            "Last-Modified": formatdate(st.st_mtime, usegmt=True),
            "ETag": etag,
        }
        from urllib.parse import quote

        if download:
            headers["Content-Disposition"] = 'attachment; filename="' + quote(str(download), safe="") + '"'
        elif inline:
            headers["Content-Disposition"] = 'inline; filename="' + quote(str(inline), safe="") + '"'
        headers["Cache-Control"] = cache or "no-cache"

        if self.headers.get("If-None-Match") == etag:
            self._send_headers(304, headers)
            return

        rng = self.headers.get("Range")
        if rng:
            import re as _re

            m = _re.match(r"^bytes=(\d*)-(\d*)$", rng.strip())
            if m:
                start_raw, end_raw = m.group(1), m.group(2)
                start = int(start_raw) if start_raw else None
                end = int(end_raw) if end_raw else None
                if start is not None or end is not None:
                    if start is None:
                        start = max(0, st.st_size - end)
                        end = st.st_size - 1
                    elif end is None or end >= st.st_size:
                        end = st.st_size - 1
                    if start > end or start >= st.st_size:
                        headers["Content-Range"] = "bytes */" + str(st.st_size)
                        self._send_headers(416, headers)
                        return
                    headers["Content-Range"] = "bytes %d-%d/%d" % (start, end, st.st_size)
                    headers["Content-Length"] = str(end - start + 1)
                    self._send_headers(206, headers)
                    if self.method == "HEAD":
                        return
                    with open(path, "rb") as fh:
                        fh.seek(start)
                        self._copy(fh, end - start + 1)
                    return

        headers["Content-Length"] = str(st.st_size)
        self._send_headers(200, headers)
        if self.method == "HEAD":
            return
        with open(path, "rb") as fh:
            self._copy(fh, None)

    def _copy(self, fh, remaining):
        chunk = 256 * 1024
        while True:
            if remaining is not None:
                if remaining <= 0:
                    return
                data = fh.read(min(chunk, remaining))
                remaining -= len(data)
            else:
                data = fh.read(chunk)
            if not data:
                return
            self.handler.wfile.write(data)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "PaperLibrary/2.0"
    verbose = True
    router = None
    static_handler = None

    def log_message(self, fmt, *args):
        return

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 客户端提前断开（阅读器拖进度条、SSE 关页签）是正常现象，不要打堆栈
            self.close_connection = True

    def do_GET(self):
        self._dispatch()

    def do_HEAD(self):
        self._dispatch()

    def do_POST(self):
        self._dispatch()

    def do_PUT(self):
        self._dispatch()

    def do_PATCH(self):
        self._dispatch()

    def do_DELETE(self):
        self._dispatch()

    def do_OPTIONS(self):
        self._dispatch()

    def _dispatch(self):
        started = time.time()
        ctx = Ctx(self, self.command, self.path, self.router)
        try:
            split = urlsplit(self.path)
            pathname = unquote(split.path)
            if pathname.startswith("/api/"):
                self.router.handle(ctx)
            elif self.command not in ("GET", "HEAD"):
                ctx.fail(405, "不支持的请求方法")
            elif pathname.startswith("/vendor/pdfjs/"):
                self.static_handler.serve_pdfjs(ctx)
            elif pathname.startswith("/vendor/katex/"):
                self.static_handler.serve_katex(ctx)
            elif pathname.startswith("/shared/"):
                self.static_handler.serve_shared(ctx)
            else:
                self.static_handler.serve_static(ctx)
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception:  # noqa: BLE001
            import traceback

            traceback.print_exc()
            if not ctx.started:
                try:
                    ctx.fail(500, "服务器内部错误")
                except Exception:
                    pass
        finally:
            if self.verbose and ctx.sent_status is not None:
                ms = int((time.time() - started) * 1000)
                print("%s %s -> %s (%dms)" % (self.command, self.path, ctx.sent_status, ms), flush=True)


class HttpServer:
    def __init__(self, router, static_handler, host, port, verbose=True):
        self.router = router
        self.static_handler = static_handler
        self.host = host
        self.port = port
        handler_cls = type("BoundHandler", (Handler,), {
            "router": router,
            "static_handler": static_handler,
            "verbose": verbose,
        })
        self.httpd = ThreadingHTTPServer((host, port), handler_cls)
        self.httpd.daemon_threads = True
        self.thread = None

    def serve_forever(self):
        self.server_address = self.httpd.server_address
        try:
            self.httpd.serve_forever(poll_interval=0.2)
        finally:
            self.httpd.server_close()

    def start_background(self):
        self.thread = threading.Thread(target=self.serve_forever, name="http", daemon=True)
        self.thread.start()

    def shutdown(self):
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()

    def close(self):
        try:
            self.httpd.server_close()
        except OSError:
            pass


def probe_port(host: str, port: int, timeout: float = 0.6) -> str:
    """探测端口：'free' | 'ours' | 'busy'（对应 Node 的 probePort）。"""
    import json as _json
    import urllib.request

    base = "http://%s:%d" % ("127.0.0.1" if host == "0.0.0.0" else host, port)

    def get_json(url):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                if resp.status != 200:
                    return None
                return _json.loads(resp.read().decode("utf-8"))
        except Exception:
            return None

    status = get_json(base + "/api/shutdown/status")
    if status and status.get("app") == "literature-vault":
        return "ours"
    if get_json(base + "/api/health"):
        return "ours"
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.bind(("0.0.0.0" if host == "0.0.0.0" else host, port))
        return "free"
    except OSError:
        return "busy"
    finally:
        sock.close()
