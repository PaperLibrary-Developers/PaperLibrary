r"""共享内核 · 从 PDF 行文本抽摘要（移植自 Sci-lib shared/pure/abstract-extract.js）。

契约：输入是 PDF 头几页的「视觉行」数组（按 y 坐标聚合，不是整页一行），两步走：
  ① 找标题 ABSTRACT / CONSPECTUS / Summary / 摘要；
  ② 没有标题时退回「位置法」：用已知标题跳过标题/作者/单位块，再跳过 DOI、投稿日期、
     页眉页脚，取第一段成段的正文（Nature / Communications Chemistry / 旧版 Angewandte 这么排）。
抽不到返回 None，让上层继续兜底（Crossref → OpenAlex）。

注意：JS 的 \p{L} 在 Python re 里没有，统一用 text_repair.LETTER（[^\W\d_]）近似。
零 I/O、零全局态。
"""
from __future__ import annotations

import re

from shared.pure.text_repair import LETTER, build_word_set, repair_text

# 摘要标题。ACS 会把 ABSTRACT 做成小型大写字母，文本层可能给出各种间距
HEADING = re.compile(
    r"^\s*(?:[■◆●▪◼▍►]\s*)?(?:A\s?B\s?S\s?T\s?R\s?A\s?C\s?T|"
    r"C\s?O\s?N\s?S\s?P\s?E\s?C\s?T\s?U\s?S|Summary|摘\s*要)\s*[:.．。\-—–]?\s*",
    re.IGNORECASE,
)

# 一眼就不是摘要内容的行（页眉页脚、投稿信息、版权、目录、数据库样板）
NOISE = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"^this journal is\b",
    r"^subscriber access provided by\b",
    r"^downloaded (on|via|from)\b",
    r"^view (article|journal) online\b",
    r"^check for updates\b",
    r"^article in press\b",
    r"^cite this\b",
    r"^first published\b",
    r"^published as part of\b",
    r"^(received|accepted|published|revised|revised manuscript)\b\s*[:\s]",
    r"^https?://",
    r"^www\.",
    r"^dx\.doi\.org\b",
    r"^doi\s*[:\s]",
    r"^pubs\.acs\.org\b",
    r"^©|\bcopyright\b",
    r"^journal homepage\b",
    r"^volume \d",
    r"^vol\.?\s*\d",
    r"^issue \d",
    r"^no\.?\s*\d",
    r"^pp\.\s*\d",
    r"^page \d",
    r"^\d+\s*[-–]\s*\d+$",
    r"^contents?$",
    r"^a nature portfolio journal\b",
    r"^advertisement\b",
    r"^return to issue\b",
    r"^prep?vious\b",
    r"^next\b",
    r"^to whom correspondence should be addressed\b",
    r"^contributed equally\b",
    r"^e-?mail\b",
    r"^\S+@\S+$",
    r"^(phone|fax)\b",
    r"^article$",
))

# 摘要到此为止：下一个真正的区块开始了
STOP = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"^\s*[■◆●▪◼▍]\s*\S",                                    # ACS/Wiley 的区块符号（■ INTRODUCTION）
    r"^\s*(?:\d+\.?\s*)?(introduction|background|results? and discussion|discussion|conclusions?|"
    r"experimental( section)?|methods?|materials? and methods?)\s*$",
    r"^\s*(?:\d+\.?\s*)?(keywords?|key words|graphical abstract|abbreviations)\b",
    r"^\s*(figure|fig\.?|table|scheme|chart|equation)\s*\d+",
    r"^\s*\d+\.?\s+[A-Z][a-z]",                               # 目录行 "1 Introduction 3"
    r"^\s*(supporting information|associated content|author information|acknowledg\w*|references?)"
    r"\s*[:.]?\s*$",
))

# 纯角标/参考文献标记之类的碎片行（裸数字也算碎片，不能当「区块开始」）
FRAGMENT = tuple(re.compile(pattern) for pattern in (
    r"^\d{1,4}$",
    r"^\[\s*\d+(?:\s*[,–-]\s*\d+)*\s*\]$",
    r"^\d+(?:[,–-]\s*\d+)*[a-z]?$",
    r"^[a-z]$",
    r"^[∗*†‡§¶✉]+$",
    r"^[（(]\s*\d+\s*[)）]$",
))

