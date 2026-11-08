"""在线状态端点：GET /api/presence（SSE 长连接）+ GET /api/presence/status（JSON，排查用）。

前端每个页面开一条 SSE；服务端据此判断「还有没有页面挂着」。全关之后按 --idle-exit
（默认 30 秒）自动停机，用户不必再手动点「停止服务」。
"""
from __future__ import annotations

import time

from paperlib.http.presence import peer_closed
from paperlib.http.sse import SseStream

PING_SECONDS = 10.0      # 保活帧间隔：写失败也就知道对端没了（另外每秒还 peek 一次）


def _tracker(ctx):
    return getattr(ctx.services.runtime, "presence", None)


def status(ctx):
    """给调试/前端排查用：在线连接数、已空闲多久、还有几秒自动停。"""
    tracker = _tracker(ctx)
    if tracker is None:
        return ctx.json(200, {"ok": True, "enabled": False})
    ctx.json(200, {"ok": True, "enabled": True, "presence": tracker.status()})


def stream(ctx):
    """一条页面 = 一条 SSE。连接在 → 页面在；断开 → 开始计空闲。"""
    tracker = _tracker(ctx)
    if tracker is None:
        return ctx.fail(503, "在线监听见未启用")

    token = tracker.attach()
    sse = SseStream(ctx)
    conn = getattr(ctx.handler, "connection", None)
    last_ping = time.monotonic()
    try:
        sse.send({
            "event": "hello",
            "clients": tracker.clients,
            "idleSeconds": tracker.idle_seconds,
            "graceSeconds": tracker.grace_seconds,
        })
        while not sse.closed:
            time.sleep(1.0)
            if peer_closed(conn):
                break
            now = time.monotonic()
            if now - last_ping >= PING_SECONDS:
                last_ping = now
                sse.send({"event": "ping", "clients": tracker.clients})
    finally:
        # 无论正常退出、异常还是写失败，都要把这条连接从计数里摘掉，
        # 否则服务端会一直以为「页面还在」，永远不会自动停机
        tracker.detach(token)
        sse.end()
