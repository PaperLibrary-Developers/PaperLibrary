"""共享内核 · 长文本选段（移植自 Sci-lib shared/pure/passages.js）。

契约：docs §3.5 pick_passages(text, terms, options=None) -> list[dict]；对话/深度分析按问题
从全文里挑「开头 + 与问题相关的片段 + 结尾」，在预算内控制 token。

页码锚点的坑：PDF 文本层里 "S-55" 常被渲染成 "S − 55"（连字符变 − 还带空格），
所以必须用宽松正则，不能拿字面串去 indexOf。

零 I/O、零全局态。
"""
from __future__ import annotations

import math
import re

from shared.pure.text import tokenize

# 关键词里太常见的词，命中一片反而没信息量
STOP = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "have", "has", "had",
    "not", "but", "you", "your", "can", "will", "would", "should", "what", "which", "when", "where",
    "how", "why", "does", "did", "been", "into", "than", "then", "they", "them", "their", "there",
    "about", "also", "such", "some", "any", "all", "one", "two", "use", "used", "using", "between",
})

# 问题里的「字母 + 数字」锚点：S55 / S-55 / GP-2（对应 JS 的 \p{L}\p{N} 写法）
_ANCHOR_RE = re.compile(r"(?:^|[_\W])([A-Za-z]{1,3})\s*[-–−—]?\s*(\d{1,4})(?!\d)", re.UNICODE)
_CJK_RE = re.compile("[\u3400-\u9fff]")

HIT_PREFIX = "……（以下是与问题相关的片段）……\n"
SEP = "\n……\n"
TAIL_PREFIX = "……（以下为文档结尾）……\n"


def _s(value) -> str:
    return "" if value is None else str(value)


def _num_or(value, fallback):
    """等价 JS 的 Number(x) || fallback：非数字与 0 都落回 fallback。"""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    if num != num:  # NaN
        return fallback
    return num if num else fallback


def anchor_patterns(question):
    """从问题里抠「字母 + 数字」的锚点，返回宽松匹配用的正则列表（S − 55 / S-55 / S55 都能中）。"""
    out = []
    seen = set()
    for m in _ANCHOR_RE.finditer(_s(question)):
        letter, digits = m.group(1), m.group(2)
        key = letter.lower() + digits
        if key in seen:
            continue
        seen.add(key)
        out.append(re.compile(
            re.escape(letter) + r"\s*[-–−—]?\s*" + digits + r"(?!\d)",
            re.IGNORECASE | re.UNICODE,
        ))
    return out


def keywords(question):
    """问题里的关键词：拉丁词（≥3、非停用词）与中日韩二字词（bigram，单字太泛）。"""
    out = []
    seen = set()
    for term in tokenize(_s(question)):
        if len(term) < 2:
            continue
        is_cjk = bool(_CJK_RE.search(term))
        if not is_cjk and (len(term) < 3 or term in STOP):
            continue
        if term in seen:
            continue
        seen.add(term)
        out.append(term)
    return out


def find_hits(text, question, limit=40):
    """收集命中位置：返回 {"hits": 已排序去重的下标, "anchors": 页码锚点命中数}。"""
    src = _s(text)
    lower = src.lower()
    seen = set()
    hits = []
    anchors = 0
    for pattern in anchor_patterns(question):
        guard = 0
        for m in pattern.finditer(src):
            if guard >= 200:
                break
            guard += 1
            if m.start() not in seen:
                seen.add(m.start())
                hits.append(m.start())
            anchors += 1
    for kw in keywords(question):
        needle = kw.lower()
        if len(needle) < 2:
            continue
        start = 0
        guard = 0
        while guard < 200:
            guard += 1
            at = lower.find(needle, start)
            if at < 0:
                break
            if at not in seen:
                seen.add(at)
                hits.append(at)
            start = at + len(needle)
    hits.sort()
    cap = int(_num_or(limit, 40))
    return {"hits": hits[:cap], "anchors": anchors}


def merge_windows(hits, radius):
    """把相邻/重叠的窗口并起来，避免同一处反复贴；返回 [{"start","end"}]。"""
    spans = []
    for at in hits:
        begin = max(0, int(at) - int(radius))
        end = int(at) + int(radius)
        if spans and begin <= spans[-1]["end"] + 200:
            spans[-1]["end"] = max(spans[-1]["end"], end)
        else:
            spans.append({"start": begin, "end": end})
    return spans


