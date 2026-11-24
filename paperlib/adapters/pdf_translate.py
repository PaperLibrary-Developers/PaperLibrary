"""保留原版式的原位翻译管线（PyMuPDF 回填）。

思路（与实验结论一致）：
  1. 版面：MinerU 的 layout.json 给块类型 + PDF 点坐标；没有 MinerU 时用 PyMuPDF 自己切块。
  2. 文本：行合并、连字符折行还原、行内公式包成 ⟦...⟧ 占位符。
  3. 翻译：交给调用方传进来的 LLM（设置页那套），逐块调用并校验占位符数量。
  4. 回填：底采样 → redaction 擦除 → XObject 残留遮盖兜底 → insert_htmlbox 排中文 → 字体子集化。

只处理 text / title；ref_text、image、table、chart、页眉页脚一律不动
（页眉页脚在 MinerU 里本来就在 discarded_blocks 里）。
"""
from __future__ import annotations

import json
import math
import re
import time
import zipfile
from pathlib import Path

import fitz  # PyMuPDF

TRANSLATABLE = ("text", "title")
PLACEHOLDER = re.compile(r"⟦.*?⟧", re.S)
TAG_RE = re.compile(r"(</?(?:sup|sub|i|b|em|strong)>)")
GREEK = {
    r"\Delta": "Δ", r"\delta": "δ", r"\gamma": "γ", r"\alpha": "α", r"\beta": "β",
    r"\lambda": "λ", r"\mu": "μ", r"\pi": "π", r"\sigma": "σ", r"\tau": "τ",
    r"\theta": "θ", r"\omega": "ω", r"\Omega": "Ω", r"\Phi": "Φ", r"\varphi": "φ",
    r"\epsilon": "ε", r"\eta": "η", r"\kappa": "κ", r"\nu": "ν", r"\rho": "ρ",
    r"\chi": "χ", r"\psi": "ψ", r"\Lambda": "Λ", r"\Sigma": "Σ",
}
SYSTEM_PROMPT = (
    "你是学术论文英译中专家。把【本段】译成规范学术中文。\n"
    "硬性规则：\n"
    "0) 译文里不得出现英文单词或英文短语（专有名词、化学式、单位、软件/仪器名除外）；\n"
    "1) 只输出译文正文，不要解释、前缀或引号；\n"
    "2) 原文中 ⟦...⟧ 包裹的是数学公式，属于不可变 token：必须逐字复制、数量与原文完全一致、位置对应；\n"
    "3) 保留 <sup>..</sup> 等 HTML 标签与文献引用标号；\n"
    "4) 化学领域通行译名，术语准确；\n"
    "5) 不增删信息。【前文】【后文】只用于理解语境，不要翻译；\n"
    "6) 不得保留英文单词（专有名词、化学式、单位、仪器/软件名除外）；\n"
    "7) 译文长度尽量与原文相当，不要扩写或解释，避免比原文长很多。"
)


# --------------------------------------------------------------------------- 文本工具


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
                parts[-1] = prev[:-1]
                parts.append(cur)
                continue
            if prev.endswith("⟧") or cur.startswith("⟦"):
                parts.append(cur)
                continue
            parts[-1] = prev + " " + cur
        else:
            parts.append(cur)
    return re.sub(r"\s+", " ", " ".join(parts)).strip()


_SMALL_EN = re.compile(r"\b[a-z]{4,}\b")


def _english_words(text):
    """译文里残留的小写英文单词（大写缩写/化学式/专名不算）。"""
    return sorted({w for w in _SMALL_EN.findall(text or "")})


def placeholder_count(text):
    return len(PLACEHOLDER.findall(text or ""))


def clean_source(text):
    plain = re.sub(r"<[^>]+>", "", text or "")
    plain = PLACEHOLDER.sub(" ", plain)
    return re.sub(r"\s+", " ", plain).strip()


def latex_to_html(src):
    """把 MinerU 给的 LaTeX 片段转成 insert_htmlbox 能排的 HTML（上下标 + 希腊字母）。"""
    s = src.strip("⟦⟧")
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
    def replace(match):
        return "<span>" + _escape_keep_tags(latex_to_html(match.group(0))) + "</span>"

    return PLACEHOLDER.sub(replace, zh or "")


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
}