_FUNC_WORD_RE = re.compile(
    r"^(?:the|a|an|of|in|on|at|to|for|with|by|as|is|are|was|were|be|been|being|has|have|had|"
    r"we|our|this|that|these|those|it|its|and|or|but|not|can|may|such|than|then|which|who|"
    r"whose|from|into|over|under|between|their|they|there|here|also|more|most|other|some|all|"
    r"both|each|any|so|if|when|while|because|however|thus|therefore)$",
    re.IGNORECASE,
)
_NAME_ATOM = r"(?:" + LETTER + r"|[.\-'])"
_AUTHOR_NAME_RE = re.compile(r"^[A-Z]" + _NAME_ATOM + r"*(?:\s+[A-Z]" + _NAME_ATOM + r"*){1,3}\s*[,&]")
_SENTENCE_END_RE = re.compile(r'[.!?。！？]["' + "'" + r"”’)]?\s*$")
_DOI_OR_URL_RE = re.compile(r"\s*\b(?:dx\.doi\.org|https?://)\S+", re.IGNORECASE)
_DOWNLOADED_RE = re.compile(r"\b(?:Downloaded via|Downloaded on|Downloaded from)\b[^.]*\.", re.IGNORECASE)
_SMALL_CAPS_RE = re.compile(r"\b([A-Z])(?:\s([A-Z])){2,}([a-z]?)\b")
_TRIM_EDGES_RE = re.compile(r"^[\s,.;:]+|[\s,;:]+$")
_TOKEN_RE = re.compile(r"[^\W_]+")


def _s(value) -> str:
    return "" if value is None else str(value)


def _num_or(value, fallback):
    try:
        num = float(value)
    except (TypeError, ValueError):
        return fallback
    if num != num:  # NaN
        return fallback
    return num if num else fallback


def is_noise(line) -> bool:
    return any(pattern.search(_s(line)) for pattern in NOISE)


def is_stop(line) -> bool:
    return any(pattern.search(_s(line)) for pattern in STOP)


def is_fragment(line) -> bool:
    return any(pattern.search(_s(line)) for pattern in FRAGMENT)


def looks_like_prose(line) -> bool:
    """像「成段正文」的行：够长，或者以句末标点结束。"""
    s = _s(line).strip()
    if len(s) >= 40:
        return True
    return bool(re.match(r"^[A-Z]", s)) and _SENTENCE_END_RE.search(s) is not None


def func_word_count(text) -> int:
    total = 0
    for word in _s(text).split():
        cleaned = re.sub(r"[\W\d_]", "", word)
        if _FUNC_WORD_RE.match(cleaned):
            total += 1
    return total


def looks_like_author_line(line) -> bool:
    """作者行：逗号分隔的短片段、尾部角标、and 串联，且几乎没有虚词。"""
    s = _s(line).strip()
    if not s or len(s) > 220:
        return False
    if re.search(r"[*†‡§¶✉]\s*$", s):
        return True
    if re.search(r"\d\s*,\s*\d", s):
        return True
    if _SENTENCE_END_RE.search(s):     # 以句号结尾的通常是正文
        return False
    commas = s.count(",")
    if func_word_count(s) > 1:
        return False
    if commas >= 2 and len(s) < 170:
        return True
    if re.search(r"\band\b", s, re.IGNORECASE) and commas >= 1 and len(s) < 130:
        return True
    if commas >= 1 and _AUTHOR_NAME_RE.match(s):
        return True
    return False


def looks_like_continuation(line) -> bool:
    """短行但明显是段落续行（以逗号/连字符结尾，或小写开头）——不能当标题丢掉。"""
    s = _s(line).strip()
    if not s:
        return False
    if re.search(r"[,;:–—-]$", s):
        return True
    return bool(re.match(r"^[a-z(]", s))


def looks_like_heading(line) -> bool:
    """短行且像小标题（首字母大写、不是续行、没有句末标点）。"""
    s = _s(line).strip()
    if not s or len(s) > 48:
        return False
    if looks_like_continuation(s):
        return False
    if _SENTENCE_END_RE.search(s):
        return False
    return bool(re.match(r"^[A-Z■◆●▪]", s))


def title_token_set(title):
    return set(_TOKEN_RE.findall(_s(title).lower()))


def overlaps_title(line, title_tokens) -> bool:
    if not title_tokens:
        return False
    words = _TOKEN_RE.findall(_s(line).lower())
    if not words:
        return False
    hit = sum(1 for word in words if word in title_tokens)
    return hit / len(words) >= 0.6


