"""原文 / 译文翻译端点。

  GET    /api/papers/:id/translation        任务状态 + 产物地址
  POST   /api/papers/:id/translation        启动（force=true 重跑）
  POST   /api/papers/:id/translation/cancel 取消
  GET    /api/papers/:id/translation/review   阅读器内的文本块坐标
  POST   /api/papers/:id/translation/retry    重译已保存的单个文本块
  POST   /api/papers/:id/translation/region   获取、创建、重译、写回或关闭选区草稿
  DELETE /api/papers/:id/translation        删除译文产物
  GET    /api/papers/:id/translation.pdf    预览 / 下载译文 PDF
"""
from __future__ import annotations

import re

from paperlib.api.paper_scope import require_paper
from paperlib.domain.translate import TranslateError


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


def review(ctx):
    paper = require_paper(ctx)
    if paper:
        return ctx.json(200, ctx.services.translator.review(paper))


def retry(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    try:
        body = ctx.read_json()
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    if not isinstance(body, dict):
        return ctx.fail(400, "请求体必须是对象")
    block_id, revision = body.get("blockId"), body.get("revision")
    if not isinstance(block_id, str) or not 1 <= len(block_id) <= 100:
        return ctx.fail(400, "缺少有效的文本块 ID")
    if not isinstance(revision, str) or not re.fullmatch("[a-f0-9]{64}", revision):
        return ctx.fail(400, "缺少有效的译文版本")
    try:
        return ctx.json(200, {"ok": True, **ctx.services.translator.retry(paper, block_id, revision)})
    except TranslateError as exc:
        return ctx.fail(409, str(exc))


def region(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    try:
        body = ctx.read_json()
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    if not isinstance(body, dict):
        return ctx.fail(400, "请求体必须是对象")
    action = body.get("action")
    translator = ctx.services.translator
    try:
        if action == "inspect":
            revision, page, rect = body.get("revision"), body.get("page"), body.get("rect")
            valid_box = (isinstance(rect, list) and len(rect) == 4
                         and all(type(value) in (int, float) and 0 <= value <= 1 for value in rect))
            if (not isinstance(revision, str) or not re.fullmatch("[a-f0-9]{64}", revision)
                    or type(page) is not int or page < 1 or not valid_box
                    or not (rect[0] < rect[2] and rect[1] < rect[3])):
                return ctx.fail(400, "缺少有效的译文版本、页码或框选坐标")
            return ctx.json(200, translator.inspect_region(paper, revision, page, rect))
        if action == "status":
            return ctx.json(200, {"draft": translator.region_status(paper)})
        if action not in ("preview", "apply", "close"):
            return ctx.fail(400, "未知的框选操作")
        token = body.get("token")
        if not isinstance(token, str) or not re.fullmatch("[a-f0-9]{32}", token):
            return ctx.fail(400, "缺少有效的框选记录")
        if action == "close":
            translator.close_region(paper, token)
            return ctx.json(200, {"draft": None})
        handler = translator.preview_region if action == "preview" else translator.apply_region
        return ctx.json(200, handler(paper, token))
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    except TranslateError as exc:
        return ctx.fail(409, str(exc))


def remove(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    try:
        state = ctx.services.translator.remove(paper)
    except TranslateError as exc:
        return ctx.fail(409, str(exc))
    return ctx.json(200, {"ok": True, "translation": state})


def pdf(ctx):
    paper = require_paper(ctx)
    if not paper:
        return
    target = ctx.services.translator.existing_pdf(paper)
    if target is None:
        return ctx.fail(404, "还没有生成译文")
    ctx.send_file(str(target), content_type="application/pdf",
                  inline="translated.pdf", cache="no-cache")