def blocks_from_content_list_v2(pages, page_sizes):
    """MinerU content_list_v2（按页的段落级块）→ 统一块结构。

    v2 比 layout.json 更干净：块是段落级（没有「整页父块」），bbox 是 0-1000 归一化坐标，
    行内公式以 equation_inline span 给出（包 ⟦⟧）。文本层损坏的页面也只有 v2 里还有字。
    """
    blocks = []
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
            blocks.append({
                "id": "p%dv%d" % (page_no, item_index),
                "page": page_no,
                "type": "title" if kind == "title" else "text",
                "bbox": [x0, y0, x1, y1],
                "source": text,
            })
    return relink(blocks)


def extract_content_list_v2(work_dir):
    """读 MinerU 结果里的 content_list_v2.json（可能没有）。"""
    work_dir = Path(work_dir)
    for candidate in sorted(work_dir.rglob("*_content_list_v2.json")) or sorted(work_dir.rglob("content_list_v2.json")):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, list) and data:
            return data
    return None


def blocks_from_layout(layout):
    """MinerU layout.json → [{page,index,type,bbox,source,prev,next}]（坐标是 PDF 点）。"""
    blocks = []
    for page in layout.get("pdf_info") or []:
        page_no = int(page.get("page_idx", 0)) + 1
        page_blocks = []
        for index, block in enumerate(page.get("para_blocks") or []):
            if block.get("type") not in TRANSLATABLE:
                continue
            text = merge_lines(block.get("lines") or [])
            rects = [fitz.Rect(line["bbox"]) for line in block.get("lines") or [] if line.get("bbox")]
            if rects:
                box = rects[0]
                for extra in rects[1:]:
                    box |= extra
            else:
                box = fitz.Rect(block["bbox"])   # 没有行信息：用块 bbox，文本稍后补
            if box.width < 12 or box.height < 5:
                continue
            page_blocks.append({
                "id": "p%db%d" % (page_no, index),
                "page": page_no,
                "type": block.get("type"),
                "bbox": [box.x0, box.y0, box.x1, box.y1],
                "source": text,
            })
        blocks.extend(page_blocks)
    return relink(blocks)


def relink(blocks):
    """按页、按阅读顺序（y 再 x）重算前后文，供翻译时给上下文。"""
    by_page = {}
    for block in blocks:
        by_page.setdefault(block["page"], []).append(block)
    for items in by_page.values():
        items.sort(key=lambda b: (round(b["bbox"][1], 1), round(b["bbox"][0], 1)))
        for index, block in enumerate(items):
            block["prev"] = items[index - 1]["source"][-160:] if index > 0 else ""
            block["next"] = items[index + 1]["source"][:160] if index + 1 < len(items) else ""
    return blocks


def fill_empty_sources(blocks, pdf_path):
    """MinerU 偶尔给出「有 bbox、无文本」的块（字体没有 ToUnicode）；用 PyMuPDF 回填。"""
    empty = [b for b in blocks if not b.get("source")]
    if not empty:
        return blocks
    doc = fitz.open(str(pdf_path))
    for block in empty:
        try:
            text = doc[block["page"] - 1].get_text("text", clip=fitz.Rect(block["bbox"]))
        except Exception:  # noqa: BLE001 - 取不到就保持空
            text = ""
        text = re.sub(r"\s+", " ", text or "").strip()
        if len(text) >= 20:
            block["source"] = text
    doc.close()
    return [b for b in blocks if b.get("source")]


_PAGE_CACHE = {}


def _page_of(blocks, page_no):
    """按页取 fitz 页面对象（块里带 _pdf_path 时）。"""
    doc = _PAGE_CACHE.get("doc")
    if doc is None:
        return None
    try:
        return doc[page_no - 1]
    except Exception:
        return None


def _locate_by_text(blocks, parent, original):
    """用父块文本在页面上搜索真实行位置；找不到返回 None。"""
    page = _page_of(blocks, parent["page"])
    if page is None:
        return None
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
        _PAGE_CACHE["doc"] = fitz.open(str(pdf_path))
    by_page = {}
    for block in blocks:
        by_page.setdefault(block["page"], []).append(block)
    for items in by_page.values():
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
            located = _locate_by_text(blocks, parent, original)
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
    doc = _PAGE_CACHE.pop("doc", None)
    if doc is not None:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
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


def extract_layout_from_zip(zip_path, work_dir):
    """解压 MinerU 结果，返回 layout.json 的 dict。"""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(str(zip_path)) as archive:
        archive.extractall(str(work_dir))
    for candidate in [work_dir / "layout.json"] + sorted(work_dir.rglob("layout.json")):
        if candidate.is_file():
            return json.loads(candidate.read_text(encoding="utf-8"))
    raise RuntimeError("MinerU 结果里没有 layout.json")


# --------------------------------------------------------------------------- 回填


def _font_dir():
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


