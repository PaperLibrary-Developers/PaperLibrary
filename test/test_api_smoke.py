"""API 冒烟：进程内起服务，覆盖契约之外的横切能力（静态/Range/405/非法 JSON/新增端点）。

只依赖 stdlib；不联网；数据目录用临时目录（PAPERLIB_SKIP_MIGRATE=1）。
"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PAPERLIB_SKIP_MIGRATE"] = "1"
os.environ["PYTHONUTF8"] = "1"

from paperlib.app import build  # noqa: E402
from paperlib.config import Config  # noqa: E402
from paperlib.http.server import HttpServer  # noqa: E402


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class ServerFixture:
    def __init__(self):
        self.data_dir = Path(tempfile.mkdtemp(prefix="pl-smoke-"))
        (self.data_dir / ".seeded").write_text("{}", encoding="utf-8")
        root = Path(__file__).resolve().parent.parent
        self.config = Config(root=root, data_dir=self.data_dir, host="127.0.0.1",
                             port=free_port(), no_open=True)
        import time
        self.config.started_at = time.time()
        self.services, self.router, self.static, self.store = build(self.config)
        self.server = HttpServer(self.router, self.static, "127.0.0.1", self.config.port, verbose=False)
        self.server.start_background()
        self.base = "http://127.0.0.1:%d" % self.config.port

    def stop(self):
        self.server.shutdown()
        self.server.close()
        self.store.repo.close()
        import shutil
        shutil.rmtree(self.data_dir, ignore_errors=True)

    def call(self, method, path, body=None, raw=None, headers=None):
        # 用 http.client 而不是 urllib：避免其它测试对 urllib 全局 opener 的 monkeypatch 串味
        import http.client

        data = None
        hdrs = dict(headers or {})
        if raw is not None:
            data, extra = raw
            hdrs.update(extra)
        elif body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.config.port, timeout=20)
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        finally:
            conn.close()

    def json(self, method, path, body=None):
        status, ctype, payload = self.call(method, path, body)
        return status, json.loads(payload.decode("utf-8") or "{}")


class SmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = ServerFixture()

    @classmethod
    def tearDownClass(cls):
        cls.fx.stop()

    def test_health_keys(self):
        status, data = self.fx.json("GET", "/api/health")
        self.assertEqual(status, 200)
        for key in ("ok", "app", "papers", "dataDir", "pid", "port", "uptimeSeconds",
                    "updatedAt", "bootMs", "rssMB", "version"):
            self.assertIn(key, data)

    def test_static_and_shared(self):
        for path, needle in (("/", b"<html"), ("/main.mjs", b"import"), ("/styles.css", b""),
                             ("/shared/html-text.mjs", b"export")):
            status, ctype, body = self.fx.call("GET", path)
            self.assertEqual(status, 200, path)
            self.assertIn(needle, body.lower(), path)

    def test_unknown_api_and_methods(self):
        status, data = self.fx.json("GET", "/api/does-not-exist")
        self.assertEqual(status, 404)
        self.assertIn("接口不存在", data["error"])
        status, data = self.fx.json("DELETE", "/api/prefs")
        self.assertEqual(status, 405)
        self.assertEqual(data["error"], "不支持的请求方法")
        status, data = self.fx.json("PATCH", "/api/tags", {"x": 1})
        self.assertEqual(status, 404)

    def test_invalid_json_500(self):
        status, ctype, body = self.fx.call("POST", "/api/papers",
                                           raw=(b"{not json", {"Content-Type": "application/json"}))
        self.assertEqual(status, 500)
        self.assertIn("JSON", json.loads(body)["error"])

    def test_paper_crud_and_range(self):
        status, data = self.fx.json("POST", "/api/papers",
                                    {"title": "冒烟 PDF 文献", "year": 2024, "authors": ["Tester"]})
        self.assertEqual(status, 201)
        paper_id = data["paper"]["id"]
        status, data = self.fx.json("GET", "/api/papers/" + paper_id)
        self.assertEqual(status, 200)
        self.assertTrue(data["pdfExists"] is False)

        import pymupdf

        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 72), "Smoke test PDF body with cucurbituril.", fontsize=12)
        pdf_bytes = doc.tobytes()
        doc.close()
        status, ctype, body = self.fx.call("POST", "/api/papers/upload",
                                           raw=(pdf_bytes, {"Content-Type": "application/pdf",
                                                            "X-Filename": "smoke.pdf",
                                                            "X-Paper-Id": paper_id}))
        self.assertEqual(status, 201)  # Node upload 返回 201
        self.assertTrue(json.loads(body)["extract"]["ok"])

        status, ctype, body = self.fx.call("GET", "/api/papers/%s/pdf" % paper_id,
                                           headers={"Range": "bytes=0-99"})
        self.assertEqual(status, 206)
        self.assertEqual(len(body), 100)
        status, ctype, body = self.fx.call("GET", "/api/papers/%s/pdf" % paper_id,
                                           headers={"If-None-Match": '"wrong"'})
        self.assertEqual(status, 200)

        status, data = self.fx.json("DELETE", "/api/papers/" + paper_id)
        self.assertEqual(status, 200)
        status, data = self.fx.json("GET", "/api/papers/" + paper_id)
        self.assertEqual(status, 404)

    def test_merged_endpoints(self):
        status, data = self.fx.json("GET", "/api/compare/templates")
        self.assertEqual(status, 200)
        self.assertEqual(len(data["templates"]), 4)
        status, data = self.fx.json("GET", "/api/terms/search?q=%E5%8F%96%E4%BB%A3")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(data["total"], 1)
        status, data = self.fx.json("GET", "/api/figures")
        self.assertEqual(status, 200)
        for key in ("items", "total", "page", "pageSize", "pageCount"):
            self.assertIn(key, data)
        status, ctype, body = self.fx.call("GET", "/api/export/obsidian")
        self.assertEqual(status, 200)
        self.assertIn("application/zip", ctype)
        self.assertTrue(body[:2] == b"PK")

    def test_ai_without_key_is_400(self):
        status, data = self.fx.json("POST", "/api/ai/summarize-sync", {"paperId": "x"})
        self.assertEqual(status, 404)  # 文献不存在优先于未配置
        status, data = self.fx.json("POST", "/api/ai/test")
        self.assertEqual(status, 400)

    def test_sse_parameter_error_before_stream(self):
        status, data = self.fx.json("POST", "/api/papers/none/summary-zh", {})
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
