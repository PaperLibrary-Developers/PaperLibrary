"""移植自 Sci-lib shared/html-text.mjs：先解实体、再剥标签，反复到稳定。

浏览器侧仍用 shared/html-text.mjs；Python 侧用本文件。两侧行为一致（有对照测试）。
"""
from __future__ import annotations

import re

ENTITIES = {
    "amp": "&", "lt": "<", "gt": ">", "quot": '"', "apos": "'", "nbsp": " ",
    "ndash": "–", "mdash": "—", "minus": "−", "times": "×", "middot": "·", "deg": "°",
}

TAG_NAME = r"[A-Za-z][A-Za-z0-9:._-]*"
TAG_ATTR = r'''(?:\s+[A-Za-z_:][-A-Za-z0-9_:.]*(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s<>"']+))?)*'''
TAG_OPEN = r"<\/?" + TAG_NAME + TAG_ATTR + r"\s*>"


def unescape_html(value) -> str:
    s = "" if value is None else str(value)

    def repl(match):
        body = match.group(1)
        if body[:1] == "#":
            try:
                code = int(body[2:], 16) if body[1:2].lower() == "x" else int(body[1:])
            except ValueError:
                return match.group(0)
            if code > 0:
                try:
                    return chr(code)
                except ValueError:
                    return match.group(0)
            return match.group(0)
        return ENTITIES.get(body.lower(), match.group(0))

    return re.sub(r"&(#x?[0-9a-f]+|[a-z]+);", repl, s, flags=re.I)


def strip_tags(value) -> str:
    s = "" if value is None else str(value)
    s = re.sub(r"<\s*(?:sup|sub)\s*>", "", s, flags=re.I)
    s = re.sub(r"<\s*/\s*(?:sup|sub)\s*>", "", s, flags=re.I)
    # 标签夹在两个词之间时先换成一个空格；否则直接删掉
    s = re.sub(r"([A-Za-z0-9])\s*" + TAG_OPEN + r"\s*(?=[A-Za-z0-9])", r"\1 ", s)
    s = re.sub(TAG_OPEN, "", s)
    return s


def to_plain_text(value, options=None) -> str:
    opts = options or {}
    s = "" if value is None else str(value)
    for _ in range(3):
        before = s
        s = strip_tags(unescape_html(s))
        if s == before:
            break
    if opts.get("keepAngleBrackets") is not True:
        s = re.sub(r"<\s*>", "", s)
        s = re.sub(r"\s*([,.;:!?，。；：！？、）)\]])", r"\1", s)
    return re.sub(r"\s+", " ", s).strip()
