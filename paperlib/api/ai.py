"""AI 总结端点。

移植自 Sci-lib server/api/ai.js（68 行）。契约：shared/contracts/api.md §3.3 / §3.4。
    POST /api/ai/test
    POST /api/ai/summarize          （SSE）
    POST /api/ai/summarize-sync
    POST /api/papers/:id/summary-zh
    POST /api/papers/summary-zh/batch

编排（组装资料、调模型、写回卡片、SSE 帧顺序）全在 domain/ai.py；本层只做
「校验入参 → 调 domain → 组响应/发帧」，不 import adapters（模型通过 services.llm 使用）。
AI 设置（GET/PUT /api/ai/settings）在 api/settings.py；对话/识图/抄录在 api/chat.py。
"""
from __future__ import annotations

from paperlib.domain import ai as ai_domain
from paperlib.http.sse import SseStream

BODY_LIMIT = 64 * 1024


class StreamOpener:
    """惰性开流：domain 校验通过后才 open()，保证「参数/配置错误发生在开流之前」。

    open() 返回 send(payload)（写一帧）；end() 收尾（幂等，未开流时什么都不做）。
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self.stream = None

    def open(self):
        if self.stream is None:
            self.stream = SseStream(self.ctx)
        return self.stream.send

    def end(self):
        if self.stream is not None:
            self.stream.end()


def _body(ctx, limit=BODY_LIMIT, tolerate_bad_json=False):
    try:
        body = ctx.read_json(limit)
    except ValueError:
        if not tolerate_bad_json:
            raise
        return {}
    return body if isinstance(body, dict) else {}


def _require_paper(ctx):
    """取 :id 对应的文献，取不到回 404（文案与 Node api/paper-scope.js 一致）。"""
    paper = ctx.services.library.get(str((ctx.params or {}).get("id") or ""))
    if not paper:
        ctx.fail(404, "找不到这条文献")
        return None
    return paper


def _fail_from(ctx, result):
    return ctx.fail(int(result.get("status") or 400), str(result.get("error") or "请求失败"))


# POST /api/ai/test —— 保存并测试连接（未配置 → 400）
def test(ctx):
    result = ctx.services.llm.test() or {}
    ok = bool(result.get("ok"))
    body = {"ok": ok}
    body.update(result)
    body["ok"] = ok
    return ctx.json(200 if ok else 400, body)


# POST /api/papers/:id/summary-zh  { mode?: translate|generate }
def summary_zh(ctx):
    paper = _require_paper(ctx)
    if not paper:
        return None
    body = _body(ctx, tolerate_bad_json=True)
    result = ai_domain.summary_zh(ctx.services, paper, body)
    if not result.get("ok"):
        return _fail_from(ctx, result)
    return ctx.json(200, result)


# POST /api/papers/summary-zh/batch —— 给所有还没有中文总结的文献补一份
def summary_zh_batch(ctx):
    result = ai_domain.summary_zh_batch(ctx.services)
    if not result.get("ok"):
        return _fail_from(ctx, result)
    return ctx.json(200, result)


# POST /api/ai/summarize  { paperId, save?, useFulltext? } —— SSE
def summarize(ctx):
    body = _body(ctx)
    paper = ctx.services.library.get(str(body.get("paperId") or ""))
    if not paper:
        return ctx.fail(404, "找不到这条文献")
    opener = StreamOpener(ctx)
    result = ai_domain.summarize_stream(ctx.services, paper, body, opener)
    if not result.get("ok"):
        return _fail_from(ctx, result)
    opener.end()
    return None


# POST /api/ai/summarize-sync  { paperId, save? } —— 不用流式的场合（脚本/测试）
def summarize_sync(ctx):
    body = _body(ctx)
    paper = ctx.services.library.get(str(body.get("paperId") or ""))
    if not paper:
        return ctx.fail(404, "找不到这条文献")
    result = ai_domain.summarize_sync(ctx.services, paper, body)
    if not result.get("ok"):
        return _fail_from(ctx, result)
    return ctx.json(200, result)
