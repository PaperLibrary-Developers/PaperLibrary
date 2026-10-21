# -*- coding: utf-8 -*-
"""title_extract.py — 从 PDF 里提取文章的真实标题

三级降级策略：
  1. PDF 内嵌元数据（publisher 通常写入了正确标题）
  2. 第一页最大字号文本（标题一般是页面上字最大的那几行）
  3. 清理后的文件名（兜底）
"""
import os
import re

import fitz

# ── 连字（ligature）还原：PDF 里 ﬁ 常被抽成 U+FB01 或直接丢失 ──
LIGATURES = {
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl",
    "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st",
}

# ── 明显不是标题的元数据 ──
_JUNK_RE = [
    re.compile(r"^\s*$"),
    re.compile(r"microsoft\s+word", re.I),
    re.compile(r"\.(docx?|pdf|tex|indd|qxd)\b", re.I),
    re.compile(r"^\s*untitled", re.I),
    re.compile(r"^\s*mmc\s*\d*", re.I),                  # Elsevier 补充材料编号
    re.compile(r"^\s*\d+\s*\.\.\s*\d+\s*$"),             # "1..1"
    re.compile(r"^[A-Z]{2,}\d{5,}"),                     # CHECAT101415
    re.compile(r"supplement(al|ary)\s+information", re.I),
    re.compile(r"supporting\s+information", re.I),
    re.compile(r"^\s*slide\s*\d", re.I),
    re.compile(r"^\s*table\s+of\s+contents?\s*$", re.I),
    re.compile(r"^\s*(tile|title)?,?\s*content\s*list\b", re.I),
    re.compile(r"^\s*(misc|general)\s+information\s*$", re.I),
    re.compile(r"^\s*[a-z0-9\-_]{1,20}\s*$"),            # 太短或 slug 状
    re.compile(r"^[^a-zA-Z一-鿿]*$"),            # 没有任何字母/汉字
]

# ── 第一页里不该算进标题的行 ──
_LINE_DROP_RE = [
    re.compile(r"^\s*(supplement(al|ary)|supporting)\s+information\s*$", re.I),
    re.compile(r"^\s*(article|review|research|communication|letter|paper|report)\s*$", re.I),
    re.compile(r"^\s*(abstract|introduction|keywords?|highlights?)\s*$", re.I),
    re.compile(r"^\s*https?://", re.I),
    re.compile(r"^\s*doi:?\s*10\.", re.I),
    re.compile(r"^\s*(received|accepted|published|available online)\b", re.I),
    re.compile(r"^\s*(vol\.?|volume|issue|no\.?)\s*\d", re.I),
    re.compile(r"^\s*issn\b", re.I),
    re.compile(r"^\s*\d{4}\s*,\s*\d+"),                  # 卷年页码行
]

# 期刊名常见特征（作为最大字出现时容易被误当标题）
_JOURNAL_HINT_RE = re.compile(
    r"\b(journal|letters?|communications?|chemistry|chem\.|review|transactions|"
    r"proceedings|advances|angewandte|nature|science|acs|rsc|wiley|elsevier|"
    r"springer|frontiers|society|bulletin)\b", re.I
)

# 章节标题 / 图表标题 —— 都不是文章标题
_SECTION_RE = re.compile(
    r"^\s*(results?(\s+and\s+discussion)?|introduction|conclusions?|experimental"
    r"|methods?|abstract|table\s+of\s+contents?|contents|discussion"
    r"|acknowledg\w*|references|bibliography|appendix|supporting"
    r"|supplement\w*|materials?\s+and|general\s+(information|procedures?)"
    r"|misc\s+information|table\s+of\s+content)\b", re.I)

_CAPTION_RE = re.compile(r"^\s*(figure|fig\.?|scheme|table|chart|equation)\s*s?\d", re.I)

# 散文虚词 —— 标题里几乎不会出现
_PROSE_RE = re.compile(
    r"\b(we|our|us|were|herein|thus|therefore|however|moreover|furthermore"
    r"|these authors|corresponding author|contributed equally)\b", re.I)

# 正文引用痕迹
_CITE_RE = re.compile(r"\((?:fig|table|scheme|eq)\.?\s*\d|\[\d+\]|\bet al\.", re.I)


