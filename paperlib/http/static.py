"""静态资源与 vendor（对应 Node 的 server/http/static.js）。

  /vendor/pdfjs/*   内置 PDF 阅读器资源（离线内置，不再依赖 npm）
  /vendor/katex/*   公式排版资源（可选；缺失时前端降级为原文）
  /shared/*         共享内核（前后端共用实现，依赖方向 public/ → shared/）
  其余路径          public/ 下的静态文件；HTML 里的 css/js 引用换成带版本号的 URL
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from paperlib.util import ensure_dir, mime_type, safe_path_join


def _base36(n: int) -> str:
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    while n:
        n, rem = divmod(n, 36)
        out.append(digits[rem])
    return "".join(reversed(out))


class StaticHandler:
    def __init__(self, root, public_dir=None, shared_dir=None, vendor_dir=None):
        self.root = Path(root)
        self.public_dir = Path(public_dir or (self.root / "public"))
        self.shared_dir = Path(shared_dir or (self.root / "shared"))
        self.vendor_dir = Path(vendor_dir or (self.root / "vendor"))
        self.pdfjs_dir = self.vendor_dir / "pdfjs"
        self.katex_dir = self.vendor_dir / "katex"

    # ---------- vendor ----------
    def serve_pdfjs(self, ctx):
        rel = re.sub(r"^/vendor/pdfjs/?", "", ctx.pathname)
        abs_path = safe_path_join(self.pdfjs_dir, rel)
        if not abs_path or not os.path.isfile(abs_path):
            return ctx.fail(404, "内置阅读器资源不存在（随包内置的 vendor/pdfjs 缺失）")
        return ctx.send_file(abs_path, cache="public, max-age=86400")

    def serve_katex(self, ctx):
        rel = re.sub(r"^/vendor/katex/?", "", ctx.pathname)
        abs_path = safe_path_join(self.katex_dir, rel)
        if not abs_path or not os.path.isfile(abs_path):
            return ctx.fail(404, "公式排版资源不存在（可选依赖 vendor/katex 缺失）")
        return ctx.send_file(abs_path, cache="public, max-age=86400")

    # ---------- shared ----------
    def serve_shared(self, ctx):
        pathname = ctx.pathname
        versioned = re.match(r"^/__v/[^/]+/(.*)$", pathname)
        if versioned:
            pathname = "/" + versioned.group(1)
        rel = re.sub(r"^/shared/?", "", pathname)
        if Path(rel).suffix.lower() != ".mjs":
            return ctx.fail(404, "共享模块不存在：" + rel)
        abs_path = safe_path_join(self.shared_dir, rel)
        if not abs_path or not os.path.isfile(abs_path):
            return ctx.fail(404, "共享模块不存在：" + rel)
        cache = "public, max-age=31536000, immutable" if versioned else "no-cache"
        return ctx.send_file(abs_path, cache=cache)

    # ---------- public ----------
    def with_version(self, abs_path: Path):
        try:
            mtime = int(round(os.stat(abs_path).st_mtime * 1000))
        except OSError:
            return None
        rel = Path(abs_path).relative_to(self.public_dir).as_posix()
        return "/__v/" + _base36(mtime) + "/" + rel

    def serve_static(self, ctx):
        pathname = ctx.pathname
        versioned = re.match(r"^/__v/[^/]+/(.*)$", pathname)
        if versioned:
            pathname = "/" + versioned.group(1)
        elif ctx.has_query("v"):
            pathname = re.sub(r"^/__v/[^/]+/", "/", pathname)
        if pathname.startswith("/shared/"):
            return self.serve_shared(ctx)

        rel = pathname.lstrip("/") or "index.html"
        target = safe_path_join(self.public_dir, rel)
        if not target:
            return ctx.fail(403, "路径不合法")
        fallback = str(self.public_dir / "index.html")
        abs_path = target
        if not os.path.isfile(abs_path):
            abs_path = fallback
        if not os.path.isfile(abs_path):
            return ctx.text(500, "缺少前端文件：public/index.html")

        if os.path.splitext(abs_path)[1].lower() == ".html":
            with open(abs_path, "r", encoding="utf-8") as fh:
                html = fh.read()
            if not versioned and not ctx.has_query("v"):
                def repl(match):
                    attr, ref = match.group(1), match.group(2)
                    candidate = self.public_dir / ref.lstrip("/")
                    version = self.with_version(candidate)
                    return attr + '="' + version + '"' if version else match.group(0)

                html = re.sub(r'(href|src)="(/(?:styles\.css|main\.mjs|js/[^"]+))"', repl, html)
            body = html.encode("utf-8")
            return ctx.send_bytes(200, body, "text/html; charset=utf-8",
                                  headers={"Cache-Control": "no-store, must-revalidate"})

        cache = "public, max-age=31536000, immutable" if (versioned or ctx.has_query("v")) else "no-cache"
        return ctx.send_file(abs_path, cache=cache)
