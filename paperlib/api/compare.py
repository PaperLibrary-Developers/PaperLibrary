"""横向对比端点（合并后新增，PaperLibrary 能力）。

  GET  /api/compare/templates   4 套模板（合成 / 电化学 / 材料 / 通用）
  POST /api/compare             { paperIds[], template? } → 横向对比结果

移植自 PaperLibrary/app/compare_engine.py（模板/Map-Reduce，见 paperlib/legacy/compare_engine.py）。
契约：docs/合并新增端点契约-20261107.md §5-§6。
无 Key（offline 目标 G2）时由 legacy.compare_engine 本地降级，**仍然 200**，不编造数值。
"""
from __future__ import annotations

from paperlib.legacy import compare_engine

MIN_PAPERS = 2
MAX_PAPERS = 8


def templates(ctx):
    ctx.json(200, {"ok": True, "templates": compare_engine.templates_public()})


def _normalize_ids(raw):
    ids = []
    seen = set()
    for value in raw if isinstance(raw, (list, tuple)) else []:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        ids.append(text)
    return ids


def run(ctx):
    try:
        body = ctx.read_json()
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    if not isinstance(body, dict):
        return ctx.fail(400, "请求体必须是 JSON 对象")

    ids = _normalize_ids(body.get("paperIds"))
    if len(ids) < MIN_PAPERS:
        return ctx.fail(400, "至少选择 2 篇文献才能对比")
    if len(ids) > MAX_PAPERS:
        return ctx.fail(400, "一次最多对比 8 篇文献")

    papers = []
    for paper_id in ids:
        paper = ctx.services.library.get(paper_id)
        if not paper:
            return ctx.fail(404, "找不到这条文献")
        papers.append(paper)

    template = str(body.get("template") or "").strip() or "general"
    result = compare_engine.run(ctx.services, papers, template)
    ctx.json(200, {"ok": True, "result": result})
