"""中英术语检索（合并后新增，PaperLibrary 119 条术语表 + 合并后补充）。

  GET /api/terms/search?q=取代&limit=20

契约：docs/合并新增端点契约-20261107.md §9（q 必填；limit 1-100，默认 20）。
术语数据在 paperlib/legacy/terms.json（155 条，含 zh/en/aliases/field）。
"""
from __future__ import annotations

from paperlib.legacy import lexicon
from paperlib.util import clamp_int

DEFAULT_LIMIT = 20
MAX_LIMIT = 100


def search_terms(ctx):
    query = str(ctx.query("q") or "").strip()
    if not query:
        return ctx.fail(400, "请输入要检索的术语（中英文均可）")
    limit = clamp_int(ctx.query("limit"), 1, MAX_LIMIT, DEFAULT_LIMIT)
    items, total = lexicon.search(query, limit)
    ctx.json(200, {"ok": True, "query": query, "items": items, "total": total})