def _estimate_height(html_text, font_size, width):
    """估算这段中文排进 width 宽需要多高（pt）。"""
    plain = re.sub(r"<[^>]+>", "", html_text or "")
    if not plain:
        return 0.0
    per_line = max(1.0, width / (max(font_size, 4.0) * 0.95))
    lines = math.ceil(len(plain) / per_line)
    return lines * font_size * 1.32


def _free_space_below(rect, obstacles, page_rect, margin=3.0):
    """rect 下方到最近障碍物（块/图/页脚区）之间的空白高度。"""
    limit = page_rect.y1 - 42.0      # 留出页脚/页码区，别把正文压到页脚上
    for obstacle in obstacles:
        if obstacle is rect:
            continue
        if obstacle.y0 >= rect.y1 - 0.5 and obstacle.y0 < limit \
                and obstacle.x1 > rect.x0 + 1 and obstacle.x0 < rect.x1 - 1:
            limit = obstacle.y0 - margin
    return max(0.0, limit - rect.y1)


def _shrink_against_images(rect, image_rects):
    """文字块与图片相交时，收缩到不相交的最大子矩形；实在不行就跳过。"""
    hit = 0.0
    for image in image_rects:
        if rect.intersects(image):
            hit += (rect & image).get_area()
    ratio = hit / max(1.0, rect.get_area())
    if ratio <= 0.02:
        return rect, ratio
    candidates = []
    for image in image_rects:
        if not rect.intersects(image):
            continue
        inter = rect & image
        if inter.y0 > rect.y0:
            candidates.append(fitz.Rect(rect.x0, rect.y0, rect.x1, inter.y0))
        if inter.y1 < rect.y1:
            candidates.append(fitz.Rect(rect.x0, inter.y1, rect.x1, rect.y1))
        if inter.x0 > rect.x0:
            candidates.append(fitz.Rect(rect.x0, rect.y0, inter.x0, rect.y1))
        if inter.x1 < rect.x1:
            candidates.append(fitz.Rect(inter.x1, rect.y0, rect.x1, rect.y1))
    best = None
    for candidate in candidates:
        if candidate.is_empty or candidate.get_area() < 100:
            continue
        if any(candidate.intersects(image) for image in image_rects):
            continue
        if best is None or candidate.get_area() > best.get_area():
            best = candidate
    if best is None or best.get_area() < rect.get_area() * 0.3:
        return None, ratio
    return best, ratio