def _looks_like_body_text(s: str) -> bool:
    """判断候选是否更像正文/图表说明，而不是文章标题"""
    if not s:
        return True
    # 去掉行首编号（"1. " / "3) " / "S2. "）后再判断
    bare = re.sub(r"^\s*(?:[A-Z]?\d+[\.\)]\s*)+", "", s)
    if not bare:
        return True
    # 以小写字母开头 → 几乎一定是正文片段
    if bare[0].islower():
        return True
    if _SECTION_RE.search(bare) or _CAPTION_RE.search(bare):
        return True
    if _PROSE_RE.search(s) or _CITE_RE.search(s):
        return True
    # 含分号的长句、或含斜杠路径
    if s.count(";") >= 1 and len(s) > 60:
        return True
    # 词数过少/过多
    words = s.split()
    if len(words) < 3 or len(words) > 45:
        return True
    # 逗号过多（正文特征）
    if s.count(",") >= 4:
        return True
    # 纯期刊名（词少且命中期刊特征）
    if len(words) <= 6 and _JOURNAL_HINT_RE.search(s) and not re.search(r"[:：]", s):
        return True
    # 编委会/出版名单
    if re.search(r"\b(acquisitions?|production|senior|assistant|copy)\s+editor"
                 r"|\bpermissions?\b|\bproduction\s+manager\b", s, re.I):
        return True
    return False


def _clean_text(s: str) -> str:
    """还原连字、统一空白、去掉控制字符"""
    if not s:
        return ""
    for bad, good in LIGATURES.items():
        s = s.replace(bad, good)
    s = s.replace("­", "")          # soft hyphen
    s = s.replace("﻿", "")
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _is_junk_title(s: str) -> bool:
    """判断候选标题是否是垃圾（文件名残留、补充材料编号等）"""
    if not s or len(s) < 12:
        return True
    for rx in _JUNK_RE:
        if rx.search(s):
            return True
    # 几乎全是数字/符号
    letters = sum(1 for c in s if c.isalpha() or "一" <= c <= "鿿")
    if letters < len(s) * 0.4:
        return True
    return False


def _similar_to_filename(title: str, filename: str) -> bool:
    """标题是否只是文件名的翻版（说明元数据不可信）

    注意：文件名本身常常就是文章标题（下载时自动命名），这种情况下
    元数据反而格式更好（有空格、大小写、下标），不能否决。
    只有当"标题"看起来仍像原始文件名时才否决。
    """
    if not filename:
        return False
    stem = os.path.splitext(os.path.basename(filename))[0]
    a = re.sub(r"[^a-z0-9]+", "", title.lower())
    b = re.sub(r"[^a-z0-9]+", "", stem.lower())
    if not a or not b:
        return False
    if not (a == b or (len(a) > 10 and (a in b or b in a))):
        return False
    # 已相似：若"标题"本身排版良好（含空格、非全小写），就采信它
    if " " in title and title != title.lower():
        return False
    return True


def _titlecase_if_shouting(s: str) -> str:
    """全大写标题转成词首大写（书封面常见）"""
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return s
    upper = sum(1 for c in letters if c.isupper())
    if upper / len(letters) < 0.75:
        return s
    small = {"a", "an", "and", "as", "at", "but", "by", "for", "in", "of",
             "on", "or", "the", "to", "vs", "via", "with"}
    words = s.split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if w.isupper() and len(w) > 1:
            if i == 0 or lw not in small:
                w = w.capitalize()
            else:
                w = lw
        out.append(w)
    return " ".join(out)


def _join_lines(lines) -> str:
    """把多行标题拼成一行：行尾连字符直接接上，否则空格分隔"""
    out = ""
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        if not out:
            out = ln
        elif out.endswith("-"):
            out += ln
        else:
            out += " " + ln
    return out


def _title_from_metadata(doc, filename: str):
    md = doc.metadata or {}
    raw = md.get("title") or ""
    t = _clean_text(raw)
    if _is_junk_title(t):
        return None
    if _similar_to_filename(t, filename):
        return None
    return t


