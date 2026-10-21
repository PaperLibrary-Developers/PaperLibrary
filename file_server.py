"""
file_server.py — 本地文件 HTTP 服务器
为浏览器提供 PDF 文件访问，解决 file:/// 协议被浏览器拦截的问题。

两个安全前提（都是刻意的，别当成小事改掉）：
  1. 只绑 127.0.0.1 —— 外面（局域网 / 公网隧道）根本连不上；
  2. 只允许发送「文献库目录」和程序自己的 data/ 下的文件 —— 就算第 1 条哪天
     被改掉（例如为了手机访问把绑定改成 0.0.0.0），对方也只能看到你的论文，
     拿不到桌面文件、聊天记录、其它盘的内容。

第 2 条通过 _is_allowed() 实现：白名单 + 真实路径解析 + 大小写不敏感比较。
"""
import json
import os
import threading
import urllib.parse
import socketserver
from http.server import SimpleHTTPRequestHandler

FILE_SERVER_PORT = 8502
APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_CONFIG_FILE = os.path.join(APP_DIR, "data", ".app_config.json")

_server_started = False
# 允许发送的目录（懒加载一次；改过扫描目录后重启服务即生效）
_allowed_roots = None


def _load_allowed_roots():
    """允许发送的根目录白名单：文献库目录 + 程序自己的 data/ 目录。

    文献库目录沿用 app.py 的 .app_config.json（pdf_scan_root），
    取不到就退回 app 目录 —— 与 app.py 的 get_scan_root() 保持一致。
    """
    roots = [os.path.join(APP_DIR, "data")]
    scan_root = ""
    try:
        with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
            scan_root = (json.load(f) or {}).get("pdf_scan_root", "") or ""
    except Exception:
        scan_root = ""
    if scan_root and os.path.isdir(scan_root):
        roots.append(scan_root)
    else:
        roots.append(APP_DIR)
    return roots


def _norm(p):
    """规范化成「真实绝对路径 + 小写」：Windows 路径不区分大小写，
    直接比较字符串会把 Y:\\私人 和 y:\\私人 判成两个地方。"""
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _inside(path, root):
    """path 是否落在 root 里（或就是 root 本身）。"""
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:
        # 不同盘符（C: 与 Y:）会抛 ValueError，直接判为不在范围内
        return False


def _is_allowed(file_path):
    """路径白名单校验，返回 (是否允许, 原因)。

    挡掉三类东西：
      · 白名单之外的任何路径（含 ../ 穿越后落在外面的）；
      · Windows 8.3 短名（PROGRA~1 这种）—— 它能绕过朴素的字符串前缀比较；
      · 解析不到真实路径的（realpath 失败）。
    """
    global _allowed_roots
    if _allowed_roots is None:
        _allowed_roots = _load_allowed_roots()

    # 8.3 短名：合法的论文 PDF 路径不会带这个，出现即拒绝
    if "~" in str(file_path):
        return False, "路径含 8.3 短名（~），已拒绝"

    try:
        target = _norm(file_path)
    except Exception as e:
        return False, f"路径无法解析: {e}"

    for root in _allowed_roots:
        if _inside(target, _norm(root)):
            return True, ""

    # 若未命中，尝试重新读取一次配置（应对用户在网页中动态切换了文献大文件夹的情况）
    _allowed_roots = _load_allowed_roots()
    for root in _allowed_roots:
        if _inside(target, _norm(root)):
            return True, ""

    return False, "不在允许的目录内"


