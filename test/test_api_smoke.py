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
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PAPERLIB_SKIP_MIGRATE"] = "1"
os.environ["PYTHONUTF8"] = "1"

from paperlib.app import build  # noqa: E402
from paperlib.adapters.llm import LLM  # noqa: E402
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

    def test_translation_concurrency_setting_validates_and_survives_reload(self):
        path = "/api/ai/settings"
        self.assertEqual(self.fx.json("GET", path)[1]["settings"]["translationConcurrency"], 64)
        try:
            status, data = self.fx.json("PUT", path, {"translationConcurrency": 11})
            self.assertEqual(status, 200)
            self.assertEqual(data["settings"]["translationConcurrency"], 11)
            for invalid in (0, 65, 1.5, True, "7", None):
                with self.subTest(invalid=invalid):
                    self.assertEqual(self.fx.json("PUT", path, {"translationConcurrency": invalid})[0], 400)
            self.assertEqual(LLM(self.fx.data_dir).public_settings()["translationConcurrency"], 11)
        finally:
            self.fx.json("PUT", path, {"translationConcurrency": 64})

    def test_translation_recovers_interrupted_job_and_cancel_preserves_pdf(self):
        import pymupdf

        status, data = self.fx.json("POST", "/api/papers", {"title": "Translation lifecycle"})
        self.assertEqual(status, 201)
        paper_id = data["paper"]["id"]
        path = "/api/papers/" + paper_id + "/translation"
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 72), "This sample PDF contains the scientific paragraph used for translation.", fontsize=12)
            pdf_bytes = doc.tobytes()
        status, _, _ = self.fx.call("POST", "/api/papers/upload", raw=(pdf_bytes, {
            "Content-Type": "application/pdf", "X-Filename": "translation-lifecycle.pdf", "X-Paper-Id": paper_id,
        }))
        self.assertEqual(status, 201)
        translator = self.fx.services.translator
        translator._write_state(paper_id, state="running", stage="translate")
        self.assertEqual(self.fx.json("GET", path)[1]["translation"]["state"], "failed")

        def wait_for_state(expected):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = self.fx.json("GET", path)[1]["translation"]
                if state["state"] == expected and paper_id not in translator._running:
                    return state
                time.sleep(0.02)
            self.fail(f"Expected {expected}, got {state}")

        with patch.object(self.fx.services.mineru, "ready", return_value=False), \
                patch.object(self.fx.services.llm, "chat", return_value={"text": "这是用于验证的译文。"}) as chat:
            self.assertTrue(self.fx.json("POST", path, {})[1]["started"])
            completed = wait_for_state("done")
            self.assertTrue(completed["exists"])
            self.assertEqual(self.fx.json("POST", path, {})[1]["reason"], "cached")
            status, media, translated = self.fx.call("GET", completed["url"])
            self.assertEqual((status, media), (200, "application/pdf"))
            with pymupdf.open(stream=translated) as doc:
                self.assertIn("这是用于验证的译文。", doc[0].get_text())

            entered = threading.Event()
            release = threading.Event()

            def hold_translation(*args, **kwargs):
                entered.set()
                release.wait(10)
                return {"text": "这次翻译已取消。"}

            chat.side_effect = hold_translation
            try:
                self.assertTrue(self.fx.json("POST", path, {"force": True})[1]["started"])
                self.assertTrue(entered.wait(10))
                self.assertEqual(self.fx.json("POST", path, {"force": True})[1]["reason"], "running")
                self.assertEqual(self.fx.json("DELETE", path)[0], 409)
                self.assertEqual(self.fx.json("POST", path + "/cancel", {})[1]["state"]["stage"], "cancelling")
            finally:
                release.set()

            wait_for_state("cancelled")
            self.assertEqual(self.fx.call("GET", completed["url"])[2], translated)
            self.assertEqual(self.fx.json("DELETE", path)[1]["translation"]["state"], "idle")
            self.assertEqual(self.fx.call("GET", completed["url"])[0], 404)

    def test_health_keys(self):
        status, data = self.fx.json("GET", "/api/health")
        self.assertEqual(status, 200)
        for key in ("ok", "app", "papers", "dataDir", "pid", "port", "uptimeSeconds",
                    "updatedAt", "bootMs", "rssMB", "version"):
            self.assertIn(key, data)

    @unittest.skipUnless(os.name == "nt", "需要 Windows 文件资源管理器")
    def test_open_papers_directory_uses_configured_store_path(self):
        path = "/api/papers/open-directory"
        with patch("paperlib.api.papers.os.startfile") as startfile:
            self.assertEqual(self.fx.call("GET", path)[0], 404)
            self.assertEqual(self.fx.call("POST", path, body={}, headers={"Origin": "https://example.org"})[0], 403)
            self.assertEqual(self.fx.call("POST", path, body={"path": "ignored"})[0], 400)
            startfile.assert_not_called()

            self.assertEqual(self.fx.json("POST", path, {}), (200, {"ok": True}))
            folder = self.fx.data_dir / "article" / "papers"
            startfile.assert_called_once_with(str(folder), "explore")

            startfile.side_effect = OSError
            status, body = self.fx.json("POST", path, {})
            self.assertEqual(status, 500)
            self.assertIn("无法打开原文文件夹", body["error"])

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
