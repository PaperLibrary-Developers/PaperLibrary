"""保留原版式的原位翻译管线（PyMuPDF 回填）。

MinerU 提供物理段框和类型，PyMuPDF 提供原始字号、基线与备用分块。
翻译前按栏框估算容量，使用编号占位保护公式及引用。回填前用 Story
实排同栏正文，再擦除能完整替换的文字。
"""
from __future__ import annotations

import json
import lzma
import math
import re
import zipfile
from collections import Counter
from contextlib import contextmanager
from difflib import SequenceMatcher
from html import unescape
from importlib.util import find_spec
from io import BytesIO
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from unicodedata import east_asian_width, normalize

import pymupdf as fitz

from paperlib.adapters.mineru_layout import (
    iter_child_blocks, iter_direct_lines, restore_cross_page_spans, valid_bbox,
)

TRANSLATABLE = ("text", "title", "abstract", "caption")
CAPTION_TYPES = ("caption", "image_caption", "chart_caption", "table_caption")
CAPTION_START = re.compile(
    r"^(?:fig(?:ure)?\.?|scheme|table|chart|图|表)\s*[A-ZＡ-Ｚ]?[0-9０-９]+", re.I
)
FONT_DIR = Path(__file__).resolve().parents[2] / "assets" / "translation-fonts"
SERIF_FONT = "SourceHanSerifCN-VF.ttf.xz"
SERIF_REGULAR = "SourceHanSerifCN-Regular.ttf"
SERIF_BOLD = "SourceHanSerifCN-Bold.ttf"
PLACEHOLDER = re.compile(r"⟦.*?⟧", re.S)
PROTECTED_TOKEN = re.compile(
    r"(?:⟦[^⟦⟧]*⟧|<sup>[^<>]*</sup>|<sub>[^<>]*</sub>|−\d+(?:\.\d+)?)+"
)
TAG_RE = re.compile(r"(</?(?:sup|sub|i|b|em|strong)>)")
LETTER_SCRIPT = re.compile(r"<(sup|sub)>([^<>]*[a-z][^<>]*)</\1>")
NATIVE_FORMULA = re.compile(
    r"(?<![A-Za-z])(?:[A-Z][a-z]?(?:<sub>[\d/]+</sub>|<sup>[IVX]+</sup>)+)+(?:⊂\d+)?"
    r"|\b\d+(?:\.\d+)?°"
)
PDF_CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
NATIVE_GLYPHS = {
    "AdvP4C4E74": str.maketrans({"\x84": "⊂", "¢": "−"}),
    "AdvPi1": str.maketrans({"8": "°"}),
    "AdvPS_GRTI": str.maketrans({"p": "π"}),
    "AdvPS_GRTU": str.maketrans({"p": "π"}),
}
GREEK = {
    r"\Delta": "Δ", r"\delta": "δ", r"\gamma": "γ", r"\alpha": "α", r"\beta": "β",
    r"\lambda": "λ", r"\mu": "μ", r"\pi": "π", r"\sigma": "σ", r"\tau": "τ",
    r"\theta": "θ", r"\omega": "ω", r"\Omega": "Ω", r"\Phi": "Φ", r"\varphi": "φ",
    r"\epsilon": "ε", r"\eta": "η", r"\kappa": "κ", r"\nu": "ν", r"\rho": "ρ",
    r"\chi": "χ", r"\psi": "ψ", r"\Lambda": "Λ", r"\Sigma": "Σ",
}
SYSTEM_PROMPT = (
    "把【本块】译成简洁、自然的学术中文，供固定栏宽的论文 PDF 排版。只输出一段译文。"
    "用短句和通行术语，省去可压缩的套话和赘词，保留所有结论、条件、数值、专名、单位和引文，"
    "不要扩写、概括、解释或附英文原句。网址、邮箱和标识符保持原样。"
    "⟦T1⟧ 之类的编号占位对应公式或引文，必须逐字复制，每个只出现一次，并放在对应的术语或结论旁。"
    "【前文】【后文】只用于理解语境。本块如果从句中开始或在句中结束，只翻译实际给出的片段，"
    "不要把相邻栏或页的文字补进来。"
)

# --------------------------------------------------------------------------- 文本工具


def _normalize_pdf_symbols(text):
    """Recover recognizable legacy PDF glyphs and keep other controls out of HTML."""
    text = re.sub(r"(?<=[(\s])\x00(?=\d)", "−", text)
    text = re.sub(r"<sup>\x00</sup>\s*<sup>(\d+)</sup>", r"<sup>−\1</sup>", text)
    text = re.sub(r"(?<=[CH])\x00(?=H|<sup>\d)", "–", text)
    text = re.sub(r"(?<=[\d) e])\x03(?=<sup>3</sup>|[.)])", "Å", text)
    text = re.sub(r"(?<=\d)\x84(?=\d)", "⊂", text)
    return PDF_CONTROLS.sub("�", text)


def _span_text(span):
    kind = span.get("type")
    content = span.get("content") or ""
    if kind == "inline_equation":
        return "⟦" + content.strip() + "⟧"
    return content


def merge_lines(lines):
    """把块内多行拼成一段：去折行连字符、公式占位两侧不补空格。"""
    parts = []
    for line in lines or []:
        cur = "".join(_span_text(s) for s in line.get("spans", []))
        if not cur:
            continue
        if parts:
            prev = parts[-1]
            if prev.endswith("-") and re.match(r"^[a-z]", cur):
                parts[-1] = prev[:-1] + cur
                continue
            if prev.endswith("⟧") or cur.startswith("⟦"):
                parts.append(cur)
                continue
            parts[-1] = prev + " " + cur
        else:
            parts.append(cur)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


def clean_source(text):
    plain = re.sub(r"<[^>]+>", "", text or "")
    plain = PLACEHOLDER.sub(" ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def latex_to_html(src):
    """把 MinerU 给的 LaTeX 片段转成 insert_htmlbox 能排的 HTML（上下标 + 希腊字母）。"""
    s = re.sub(r"\\[ \t]+", " ", src.strip("⟦⟧"))
    s = re.sub(r"\\tag\{[^{}]*\}", "", s)
    s = re.sub(
        r"\\(?:mathrm|mathbf|mathit|boldsymbol|text|operatorname|textrm|mbox|textsf|bar|hat|tilde|vec|dot|mathsf|mathtt)\s*",
        "", s)
    s = re.sub(r"\s*([{}_^])\s*", r"\1", s)
    for _ in range(6):
        if "{{" not in s and "}}" not in s:
            break
        s = s.replace("{{", "{").replace("}}", "}")
    for _ in range(6):
        before = s
        s = re.sub(r"_\{([^{}]*)\}", lambda m: "<sub>" + m.group(1).replace(" ", "") + "</sub>", s)
        s = re.sub(r"\^\{([^{}]*)\}", lambda m: "<sup>" + m.group(1).replace(" ", "") + "</sup>", s)
        if s == before:
            break
    for key, value in sorted(GREEK.items(), key=lambda kv: -len(kv[0])):
        s = s.replace(key, value)
    for key, value in ((r"\times", "×"), (r"\approx", "≈"), (r"\cdot", "·"), (r"\pm", "±"),
                       (r"\leq", "≤"), (r"\geq", "≥"), (r"\neq", "≠"), (r"\qquad", ","),
                       (r"\quad", ","), (r"\!", ""), (r"\,", ""), (r"\%", "%"), (r"\&", "&"),
                       (r"\mathring", ""), (r"\circ", "°"), (r"\prime", "′"), (r"\infty", "∞")):
        s = s.replace(key, value)
    s = re.sub(r"\\[a-zA-Z]+", "", s)
    s = s.replace("{", "").replace("}", "")
    s = re.sub(r"_([A-Za-z0-9])", r"<sub>\1</sub>", s)
    s = re.sub(r"\^([A-Za-z0-9])", r"<sup>\1</sup>", s)
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"(?<=[0-9])\s+(?=[0-9.])", "", s)
    s = re.sub(r"(?<=\.)\s+(?=[0-9])", "", s)
    s = re.sub(r"(?<=[A-Za-z\u0391-\u03c9])\s+(?=[A-Za-z\u0391-\u03c9])", "", s)
    s = re.sub(r"\s+([,;:\.\]\)])", r"\1", s)
    s = re.sub(r"([\(\[])\s+", r"\1", s)
    s = re.sub(r"\s*([=×·])\s*", r" \1 ", s)
    return re.sub(r"\s+", " ", s).strip()


