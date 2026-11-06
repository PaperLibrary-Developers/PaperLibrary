"""图表端点（合并后新增，PaperLibrary 能力）。

  GET  /api/figures                      图表画廊（q / paperId / type / page / pageSize）
  GET  /api/figures/:id/image            图表图片流（Range/ETag，复用 ctx.send_file）
  GET  /api/papers/:id/figures           某篇文献的图表
  POST /api/papers/:id/figures/extract   从 PDF 重新提取（PyMuPDF 渲染 + 图注 bbox 匹配）

契约：docs/合并新增端点契约-20261107.md §1-§4（形状/状态码/错误文案逐字段对齐）。
数据访问一律经 store / store.repo（figures 表），本文件不出现 SQL。
"""
from __future__ import annotations

import os

from paperlib.api.paper_scope import require_paper
from paperlib.legacy import figures as legacy_figures
from paperlib.legacy import lexicon
from paperlib.util import clamp_int

DEFAULT_PAGE_SIZE = 48
MAX_PAGE_SIZE = 100


def _row_id(row):
    try:
        return int(row.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _figure_kind(row):
    """把行归一成 figure/table/scheme/chart 之一（图表类型筛选与展示用）。"""
    declared = str(row.get("figure_type") or "").strip().lower()
    for kind in ("table", "scheme", "chart", "figure"):
        if kind in declared:
            return kind
    caption = str(row.get("caption") or "").strip().lower()
    if caption.startswith("table"):
        return "table"
    if caption.startswith("scheme"):
        return "scheme"
    if caption.startswith("chart"):
        return "chart"
    if caption.startswith(("fig", "图")):
        return "figure"
    return declared or ""


def _has_image(data_dir, image_path):
    return bool(legacy_figures.resolve_image_path(data_dir, image_path))


def figure_item(row, data_dir, paper_title=""):
    """契约 §1 的图表项（paperTitle 由调用方决定是否带）。"""
    image_path = str(row.get("image_path") or "")
    item = {
        "id": _row_id(row),
        "paperId": str(row.get("paper_id") or ""),
        "figIndex": int(row.get("fig_index") or 0),
        "caption": str(row.get("caption") or ""),
        "contextText": str(row.get("context_text") or ""),
        "aiDescription": str(row.get("ai_description") or ""),
        "figureType": str(row.get("figure_type") or ""),
        "aiConclusion": str(row.get("ai_conclusion") or ""),
        "imagePath": image_path,
        "hasImage": _has_image(data_dir, image_path),
    }
    if paper_title != "":
        item["paperTitle"] = paper_title
    return item


def _match_query(row, needle, extra_forms):
    if not needle and not extra_forms:
        return True
    haystack = " ".join(str(row.get(key) or "") for key in
                        ("caption", "context_text", "ai_description",
                         "ai_conclusion", "figure_type")).lower()
    if needle and needle in haystack:
        return True
    for form in extra_forms:
        if form and form.lower() in haystack:
            return True
    return False


def list_figures(ctx):
    services = ctx.services
    store = services.db
    data_dir = store.data_dir
    q = str(ctx.query("q") or "").strip()
    paper_id = str(ctx.query("paperId") or "").strip()
    kind = str(ctx.query("type") or "").strip().lower()
    page = clamp_int(ctx.query("page"), 1, 100000, 1)
    page_size = clamp_int(ctx.query("pageSize"), 1, MAX_PAGE_SIZE, DEFAULT_PAGE_SIZE)

    titles = {p.get("id"): str(p.get("title") or "") for p in store.list_papers()}
    rows = store.repo.list_figures(paper_id or None)
    needle = q.lower()
    extra_forms = []
    if q:
        for form in lexicon.english_forms(q):
            if form and form.lower() not in extra_forms:
                extra_forms.append(form.lower())

    filtered = []
    for row in rows:
        if paper_id and str(row.get("paper_id")) != paper_id:
            continue
        if kind:
            declared = str(row.get("figure_type") or "").lower()
            if kind not in declared and _figure_kind(row) != kind:
                continue
        if not _match_query(row, needle, extra_forms):
            continue
        filtered.append(row)

    total = len(filtered)
    start = (page - 1) * page_size
    window = filtered[start:start + page_size]
    items = [figure_item(row, data_dir, titles.get(row.get("paper_id"), "")) for row in window]
    page_count = (total + page_size - 1) // page_size if total else 1
    ctx.json(200, {
        "ok": True,
        "items": items,
        "total": total,
        "page": page,
        "pageSize": page_size,
        "pageCount": page_count,
    })


def figure_image(ctx):
    services = ctx.services
    store = services.db
    try:
        figure_id = int(str((ctx.params or {}).get("id") or "").strip())
    except (TypeError, ValueError):
        return ctx.fail(404, "图表不存在")

    row = None
    for candidate in store.repo.list_figures():
        if _row_id(candidate) == figure_id:
            row = candidate
            break
    if row is None:
        return ctx.fail(404, "图表不存在")

    abs_path = legacy_figures.resolve_image_path(store.data_dir, row.get("image_path"))
    if not abs_path:
        return ctx.fail(404, "图表文件不存在")
    return ctx.send_file(abs_path, cache="public, max-age=86400")


def paper_figures(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    data_dir = ctx.services.db.data_dir
    rows = ctx.services.db.repo.list_figures(paper.get("id")) or []
    ctx.json(200, {
        "ok": True,
        "paperId": paper.get("id"),
        "figures": [figure_item(row, data_dir) for row in rows],
    })


def _read_options(ctx):
    body = ctx.read_json()
    if not isinstance(body, dict):
        return None, "请求体必须是 JSON 对象"
    return body, ""


def extract_figures(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    try:
        body, error = _read_options(ctx)
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    if error:
        return ctx.fail(400, error)

    services = ctx.services
    store = services.db
    paper_id = paper.get("id")
    force = bool(body.get("force"))
    data_dir = store.data_dir

    existing = store.repo.list_figures(paper_id) or []
    if existing and not force:
        items = [figure_item(row, data_dir) for row in existing]
        return ctx.json(200, {"ok": True, "extracted": len(items), "figures": items})

    pdf_file = str(paper.get("pdfFile") or "").strip()
    if not pdf_file:
        return ctx.fail(400, "这条文献没有关联 PDF")
    abs_pdf = store.path_for_paper_file(pdf_file)
    if not abs_pdf or not os.path.isfile(abs_pdf):
        return ctx.fail(400, "PDF 文件不存在：" + pdf_file)

    try:
        result = legacy_figures.extract_pdf_figures(abs_pdf, paper_id, data_dir)
    except Exception as exc:  # noqa: BLE001 - 契约 §4：提取失败 500 且带原因
        return ctx.fail(500, "图表提取失败：" + (str(exc) or type(exc).__name__))

    rows = result.get("figures") or []
    if not rows and not existing:
        # 旧库迁移过来的图片已经在 data/images/<paperId>/ 里：直接复用，不重复渲染
        rows = legacy_figures.rows_from_existing_images(data_dir, paper_id)
    if rows:
        store.repo.insert_figures(paper_id, rows, replace=True)
        saved = store.repo.list_figures(paper_id) or []
    elif force:
        store.repo.insert_figures(paper_id, [], replace=True)
        saved = []
    else:
        saved = existing
    items = [figure_item(row, data_dir) for row in saved]
    ctx.json(200, {"ok": True, "extracted": len(items), "figures": items})
