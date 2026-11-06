"""共享内核 · 文本纯函数（移植自 Sci-lib shared/pure/text.js）。

零 I/O、零全局态；HTML 处理来自 shared/html-text.mjs（Python 版见 shared/pure/html_text.py）。
"""
from __future__ import annotations

import re
from pathlib import Path

from shared.pure.html_text import to_plain_text

CJK_RE = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff]")


def escape_html(value) -> str:
    s = "" if value is None else str(value)
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def to_string_array(value):
    """任意输入 → 字符串数组：去空、去重（大小写不敏感）、保序。"""
    if isinstance(value, (list, tuple)):
        items = list(value)
    elif isinstance(value, str):
        items = re.split(r"[,，;；\n]", value)
    elif value is None:
        items = []
    else:
        items = [value]
    seen = set()
    out = []
    for raw in items:
        s = ("" if raw is None else str(raw)).strip()
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out


def clamp_int(value, minimum: int, maximum: int, fallback: int) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return min(maximum, max(minimum, n))


def safe_path_join(root_dir, relative):
    """路径拼接 + 越界防护（对应 Node 的 safePathJoin）；越界返回 None。"""
    root = Path(str(root_dir)).resolve()
    raw = str(relative or "").lstrip("/\\")
    try:
        target = (root / raw).resolve()
    except OSError:
        return None
    if target != root and root not in target.parents:
        return None
    return str(target)


def strip_bom(text) -> str:
    return re.sub("^\ufeff", "", "" if text is None else str(text))


def tokenize(text):
    """中英文混合分词：拉丁整词（>=2 或单字母）+ 中日韩单字与 bigram。"""
    out = []
    if not text:
        return out
    s = str(text).lower()
    latin = ""
    cjk = []

    def flush_latin():
        nonlocal latin
        if len(latin) == 1:
            if latin.isalpha():
                out.append(latin)
        elif len(latin) >= 2 and not latin.isdigit():
            out.append(latin)
        latin = ""

    def flush_cjk():
        nonlocal cjk
        if not cjk:
            return
        for i, ch in enumerate(cjk):
            out.append(ch)
            if i + 1 < len(cjk):
                out.append(ch + cjk[i + 1])
        cjk = []

    for ch in s:
        if ch.isascii() and ch.isalnum():
            flush_cjk()
            latin += ch
        elif CJK_RE.match(ch):
            flush_latin()
            cjk.append(ch)
        else:
            flush_latin()
            flush_cjk()
    flush_latin()
    flush_cjk()
    return out


def query_terms(query):
    return [t.strip().lower() for t in re.split(r"[\s,，;；]+", str(query or "")) if t.strip()]


def normalize_doi(value) -> str:
    s = str(value or "").strip()
    if not s:
        return ""
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip()


def truncate(text, maximum: int) -> str:
    s = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(s) <= maximum:
        return s
    return s[: maximum - 1] + "…"


def clean_title(value) -> str:
    return join_formula_fragments(to_plain_text(value))


def is_formula_fragment(token) -> bool:
    token = str(token)
    if re.fullmatch(r"\d+", token):
        return True
    if re.fullmatch(r"[A-Z]", token):
        return True
    if re.fullmatch(r"(?:[IVXLC]{1,4}|[A-Z]{2,3})", token):
        return True
    return False


def join_formula_fragments(text) -> str:
    """把化学式被标签切开的碎片接回去：M II 4 L 6 → MII4L6。"""
    words = str(text).split(" ")
    if len(words) < 2:
        return str(text)
    out = []
    run = []

    def flush():
        if not run:
            return
        has_digit = any(re.fullmatch(r"\d+", w) for w in run)
        out.append("".join(run) if len(run) >= 2 and has_digit else " ".join(run))
        del run[:]

    for word in words:
        if is_formula_fragment(word):
            run.append(word)
        else:
            flush()
            out.append(word)
    flush()
    return re.sub(r"\s+", " ", " ".join(w for w in out if w)).strip()


def sanitize_file_name(title, max_length: int = 70) -> str:
    name = "" if title is None else str(title)
    name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    name = re.sub(r"([A-Za-z])(\d{4})", r"\1 \2", name)
    name = re.sub(r'[\\/:*?"<>|\u0000-\u001f]', " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = re.sub(r"^[.\s]+", "", name)
    name = re.sub(r"[.\s]+$", "", name)
    if len(name) > max_length:
        name = name[:max_length].strip()
    return name or "document"


def zh_collation_key(text):
    """近似 ICU zh-Hans-CN 的排序键：汉字在拉丁字母之前，拉丁不区分大小写。

    Node 用 localeCompare(x, 'zh-Hans-CN')；Python 没有 ICU，这里按字符类别生成可比较键
    （0=汉字/日文/韩文，1=字母数字，2=空白，3=其他）。汉字内部按码位，不做拼音排序。
    """
    key = []
    for ch in str(text or "").casefold():
        if CJK_RE.match(ch):
            key.append((0, ch))
        elif ch.isalnum():
            key.append((1, ch))
        elif ch.isspace():
            key.append((2, " "))
        else:
            key.append((3, ch))
    return key
