"""全库智能问答助手（Copilot）端点。

    GET  /api/copilot/overview   { ok, ready, model, reason, stats }  非流式，页面初始化用
    POST /api/copilot/ask        { question, history? }               SSE

编排（检索、资料包、提示词、离线兜底）都在 domain/copilot.py；
本层只做「读 body → 调 domain → 发帧」，不 import adapters。
"""
from __future__ import annotations

from paperlib.api.ai import StreamOpener
from paperlib.domain import copilot as copilot_domain

BODY_LIMIT = 256 * 1024


# GET /api/copilot/overview
def overview(ctx):
    services = ctx.services
    info = copilot_domain.engine_info(services)
    return ctx.json(200, {
        "ok": True,
        "ready": info["ready"],
        "engine": info["engine"],
        "provider": info["provider"],
        "providerLabel": info["providerLabel"],
        "model": info["model"],
        "reason": info["reason"],
        "stats": copilot_domain.stats_snapshot(services),
    })


# POST /api/copilot/ask  { question, history? } —— SSE
def ask(ctx):
    body = ctx.read_json(BODY_LIMIT)
    if not isinstance(body, dict):
        body = {}
    opener = StreamOpener(ctx)
    result = copilot_domain.ask_stream(ctx.services, body, opener)
    if not result.get("ok"):
        return ctx.fail(int(result.get("status") or 400), str(result.get("error") or "请求失败"))
    opener.end()
    return None
