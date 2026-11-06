"""SSE 小工具（对应 Node 的 server/http/sse.js）：帧格式 data: <json>\n\n。"""
from __future__ import annotations

import json

SSE_HEADERS = {
    "Content-Type": "text/event-stream; charset=utf-8",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


class SseStream:
    """打开一条 SSE 流；send() 写一帧，end() 收尾（幂等）。"""

    def __init__(self, ctx):
        self.ctx = ctx
        self.closed = False
        ctx.start_stream(200, SSE_HEADERS)

    def send(self, payload):
        if self.closed:
            return
        try:
            self.ctx.write_chunk(b"data: " + json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n\n")
        except (BrokenPipeError, ConnectionResetError, OSError):
            self.closed = True

    def end(self):
        if self.closed:
            return
        self.closed = True
        try:
            self.ctx.end_stream()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