def _title_from_fonts(doc, max_pages: int = 2):
    """从首页（最多前两页）按字号找标题"""
    candidates = []
    for pno in range(min(max_pages, doc.page_count)):
        try:
            page = doc[pno]
            ph = page.rect.height or 1
            d = page.get_text("dict")
        except Exception:
            continue

        items = []           # (size, y, x, text)
        for block in d.get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    t = _clean_text(span.get("text", ""))
                    if len(t) < 2:
                        continue
                    size = float(span.get("size", 0) or 0)
                    bbox = span.get("bbox", [0, 0, 0, 0])
                    y, x = float(bbox[1]), float(bbox[0])
                    if y > ph * 0.40:      # 标题只在页面上部 40%
                        continue
                    items.append((size, y, x, t))

        if not items:
            continue

        # 按字号分档，从大到小依次尝试
        # （SI 封面常把 "Supporting Information" 设成最大字，真标题在第二档）
        sizes = sorted({round(i[0], 1) for i in items}, reverse=True)
        for tier, base in enumerate(sizes[:3]):
            if tier > 0 and base < 11:      # 太小就不再往下找，那是正文
                break
            big = [i for i in items if abs(i[0] - base) < 0.6]
            if len(big) < 1:
                continue

            # 按 y 分行，同一行的按 x 排序拼接
            big.sort(key=lambda i: (round(i[1], 1), i[2]))
            lines, cur_y, cur = [], None, []
            for size, y, x, t in big:
                if cur_y is None or abs(y - cur_y) < 4:
                    cur.append(t)
                    cur_y = y if cur_y is None else cur_y
                else:
                    lines.append(" ".join(cur))
                    cur, cur_y = [t], y
            if cur:
                lines.append(" ".join(cur))

            # 丢掉不该算标题的行
            lines = [ln for ln in lines
                     if ln.strip() and not any(rx.search(ln) for rx in _LINE_DROP_RE)]
            if not lines:
                continue

            # 若第一行像期刊名，且后面还有行，则去掉它
            if len(lines) > 1 and _JOURNAL_HINT_RE.search(lines[0]) \
                    and not _JOURNAL_HINT_RE.search(" ".join(lines[1:])):
                lines = lines[1:]

            # 逐行累积：一行行加进去，只要当前累积还像标题就留着，
            # 一旦某行开始像正文就停（标题通常是最前面连续几行）
            acc = []
            for ln in lines:
                trial = _clean_text(_join_lines(acc + [ln]))
                if _looks_like_body_text(trial) or _is_junk_title(trial):
                    break
                acc.append(ln)
            if acc:
                cand = _clean_text(_join_lines(acc))
                if not _looks_like_body_text(cand) and not _is_junk_title(cand):
                    candidates.append(cand)
                    break       # 该页找到就不再看更小的字号

    if not candidates:
        return None
    # 取最长的候选（通常信息最完整）
    return max(candidates, key=len)


def _title_from_filename(filename: str) -> str:
    """兜底：从文件名清理出一个像样的标题"""
    name = os.path.basename(filename or "")
    name = re.sub(r"\.pdf$", "", name, flags=re.I)
    # 去掉 "作者 等 - 年份 - " 前缀（Zotero 导出格式）
    name = re.sub(r"^.{1,60}?\s*[-–—]\s*\d{4}\s*[-–—]\s*", "", name)
    # 去掉出版社稿件编号（RSC: d1qo01219c / ACS: ja5c10757 / Wiley: adma202001234）
    name = re.sub(r"\b[a-z]\d[a-z]{2}\d{5}[a-z]?\b", " ", name)
    name = re.sub(r"\b[a-z]{2}\d[a-z]\d{5}\b", " ", name)
    name = re.sub(r"\b[a-z]{2,4}\d{8,}\b", " ", name)
    name = re.sub(r"\bsi\s*\d{2,}\b", " ", name, flags=re.I)
    # 连字符/下划线转空格
    name = re.sub(r"[-_]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" .-–—")
    return name or "未命名文献"


def extract_title(pdf_path: str, filename: str = "") -> tuple:
    """返回 (标题, 来源)，来源 ∈ {'metadata', 'font', 'filename'}"""
    filename = filename or os.path.basename(pdf_path)
    try:
        with fitz.open(pdf_path) as doc:
            t = _title_from_metadata(doc, filename)
            if t:
                return (_titlecase_if_shouting(t), "metadata")

            t = _title_from_fonts(doc)
            if t:
                return (_titlecase_if_shouting(t), "font")
    except Exception:
        pass

    return (_title_from_filename(filename), "filename")


if __name__ == "__main__":
    import sys
    from collections import Counter
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import sqlite3

    conn = sqlite3.connect(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "data", "papers.db"))
    rows = conn.execute("SELECT filename, abs_path FROM papers").fetchall()
    src_count = Counter()
    out = []
    for fn, ap in rows:
        if not ap or not os.path.exists(ap):
            src_count["missing"] += 1
            continue
        title, src = extract_title(ap, fn)
        src_count[src] += 1
        out.append(f"[{src:8}] {title[:95]}")
    out.append("")
    out.append("来源统计: " + dict(src_count).__repr__())
    with open(os.path.join(os.environ["TEMP"], "title_preview.txt"), "w",
              encoding="utf-8") as f:
        f.write("\n".join(out))
    print("written")
