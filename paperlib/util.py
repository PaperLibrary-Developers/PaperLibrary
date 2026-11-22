"""I/O 小工具（对应 Node 的 server/utils.js）。

纯函数（文本清洗/分词/标题/文件名）在 shared/pure/text.py；这里只放 I/O 与 id 生成，
并把纯函数按 Node 版的习惯 re-export 一份，业务代码 import 一个 util 就够。
"""
from __future__ import annotations

import datetime
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

from shared.pure.text import (  # noqa: F401  (re-export)
    CJK_RE,
    clamp_int,
    clean_title,
    escape_html,
    is_formula_fragment,
    join_formula_fragments,
    normalize_doi,
    query_terms,
    safe_path_join,
    sanitize_file_name,
    strip_bom,
    to_string_array,
    tokenize,
    truncate,
)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".otf": "font/otf",
    ".pfb": "application/octet-stream",
    ".bcmap": "application/octet-stream",
    ".wasm": "application/wasm",
    ".map": "application/json; charset=utf-8",
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".bib": "text/plain; charset=utf-8",
}


def mime_type(name) -> str:
    return MIME.get(Path(str(name)).suffix.lower(), "application/octet-stream")


def uid() -> str:
    """16 位十六进制 id（与 Node 版 crypto.randomBytes(8).toString('hex') 同形）。"""
    return secrets.token_hex(8)


def ensure_dir(path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def read_json(path, fallback: Any = None) -> Any:
    """读 JSON；容忍 BOM；读不到/解析失败返回 fallback（不抛错）。"""
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            return json.loads(strip_bom(fh.read()))
    except (OSError, ValueError):
        return fallback


def write_json_atomic(path, data: Any) -> None:
    p = Path(path)
    ensure_dir(p.parent)
    tmp = p.with_name(p.name + ".tmp-" + uid())
    try:
        with tmp.open("x", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        for attempt in range(10):
            try:
                os.replace(tmp, p)
                return
            except PermissionError as exc:
                # Windows readers may hold the destination without delete sharing.
                if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 9:
                    raise
                time.sleep(0.02)
    finally:
        tmp.unlink(missing_ok=True)


def dump_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def parse_int(value, fallback=None):
    try:
        if value is None or value == "":
            return fallback
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def parse_float(value, fallback=None):
    try:
        if value is None or value == "":
            return fallback
        return float(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def now_ms() -> int:
    return int(time.time() * 1000)


def now_iso() -> str:
    """与 Node 的 new Date().toISOString() 同格式：2026-11-07T02:39:02.123Z。"""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def read_body(handler, limit: int = 40 * 1024 * 1024) -> bytes:
    """读取原始请求体（上传接口用）；超限抛 ValueError（由 HTTP 层转 413/400）。"""
    length = handler.headers.get("Content-Length")
    if length is not None:
        try:
            size = int(length)
        except ValueError:
            size = -1
        if size > limit:
            raise ValueError("请求体过大（上限 %dMB）" % round(limit / 1024 / 1024))
        if size == 0:
            return b""
        if size > 0:
            return handler.rfile.read(size)
    # 分块读取（无 Content-Length 的情况）
    chunks = []
    total = 0
    while True:
        chunk = handler.rfile.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ValueError("请求体过大（上限 %dMB）" % round(limit / 1024 / 1024))
        chunks.append(chunk)
    return b"".join(chunks)


FORBIDDEN_JSON_CONTENT_TYPES = frozenset({
    "text/plain",
    "application/x-www-form-urlencoded",
    "multipart/form-data",
})


class UnsupportedMediaTypeError(Exception):
    """JSON 端点拒绝浏览器简单请求 Content-Type（防跨站 CSRF 走私 JSON）。"""

    status = 415


def read_json_body(handler, limit: int | None = None) -> Any:
    headers = getattr(handler, "headers", None)
    raw_ctype = str((headers.get("Content-Type") if headers else "") or "").strip()
    media_type = raw_ctype.split(";", 1)[0].strip().lower()
    if media_type in FORBIDDEN_JSON_CONTENT_TYPES:
        raise UnsupportedMediaTypeError("JSON 接口不支持该 Content-Type，请使用 application/json")
    limit = limit or 40 * 1024 * 1024
    raw = read_body(handler, limit)
    if not raw:
        return {}
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return {}
    try:
        return json.loads(text)
    except ValueError as exc:
        raise ValueError("请求体不是合法 JSON：" + str(exc))