def build_html(zh):
    zh = _normalize_pdf_symbols(zh or "")

    def replace(match):
        return "<span>" + _escape_keep_tags(latex_to_html(match.group(0))) + "</span>"

    parts = PLACEHOLDER.split(zh)
    formulas = PLACEHOLDER.finditer(zh)
    html = [_escape_keep_tags(parts[0])]
    for formula, tail in zip(formulas, parts[1:]):
        html.extend((replace(formula), _escape_keep_tags(tail)))
    return "".join(html)


def _escape_keep_tags(text):
    out = []
    for index, piece in enumerate(TAG_RE.split(text)):
        if index % 2 == 1:
            out.append(piece)
        else:
            out.append(piece.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    return "".join(out)


# --------------------------------------------------------------------------- 版面


V2_SKIP_TYPES = {
    "image", "chart", "table", "page_header", "page_footer", "page_number",
    "header", "footer", "footnote", "aside_text", "list", "index", "ref_text",
    "page_footnote", "page_aside_text", "equation_interline",
}


def blocks_from_content_list_v2(pages, page_sizes, layout=None):
    """MinerU content_list_v2（按页的段落级块）→ 统一块结构。

    v2 比 layout.json 更干净：块是段落级（没有「整页父块」），bbox 是 0-1000 归一化坐标，
    行内公式以 equation_inline span 给出（包 ⟦⟧）。文本层损坏的页面也只有 v2 里还有字。
    """
    blocks = []
    geometry = {page["page_idx"]: page for page in (layout or {}).get("pdf_info", [])
                if "page_idx" in page}
    for page_index, items in enumerate(pages or []):
        page_no = page_index + 1
        size = page_sizes[page_index] if page_index < len(page_sizes) else (595.0, 841.0)
        width, height = float(size[0] or 595.0), float(size[1] or 841.0)
        for item_index, item in enumerate(items or []):
            kind = str(item.get("type") or "")
            if kind in V2_SKIP_TYPES:
                continue
            content = item.get("content") or {}
            parts = (content.get("paragraph_content") or content.get("title_content")
                     or content.get("text_content") or [])
            text = ""
            for span in parts:
                span_type = str(span.get("type") or "")
                value = span.get("content") or ""
                if "equation" in span_type:
                    text += "⟦" + value.strip() + "⟧"
                else:
                    text += value
            text = re.sub(r"\s+", " ", text).strip()
            bbox = item.get("bbox") or []
            if len(bbox) != 4:
                continue
            x0, y0 = bbox[0] / 1000.0 * width, bbox[1] / 1000.0 * height
            x1, y1 = bbox[2] / 1000.0 * width, bbox[3] / 1000.0 * height
            if x1 - x0 < 20 or y1 - y0 < 6:
                continue
            rect = fitz.Rect(x0, y0, x1, y1)
            page_layout = geometry.get(page_index, {})
            physical = _matching_layout_block(rect, page_layout.get("preproc_blocks", []))
            paragraph = _matching_layout_block(rect, page_layout.get("para_blocks", []))
            continuation = ""
            if physical and _redistributed_paragraph(paragraph, rect, bool(text)):
                # preproc_blocks retains the text at its physical location before MinerU joins frames.
                text = merge_lines(physical.get("lines")) or text
                continuation = _first_continuation_line(paragraph, rect)
            blocks.append({
                "id": "p%dv%d" % (page_no, item_index),
                "page": page_no,
                "type": "title" if kind == "title" else "text",
                "bbox": [x0, y0, x1, y1],
                "source": text,
                "_continuation": continuation,
            })
    return _join_frame_clauses(blocks)


def _matching_layout_block(rect, blocks):
    best_ratio = 0.70
    best = None
    for block in blocks:
        if block.get("type") not in TRANSLATABLE or not block.get("bbox"):
            continue
        box = fitz.Rect(block["bbox"])
        ratio = (box & rect).get_area() / ((box | rect).get_area() or 1)
        if ratio >= best_ratio:
            best_ratio, best = ratio, block
    return best


def _outside_frame(line, rect):
    return (
        any(span.get("cross_page") for span in line.get("spans", []))
        or (line.get("bbox") and not _line_in_frame(fitz.Rect(line["bbox"]), rect + (-3, -3, 3, 3)))
    )


def _redistributed_paragraph(paragraph, rect, has_text):
    return (not has_text or (paragraph and paragraph.get("lines_deleted"))
            or any(_outside_frame(line, rect) for line in (paragraph or {}).get("lines", [])))


def _first_continuation_line(paragraph, rect):
    for line in (paragraph or {}).get("lines", []):
        if _outside_frame(line, rect):
            return merge_lines([line])
    return ""


_FRAME_TAIL = re.compile(
    r"(?<![A-Za-z])((?:however|nevertheless|nonetheless|therefore|consequently|accordingly|thus|"
    r"in contrast|in particular|for example|for instance),?(?:\s+(?:this|these|those|the|such))?)$",
    re.IGNORECASE,
)


def _join_frame_clauses(blocks):
    """Keep a dangling transition with its clause across a verified physical break."""
    for i, block in enumerate(blocks):
        continuation = block.pop("_continuation", "")
        if not continuation or i + 1 == len(blocks):
            continue
        following = blocks[i + 1]
        # Match the actual opening words of the continuation. PDF block order alone
        # cannot establish that two neighboring frames are part of the same paragraph.
        opening = re.findall(r"[A-Za-z]+", clean_source(continuation))[:4]
        next_words = re.findall(r"[A-Za-z]+", clean_source(following["source"]))[:len(opening)]
        tail = _FRAME_TAIL.search(block["source"])
        if (tail and len(opening) >= 2 and opening == next_words
                and re.match(r"^[a-z]", following["source"])):
            block["source"] = block["source"][:tail.start()].rstrip()
            following["source"] = tail.group() + " " + following["source"]
    return relink(blocks)


def normalize_mineru_layout(layout):
    """Assign moved spans to their physical PDF pages before choosing frames."""
    pages, report = restore_cross_page_spans(layout.get("pdf_info") or [])
    return {**layout, "pdf_info": pages}, report


def _layout_text_blocks(blocks, prefix=""):
    for index, block in enumerate(blocks):
        if not isinstance(block, dict):
            continue
        path = f"{prefix}{index}"
        kind = block.get("type")
        if kind in TRANSLATABLE or kind in CAPTION_TYPES or kind in ("doc_title", "paragraph_title"):
            yield path, block
        elif kind in ("image", "table", "chart"):
            yield from _layout_text_blocks(iter_child_blocks(block), path + "-")


def blocks_from_layout(layout, *, physical_pages=False):
    """MinerU layout.json → [{page,index,type,bbox,source,prev,next}]（坐标是 PDF 点）。"""
    blocks = []
    for page in layout.get("pdf_info") or []:
        page_no = int(page.get("page_idx", 0)) + 1
        page_blocks = []
        for index, block in _layout_text_blocks(page.get("para_blocks") or []):
            raw_box = valid_bbox(block.get("bbox"))
            if raw_box is None or (physical_pages and block.get("lines_deleted")):
                continue
            kind = block.get("type")
            is_caption = kind in CAPTION_TYPES
            if is_caption:
                kind = "caption"
            elif kind in ("doc_title", "paragraph_title"):
                kind = "title"
            box = fitz.Rect(raw_box)
            lines = list(iter_direct_lines(block))
            physical = None if is_caption else _matching_layout_block(box, page.get("preproc_blocks") or [])
            continuation = ""
            if physical and not block.get("_retainpdf_cross_page_recovery") and _redistributed_paragraph(block, box, bool(lines)):
                lines = physical.get("lines") or lines
                continuation = _first_continuation_line(block, box)
            text = merge_lines(lines)
            if is_caption and not CAPTION_START.match(clean_source(text)):
                continue
            rects = [fitz.Rect(line_box) for line in lines
                     if (line_box := valid_bbox(line.get("bbox"))) is not None]
            if rects:
                box = rects[0]
                for extra in rects[1:]:
                    box |= extra
            else:
                box = fitz.Rect(raw_box)   # 没有行信息：用块 bbox，文本稍后补
            if box.width < 12 or box.height < 5:
                continue
            page_blocks.append({
                "id": "p%db%s" % (page_no, index),
                "page": page_no,
                "type": kind,
                "bbox": [box.x0, box.y0, box.x1, box.y1],
                "source": text,
                "_continuation": continuation,
            })
        blocks.extend(page_blocks)
    return _join_frame_clauses(blocks)


def supplement_mineru_frames(blocks, physical_frames):
    """Fill unclaimed same-page frames using the provider's physical block list."""
    pages = {}
    for block in blocks:
        pages.setdefault(block["page"], []).append(block)
    candidates = {}
    for block in physical_frames:
        candidates.setdefault(block["page"], []).append(block)

    supplemented = []
    for page, frames in candidates.items():
        known = pages.setdefault(page, [])
        anchors = []
        for frame in frames:
            rect = fitz.Rect(frame["bbox"])
            match = None
            best = 0.25
            for block in known:
                other = fitz.Rect(block["bbox"])
                area = min(rect.get_area(), other.get_area())
                covered = (rect & other).get_area() / area if area else 0
                if covered >= best:
                    match, best = block, covered
            anchors.append(match)
        next_anchor = None
        insert_before = {}
        at_end = []
        for frame, match in reversed(list(zip(frames, anchors))):
            if match is not None:
                next_anchor = match["id"]
            elif clean_source(frame["source"]):
                supplemented.append(frame["id"])
                if next_anchor is None:
                    at_end.append(frame)
                else:
                    insert_before.setdefault(next_anchor, []).append(frame)
        merged = []
        for block in known:
            merged.extend(reversed(insert_before.get(block["id"], [])))
            merged.append(block)
        merged.extend(reversed(at_end))
        pages[page] = merged
    return [block for page in sorted(pages) for block in pages[page]], sorted(supplemented)


def relink(blocks):
    """Use the parser's reading order, including continuations into the next column or page."""
    for index, block in enumerate(blocks):
        block["prev"] = blocks[index - 1]["source"][-160:] if index else ""
        block["next"] = blocks[index + 1]["source"][:160] if index + 1 < len(blocks) else ""
    return blocks



def repair_sources(blocks, pdf_path):
    """Use the PDF text layer where OCR is empty or repeatedly marks prose as scripts."""
    suspect = [b for b in blocks if b.get("type") == "caption" or not b.get("source")
               or len(LETTER_SCRIPT.findall(b["source"])) >= 5]
    if not suspect:
        return blocks
    with fitz.open(str(pdf_path)) as doc:
        page_lines = {}
        for block in suspect:
            source = block.get("source") or ""
            page_no = block["page"]
            if page_no not in page_lines:
                page_lines[page_no] = _page_text_lines(doc[page_no - 1])
            rect = fitz.Rect(block["bbox"])
            lines = [(box, spans) for box, spans in page_lines[page_no] if _line_in_frame(box, rect)]
            native = _native_frame_text(lines)
            if not source:
                if len(clean_source(native)) >= 20:
                    block["source"] = native
                continue
            # Compare letters since the OCR path represents formulas as LaTeX.
            ocr_letters = re.sub(r"[^a-zA-Z]", "", clean_source(source)).lower()
            native_letters = re.sub(r"[^a-zA-Z]", "", clean_source(native)).lower()
            similarity = SequenceMatcher(None, ocr_letters, native_letters, autojunk=False).ratio()
            if block.get("type") == "caption":
                if native_letters and "�" not in native and similarity >= 0.86:
                    block["source"] = NATIVE_FORMULA.sub(lambda match: f"⟦{match[0]}⟧", native)
            elif native_letters and "�" not in native and similarity >= 0.68:
                # Keep each complete chemical label together through translation.
                block["source"] = NATIVE_FORMULA.sub(lambda match: f"⟦{match[0]}⟧", native)
            else:
                block["source"] = LETTER_SCRIPT.sub(r"\2", source)
    return [b for b in blocks if b.get("source")]


def _native_frame_text(lines):
    rows = []
    for _, spans in lines:
        main = max(spans, key=lambda span: span["size"])
        y = main["origin"][1]
        for baseline, size, row in rows:
            if abs(y - baseline) < max(size, main["size"]) * 0.24:
                row.extend(spans)
                break
        else:
            rows.append((y, main["size"], list(spans)))
    merged = []
    for baseline, size, spans in sorted(rows, key=lambda row: row[0]):
        pieces = []
        previous_right = None
        for span in sorted(spans, key=lambda span: span["origin"][0]):
            x, y = span["origin"]
            value = span["text"]
            glyphs = NATIVE_GLYPHS.get(span["font"])
            if glyphs:
                value = value.translate(glyphs)
            if previous_right is not None and x - previous_right > size * 0.16:
                pieces.append(" ")
            if span["size"] < size * 0.83 and abs(y - baseline) > size * 0.12 and value.strip():
                tag = "sup" if y < baseline else "sub"
                value = f"<{tag}>{value}</{tag}>"
            pieces.append(value)
            previous_right = span["bbox"][2]
        merged.append({"spans": [{"content": "".join(pieces).strip()}]})
    return _normalize_pdf_symbols(merge_lines(merged))


def _locate_by_text(page, parent, original):
    """用父块文本在页面上搜索真实行位置；找不到返回 None。"""
    probe = clean_source(parent.get("source") or "")[:70]
    for attempt in (probe, " ".join(re.findall(r"[A-Za-z]{3,}", probe)[:6])):
        if not attempt or len(attempt) < 8:
            continue
        try:
            hits = page.search_for(attempt)
        except Exception:  # noqa: BLE001
            hits = []
        if hits:
            box = hits[0]
            # 用命中行所在的 PyMuPDF 段落块（get_text("blocks") 已按段落合并），
            # 这样拿到的是一整段而不是单行
            try:
                fitz_blocks = page.get_text("blocks")
            except Exception:  # noqa: BLE001
                fitz_blocks = []
            for raw in fitz_blocks:
                rect = fitz.Rect(raw[0], raw[1], raw[2], raw[3])
                if rect.contains(box) or (rect & box).get_area() > 0:
                    if rect.height >= 8 and rect.width >= 20:
                        return rect
            left = min(original.x0, box.x0)
            right = max(original.x1, box.x1)
            return fitz.Rect(left, box.y0 - 1, right, box.y1 + 1)
    return None


def relocate_by_text(blocks, pdf_path):
    """用文本在页面上把每块「钉」回它真正的段落位置（MinerU 的 bbox 常是整页/整栏）。

    只接受与原 bbox 有实质重叠的结果，避免把段落钉到别处。
    """
    try:
        doc = fitz.open(str(pdf_path))
    except Exception:  # noqa: BLE001
        return blocks
    cache = {}
    occupied = {}
    for block in blocks:
        # 公式/占位符会打断文本连续性：在第一个公式标记处截断，用前面的纯文字定位
        probe = re.split(r"⟦|\\", clean_source(block.get("source") or ""))[0][:60].strip()
        if len(probe) < 12:
            continue
        page = cache.get(block["page"])
        if page is None:
            page = doc[block["page"] - 1]
            cache[block["page"]] = page
        words = probe.split()
        candidates = [probe]
        if len(words) > 6:
            candidates.append(" ".join(words[:6]))
        if len(words) > 4:
            candidates.append(" ".join(words[:4]))
        hits = []
        for candidate in candidates:
            if len(candidate) < 10:
                continue
            try:
                hits = page.search_for(candidate)
            except Exception:  # noqa: BLE001
                hits = []
            if hits:
                break
        if not hits:
            continue
        target = hits[0]
        original = fitz.Rect(block["bbox"])
        for raw in page.get_text("blocks"):
            rect = fitz.Rect(raw[0], raw[1], raw[2], raw[3])
            if not rect.intersects(target):
                continue
            if rect.height < 12 or rect.width < 20:
                continue
            inter = (rect & original).get_area()
            if inter <= 0.3 * min(rect.get_area(), original.get_area()):
                break
            if any((rect & used).get_area() > 0.8 * min(rect.get_area(), used.get_area())
                   for used in occupied.get(block["page"], [])):
                break
            # 定位到一行（原段有 4 行）说明文本层被拆碎，位置不可靠：保留原 bbox
            if rect.height < min(24.0, original.height * 0.5):
                break
            block["bbox"] = [rect.x0, rect.y0, rect.x1, rect.y1]
            block["relocated"] = True
            occupied.setdefault(block["page"], []).append(rect)
            break
    doc.close()
    return blocks


def _covers(outer, inner):
    """outer 覆盖 inner 至少 80%，且面积明显更大（父子块判定，不要求严格包含）。"""
    if inner.is_empty:
        return False
    inter = (outer & inner).get_area()
    return inter / inner.get_area() >= 0.8 and outer.get_area() >= inner.get_area() * 1.4


def _subtract_rect(rect, cut):
    """rect 减去 cut 后剩下的矩形列表（最多 4 块）。"""
    if not rect.intersects(cut):
        return [rect]
    out = []
    if cut.y0 > rect.y0:
        out.append(fitz.Rect(rect.x0, rect.y0, rect.x1, cut.y0))
    if cut.y1 < rect.y1:
        out.append(fitz.Rect(rect.x0, cut.y1, rect.x1, rect.y1))
    top, bottom = max(rect.y0, cut.y0), min(rect.y1, cut.y1)
    if cut.x0 > rect.x0:
        out.append(fitz.Rect(rect.x0, top, cut.x0, bottom))
    if cut.x1 < rect.x1:
        out.append(fitz.Rect(cut.x1, top, rect.x1, bottom))
    return [r for r in out if not r.is_empty]


def dedupe_hierarchy(blocks, pdf_path=None):
    """处理 MinerU 的「父块」：bbox 罩住多块、文本却只是其中一段的块。

    之前直接丢弃会让父块自己的那段整段漏翻（p9 右栏底部的 Cbn 段）；
    现在改成「父块减去所有子块」，把父块收缩到剩下的空白区域（通常正是它那段文字的位置），
    减去后没有可用区域才丢弃。
    """
    if pdf_path:
        with fitz.open(str(pdf_path)) as doc:
            return _dedupe_hierarchy(blocks, doc)
    return _dedupe_hierarchy(blocks, None)


def _dedupe_hierarchy(blocks, doc):
    by_page = {}
    for block in blocks:
        by_page.setdefault(block["page"], []).append(block)
    for page_no, items in by_page.items():
        for parent in items:
            outer = fitz.Rect(parent["bbox"]) + (-3, -3, 3, 3)
            children = [b for b in items if b is not parent and _covers(outer, fitz.Rect(b["bbox"]))]
            parent_text = parent.get("source") or ""
            # 冗余子块：B 被 A 包含、且 B 的文本就是 A 的一段 → 丢 B（否则两段译文会叠印）
            for child in children:
                child_text = child.get("source") or ""
                if child_text and child_text in parent_text:
                    child["drop"] = True
            children = [c for c in children if not c.get("drop")]
            if not children:
                continue
            if len(children) < 2:
                continue
            if any((parent_text in (c.get("source") or ""))
                   or ((c.get("source") or "") in parent_text) for c in children):
                continue
            original = fitz.Rect(parent["bbox"])
            # 优先用文字定位：父块的文本常只占其中一段，搜索到真实行位置最可靠
            located = _locate_by_text(doc[page_no - 1], parent, original) if doc is not None else None
            if located is not None:
                parent["bbox"] = [located.x0, located.y0, located.x1, located.y1]
                continue
            remain = [original]
            for child in children:
                cut = fitz.Rect(child["bbox"])
                next_remain = []
                for rect in remain:
                    next_remain.extend(_subtract_rect(rect, cut))
                remain = next_remain
            candidates = [r for r in remain if r.height >= 12 and r.width >= 60]
            if not candidates:
                parent["drop"] = True
                continue
            best = max(candidates, key=lambda r: r.get_area())
            # 减法把块切窄一半以上（典型是跨栏块），说明位置不可信：丢掉，别错位
            if best.width < original.width * 0.8:
                parent["drop"] = True
                continue
            parent["bbox"] = [best.x0, best.y0, best.x1, best.y1]
    return [b for b in blocks if not b.get("drop")]


def blocks_from_pdf(pdf_path):
    """没有 MinerU 时的兜底：PyMuPDF 自带文本块（无类型区分，图注也会被翻）。"""
    blocks = []
    doc = fitz.open(str(pdf_path))
    for page_index in range(len(doc)):
        page = doc[page_index]
        items = []
        for index, raw in enumerate(page.get_text("blocks")):
            x0, y0, x1, y1, text = raw[0], raw[1], raw[2], raw[3], raw[4]
            text = re.sub(r"\s+", " ", text or "").strip()
            if len(text) < 40:            # 短行多半是页眉页脚、图注标签
                continue
            if not re.search(r"[A-Za-z]{3,}", text):
                continue
            items.append({
                "id": "p%db%d" % (page_index + 1, index),
                "page": page_index + 1,
                "type": "text",
                "bbox": [x0, y0, x1, y1],
                "source": text,
            })
        for pos, item in enumerate(items):
            item["prev"] = items[pos - 1]["source"][-160:] if pos > 0 else ""
            item["next"] = items[pos + 1]["source"][:160] if pos + 1 < len(items) else ""
        blocks.extend(items)
    doc.close()
    return blocks


def read_mineru_result(zip_path):
    """从本次 MinerU ZIP 读取版面与可选的段落数据。"""
    with zipfile.ZipFile(str(zip_path)) as archive:
        files = sorted(archive.namelist())
        layout_name = "layout.json" if "layout.json" in files else next(
            (name for name in files if name.endswith("/layout.json")), None,
        )
        if layout_name is None:
            middle = [name for name in files if name.rsplit("/", 1)[-1] == "middle.json"
                      or name.endswith("_middle.json")]
            if len(middle) != 1:
                raise RuntimeError("MinerU 结果中没有唯一的 layout.json 或 middle.json")
            layout_name = middle[0]
        layout = json.loads(archive.read(layout_name))
        if not isinstance(layout, dict):
            raise RuntimeError("MinerU 版面数据格式错误")
        candidates = [name for name in files if name.endswith("_content_list_v2.json")]
        candidates.extend(name for name in files if name.rsplit("/", 1)[-1] == "content_list_v2.json")
        for candidate in candidates:
            try:
                content = json.loads(archive.read(candidate))
            except ValueError:
                continue
            if isinstance(content, list) and content:
                return layout, content
        return layout, None


# --------------------------------------------------------------------------- 回填


def _font_dir():
    if find_spec("fontTools") and (FONT_DIR / SERIF_FONT).is_file():
        return FONT_DIR, SERIF_FONT
    for name in ("msyh.ttc", "msyhbd.ttc", "simsun.ttc", "simhei.ttf", "NotoSansCJK-Regular.ttc"):
        for folder in (Path(r"C:\Windows\Fonts"), Path("/usr/share/fonts"), Path("/System/Library/Fonts")):
            path = folder / name
            if path.is_file():
                return folder, name
    return None, None


def _image_rects(page_info):
    rects = []
    for block in page_info.get("para_blocks") or []:
        if block.get("type") not in ("image", "table", "chart"):
            continue
        kids = block.get("blocks") or []
        for child in kids:
            if child.get("type") in ("image_body", "table_body", "chart_body") or not child.get("type"):
                rects.append(fitz.Rect(child["bbox"]))
        if not kids:
            rects.append(fitz.Rect(block["bbox"]))
    return rects


def _caption_rects(page_info):
    """Captions and table notes remain fixed alongside their graphic bodies."""
    return [
        fitz.Rect(child["bbox"])
        for block in page_info.get("para_blocks") or []
        if block.get("type") in ("image", "table", "chart")
        for child in block.get("blocks") or []
        if child.get("bbox") and (child.get("type") or "").endswith(("_caption", "_footnote"))
    ]


def _sample_color(pix, page_rect, rect, inset=2.0, band=5.0):
    """块外环带中位色：彩条 / 灰底 / 照片旁都能贴合，不留下白块。"""
    samples = pix.samples
    channels = pix.n
    width, height = pix.width, pix.height
    sx, sy = width / page_rect.width, height / page_rect.height
    rx0 = max(0, int((rect.x0 - band) * sx))
    rx1 = min(width - 1, int((rect.x1 + band) * sx))
    ry0 = max(0, int((rect.y0 - band) * sy))
    ry1 = min(height - 1, int((rect.y1 + band) * sy))
    ix0, ix1 = int((rect.x0 + inset) * sx), int((rect.x1 - inset) * sx)
    iy0, iy1 = int((rect.y0 + inset) * sy), int((rect.y1 - inset) * sy)
    pixels = []
    for yy in range(ry0, ry1, 2):
        inside_row = iy0 <= yy <= iy1
        base = yy * width
        for xx in range(rx0, rx1, 2):
            if inside_row and ix0 <= xx <= ix1:
                continue
            offset = (base + xx) * channels
            if offset + 2 < len(samples):
                pixels.append((samples[offset], samples[offset + 1], samples[offset + 2]))
    if not pixels:
        return (255, 255, 255)
    pixels.sort(key=lambda c: c[0] * 0.299 + c[1] * 0.587 + c[2] * 0.114)
    return pixels[len(pixels) // 2]


def _page_text_lines(page):
    lines = []
    for block in page.get_text("dict", flags=fitz.TEXTFLAGS_TEXT)["blocks"]:
        for line in block.get("lines", []):
            if line.get("dir", (1, 0))[0] < 0.99:
                continue
            lines.append((fitz.Rect(line["bbox"]), line["spans"]))
    return lines


def _line_in_frame(line, rect):
    center_y = (line.y0 + line.y1) / 2
    overlap = min(line.x1, rect.x1) - max(line.x0, rect.x0)
    return rect.y0 <= center_y <= rect.y1 and overlap >= 0.55 * line.width > 0


def _typography(lines, rect):
    """Measure body size by character count, so math, citations and superscripts cannot dominate."""
    sizes = Counter()
    for line, spans in lines:
        if _line_in_frame(line, rect):
            for span in spans:
                size = round(span["size"], 1)
                text = span["text"].strip()
                if text and 4 <= size <= 60:
                    sizes[size] += len(text)
    size = sizes.most_common(1)[0][0] if sizes else 9.0
    baselines = sorted({
        round(span["origin"][1], 1)
        for line, spans in lines if _line_in_frame(line, rect)
        for span in spans if abs(span["size"] - size) < 0.25 and span["text"].strip()
    })
    strides = [y2 - y1 for y1, y2 in zip(baselines, baselines[1:])
               if size * 0.95 < y2 - y1 < size * 1.65]
    leading = min(1.36, max(1.17, median(strides) / size)) if strides else 1.23
    return size, leading


def _title_color(lines, frame):
    colors = Counter()
    for line, spans in lines:
        if _line_in_frame(line, frame):
            for span in spans:
                colors[span["color"]] += len(span["text"].strip())
    return colors.most_common(1)[0][0] if colors else 0x111111


def _text_units(html):
    plain = unescape(re.sub(r"<[^>]+>", "", html))
    return sum(1.0 if east_asian_width(ch) in ("W", "F") else 0.54 for ch in plain)


def apply_text_budgets(pdf_path, blocks, layout=None):
    """Give the translator a soft length target in full-width glyphs for each physical frame."""
    by_page = {}
    for block in blocks:
        by_page.setdefault(block["page"], []).append(block)
    geometry = _layout_pages(layout)
    with fitz.open(str(pdf_path)) as doc:
        for page_no, items in by_page.items():
            lines = _page_text_lines(doc[page_no - 1])
            images = _page_images(doc[page_no - 1], geometry.get(page_no - 1, {}))
            for block in items:
                rect = fitz.Rect(block["bbox"])
                # A parser may classify text printed inside one graphic as a paragraph.
                if _overlaps_image(rect, images):
                    block["protected_layout"] = True
                    continue
                size, leading = _typography(lines, rect)
                rows = max(1, round(rect.height / (size * leading)))
                block["text_budget"] = max(6, math.floor(rect.width / size * rows * 0.94))


def _layout_pages(layout):
    return {p.get("page_idx", index): p
            for index, p in enumerate((layout or {}).get("pdf_info") or [])}


def _page_images(page, page_info):
    # MinerU can provide only a partial box for a raster illustration.
    return _image_rects(page_info) + [fitz.Rect(info["bbox"]) for info in page.get_image_info()]


def _overlaps_image(rect, images):
    # Also preserve graphic labels such as publication buttons with a small inline icon.
    ratio = 0.08 if rect.height < 24 else 0.10
    return any((rect & image).get_area() > ratio * rect.get_area() for image in images)


def _fit_right(item, obstacles, page_rect):
    """Allow a short title or byline to use adjacent free width on the same line."""
    rect = item["rect"]
    size = item["fs"]
    if rect.height > size * 1.55:
        return rect.x1
    right = min(rect.x1 + size * 8, page_rect.x1 - min(rect.x0, page_rect.width * 0.08))
    for obstacle in obstacles:
        if obstacle.x0 >= rect.x1 - 1 and obstacle.y0 < rect.y0 + size * 1.35 and obstacle.y1 > rect.y0:
            right = min(right, obstacle.x0 - 2.0)
    return max(rect.x1, right)


def _column_runs(items, obstacles):
    """Join close frames in a column across small subheadings and their body text."""
    runs = []
    for item in sorted(items, key=lambda it: (it["rect"].y0, it["rect"].x0)):
        rect = item["rect"]
        candidates = []
        for run in runs:
            prev = run[-1]
            box = prev["rect"]
            size = item["fs"]
            prev_size = prev["fs"]
            overlap = min(rect.x1, box.x1) - max(rect.x0, box.x0)
            gap = rect.y0 - box.y1
            short_frame = (rect.height <= size * 1.65 or box.height <= prev_size * 1.65
                           or item["type"] == "title" or prev["type"] == "title")
            if short_frame:
                # Section titles, correspondence labels and indented first lines may be narrower.
                aligned = (max(size, prev_size) <= 15 and 0.72 <= size / prev_size <= 1.38
                           and abs(rect.x0 - box.x0) <= size * 2.8
                           and overlap >= 0.50 * min(rect.width, box.width))
            else:
                width = max(rect.width, box.width)
                indent = abs(rect.x0 - box.x0)
                aligned = (0.90 <= size / prev_size <= 1.12
                           and ((indent <= size * 1.6 and overlap >= 0.70 * width)
                                or (indent <= size * 3.0 and overlap >= 0.83 * width)))
            if not aligned or not -2.5 <= gap <= size * 1.55 or rect.y0 - box.y0 < min(size, prev_size) * 0.75:
                continue
            boundaries = obstacles + [other["rect"] for other in items if other is not item and other is not prev]
            if gap > 0 and any(rect.x0 < ob.x1 and ob.x0 < rect.x1
                               and ob.y0 < rect.y0 - 1 and ob.y1 > box.y1 + 1
                               for ob in boundaries):
                continue
            candidates.append(run)
        if candidates:
            max(candidates, key=lambda run: run[-1]["rect"].y1).append(item)
        else:
            runs.append([item])
    return runs


def _frame_limits(item, right, obstacles, page_rect):
    """Keep a frame on its original side of every object crossing its own width."""
    rect = item["rect"]
    top = page_rect.y0
    bottom = page_rect.y1
    for obstacle in obstacles:
        if min(right, obstacle.x1) - max(item["left"], obstacle.x0) <= 1:
            continue
        if obstacle.y0 <= rect.y0:
            top = max(top, obstacle.y1 + 2.0)
        else:
            bottom = min(bottom, obstacle.y0 - 1.5)
    return top, bottom


def _story(item, size, leading, css, archive):
    # Set margins on named elements. MuPDF's HTML engine retains its default 1em
    # body margins when the reset is written as a universal selector.
    style = css + (
        "html, body, div { margin:0; padding:0; font-size:%.3fpt; "
        "line-height:%.3f; color:#%06x; } sub, sup { font-size:68%%; }"
        % (size, leading, item["color"])
    )
    align = "justify" if item["type"] in ("text", "abstract", "caption") else "left"
    weight = "bold" if item["type"] == "title" else "normal"
    body = f'<div style="text-align:{align};font-weight:{weight}">' + item["html"] + "</div>"
    return fitz.Story(html=body, user_css=style, archive=archive)


def _fit_run(run, obstacles, page_rect, css, archive):
    last = run[-1]
    original_bottom = last["rect"].y1
    limits = [_frame_limits(item, item["rect"].x1, obstacles, page_rect) for item in run]
    original_leading = median(item["leading"] for item in run)
    gaps = []
    for a, b in zip(run, run[1:]):
        before_title = b["type"] == "title" and a["type"] != "title"
        gap = b["rect"].y0 - a["rect"].y1
        gaps.append(min(11.0, max(6.0, gap)) if before_title else min(7.0, max(3.0, gap + 3.0)))
    # Spread a short translation across the available column using both leading
    # and paragraph gaps. Keep a gap bonus bounded by one line of body type.
    styles = []
    if len(run) >= 3:
        body_size = median(item["fs"] for item in run)
        styles.extend((
            (1.00, min(1.58, original_leading + 0.24), body_size * 0.85),
            (1.00, min(1.52, original_leading + 0.16), body_size * 0.55),
            (1.00, min(1.46, original_leading + 0.11), body_size * 0.30),
        ))
    if gaps or last["rect"].height >= 2 * last["fs"]:
        styles.extend((
            (1.00, min(1.43, original_leading + 0.08), 1.4),
            (1.00, min(1.40, original_leading + 0.04), 0.7),
        ))
    styles.extend((
        (1.00, original_leading, 0.0), (1.00, max(1.15, original_leading - 0.08), 0.0),
        (0.96, original_leading, 0.0), (0.92, max(1.15, original_leading - 0.04), 0.0),
        (0.88, max(1.14, original_leading - 0.08), 0.0),
    ))
    for scale, leading, extra_gap in styles:
        run_bottom = original_bottom if extra_gap else original_bottom + last["fs"] * 0.85
        run_bottom = min(page_rect.y1, run_bottom)
        ready = []
        y = run[0]["rect"].y0
        for index, item in enumerate(run):
            box = item["rect"]
            top, limit = limits[index]
            y = max(y, top)
            bottom = min(run_bottom, limit)
            if bottom <= y:
                break
            floor = min(8.0, item["fs"])
            size = max(floor, item["fs"] * scale)
            story = _story(item, size, leading, css, archive)
            left = item["left"]
            probe = fitz.Rect(0, 0, box.x1 - left, bottom - y)
            more, filled = story.place(probe, flags=fitz.mupdf.FZ_PLACE_STORY_FLAG_NO_OVERFLOW)
            right = box.x1
            if item.get("right", right) > right and (more or filled[3] > size * leading * 1.1):
                expanded = item["right"]
                wide_top, wide_limit = _frame_limits(item, expanded, obstacles, page_rect)
                wide_bottom = min(run_bottom, wide_limit)
                if wide_top <= y < wide_bottom:
                    story.reset()
                    wide_more, wide_filled = story.place(
                        fitz.Rect(0, 0, expanded - left, wide_bottom - y),
                        flags=fitz.mupdf.FZ_PLACE_STORY_FLAG_NO_OVERFLOW,
                    )
                    if not wide_more and wide_filled[3] <= size * leading * 1.1:
                        more, filled, right, bottom = wide_more, wide_filled, expanded, wide_bottom
            height = filled[3] + 0.15
            if more or height <= 0.15 or y + height > bottom + 0.05:
                break
            ready.append((item, fitz.Rect(left, y, right, y + height), story))
            y += height
            if index < len(gaps):
                y += gaps[index] + extra_gap
        if len(ready) == len(run):
            return ready
    return []


def _fit_page(items, forbidden, page_rect, css, archive):
    ready = []
    runs = _column_runs(items, forbidden)
    for run in runs:
        if len(run) == 1:
            item = run[0]
            item["right"] = _fit_right(item, forbidden + [it["rect"] for it in items if it is not item], page_rect)
        obstacles = forbidden + [item["rect"] for other in runs if other is not run for item in other]
        fitted = _fit_run(run, obstacles, page_rect, css, archive)
        if fitted or len(run) == 1:
            ready.extend(fitted)
            continue
        # If the run cannot fit together, keep each paragraph on its own anchor.
        # This also keeps the position of any original text left on the page.
        for item in run:
            boundaries = obstacles + [other["rect"] for other in run if other is not item]
            ready.extend(_fit_run([item], boundaries, page_rect, css, archive))
    return ready


def _protected_boxes(page_info, images, lines, items, blocked):
    forbidden = list(images) + blocked
    for caption in _caption_rects(page_info):
        if not any(item["type"] == "caption" and
                   (caption & item["rect"]).get_area() >= 0.60 * caption.get_area()
                   for item in items):
            forbidden.append(caption)
    for block in (page_info.get("para_blocks") or []) + (page_info.get("discarded_blocks") or []):
        if block.get("bbox") and block.get("type") not in (*TRANSLATABLE, *CAPTION_TYPES, "image", "chart", "table"):
            forbidden.append(fitz.Rect(block["bbox"]))
    # A known frame is more precise than a PDF line box for a decorative glyph
    # with oversized font metrics, such as a square before a section heading.
    known = [item["rect"] for item in items] + forbidden
    forbidden.extend(box for box, _ in lines if not any(_line_in_frame(box, frame) for frame in known))
    return forbidden


def _erase_original(page, lines, ready):
    """Erase text only; mask residual text in a form XObject one line at a time."""
    original = []
    # The inline ORCID badge is vector artwork, separate from the author's text.
    # The translated line includes the ID, so mask only the old badge inside its link.
    orcids = [
        link["from"] for link in page.get_links()
        if (link.get("uri") or "").startswith(("https://orcid.org/", "http://orcid.org/"))
        and any(item["rect"].intersects(link["from"]) for item, _, _ in ready)
    ]
    badges = []
    if orcids:
        for path in page.get_drawings():
            box = path["rect"]
            if (path.get("fill") and 4 < box.width < 9 and 4 < box.height < 9
                    and any(link.contains(box) for link in orcids)):
                badges.append(box)
    for item, _, _ in ready:
        frame = item["rect"]
        for box, spans in lines:
            if not _line_in_frame(box, frame):
                continue
            # Use each span's center band: subscripts and superscripts may lie
            # entirely outside the median glyph band of the parent text line.
            for span in spans:
                glyph_box = fitz.Rect(span["bbox"])
                left = max(glyph_box.x0, frame.x0)
                right = min(glyph_box.x1, frame.x1)
                if left >= right or not span["text"].strip():
                    continue
                center = (glyph_box.y0 + glyph_box.y1) / 2
                band = min(span["size"] * 0.16, glyph_box.height / 4)
                page.add_redact_annot(
                    fitz.Rect(left, center - band, right, center + band),
                    fill=False, cross_out=False,
                )
            original.append((item["id"], box, "".join(span["text"] for span in spans).strip()))
    if not original:
        return 0
    page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=fitz.PDF_REDACT_LINE_ART_NONE)

    remaining = _page_text_lines(page)
    residual = []
    for box, spans in remaining:
        text = "".join(span["text"] for span in spans).strip()
        if not text:
            continue
        for key, old_box, old_text in original:
            if text == old_text and (box & old_box).get_area() > 0.8 * box.get_area():
                residual.append((key, box))
                break
    if not (residual or badges):
        return 0
    unrotated = page.rect * page.derotation_matrix
    pix = page.get_pixmap(matrix=fitz.Matrix(96 / 72, 96 / 72).prerotate(-page.rotation))
    for box in badges + [box for _, box in residual]:
        fill = tuple(c / 255 for c in _sample_color(pix, unrotated, box))
        page.draw_rect(box + (-0.3, -0.3, 0.3, 0.3), color=None, fill=fill, width=0)
    return len({key for key, _ in residual})


@contextmanager
def _translation_fonts(font_dir, font_name, translations):
    if font_name != SERIF_FONT:
        yield font_dir
        return

    from fontTools import subset
    from fontTools.varLib.instancer import instantiateVariableFont

    # MuPDF Story uses static font weights. Subset the distributable variable
    # font once per document, then materialize the two weights locally.
    glyphs = set(chr(code) for code in range(32, 127))
    for block in translations:
        glyphs.update(unescape(re.sub(r"<[^>]+>", "", build_html(block.get("translation")))))
    with TemporaryDirectory(prefix="nexus-translation-fonts-") as directory:
        work_dir = Path(directory)
        options = subset.Options()
        data = lzma.decompress((Path(font_dir) / font_name).read_bytes())
        with subset.load_font(BytesIO(data), options) as font:
            subsetter = subset.Subsetter(options=options)
            subsetter.populate(unicodes={ord(char) for char in glyphs})
            subsetter.subset(font)
            for name, weight in ((SERIF_REGULAR, 400), (SERIF_BOLD, 700)):
                with instantiateVariableFont(font, {"wght": weight}, updateFontNames=True) as instance:
                    instance.save(work_dir / name)
        yield work_dir


def render_translated(*, pdf_path, layout, translations, out_pdf, font_dir, font_name, on_page=None):
    """Fit complete translated paragraphs before modifying the original page."""
    geometry = _layout_pages(layout)
    by_page = {}
    for translation in translations:
        by_page.setdefault(translation["page"], []).append(translation)
    pages_report = []
    with _translation_fonts(font_dir, font_name, translations) as font_dir, fitz.open(str(pdf_path)) as doc:
        archive = fitz.Archive(str(font_dir)) if font_dir else None
        regular = SERIF_REGULAR if font_name == SERIF_FONT else font_name
        css = ("@font-face { font-family: han; src: url(%s); }" % regular if regular else "")
        if font_name == SERIF_FONT:
            css += f"@font-face {{ font-family:han; font-weight:bold; src:url({SERIF_BOLD}); }}"
        css += "body { font-family: han, serif; color:#111; }"
        for page_no, page in enumerate(doc, 1):
            page_rect = page.rect * page.derotation_matrix
            lines = _page_text_lines(page)
            page_info = geometry.get(page_no - 1, {})
            images = _page_images(page, page_info)
            items = []
            skipped_notr = protected = 0
            blocked = []
            issues = []
            for block in by_page.get(page_no, []):
                rect = fitz.Rect(block["bbox"]) & page_rect
                zh = block.get("translation")
                if block.get("protected_layout") or _overlaps_image(rect, images):
                    if block.get("type") == "caption":
                        issues.append({"id": block["id"], "reason": "overlapping_graphic"})
                    else:
                        protected += 1
                elif zh == block.get("source"):
                    protected += 1
                elif not zh:
                    skipped_notr += 1
                    issues.append({"id": block["id"], "reason": block.get("translation_issue", "missing_translation")})
                elif (rect.width >= 20 and rect.height >= 6
                      and any(_line_in_frame(line, rect) for line, _ in lines)):
                    size, leading = _typography(lines, rect)
                    kind = block.get("type", "text")
                    color = _title_color(lines, rect) if kind == "title" else 0x111111
                    left = min((line.x0 for line, _ in lines
                                if line.width > rect.width * 0.5 and _line_in_frame(line, rect)),
                               default=rect.x0)
                    left = min(rect.x0 + 3, max(rect.x0, left))
                    items.append({"id": block["id"], "rect": rect, "type": kind, "left": left,
                                  "fs": size, "leading": leading, "color": color, "html": build_html(zh)})
                    continue
                else:
                    issues.append({"id": block["id"], "reason": "missing_native_text_frame"})
                blocked.append(rect)
            # A conflicting parser frame cannot be safely erased or painted as a rectangle.
            conflicts = set()
            for i, item in enumerate(items):
                for other in items[:i]:
                    box, previous = item["rect"], other["rect"]
                    if (box & previous).get_area() > 0.30 * min(box.get_area(), previous.get_area()):
                        conflicts.update((item["id"], other["id"]))
            blocked.extend(item["rect"] for item in items if item["id"] in conflicts)
            issues.extend({"id": key, "reason": "overlapping_frames"} for key in sorted(conflicts))
            items = [item for item in items if item["id"] not in conflicts]
            forbidden = _protected_boxes(page_info, images, lines, items, blocked)
            ready = _fit_page(items, forbidden, page_rect, css, archive)
            errors = covered = 0
            if ready:
                # A staging page ensures the source survives even if the actual paint fails.
                with fitz.open() as layer:
                    overlay = layer.new_page(width=page_rect.width, height=page_rect.height)
                    for item, target, story in ready:
                        try:
                            spare, _ = overlay.insert_htmlbox(target, story, scale_low=1)
                            if spare < 0:
                                errors += 1
                        except Exception as exc:  # noqa: BLE001 - preserve the entire page on draw failure
                            errors += 1
                            print("[translate] 回填失败 %s：%s" % (item["id"], exc))
                    if errors:
                        ready = []
                    else:
                        covered = _erase_original(page, lines, ready)
                        page.show_pdf_page(page_rect, layer, 0)
            ready_ids = {item["id"] for item, _, _ in ready}
            failed_fit = len(items) - len(ready) if not errors else 0
            issues.extend({"id": item["id"], "reason": "draw_failed" if errors else "no_safe_fit"}
                          for item in items if item["id"] not in ready_ids)
            skipped = len(by_page.get(page_no, [])) - protected - len(ready)
            page_boxes = {block["id"]: block["bbox"] for block in by_page.get(page_no, [])}
            for issue in issues:
                issue["bbox"] = page_boxes[issue["id"]]
            pages_report.append({
                "page": page_no, "replaced": len(ready), "covered_xobject": covered,
                "no_translation": skipped_notr, "dropped_overlap": len(conflicts),
                "errors": errors, "crowded": failed_fit, "preserved_original": skipped,
                "protected_layout": protected,
                "caption_replaced": sum(item["type"] == "caption" for item, _, _ in ready),
                "issues": issues,
            })
            if on_page:
                on_page(page_no, len(doc))

        if font_name != SERIF_FONT:
            doc.subset_fonts()
        doc.save(str(out_pdf), garbage=4, deflate=True, clean=True)
    return pages_report


# --------------------------------------------------------------------------- 主入口


def _translation_problem(source, candidate):
    """Detect empty, echoed or severely truncated prose before replacing the source."""
    english = sum(len(word) for word in re.findall(r"[A-Za-z]{2,}", clean_source(source)))
    if english >= 24 and clean_source(source).casefold() == clean_source(candidate).casefold():
        return "source_echo"
    visible = len(re.findall(r"[A-Za-z㐀-鿿]", clean_source(candidate)))
    if english >= 200 and visible < english * 0.18:
        return "suspected_truncation"
    return ""


def translate_blocks(llm, blocks, *, timeout_ms=90000, should_cancel=None, on_block=None):
    """逐块翻译；返回带 translation 的块列表（原地补充）。"""
    total = len(blocks)
    for index, block in enumerate(blocks):
        if should_cancel and should_cancel():
            break
        if block.get("protected_layout"):
            if on_block:
                on_block(index + 1, total)
            continue
        # OCR may mark the accent of an accented Latin letter as superscript.
        source = _normalize_pdf_symbols(block["source"])
        source = normalize("NFC", re.sub(r"<sup>([\u0300-\u036f]+)</sup>", r"\1", source))
        tokens = {"⟦T%d⟧" % number: match.group(0)
                  for number, match in enumerate(PROTECTED_TOKEN.finditer(source), 1)}
        token_ids = iter(tokens)
        text = PROTECTED_TOKEN.sub(lambda _: next(token_ids), source)
        prompt = ("【前文（仅供理解，不要翻译）】\n%s\n\n【本块（请翻译）】\n%s\n\n"
                  "【后文（仅供理解，不要翻译）】\n%s") % (
            build_html(block.get("prev")) or "(无)", text, build_html(block.get("next")) or "(无)")
        if tokens:
            meanings = ["%s = %s" % (key, re.sub(r"<[^>]+>", "", build_html(value)))
                        for key, value in tokens.items()]
            prompt += "\n\n以下占位值仅供理解。译文只保留【本块】中的编号，各出现一次。\n" + "\n".join(meanings)
        budget = block.get("text_budget")
        if budget:
            prompt += "\n\n版面约容纳 %d 个全角字宽，请尽量贴近该长度，并保留全部原文信息。" % budget
        best = ""
        best_width = float("inf")
        expected = Counter(tokens.keys())
        issue = "missing_translation"
        max_tokens = 4096
        for attempt in range(3):
            try:
                result = llm.chat(
                    [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                    {"timeoutMs": timeout_ms, "maxTokens": max_tokens},
                )
                if result.get("finish_reason") == "length":
                    issue = "truncated_response"
                    max_tokens = 8192
                    continue
                candidate = str(result.get("text") or "").strip()
            except Exception as exc:  # noqa: BLE001 - 单块失败留空，最后统一报告
                print("[translate] 块 %s 翻译失败：%s" % (block["id"], exc))
                candidate = ""
            found = Counter(PLACEHOLDER.findall(candidate))
            if not candidate or found != expected:
                if candidate and tokens:
                    issue = "invalid_placeholders"
                    missing = "、".join((expected - found).elements()) or "无"
                    extra = "、".join((found - expected).elements()) or "无"
                    prompt += "\n上次缺少占位 %s，多写占位 %s。请重新翻译，逐字复制所有指定占位。" % (missing, extra)
                continue
            candidate = PLACEHOLDER.sub(lambda m: tokens[m.group(0)], candidate)
            candidate = re.sub(r"\s+", " ", candidate).strip()
            problem = _translation_problem(source, candidate)
            if problem:
                issue = problem
                prompt += "\n上一版疑似漏译或仍为原文。请完整翻译本块的全部语句，再核对数字、引文及限制条件。"
                continue
            width = _text_units(build_html(candidate))
            if width < best_width:
                best, best_width = candidate, width
            if not budget or width <= budget * 1.08 or attempt:
                break
            prompt += (
                "\n上次译文约占 %d 个全角字宽，排版目标为 %d。请以更紧凑的学术中文重新翻译，"
                "保持全部事实、数据、限定条件和引文。"
            ) % (math.ceil(width), budget)
        block["translation"] = best
        if best:
            block.pop("translation_issue", None)
        else:
            block["translation_issue"] = issue
        if on_block:
            on_block(index + 1, total)
    return blocks