def clean_abstract(text, vocab=None) -> str:
    """清洗抽出来的摘要：去 DOI/下载水印、修连字与断词、小型大写字母，收边。"""
    s = _DOI_OR_URL_RE.sub(" ", _s(text))
    s = _DOWNLOADED_RE.sub(" ", s)
    s = repair_text(s, vocab)
    # 相邻的单个大写字母是小型大写字母被拆开的结果（"S C Cs" → "SCCs"）
    s = _SMALL_CAPS_RE.sub(lambda m: m.group(0).replace(" ", ""), s)
    return _TRIM_EDGES_RE.sub("", s)


def trim_to_limit(text, max_chars) -> str:
    """截断时尽量切在句末，别把句子砍成半句。"""
    s = _s(text)
    limit = int(max_chars)
    if len(s) <= limit:
        return s
    head = s[:limit]
    cut = max(head.rfind(". "), head.rfind("。"), head.rfind("? "), head.rfind("! "))
    if cut >= limit * 0.5:
        return head[:cut + 1].strip()
    space = head.rfind(" ")
    return (head[:space] if space > 0 else head).strip() + "…"


def extract_abstract_from_lines(lines, opts=None):
    """从行数组里抽摘要。

    lines: 视觉行字符串列表；opts: {"title": str, "lineY": [float], "maxChars": int}
    return: {"text", "method": "heading"|"position", "startLine", "endLine"} | None
    """
    o = opts or {}
    src = [_s(line) for line in (lines or []) if _s(line).strip()]
    if not src:
        return None
    max_chars = int(max(400, _num_or(o.get("maxChars") or o.get("max_chars"), 2500)))
    title_tokens = title_token_set(o.get("title"))

    # 只在前几页里找；摘要不会出现在第 3 页之后
    scan = src[:140]
    # 本文词表：判断「连字符断行」是被切开的词还是真正的连字符复合词
    vocab = build_word_set(" ".join(scan))

    # ---------- ① 有标题：从标题行取到下一个区块 ----------
    for i, line in enumerate(scan):
        m = HEADING.match(line)
        if not m:
            continue
        rest = line[m.end():].strip()
        parts = [rest] if rest else []
        end = i
        for j in range(i + 1, len(scan)):
            cur = scan[j]
            if is_stop(cur):
                break
            if is_noise(cur) or is_fragment(cur):
                continue
            parts.append(cur)
            end = j
            if len(" ".join(parts)) > max_chars * 2:
                break
        text = clean_abstract(" ".join(parts), vocab)
        # 标题匹配也可能撞上目录里的 "Abstract" 或图注，太短就别信
        if len(text) >= 120:
            return {"text": trim_to_limit(text, max_chars), "method": "heading", "startLine": i, "endLine": end}

    # ---------- ② 没标题：取「标题/作者/单位块」之后的第一段成段正文 ----------
    ys = o.get("lineY") if isinstance(o.get("lineY"), list) else o.get("line_y")
    if not isinstance(ys, list) or len(ys) < len(scan):
        ys = None
    leadings = []
    for k in range(1, len(scan)):
        d = (ys[k - 1] - ys[k]) if ys else 0
        if 2 < d < 40:
            leadings.append(d)
    leadings.sort()
    leading = leadings[len(leadings) // 2] if leadings else 0
    break_gap = leading * 1.8 if leading else 0

    def is_para_break(k: int) -> bool:
        if not ys or not break_gap or k <= 0:
            return False
        return (ys[k - 1] - ys[k]) > break_gap

    i = 0
    while i < len(scan):
        line = scan[i]
        if is_noise(line) or is_fragment(line):
            i += 1
            continue
        if overlaps_title(line, title_tokens):
            i += 1
            continue
        if looks_like_author_line(line):
            i += 1
            continue
        if is_stop(line):
            return None
        if looks_like_prose(line):
            break
        i += 1
    if i >= len(scan):
        return None

    parts = []
    end = i
    for j in range(i, len(scan)):
        line = scan[j]
        if is_stop(line):
            break
        if is_noise(line) or is_fragment(line):
            continue
        # 段落结束：已经攒够一段文字，又出现明显段间距 → 摘要到头了
        if j > i and parts and is_para_break(j) and len(" ".join(parts)) >= 150:
            break
        prev_ends_sentence = bool(_SENTENCE_END_RE.search(parts[-1] if parts else ""))
        if (not looks_like_prose(line)) and (not looks_like_continuation(line)) and prev_ends_sentence \
                and looks_like_heading(line):
            break
        parts.append(line)
        end = j
        if len(" ".join(parts)) > max_chars * 2:
            break
    text = clean_abstract(" ".join(parts), vocab)
    if len(text) < 150:
        return None
    return {"text": trim_to_limit(text, max_chars), "method": "position", "startLine": i, "endLine": end}
