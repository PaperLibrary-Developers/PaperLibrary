# -*- coding: utf-8 -*-
"""PDF 图表提取（PyMuPDF 渲染 + 图注 bbox/上下文匹配）。

移植自 旧 PaperLibrary 工程\\parser.py（extract_figures）
与 figures_arxiv.py（落盘规则 / 最小体积过滤）。
契约名：figures（extract_pdf_figures / rows_from_existing_images / resolve_image_path）

与旧版的差异：
  · 去掉直接 sqlite3 写入与 streamlit 缓存：只返回行 dict，落库由 api 层走 store.repo；
  · 图片一律落在 <data>/images/<paperId>/fig_NNN.<ext>，行里存相对 data 目录的路径
    （契约 §1 的 imagePath = "images/<paperId>/fig-3.png"）；
  · 增加矢量图裁切渲染：很多化学论文的图是矢量绘制（get_images 拿不到位图），
    旧引擎直接丢图 —— 这里按图注上方区域渲染成 PNG 兜底。

算法（两步，顺序即优先级）：
  1. 位图优先：page.get_images(full=True) + page.get_image_info(xrefs=True) 取内嵌位图，
     用 caption_matcher 的 bbox 贪心匹配绑定图注；
  2. 矢量兜底：本页有图注但没有任何位图绑定成功时，把图注上方的版面区域按 150dpi 渲染。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

try:
    import pymupdf as fitz
except ImportError:  # pragma: no cover
    import fitz  # type: ignore

from paperlib.legacy import caption_matcher
from paperlib.util import ensure_dir

MIN_BYTES = 3072          # 小于这个体积的基本是 logo / 装饰线
MIN_BOX = caption_matcher.MIN_BOX
MAX_FIGURES = 120
MAX_VECTOR_RENDERS = 24
RENDER_DPI = 150
VECTOR_TOP_PAD = 320      # 图注上方多少 pt 以内算「图的版面区域」
CONTEXT_CHARS = 400
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff")


def images_dir(data_dir, paper_id) -> Path:
    return Path(data_dir) / "images" / str(paper_id)


def relative_image_path(paper_id, file_name) -> str:
    return "images/" + str(paper_id) + "/" + str(file_name)


def resolve_image_path(data_dir, image_path):
    """把行里的相对路径解析成绝对路径；越界/不存在返回 None。"""
    rel = str(image_path or "").strip().replace("\\", "/")
    if not rel:
        return None
    data_abs = Path(data_dir).resolve()
    try:
        target = (data_abs / rel).resolve()
    except OSError:
        return None
    try:
        target.relative_to(data_abs)
    except ValueError:
        return None
    return str(target) if target.is_file() else None


def _clean_text(value, limit=CONTEXT_CHARS):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _page_context(page):
    try:
        return _clean_text(page.get_text("text") or "")
    except Exception:  # noqa: BLE001
        return ""


def _pixmap_is_blank(pix) -> bool:
    """粗略判断渲染区域是不是一张白纸（避免把空白版面存成图）。"""
    try:
        samples = pix.samples
    except Exception:  # noqa: BLE001
        return True
    if not samples:
        return True
    step = max(1, len(samples) // 4096)
    values = samples[::step][:4096]
    return len(set(values)) <= 3


def _save_png(pix, target: Path):
    ensure_dir(target.parent)
    pix.save(str(target))
    return target.stat().st_size


def extract_pdf_figures(pdf_path, paper_id, data_dir, options=None):
    """从 PDF 提取图表并落盘。

    返回 {"ok", "figures": [row...], "pages", "raster", "rendered", "skipped": [str...]}；
    打不开 PDF 抛异常（由 api 层转 500 并带原因）。
    """
    options = options or {}
    max_figures = int(options.get("maxFigures") or MAX_FIGURES)
    max_renders = int(options.get("maxVectorRenders") or MAX_VECTOR_RENDERS)
    doc = fitz.open(str(pdf_path))
    out_dir = images_dir(data_dir, paper_id)
    ensure_dir(out_dir)

    figures = []
    skipped = []
    seen_xref = set()
    seen_hash = set()
    render_budget = max_renders
    fig_index = 0
    raster_count = 0
    rendered_count = 0
    page_count = 0

    try:
        page_count = len(doc)
        for page_no in range(page_count):
            if fig_index >= max_figures:
                break
            page = doc[page_no]
            context = _page_context(page)
            captions = caption_matcher.extract_page_captions_with_boxes(page)
            geo = caption_matcher.match_page_images_with_captions(page) or {}
            matched_caption_ids = set()
            if geo:
                for idx, cap in enumerate(captions):
                    if cap["text"] in geo.values():
                        matched_caption_ids.add(idx)

            # ---------- 1. 位图 ----------
            try:
                image_list = page.get_images(full=True) or []
            except Exception:  # noqa: BLE001
                image_list = []
            for info in image_list:
                if fig_index >= max_figures:
                    break
                xref = int(info[0])
                if xref in seen_xref:
                    continue
                seen_xref.add(xref)
                try:
                    base = doc.extract_image(xref)
                except Exception:  # noqa: BLE001
                    skipped.append("xref %d 解包失败" % xref)
                    continue
                blob = base.get("image") or b""
                if len(blob) < MIN_BYTES:
                    continue
                digest = hashlib.sha1(blob).hexdigest()
                if digest in seen_hash:
                    continue
                seen_hash.add(digest)

                ext = str(base.get("ext") or "png").lower()
                if ext == "jpeg":
                    ext = "jpg"
                if ext not in ("png", "jpg", "gif", "webp", "bmp", "tif", "tiff"):
                    ext = "png"

                fig_index += 1
                raster_count += 1
                name = "fig_%03d.%s" % (fig_index, ext)
                target = out_dir / name
                with open(target, "wb") as fh:
                    fh.write(blob)

                caption = geo.get(xref, "")
                if not caption and captions:
                    pos = image_list.index(info) if info in image_list else 0
                    if pos < len(captions):
                        caption = captions[pos]["text"]
                figures.append({
                    "figIndex": fig_index,
                    "imagePath": relative_image_path(paper_id, name),
                    "caption": _clean_text(caption, 600),
                    "contextText": context,
                    "figureType": "raster",
                    "aiDescription": "",
                    "aiConclusion": "",
                })

            # ---------- 2. 矢量图兜底 ----------
            if render_budget <= 0 or not captions or fig_index >= max_figures:
                continue
            pending = [cap for idx, cap in enumerate(captions) if idx not in matched_caption_ids]
            for cap in pending:
                if render_budget <= 0 or fig_index >= max_figures:
                    break
                clip = _caption_clip(page, cap["bbox"])
                if clip is None:
                    continue
                try:
                    pix = page.get_pixmap(dpi=RENDER_DPI, clip=clip)
                except Exception:  # noqa: BLE001
                    skipped.append("第 %d 页矢量裁切失败" % (page_no + 1))
                    continue
                if _pixmap_is_blank(pix):
                    continue
                fig_index += 1
                rendered_count += 1
                render_budget -= 1
                name = "fig_%03d.png" % fig_index
                try:
                    _save_png(pix, out_dir / name)
                except Exception as exc:  # noqa: BLE001
                    fig_index -= 1
                    rendered_count -= 1
                    skipped.append("第 %d 页写出失败：%s" % (page_no + 1, exc))
                    continue
                figures.append({
                    "figIndex": fig_index,
                    "imagePath": relative_image_path(paper_id, name),
                    "caption": _clean_text(cap["text"], 600),
                    "contextText": context,
                    "figureType": "vector_render",
                    "aiDescription": "",
                    "aiConclusion": "",
                })
    finally:
        doc.close()

    return {
        "ok": bool(figures),
        "figures": figures,
        "pages": page_count,
        "raster": raster_count,
        "rendered": rendered_count,
        "skipped": skipped[:40],
    }


def _caption_clip(page, cap_bbox):
    """图注上方的版面区域（矢量图/表格都在那里）；太小就不用。"""
    rect = page.rect
    x0 = max(rect.x0 + 18, float(cap_bbox[0]) - 24)
    x1 = min(rect.x1 - 18, float(cap_bbox[2]) + 24)
    y1 = float(cap_bbox[1]) - 2
    y0 = max(rect.y0 + 18, y1 - VECTOR_TOP_PAD)
    if x1 - x0 < 120 or y1 - y0 < 80:
        return None
    return fitz.Rect(x0, y0, x1, y1)


def rows_from_existing_images(data_dir, paper_id):
    """复用旧库已经落盘的图片（没有图注，只有路径与序号）。"""
    folder = images_dir(data_dir, paper_id)
    if not folder.is_dir():
        return []
    rows = []
    files = sorted(p for p in folder.iterdir()
                   if p.is_file() and p.suffix.lower() in IMAGE_EXTS)
    for index, path in enumerate(files, 1):
        rows.append({
            "figIndex": index,
            "imagePath": relative_image_path(paper_id, path.name),
            "caption": "",
            "contextText": "",
            "figureType": "",
            "aiDescription": "",
            "aiConclusion": "",
        })
    return rows