def render_translated(*, pdf_path, layout, translations, out_pdf, font_dir, font_name, on_page=None):
    """把译文写回 PDF；返回每页统计。"""
    doc = fitz.open(str(pdf_path))
    archive = fitz.Archive(str(font_dir)) if font_dir else None
    css = ("@font-face { font-family: han; src: url(%s); }\n" % font_name
           if font_name else "")
    css += ("* { font-family: han; margin:0; padding:0; line-height:1.30; color:#111; }\n"
            "sub, sup { font-size: 68%; }")
    pages_report = []
    for page_no in range(1, len(doc) + 1):
        page = doc[page_no - 1]
        page_info = layout["pdf_info"][page_no - 1] if layout and page_no - 1 < len(layout.get("pdf_info") or []) else {}
        image_rects = _image_rects(page_info)
        pix = page.get_pixmap(dpi=150)
        plan = []
        skipped_notr = 0
        for block in translations:
            if block.get("page") != page_no:
                continue
            zh = block.get("translation")
            if not zh:
                skipped_notr += 1
                continue
            rect = fitz.Rect(block["bbox"])
            if rect.width < 20 or rect.height < 6:
                continue
            rect, ratio = _shrink_against_images(rect, image_rects)
            if rect is None:
                continue
            plan.append({
                "id": block["id"], "rect": rect, "source": block["source"],
                "html": build_html(zh), "color": _sample_color(pix, page.rect, rect),
                "fs": float(block.get("font_size") or 9.0), "ratio": round(ratio, 3),
            })
        # 同页仍重叠的块：合并成一块（bbox 并集 + 译文按阅读顺序拼接），不再丢弃
        kept = []
        merged_count = 0
        for item in sorted(plan, key=lambda it: (round(it["rect"].y0, 1), round(it["rect"].x0, 1))):
            merged = False
            for other in kept:
                small = min(item["rect"].get_area(), other["rect"].get_area())
                if not small:
                    continue
                if (item["rect"] & other["rect"]).get_area() / small <= 0.30:
                    continue
                if item["rect"].y0 < other["rect"].y0:
                    other["html"] = item["html"] + other["html"]
                else:
                    other["html"] = other["html"] + item["html"]
                other["rect"] |= item["rect"]
                other["fs"] = min(other["fs"], item["fs"])
                merged = True
                merged_count += 1
                break
            if not merged:
                kept.append(item)
        dropped = merged_count
        plan = kept

        # 译文可能比原文长：先估算，需要的空间比原块多就向下借空白（不越过相邻块/图）
        obstacles = [it["rect"] for it in plan] + list(image_rects)
        for item in plan:
            item["fill_rect"] = item["rect"]
            needed = _estimate_height(item["html"], item["fs"], item["rect"].width)
            if needed > item["rect"].height * 1.03:
                extra = _free_space_below(item["rect"], obstacles, page.rect)
                if extra > 6:
                    grow = min(needed - item["rect"].height, extra)
                    item["fill_rect"] = fitz.Rect(item["rect"].x0, item["rect"].y0,
                                                  item["rect"].x1, item["rect"].y1 + grow)

        for item in plan:
            page.add_redact_annot(item["rect"], fill=tuple(c / 255 for c in item["color"]))
        if plan:
            page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                                  graphics=fitz.PDF_REDACT_LINE_ART_NONE)

        # XObject 里的文字 redaction 不生效：回读文本，残留就改遮盖
        flat = re.sub(r"\s+", "", page.get_text("text"))
        covered = 0
        for item in plan:
            probe = re.sub(r"\s+", "", clean_source(item["source"]))[:24]
            item["residual"] = bool(probe) and probe in flat
            if item["residual"]:
                page.draw_rect(item["rect"], color=None,
                               fill=tuple(c / 255 for c in item["color"]), width=0, overlay=True)
                covered += 1

        errors = 0
        crowded = 0
        for item in plan:
            try:
                spare, scale = page.insert_htmlbox(item["fill_rect"], item["html"],
                                                   css="*{font-size:%.2fpt;}" % item["fs"] + css,
                                                   archive=archive, scale_low=0.55)
                if (isinstance(scale, (int, float)) and scale < 0.6) or \
                        (isinstance(spare, (int, float)) and spare < 0):
                    crowded += 1
            except Exception as exc:  # noqa: BLE001 - 单块失败不影响整页
                errors += 1
                print("[translate] 回填失败 %s：%s" % (item["id"], exc))
            item.pop("html", None)

        pages_report.append({
            "page": page_no, "replaced": len(plan), "covered_xobject": covered,
            "no_translation": skipped_notr, "dropped_overlap": dropped,
            "errors": errors, "crowded": crowded,
        })
        if on_page:
            on_page(page_no, len(doc))

    doc.subset_fonts()
    doc.save(str(out_pdf), garbage=4, deflate=True, clean=True)
    doc.close()
    return pages_report


# --------------------------------------------------------------------------- 主入口


def translate_blocks(llm, blocks, *, timeout_ms=90000, should_cancel=None, on_block=None):
    """逐块翻译；返回带 translation 的块列表（原地补充）。"""
    total = len(blocks)
    for index, block in enumerate(blocks):
        if should_cancel and should_cancel():
            break
        text = block["source"]
        expected = placeholder_count(text)
        prompt = ("【前文（仅供理解，不要翻译）】\n%s\n\n【本段（请翻译）】\n%s\n\n"
                  "【后文（仅供理解，不要翻译）】\n%s") % (
            block.get("prev") or "(无)", text, block.get("next") or "(无)")
        if expected:
            prompt += "\n\n注意：本段含 %d 个 ⟦...⟧ 公式 token，译文必须包含同样多个。" % expected
        best = ""
        nudged = False
        for attempt in range(2):
            try:
                result = llm.chat(
                    [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                    {"timeoutMs": timeout_ms, "maxTokens": 4096},
                )
                candidate = str(result.get("text") or "").strip()
            except Exception as exc:  # noqa: BLE001 - 单块失败留空，最后统一报告
                print("[translate] 块 %s 翻译失败：%s" % (block["id"], exc))
                candidate = ""
            if candidate and placeholder_count(candidate) == expected:
                # 残留小写英文单词（expediently、presumed 这类）：带点名指令再试一次
                leftovers = _english_words(candidate)
                if leftovers and not nudged:
                    prompt += ("\n\n注意：你的译文中出现了英文单词 %s，必须全部译成中文后再输出，"
                               "公式占位 ⟦...⟧ 仍保持原样。" % "、".join(leftovers[:6]))
                    nudged = True
                    best = candidate
                    continue
                best = candidate
                break
            if candidate and not best:
                best = candidate
        block["translation"] = best
        if on_block:
            on_block(index + 1, total)
    return blocks