def _question_of(terms) -> str:
    """terms 可以是问题串，也可以是词表（T4 传关键词列表）。"""
    if isinstance(terms, str) or terms is None:
        return _s(terms)
    if isinstance(terms, (list, tuple, set)):
        return " ".join(_s(t) for t in terms if _s(t).strip())
    return _s(terms)


def _budget_of(options) -> int:
    return int(max(1000, int(_num_or((options or {}).get("budget"), 12000))))


def _pick(text, question, options=None):
    """内部统一实现：返回 head/chunks/tail 三段与统计。"""
    o = options or {}
    src = _s(text)
    budget = _budget_of(o)
    result = {"head": "", "chunks": [], "tail": "", "anchors": 0, "truncated": False}
    if not src:
        return result
    if len(src) <= budget:
        result["head"] = src
        return result

    head_chars = int(max(200, min(_num_or(o.get("headChars"), 2500), math.floor(budget * 0.5))))
    # JS 里 Number(undefined) 是 NaN，而 NaN == null 为假 → 未传 tailChars 时尾部其实是空（保持同行为）
    raw_tail = float("nan")
    if "tailChars" in o:
        try:
            raw_tail = float(o.get("tailChars"))
        except (TypeError, ValueError):
            raw_tail = 0.0 if o.get("tailChars") is None else float("nan")
    tail_chars = 0
    if raw_tail == raw_tail:  # 不是 NaN
        tail_chars = int(max(0, min(raw_tail, math.floor(budget * 0.25))))
    radius = int(max(200, _num_or(o.get("radius"), 700)))

    head = src[:head_chars]
    tail = src[-tail_chars:] if tail_chars else ""

    found = find_hits(src, question, 60)
    spends_left = budget - len(head) - len(tail)
    chunks = []
    if spends_left > radius:
        for span in merge_windows(found["hits"], radius):
            # 已经在开头里出现过的，不重复贴
            if span["start"] < head_chars:
                continue
            piece = src[span["start"]:span["end"]]
            if len(piece) > spends_left:
                keep = max(0, spends_left)
                chunks.append({"start": span["start"], "end": span["start"] + keep, "text": piece[:keep]})
                spends_left = 0
                break
            chunks.append({"start": span["start"], "end": span["end"], "text": piece})
            spends_left -= len(piece)

    result.update({"head": head, "chunks": chunks, "tail": tail, "anchors": found["anchors"], "truncated": True})
    return result


def pick_passages_report(text, terms, options=None):
    """与 JS pickPassages 同构的报告：{text, used, passages, anchors, truncated}。"""
    src = _s(text)
    budget = _budget_of(options)
    if not src:
        return {"text": "", "used": 0, "passages": 0, "anchors": 0, "truncated": False}
    picked = _pick(src, _question_of(terms), options)
    if len(src) <= budget:
        return {"text": src, "used": len(src), "passages": 1, "anchors": 0, "truncated": False}

    segs = [picked["head"].strip()]
    chunks = picked["chunks"]
    if chunks:
        segs.append(HIT_PREFIX + SEP.join(c["text"].strip() for c in chunks))
    if picked["tail"]:
        segs.append(TAIL_PREFIX + picked["tail"].strip())
    out = "\n\n".join(seg for seg in segs if seg)
    return {
        "text": out,
        "used": len(out),
        "passages": len(chunks),
        "anchors": picked["anchors"],
        "truncated": len(out) < len(src),
    }


def pick_passages(text, terms, options=None):
    """docs §3.5 契约入口：返回片段列表 [{kind, text, start, end}]（head / hit / tail，按出现顺序）。"""
    src = _s(text)
    if not src:
        return []
    budget = _budget_of(options)
    picked = _pick(src, _question_of(terms), options)
    if len(src) <= budget:
        return [{"kind": "head", "text": src, "start": 0, "end": len(src)}]
    parts = []
    head_text = picked["head"].strip()
    if head_text:
        parts.append({"kind": "head", "text": head_text, "start": 0, "end": len(picked["head"])})
    for chunk in picked["chunks"]:
        piece = chunk["text"].strip()
        if piece:
            parts.append({"kind": "hit", "text": piece, "start": chunk["start"], "end": chunk["end"]})
    tail_text = picked["tail"].strip()
    if tail_text:
        parts.append({
            "kind": "tail", "text": tail_text,
            "start": len(src) - len(picked["tail"]), "end": len(src),
        })
    return parts
