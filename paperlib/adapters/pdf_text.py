"""PDF 文本层（移植自 server/adapters/pdf-text.js，188 行；契约名：pdf.extract_text(path) → Text）。

权威实现是 **PyMuPDF**（不是 pdfjs）：pdfjs 只在前端展示，抽取一律走这里。
与 Node 版的差异只在实现手段，输出形状保持同构：

    extract_text(abs)  -> {pages:int, text:str, perPage:[{page,lines,chars}]}
    extract_head(abs)  -> {pages, headPages, lines:[str], lineY:[int], info:{}, xmp:{}|None}
    extract_page(abs, n)-> {page, lines:[str], text}

行（line）的语义与 Node 的 richLinesFromTextContent 一致：一「视觉行」一条，
按页取、保持页内顺序，并保留 y 坐标——段落边界判断（摘要抽取）要靠行距差。
PyMuPDF 的 "dict" 模式本身已经给出视觉行，不再需要像 pdfjs 那样按 y 聚合 item。

单页坏掉不影响整篇：page_lines / page_rich_lines 内部吞掉异常并返回空列表。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

# 预检时看前几页（正式文献的 DOI 基本都在首页页眉/页脚或首页摘要旁）
HEAD_PAGES = 3

_lib = None
_probed = False
_available = None
_CONTINUATION = r"[\x80-\xbf€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ]"
_MOJIBAKE = re.compile(r"[ÃÂ]" + _CONTINUATION + "|â" + _CONTINUATION + "{2}")
_UNMAPPED = re.compile(r"[\ufffd\x00-\x08\x0e-\x1f\x7f-\x9f\ue000-\uf8ff\U000f0000-\U0010fffd]")


def _pymupdf():
    """延迟导入 PyMuPDF（fitz 是旧名；优先新名）。"""
    global _lib
    if _lib is None:
        try:
            import pymupdf as _mod
        except ImportError:  # pragma: no cover - 只可能出现在极老的环境
            import fitz as _mod

        _lib = _mod
    return _lib


def available():
    """PyMuPDF 是否可用（随包内置，正常恒 True）。

    首次调用即「探测过」，并记住结论——Node 的 availabilityKnown() 语义要靠这个区分
    「还没探测过（null）」和「探测过了（true/false）」。
    """
    global _probed, _available
    _probed = True
    if _available is None:
        try:
            _pymupdf()
            _available = True
        except Exception:  # noqa: BLE001
            _available = False
    return _available


def availability_known():
    """已探测过 → True/False；还没探测过 → None（Node: availabilityKnown()）。"""
    return _available if _probed else None


def install_hint():
    return "PDF 文本提取需要 PyMuPDF：请使用完整发布包（随包内置），或执行 pip install PyMuPDF"


def open_document(abs_path_or_bytes):
    """打开文档；调用方负责 close()。path 或 PDF 字节流都可以。"""
    lib = _pymupdf()
    if isinstance(abs_path_or_bytes, (bytes, bytearray)):
        return lib.open(stream=bytes(abs_path_or_bytes), filetype="pdf")
    return lib.open(str(abs_path_or_bytes))


def _clean_line(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _repair_encoding(text):
    def decode(match):
        fragment = match[0]
        for encoding in ("cp1252", "latin-1"):
            try:
                return fragment.encode(encoding).decode("utf-8")
            except UnicodeError:
                continue
        return fragment

    return _MOJIBAKE.sub(decode, text)


def rich_lines_from_page(page, with_fonts=False):
    """一页的视觉行：[{text, y, fonts?}]。

    y 取行的上沿（bbox[1]），只用于比较行间距，绝对值不重要。
    """
    out = []
    try:
        lib = _pymupdf()
        flags = lib.TEXTFLAGS_DICT & ~(lib.TEXT_PRESERVE_IMAGES | lib.TEXT_CID_FOR_UNKNOWN_UNICODE)
        data = page.get_text("dict", flags=flags)
    except Exception:  # noqa: BLE001 - 单页坏掉只丢这一页
        return out
    for block in (data or {}).get("blocks") or []:
        if block.get("type", 0) != 0:  # 0 = 文本块，其余是图片
            continue
        for line in block.get("lines") or []:
            parts = []
            fonts = {}
            for span in line.get("spans") or []:
                piece = span.get("text") or ""
                if not piece.strip():
                    continue
                parts.append(_repair_encoding(piece))
                if with_fonts:
                    key = (str(span.get("font") or ""), round(float(span.get("size") or 0), 1))
                    fonts[key] = fonts.get(key, 0) + len(piece)
            text = _clean_line(" ".join(parts))
            if not text:
                continue
            bbox = line.get("bbox") or (0, 0, 0, 0)
            item = {"text": text, "y": int(round(float(bbox[1] or 0)))}
            if with_fonts:
                item["fonts"] = [{"font": f, "size": s, "chars": n}
                                 for (f, s), n in sorted(fonts.items(), key=lambda kv: -kv[1])]
            out.append(item)
    return out


def page_rich_lines(doc, page_number):
    try:
        page = doc.load_page(int(page_number) - 1)
    except Exception:  # noqa: BLE001
        return []
    return rich_lines_from_page(page)


def page_lines(doc, page_number):
    return [line["text"] for line in page_rich_lines(doc, page_number)]


def _repair_pages(chunks):
    """整篇的词表只建一次，各页共用。"""
    try:
        from shared.pure import text_repair as tr
        vocab = tr.build_word_set(" ".join(chunks))
        return [tr.repair_text(chunk, vocab) for chunk in chunks]
    except Exception:  # noqa: BLE001 - 修复器失败仍保留原始抽取结果
        return chunks


def _bad_mapping(text):
    unknown = len(_UNMAPPED.findall(text))
    return unknown >= 3 and unknown * 20 >= len(text)


def _ocr_settings():
    configured = os.getenv("TESSDATA_PREFIX")
    executable = shutil.which("tesseract")
    if not configured and not executable:
        return None
    try:
        data = Path(configured or _pymupdf().get_tessdata())
    except (AttributeError, OSError, RuntimeError):
        return None
    languages = [lang for lang in ("eng", "chi_sim", "chi_tra")
                 if (data / (lang + ".traineddata")).is_file()]
    return {"tessdata": str(data), "language": "+".join(languages)} if languages else None


def _recover_page(page, settings):
    if settings:
        try:
            textpage = page.get_textpage_ocr(dpi=200, full=True, **settings)
            recovered = _clean_line(page.get_text("text", textpage=textpage))
            if len(recovered) >= 20 and not _bad_mapping(recovered):
                return recovered
        except Exception:  # noqa: BLE001 - OCR 不可用时由调用方报告页码
            pass
    return ""


def extract_text(abs_path, options=None):
    """逐页抽取文本，按页拼接；返回 {pages, text, perPage}。

    页内行用空格拼（与 Node 一致），页间用空行分隔；空页不占段落。
    """
    doc = open_document(abs_path)
    try:
        chunks = []
        per_page = []
        unreadable = []
        ocr = None
        checked_ocr = False
        for number in range(1, doc.page_count + 1):
            lines = page_lines(doc, number)
            text = _clean_line(" ".join(lines))
            if _bad_mapping(text):
                if not checked_ocr:
                    ocr = _ocr_settings()
                    checked_ocr = True
                text = _recover_page(doc.load_page(number - 1), ocr)
                if not text:
                    unreadable.append(number)
            if text:
                chunks.append(text)
            per_page.append({"page": number, "lines": len(lines), "chars": len(text)})
        pages = _repair_pages(chunks)
        return {
            "pages": int(doc.page_count),
            "text": "\n\n".join(p for p in pages if p),
            "perPage": per_page,
            "unreadablePages": unreadable,
        }
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass


def extract_page(abs_path, page_number):
    """单页文本（识图转录、调试用）。"""
    doc = open_document(abs_path)
    try:
        total = int(doc.page_count)
        number = max(1, min(int(page_number or 1), total)) if total else 1
        lines = page_lines(doc, number)
        return {"page": number, "lines": lines, "text": _clean_line(" ".join(lines))}
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass


# --------------------------------------------------------------------------- 元数据

INFO_KEYS = {
    "title": "Title",
    "author": "Author",
    "subject": "Subject",
    "keywords": "Keywords",
    "creationDate": "CreationDate",
    "modDate": "ModDate",
    "creator": "Creator",
    "producer": "Producer",
}

_XMLNS_RE = re.compile(r'xmlns:([A-Za-z0-9_.-]+)\s*=\s*"([^"]+)"')


def _local(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _ns_of(tag):
    return tag.split("}", 1)[0][1:] if tag.startswith("{") else ""


def parse_xmp(xml_text):
    """XMP 元数据 → {'dc:creator': [...], 'prism:doi': '...', ...}；解析不了返回 None。

    Node 版直接拿 pdfjs 的 metadata.getAll()；PyMuPDF 只给原始 XML，这里自己摊平成
    带前缀的键（dc:/prism:/xmp:），否则 pdf_rules 里那套「按 XMP 键名取作者/年份/DOI」的规则没法复用。
    """
    raw = str(xml_text or "").strip()
    if not raw:
        return None
    try:
        import xml.etree.ElementTree as ET

        prefixes = {uri: prefix for prefix, uri in _XMLNS_RE.findall(raw)}
        root = ET.fromstring(raw)
    except Exception:  # noqa: BLE001 - XMP 坏了不算失败
        return None
    out = {}

    def add(key, value):
        value = str(value or "").strip()
        if not value:
            return
        bucket = out.setdefault(key, [])
        if value not in bucket:
            bucket.append(value)

    for element in root.iter():
        key = _local(element.tag)
        # rdf 的结构标签本身不是字段；rdf:Description / rdf:RDF 是容器，逐个子元素处理
        if key in ("li", "Bag", "Seq", "Alt", "Description", "RDF"):
            continue
        prefix = prefixes.get(_ns_of(element.tag), "")
        name = (prefix + ":" + key) if prefix else key
        # 结构化容器（dc:creator 里套 rdf:Bag/rdf:Seq 的 rdf:li）：取全部后代 li
        values = [(node.text or "").strip() for node in element.iter()
                  if _local(node.tag) == "li" and (node.text or "").strip()]
        if values:
            for value in values:
                add(name, value)
            continue
        text = (element.text or "").strip()
        if text:
            add(name, text)
    flat = {}
    for key, values in out.items():
        flat[key] = values[0] if len(values) == 1 else values
    return flat or None


def document_info(doc):
    """(info, xmp)：info 用 Node 的大小写键（Title/Author/CreationDate…），xmp 为摊平字典。"""
    info = {}
    xmp = None
    try:
        meta = doc.metadata or {}
        for key, label in INFO_KEYS.items():
            value = meta.get(key)
            if value:
                info[label] = str(value)
    except Exception:  # noqa: BLE001
        pass
    try:
        xmp = parse_xmp(doc.get_xml_metadata())
    except Exception:  # noqa: BLE001
        xmp = None
    return info, xmp


def extract_head(abs_path, max_pages=HEAD_PAGES):
    """前 N 页的行 + 内嵌元数据（上传预检用：找 DOI / 标题 / 摘要）。"""
    doc = open_document(abs_path)
    try:
        total = int(doc.page_count)
        info, xmp = document_info(doc)
        try:
            limit = max(1, min(int(max_pages or HEAD_PAGES), total))
        except (TypeError, ValueError):
            limit = max(1, min(HEAD_PAGES, total))
        lines = []
        line_y = []
        for number in range(1, limit + 1):
            for line in page_rich_lines(doc, number):
                lines.append(line["text"])
                line_y.append(line["y"])
        return {
            "pages": total,
            "headPages": limit,
            "lines": lines,
            "lineY": line_y,
            "info": info,
            "xmp": xmp,
        }
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass
