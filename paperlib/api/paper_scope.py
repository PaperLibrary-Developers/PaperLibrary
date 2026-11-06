"""辅助：取 :id 对应的文献，取不到回 404（对应 Node api/paper-scope.js）。"""
from __future__ import annotations


def require_paper(ctx):
    paper_id = (ctx.params or {}).get("id") or ""
    paper = ctx.services.library.get(paper_id)
    if not paper:
        ctx.fail(404, "找不到这条文献")
        return None
    return paper
