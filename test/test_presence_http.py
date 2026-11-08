"""SSE 在线连接的集成测试：真起一个 HttpServer，用裸 socket 连 /api/presence。

要验证的是「页面关掉后服务端能不能知道」：
  · 连上 → tracker.clients == 1
  · 客户端断开（socket 关闭）→ 1 秒级内 clients 归零，空闲计时才能开始
不依赖浏览器，避免把浏览器进程清理的延迟算到服务端头上。
"""
from __future__ import annotations

import socket
import threading
import time
import unittest
from types import SimpleNamespace

from paperlib.api import presence as presence_api
from paperlib.http.presence import PresenceTracker
from paperlib.http.router import Router
from paperlib.http.server import HttpServer

ROUTES = [
    {"name": "presence", "methods": ("GET",), "pattern": "/api/presence", "handler": presence_api.stream},
]


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_until(predicate, timeout=5.0, step=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return bool(predicate())


class PresenceStreamTest(unittest.TestCase):
    def setUp(self):
        self.tracker = PresenceTracker(idle_seconds=30, grace_seconds=0)
        services = SimpleNamespace(runtime=SimpleNamespace(presence=self.tracker))
        self.port = free_port()
        self.server = HttpServer(Router(ROUTES, services), None, "127.0.0.1", self.port, verbose=False)
        self.server.start_background()
        time.sleep(0.2)

    def tearDown(self):
        self.server.shutdown()
        self.server.close()

    def _open_stream(self):
        sock = socket.create_connection(("127.0.0.1", self.port), timeout=3)
        sock.sendall(b"GET /api/presence HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\n\r\n")
        head = b""
        while b"\r\n\r\n" not in head:
            chunk = sock.recv(1024)
            if not chunk:
                break
            head += chunk
        return sock, head

    def test_connect_registers_and_disconnect_detaches(self):
        sock, head = self._open_stream()
        try:
            self.assertIn(b"200", head.split(b"\r\n")[0])
            self.assertIn(b"text/event-stream", head)
            self.assertTrue(wait_until(lambda: self.tracker.clients == 1, 3),
                            "SSE 连上后应计为 1 条在线连接")
            self.assertEqual(self.tracker.idle_for(), 0.0)
        finally:
            sock.close()
        # 关掉客户端 → 服务端应在秒级内发现并把计数归零
        self.assertTrue(wait_until(lambda: self.tracker.clients == 0, 5),
                        "客户端断开后在线连接数应归零")

    def test_second_page_counts_twice(self):
        a, _ = self._open_stream()
        b, _ = self._open_stream()
        try:
            self.assertTrue(wait_until(lambda: self.tracker.clients == 2, 3))
        finally:
            a.close()
            b.close()
        self.assertTrue(wait_until(lambda: self.tracker.clients == 0, 5))


if __name__ == "__main__":
    unittest.main()
