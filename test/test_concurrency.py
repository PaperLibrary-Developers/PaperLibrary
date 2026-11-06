"""并发与 Range 边界回归（P1-1/P1-3 的固化测试）。

覆盖：
  · 30 并发 GET /api/papers + 20 并发 POST /api/papers 全部成功；
  · 并发写后 SQLite integrity_check = ok；
  · Range 越界 → 416 + Content-Range: bytes */<size>；
  · POST /api/release/build 并发两发 → 200 / 409（需要 runtime/portable 存在）。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
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


class ConcurrencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(tempfile.mkdtemp(prefix="pl-conc-"))
        (cls.data_dir / ".seeded").write_text("{}", encoding="utf-8")
        cls.config = Config(root=ROOT, data_dir=cls.data_dir, host="127.0.0.1", port=free_port(), no_open=True)
        cls.config.started_at = time.time()
        cls.services, cls.router, cls.static, cls.store = build(cls.config)
        cls.server = HttpServer(cls.router, cls.static, "127.0.0.1", cls.config.port, verbose=False)
        cls.server.start_background()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.close()
        cls.store.repo.close()
        import shutil
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def call(self, method, path, body=None, headers=None):
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        hdrs = dict(headers or {})
        if data:
            hdrs["Content-Type"] = "application/json"
        conn = http.client.HTTPConnection("127.0.0.1", self.config.port, timeout=30)
        try:
            conn.request(method, path, body=data, headers=hdrs)
            resp = conn.getresponse()
            return resp.status, dict(resp.getheaders()), resp.read()
        finally:
            conn.close()

    def test_parallel_reads_and_writes(self):
        results = []
        lock = threading.Lock()

        def one(index):
            try:
                if index % 3 == 0:
                    status, _h, body = self.call("POST", "/api/papers",
                                                 {"title": "并发文献 %d" % index, "year": 2024})
                    ok = status == 201 and json.loads(body).get("ok")
                else:
                    status, _h, body = self.call("GET", "/api/papers?pageSize=10")
                    ok = status == 200 and json.loads(body).get("ok")
            except Exception as exc:  # noqa: BLE001
                status, ok = 0, str(exc)
            with lock:
                results.append((status, ok))

        threads = [threading.Thread(target=one, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        bad = [r for r in results if r[0] not in (200, 201) or not r[1]]
        self.assertEqual(bad, [], bad[:5])
        with self.store.repo._lock:
            integrity = self.store.repo.conn.execute("PRAGMA integrity_check").fetchone()[0]
        self.assertEqual(integrity, "ok")

    def test_range_out_of_bounds_416(self):
        status, _h, body = self.call("POST", "/api/papers",
                                     {"title": "Range 越界测试", "year": 2024})
        paper_id = json.loads(body)["paper"]["id"]
        import pymupdf

        doc = pymupdf.open()
        doc.new_page().insert_text((72, 72), "range test body", fontsize=12)
        pdf = doc.tobytes()
        doc.close()
        # 上传用原始流（不是 JSON），单独构造请求
        conn = http.client.HTTPConnection("127.0.0.1", self.config.port, timeout=30)
        try:
            conn.request("POST", "/api/papers/upload", body=pdf, headers={
                "Content-Type": "application/pdf", "X-Filename": "r.pdf", "X-Paper-Id": paper_id})
            conn.getresponse().read()
        finally:
            conn.close()
        status, headers, _body = self.call("GET", "/api/papers/%s/pdf" % paper_id,
                                           headers={"Range": "bytes=99999999-"})
        self.assertEqual(status, 416)
        self.assertIn("bytes */", headers.get("Content-Range", ""))

    @unittest.skipUnless((ROOT / "runtime" / "portable" / "python.exe").is_file(),
                         "需要 runtime/portable（先跑 tools/make_portable.py）")
    def test_release_build_concurrent_409(self):
        results = []
        lock = threading.Lock()

        def build():
            try:
                status, _h, body = self.call("POST", "/api/release/build", {})
            except Exception as exc:  # noqa: BLE001
                status, body = 0, str(exc)
            with lock:
                results.append(status)

        threads = [threading.Thread(target=build) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(results), [200, 409], results)


if __name__ == "__main__":
    unittest.main()
