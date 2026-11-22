"""HTTP 本机安全防护、LLM 换源清密钥与 /shared/ 边界回归测试。"""
from __future__ import annotations

import http.client
import io
import json
import os
import shutil
import socket
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PAPERLIB_SKIP_MIGRATE"] = "1"
os.environ["PYTHONUTF8"] = "1"

from paperlib.adapters.llm import LLM  # noqa: E402
from paperlib.app import build  # noqa: E402
from paperlib.config import Config  # noqa: E402
from paperlib.http.server import (  # noqa: E402
    HttpServer,
    is_allowed_fetch_site,
    is_allowed_host,
    is_allowed_origin,
)
from paperlib.util import UnsupportedMediaTypeError, read_json_body  # noqa: E402


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class PureHeaderGuardTests(unittest.TestCase):
    def test_allowed_host_checks(self):
        self.assertTrue(is_allowed_host("127.0.0.1:4321", "127.0.0.1", 4321))
        self.assertTrue(is_allowed_host("localhost:4321", "127.0.0.1", 4321))
        self.assertTrue(is_allowed_host("[::1]:4321", "127.0.0.1", 4321))
        self.assertTrue(is_allowed_host("127.0.0.1", "127.0.0.1", 4321))
        # DNS rebinding / spoofed host / wrong port
        self.assertFalse(is_allowed_host("", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_host("evil.example:4321", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_host("127.0.0.1.evil.example:4321", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_host("127.0.0.1:9999", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_host("127.0.0.1@evil.example:4321", "127.0.0.1", 4321))

    def test_allowed_origin_and_fetch_site(self):
        self.assertTrue(is_allowed_origin("", "127.0.0.1", 4321))
        self.assertTrue(is_allowed_origin("http://127.0.0.1:4321", "127.0.0.1", 4321))
        self.assertTrue(is_allowed_origin("http://localhost:4321", "127.0.0.1", 4321))
        self.assertTrue(is_allowed_origin("http://[::1]:4321", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_origin("null", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_origin("https://evil.example", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_origin("https://127.0.0.1:4321", "127.0.0.1", 4321))
        self.assertFalse(is_allowed_origin("http://127.0.0.1:9999", "127.0.0.1", 4321))

        self.assertTrue(is_allowed_fetch_site(""))
        self.assertTrue(is_allowed_fetch_site("same-origin"))
        self.assertTrue(is_allowed_fetch_site("same-site"))
        self.assertTrue(is_allowed_fetch_site("none"))
        self.assertFalse(is_allowed_fetch_site("cross-site"))

    def test_read_json_body_rejects_simple_cors_content_types(self):
        payload = b'{"baseURL":"https://evil.example/v1"}'
        for ctype in (
            "text/plain",
            "text/plain; charset=utf-8",
            "application/x-www-form-urlencoded",
            "multipart/form-data; boundary=abc",
        ):
            handler = SimpleNamespace(
                headers={"Content-Type": ctype, "Content-Length": str(len(payload))},
                rfile=io.BytesIO(payload),
            )
            with self.assertRaises(UnsupportedMediaTypeError):
                read_json_body(handler)

        for ctype in ("application/json", "application/json; charset=utf-8", ""):
            handler = SimpleNamespace(
                headers={"Content-Type": ctype, "Content-Length": str(len(payload))},
                rfile=io.BytesIO(payload),
            )
            self.assertEqual(read_json_body(handler), {"baseURL": "https://evil.example/v1"})


class LlmBaseUrlOriginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pl-llm-sec-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_changing_baseurl_origin_clears_existing_api_key(self):
        llm = LLM(self.tmp)
        llm.update({"baseURL": "https://api.deepseek.com/v1", "apiKey": "sk-secret-deepseek"})
        self.assertEqual(llm.settings["apiKey"], "sk-secret-deepseek")

        # 同一 origin 下调整路径保留密钥
        llm.update({"baseURL": "https://api.deepseek.com/beta"})
        self.assertEqual(llm.settings["apiKey"], "sk-secret-deepseek")

        # 切换到不同主机且未给新密钥：必须清空旧密钥
        llm.update({"baseURL": "https://attacker.example/v1"})
        self.assertEqual(llm.settings["apiKey"], "")

        # 降级协议（https -> http）且传打码值：同样清空旧密钥
        llm.update({"baseURL": "https://api.deepseek.com/v1", "apiKey": "sk-secret-deepseek"})
        llm.update({"baseURL": "http://api.deepseek.com/v1", "apiKey": "sk-s…seek"})
        self.assertEqual(llm.settings["apiKey"], "")

        # 切换来源的同时显式提供新密钥：保存新密钥
        llm.update({"baseURL": "https://api.openai.com/v1", "apiKey": "sk-new-openai"})
        self.assertEqual(llm.settings["apiKey"], "sk-new-openai")


class HttpSecurityIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(tempfile.mkdtemp(prefix="pl-http-sec-"))
        (cls.data_dir / ".seeded").write_text("{}", encoding="utf-8")
        cls.port = _free_port()
        cls.config = Config(root=ROOT, data_dir=cls.data_dir, host="127.0.0.1",
                            port=cls.port, no_open=True)
        cls.config.started_at = time.time()
        cls.services, cls.router, cls.static, cls.store = build(cls.config)
        cls.server = HttpServer(cls.router, cls.static, "127.0.0.1", cls.port, verbose=False)
        cls.server.start_background()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.close()
        cls.store.repo.close()
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def _raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, path, body=body, headers=headers or {})
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_dns_rebinding_host_rejected(self):
        status, body = self._raw("GET", "/api/health", headers={"Host": f"evil.example:{self.port}"})
        self.assertEqual(status, 403)
        self.assertIn("Host", json.loads(body)["error"])

    def test_cross_origin_and_cross_site_mutations_rejected(self):
        self.services.llm.update({"baseURL": "https://api.deepseek.com/v1", "apiKey": "sk-keep-me"})
        payload = json.dumps({"baseURL": "https://attacker.example/v1"}).encode("utf-8")

        status, _ = self._raw("POST", "/api/ai/settings", body=payload, headers={
            "Origin": "https://attacker.example",
            "Content-Type": "application/json",
        })
        self.assertEqual(status, 403)

        status, _ = self._raw("POST", "/api/ai/settings", body=payload, headers={
            "Sec-Fetch-Site": "cross-site",
            "Content-Type": "application/json",
        })
        self.assertEqual(status, 403)

        status, _ = self._raw("POST", "/api/shutdown", body=b"", headers={
            "Origin": "https://attacker.example",
        })
        self.assertEqual(status, 403)

        # 原配置和服务未被篡改/关闭
        self.assertEqual(self.services.llm.settings["baseURL"], "https://api.deepseek.com/v1")
        self.assertEqual(self.services.llm.settings["apiKey"], "sk-keep-me")
        status, _ = self._raw("GET", "/api/health")
        self.assertEqual(status, 200)

    def test_text_plain_rejected_for_json_but_allowed_for_attachment_upload(self):
        payload = b'{"baseURL":"https://attacker.example/v1"}'
        status, body = self._raw("POST", "/api/ai/settings", body=payload, headers={
            "Content-Type": "text/plain; charset=utf-8",
        })
        self.assertEqual(status, 415)
        self.assertIn("Content-Type", json.loads(body)["error"])

        # 同源上传 .txt 补充材料（浏览器会发 text/plain）仍应正常成功
        paper = self.services.library.create({"title": "Attachment upload check"})
        status, body = self._raw(
            "POST",
            f"/api/papers/{paper['id']}/attachments",
            body=b"supplementary table content line 1\n",
            headers={
                "Origin": f"http://127.0.0.1:{self.port}",
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "text/plain",
                "X-Filename": "si-notes.txt",
            },
        )
        self.assertEqual(status, 201)
        self.assertTrue(json.loads(body)["ok"])

    def test_shared_only_serves_mjs(self):
        status, _ = self._raw("GET", "/shared/html-text.mjs")
        self.assertEqual(status, 200)
        for blocked in ("/shared/pure/text.py", "/shared/contracts/api.md", "/shared/fixtures/seed-library.json"):
            status, _ = self._raw("GET", blocked)
            self.assertEqual(status, 404, blocked)


if __name__ == "__main__":
    unittest.main()