class FileHandler(SimpleHTTPRequestHandler):
    """处理本地文件 HTTP 服务：
      · GET /static/pdfjs/<file> -> 提供本地 PDF.js 静态库
      · GET /view?path=<url-encoded-path> -> 提供 HTML5 PDF.js 流式阅读器 (MIME: text/html，免疫 Edge 拦截)
      · GET /?path=<url-encoded-path> -> 提供原生文件流 (带 CORS + inline 放行头)
    """

    def do_OPTIONS(self):
        """处理 CORS 预检请求"""
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)

        # 1. 静态资源路由：/static/pdfjs/xxx
        if parsed.path.startswith("/static/pdfjs/"):
            sub_name = parsed.path[len("/static/pdfjs/"):]
            local_static = os.path.join(APP_DIR, "data", "pdfjs", sub_name)
            if not os.path.isfile(local_static):
                self.send_error(404, "Static file not found")
                return
            ext = os.path.splitext(local_static)[1].lower()
            ctype = {
                ".js": "application/javascript; charset=utf-8",
                ".html": "text/html; charset=utf-8",
                ".css": "text/css; charset=utf-8",
            }.get(ext, "application/octet-stream")
            try:
                with open(local_static, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(content)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 2. HTML5 PDF.js 流式阅读器路由：/view?path=xxx
        if parsed.path == "/view":
            viewer_path = os.path.join(APP_DIR, "data", "pdfjs", "viewer.html")
            if not os.path.isfile(viewer_path):
                self.send_error(404, "viewer.html not found")
                return
            try:
                with open(viewer_path, "rb") as f:
                    html_data = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html_data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("X-Frame-Options", "ALLOWALL")
                self.send_header("Content-Security-Policy", "frame-ancestors *")
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(html_data)
            except Exception as e:
                self.send_error(500, str(e))
            return

        # 3. 原始文件获取路由：/?path=xxx
        params = urllib.parse.parse_qs(parsed.query)
        file_path = params.get("path", [None])[0]

        if not file_path:
            self.send_error(400, "Missing ?path= parameter")
            return

        file_path = os.path.normpath(os.path.abspath(file_path))

        # ── 白名单校验：必须在发送任何内容之前完成 ──
        allowed, reason = _is_allowed(file_path)
        if not allowed:
            self._deny(file_path, reason)
            return

        if not os.path.isfile(file_path):
            self.send_error(404, f"File not found: {file_path}")
            return

        try:
            file_size = os.path.getsize(file_path)
            ext = os.path.splitext(file_path)[1].lower()

            content_type = {
                ".pdf": "application/pdf",
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".svg": "image/svg+xml",
                ".txt": "text/plain; charset=utf-8",
                ".csv": "text/csv; charset=utf-8",
            }.get(ext, "application/octet-stream")

            # 支持 Range 请求（浏览器翻页大 PDF 需要）
            range_header = self.headers.get("Range")
            if range_header:
                self._send_range(file_path, file_size, content_type, range_header)
            else:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(file_size))
                self.send_header("Accept-Ranges", "bytes")
                self.send_header("Cache-Control", "no-cache")
                # ── 全面的跨域与浏览器内联安全放行头（解决 Edge/Chrome iframe 拦截） ──
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                self.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Range, Content-Length, Content-Type")
                self.send_header("X-Frame-Options", "ALLOWALL")
                self.send_header("Content-Security-Policy", "frame-ancestors *")
                if ext == ".pdf":
                    clean_name = os.path.basename(file_path).encode("ascii", "ignore").decode("ascii") or "paper.pdf"
                    self.send_header("Content-Disposition", f'inline; filename="{clean_name}"')
                self.end_headers()
                with open(file_path, "rb") as f:
                    self.wfile.write(f.read())

        except Exception as e:
            self.send_error(500, str(e))

    def _deny(self, file_path, reason):
        """拒绝发送：403 + UTF-8 正文。

        注意状态行只能用 ASCII（HTTP 规范要求 latin-1），所以原因短语写英文、
        详细说明放进 UTF-8 的正文。早先把中文塞进状态行会抛
        UnicodeEncodeError，客户端看到的是「连接被意外关闭」而不是 403（踩过）。
        """
        roots = "、".join(_allowed_roots or [])
        detail = (f"403 已拒绝：这个文件不在允许的目录内。\n"
                  f"原因：{reason}\n"
                  f"允许的目录：{roots}\n"
                  f"请求的路径：{file_path}\n")
        body = detail.encode("utf-8")
        try:
            self.send_response(403, "Forbidden")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "*")
            self.end_headers()
            self.wfile.write(body)
        except Exception:
            pass

    def _send_range(self, file_path, file_size, content_type, range_header):
        """处理 HTTP Range 请求"""
        try:
            ext = os.path.splitext(file_path)[1].lower()
            unit, _, ranges = range_header.partition("=")
            if unit != "bytes":
                self.send_error(416, "Only bytes range supported")
                return

            start_str, _, end_str = ranges.partition("-")
            start = int(start_str) if start_str else 0
            end = int(end_str) if end_str else file_size - 1

            if start >= file_size or end >= file_size:
                self.send_error(416, "Range not satisfiable")
                return

            length = end - start + 1
            self.send_response(206)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(length))
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Expose-Headers", "Accept-Ranges, Content-Range, Content-Length, Content-Type")
            self.send_header("X-Frame-Options", "ALLOWALL")
            self.send_header("Content-Security-Policy", "frame-ancestors *")
            if ext == ".pdf":
                clean_name = os.path.basename(file_path).encode("ascii", "ignore").decode("ascii") or "paper.pdf"
                self.send_header("Content-Disposition", f'inline; filename="{clean_name}"')
            self.end_headers()

            with open(file_path, "rb") as f:
                f.seek(start)
                self.wfile.write(f.read(length))
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.send_error(500, f"Range request failed: {e}")

    def log_message(self, format, *args):
        pass  # 静默日志


class _ReusableTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


def start_file_server(port=FILE_SERVER_PORT):
    """后台线程启动文件服务器（仅本机可访问）"""
    global _server_started
    if _server_started:
        return

    try:
        server = _ReusableTCPServer(("127.0.0.1", port), FileHandler)
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        _server_started = True
        print(f"[file_server] 已在 127.0.0.1:{port} 监听；"
              f"允许目录：{'、'.join(_load_allowed_roots())}")
    except OSError as e:
        # 端口被占用（通常已经有一个实例在跑）。这里必须出声：
        # 以前是静默忽略，结果「新版本没起来、旧版本还在服务」完全看不出来 ——
        # 排查安全问题时会误以为改动已生效（踩过一次）。
        _server_started = True
        print(f"[file_server] 端口 {port} 已被占用，本次未启动（{e}）。"
              f"若在改这个文件，请先停掉旧的 file_server 再重启，否则改动不会生效。")


if __name__ == "__main__":
    server = _ReusableTCPServer(("127.0.0.1", FILE_SERVER_PORT), FileHandler)
    print(f"File server running on http://127.0.0.1:{FILE_SERVER_PORT}/?path=<file>")
    print("允许的目录：")
    for r in _load_allowed_roots():
        print("   ", r)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
