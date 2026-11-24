"""原文 / 译文翻译端点。

  GET    /api/papers/:id/translation        任务状态 + 产物地址
  POST   /api/papers/:id/translation        启动（force=true 重跑）
  POST   /api/papers/:id/translation/cancel 取消
  DELETE /api/papers/:id/translation        删除译文产物
  GET    /api/papers/:id/translation.pdf    预览 / 下载译文 PDF
"""
from __future__ import annotations

from pathlib import Path

from paperlib.api.paper_scope import require_paper


def status(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    state = ctx.services.translator.state(paper)
    return ctx.json(200, {"ok": True, "translation": state})


def start(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    try:
        body = ctx.read_json() or {}
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    if not isinstance(body, dict):
        return ctx.fail(400, "请求体必须是对象")
    result = ctx.services.translator.start(paper, force=bool(body.get("force")))
    return ctx.json(200, {"ok": True, **result})


def cancel(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    result = ctx.services.translator.cancel(paper)
    return ctx.json(200, {"ok": True, **result})


def remove(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    translator = ctx.services.translator
    target = translator.pdf_path(paper)
    if target.is_file():
        target.unlink()
    translator._write_state(paper.get("id"), state="idle", stage="", progress=0,
                            total=0, pages=0, pagesDone=0, error="")
    return ctx.json(200, {"ok": True, "translation": translator.state(paper)})


def pdf(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    target = ctx.services.translator.pdf_path(paper)
    if not target.is_file():
        return ctx.fail(404, "还没有生成译文")
    ctx.send_file(str(target), content_type="application/pdf",
                  inline="translated.pdf", cache="no-cache")
