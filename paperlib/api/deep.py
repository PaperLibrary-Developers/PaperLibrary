"""深度分析端点（合并后新增，PaperLibrary 能力）。

  GET  /api/papers/:id/deep-analysis    取已存结果（没有则 analysis: null）
  POST /api/papers/:id/deep-analysis    运行分析（无 Key 时本地降级，仍 200）

移植自 PaperLibrary/app/deep_analysis.py（字段与五维评分，见 paperlib/legacy/deep_analysis.py）。
契约：docs/合并新增端点契约-20261107.md §7-§8。
落库走 store.repo.get_analysis / save_analysis（analysis 表，kind = "deep"）。
"""
from __future__ import annotations

from paperlib.api.paper_scope import require_paper
from paperlib.legacy import deep_analysis

KIND = "deep"


def get_analysis(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    payload = ctx.services.db.repo.get_analysis(paper.get("id"), KIND)
    ctx.json(200, {"ok": True, "analysis": payload})


def run_analysis(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    try:
        body = ctx.read_json()
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    if not isinstance(body, dict):
        return ctx.fail(400, "请求体必须是 JSON 对象")

    services = ctx.services
    store = services.db
    paper_id = paper.get("id")
    force = bool(body.get("force"))

    if not force:
        cached = store.repo.get_analysis(paper_id, KIND)
        if cached:
            return ctx.json(200, {"ok": True, "analysis": cached, "saved": True})

    figures = store.repo.list_figures(paper_id) or []
    try:
        full_text = store.get_text(paper_id) or ""
    except Exception:  # noqa: BLE001
        full_text = ""

    analysis = deep_analysis.run(services, paper, figures, full_text)
    try:
        store.repo.save_analysis(paper_id, KIND, analysis)
    except Exception as exc:  # noqa: BLE001 - 落库失败仍返回结果（前端能看）
        print("[deep] 分析结果落库失败：%s" % exc)
    ctx.json(200, {"ok": True, "analysis": analysis, "saved": True})
