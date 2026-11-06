"""文献对话 / 识图 / 抄录端点。

移植自 Sci-lib server/api/chat.js（83 行）。契约：shared/contracts/api.md §3.3。
    GET    /api/papers/:id/chat
    DELETE /api/papers/:id/chat[?pair=<n>]
    POST   /api/papers/:id/chat       { question, useLocal?, useExternal?, web? }   SSE
    POST   /api/papers/:id/vision     { question, images: [dataURL], label? }      SSE
    POST   /api/papers/:id/transcribe { image: dataURL, page? }

编排（资料包组装、调模型、写回 chat、SSE 帧顺序）全在 domain/chat.py；
本层只做「校验 → 调 domain → 发帧」，不 import adapters（模型通过 services.llm 使用）。
"""
from __future__ import annotations

from paperlib.api.ai import StreamOpener
from paperlib.domain import chat as chat_domain

CHAT_BODY_LIMIT = 256 * 1024
IMAGE_BODY_LIMIT = 24 * 1024 * 1024


def _require_paper(ctx):
    paper = ctx.services.library.get(str((ctx.params or {}).get("id") or ""))
    if not paper:
        ctx.fail(404, "找不到这条文献")
        return None
    return paper


def _body(ctx, limit):
    body = ctx.read_json(limit)
    return body if isinstance(body, dict) else {}


def _fail_from(ctx, result):
    return ctx.fail(int(result.get("status") or 400), str(result.get("error") or "请求失败"))


# GET /api/papers/:id/chat
def chat_get(ctx):
    paper = _require_paper(ctx)
    if not paper:
        return None
    fresh = ctx.services.library.get(paper.get("id")) or paper
    return ctx.json(200, {"ok": True, "messages": fresh.get("chat") or []})


# DELETE /api/papers/:id/chat            —— 清空全部对话历史
# DELETE /api/papers/:id/chat?pair=<n>   —— 只删第 n 条提问起的那一问一答
def chat_delete(ctx):
    paper = _require_paper(ctx)
    if not paper:
        return None
    library = ctx.services.library
    raw = ctx.query("pair", "")
    if not ctx.has_query("pair") or str(raw).strip() == "":
        library.update(paper.get("id"), {"chat": []})
        return ctx.json(200, {"ok": True, "messages": []})
    messages = list((library.get(paper.get("id")) or {}).get("chat") or [])
    try:
        number = float(str(raw).strip())
    except (TypeError, ValueError):
        return ctx.fail(400, "要删除的提问不存在")
    if not number.is_integer():
        return ctx.fail(400, "要删除的提问不存在")
    index = int(number)
    if index < 0 or index >= len(messages):
        return ctx.fail(400, "要删除的提问不存在")
    end = index + 1
    while end < len(messages) and (messages[end].get("role") or "user") != "user":
        end += 1
    library.update(paper.get("id"), {"chat": list(messages[:index]) + list(messages[end:])})
    fresh = list((library.get(paper.get("id")) or {}).get("chat") or [])
    return ctx.json(200, {"ok": True, "messages": fresh, "removed": end - index})


# POST /api/papers/:id/chat  { question, useLocal?, useExternal?, web? } —— SSE
def chat_post(ctx):
    paper = _require_paper(ctx)
    if not paper:
        return None
    body = _body(ctx, CHAT_BODY_LIMIT)
    opener = StreamOpener(ctx)
    result = chat_domain.chat_stream(ctx.services, paper, body, opener)
    if not result.get("ok"):
        return _fail_from(ctx, result)
    opener.end()
    return None


# POST /api/papers/:id/vision  { question, images: [dataURL], label? } —— SSE
def vision(ctx):
    paper = _require_paper(ctx)
    if not paper:
        return None
    body = _body(ctx, IMAGE_BODY_LIMIT)
    opener = StreamOpener(ctx)
    result = chat_domain.vision_stream(ctx.services, paper, body, opener)
    if not result.get("ok"):
        return _fail_from(ctx, result)
    opener.end()
    return None


# POST /api/papers/:id/transcribe  { image: dataURL, page? } —— 非流式
def transcribe(ctx):
    paper = _require_paper(ctx)
    if not paper:
        return None
    body = _body(ctx, IMAGE_BODY_LIMIT)
    result = chat_domain.transcribe(ctx.services, paper, body)
    if not result.get("ok"):
        return _fail_from(ctx, result)
    return ctx.json(200, result)
