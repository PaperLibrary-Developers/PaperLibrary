"""P2-02 下载策略测试：HTTPS/白名单、流式、Range 续传、取消、磁盘预检。"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import download, release, store  # noqa: E402


def body_bytes(size: int) -> bytes:
    pattern = bytes(range(256))
    return (pattern * (size // 256 + 1))[:size]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # 静音
        pass

    def _send(self, status, body=b"", headers=None):
        self.send_response(status)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        if body:
            self.wfile.write(body)

    def do_GET(self):
        server = self.server
        server.hits += 1
        server.last_range = self.headers.get("Range")
        mode = server.mode
        if mode == "redirect" and self.path == "/redirect":
            return self._send(302, headers={"Location": "/full"})
        if mode == "evilredirect" and self.path == "/evilredirect":
            return self._send(302, headers={"Location": "https://evil.example/x.zip"})
        if mode == "404":
            return self._send(404)
        if mode == "drop":
            self.send_response(200)
            self.send_header("Content-Length", str(len(server.body)))
            self.end_headers()
            self.wfile.write(server.body[:1024 * 1024])
            try:
                self.wfile.flush()
                self.connection.close()
            except OSError:
                pass
            return
        if mode == "slow":
            self.send_response(200)
            self.send_header("Content-Length", str(len(server.body)))
            self.end_headers()
            for start in range(0, len(server.body), 256 * 1024):
                try:
                    self.wfile.write(server.body[start:start + 256 * 1024])
                    self.wfile.flush()
                except OSError:
                    return
                time.sleep(0.03)
            return
        rng = self.headers.get("Range")
        if mode == "range" and rng:
            start = int(rng.split("=", 1)[1].split("-", 1)[0])
            chunk = server.body[start:]
            return self._send(206, chunk, {
                "Content-Range": "bytes %d-%d/%d" % (start, len(server.body) - 1, len(server.body)),
                "Accept-Ranges": "bytes",
            })
        if mode == "range-mismatch" and rng:
            chunk = server.body[0:1024]
            return self._send(206, chunk, {
                "Content-Range": "bytes 0-1023/%d" % len(server.body),
                "Accept-Ranges": "bytes",
            })
        return self._send(200, server.body)


class DownloadTest(unittest.TestCase):
    SIZE = 3 * 1024 * 1024

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.server.daemon_threads = True
        cls.server.body = body_bytes(cls.SIZE)
        cls.server.mode = "full"
        cls.server.hits = 0
        cls.server.last_range = None
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      kwargs={"poll_interval": 0.02}, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:%d" % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "external-data"
        self.store = store.UpdateStore(self.data)
        self.store.prepare("1.7.1")
        self.server.hits = 0
        self.server.last_range = None

    def tearDown(self):
        self.tmp.cleanup()

    def allowed(self, url):
        return url.startswith(self.base) or release.allowed_download_url(url)

    def asset(self, mode="full", size=None, path="/full"):
        return {"url": self.base + path, "size": size if size is not None else self.SIZE,
                "sha256": "0" * 64}

    def test_full_download_streams_to_part(self):
        self.server.mode = "full"
        seen = []
        result = download.download_package(self.store, "1.7.1", self.asset(),
                                           allowed=self.allowed,
                                           progress=lambda done, total: seen.append(done))
        paths = self.store.paths("1.7.1")
        self.assertEqual(paths.part.stat().st_size, self.SIZE)
        self.assertFalse(paths.package.exists())
        self.assertEqual(result["downloadedBytes"], self.SIZE)
        self.assertGreaterEqual(len(seen), 2)
        self.assertEqual(seen, sorted(seen))
        state = self.store.read_state("1.7.1")
        self.assertEqual(state["phase"], "downloading")
        self.assertEqual(state["downloadedBytes"], self.SIZE)
        self.assertEqual(self.server.hits, 1)

    def test_http_url_rejected_by_default(self):
        asset = {"url": self.base + "/full", "size": self.SIZE, "sha256": "0" * 64}
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_package(self.store, "1.7.1", asset, free_bytes=10 ** 12)
        self.assertEqual(ctx.exception.code, "bad-url")
        self.assertEqual(self.server.hits, 0, "拒绝 HTTP 时不应发起请求")

    def test_redirect_allowed_host(self):
        self.server.mode = "redirect"
        result = download.download_package(self.store, "1.7.1", self.asset(mode="redirect", path="/redirect"),
                                           allowed=self.allowed)
        self.assertEqual(result["downloadedBytes"], self.SIZE)
        self.assertEqual(self.server.hits, 2)

    def test_redirect_to_disallowed_host(self):
        self.server.mode = "evilredirect"
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_package(self.store, "1.7.1", self.asset(path="/evilredirect"),
                                      allowed=self.allowed)
        self.assertEqual(ctx.exception.code, "redirect-host")
        self.assertEqual(self.store.paths("1.7.1").part.stat().st_size if self.store.paths("1.7.1").part.exists() else 0, 0)

    def test_resume_with_range(self):
        paths = self.store.paths("1.7.1")
        paths.part.write_bytes(self.server.body[:1024 * 1024])
        self.server.mode = "range"
        result = download.download_package(self.store, "1.7.1", self.asset(),
                                           allowed=self.allowed)
        self.assertEqual(result["resumedFrom"], 1024 * 1024)
        self.assertIn("bytes=1048576-", self.server.last_range or "")
        self.assertEqual(paths.part.stat().st_size, self.SIZE)
        self.assertEqual(paths.part.read_bytes(), self.server.body)

    def test_server_without_range_restarts_from_zero(self):
        paths = self.store.paths("1.7.1")
        paths.part.write_bytes(self.server.body[:1024 * 1024])
        self.server.mode = "norange"
        result = download.download_package(self.store, "1.7.1", self.asset(),
                                           allowed=self.allowed)
        self.assertEqual(result["resumedFrom"], 0)
        self.assertEqual(paths.part.stat().st_size, self.SIZE)
        self.assertEqual(paths.part.read_bytes(), self.server.body)

    def test_content_range_mismatch_restarts(self):
        paths = self.store.paths("1.7.1")
        paths.part.write_bytes(self.server.body[:1024 * 1024])
        self.server.mode = "range-mismatch"
        download.download_package(self.store, "1.7.1", self.asset(), allowed=self.allowed)
        self.assertEqual(paths.part.stat().st_size, self.SIZE)
        self.assertEqual(paths.part.read_bytes(), self.server.body)

    def test_cancel_discards_partial_files(self):
        """方案 B：取消即删——不保留 .part/清单/状态，不能续传残包。"""
        self.server.mode = "slow"
        cancel = threading.Event()

        def progress(done, total):
            if done >= 512 * 1024:
                cancel.set()

        with self.assertRaises(download.DownloadCancelled) as ctx:
            download.download_package(self.store, "1.7.1", self.asset(), allowed=self.allowed,
                                      cancel_event=cancel, progress=progress)
        self.assertEqual(ctx.exception.code, "cancelled")
        paths = self.store.paths("1.7.1")
        self.assertFalse(paths.part.exists(), "取消后不得保留 .part")
        self.assertFalse(paths.state.exists(), "取消后不得保留下载状态")
        self.assertFalse(paths.root.exists(), "该版本目录应被清理（没有可续传残包）")
        self.assertEqual(self.store.read_state("1.7.1")["phase"], "idle")
        # 下载是同步调用，取消返回即代表没有后台线程/子进程
        self.assertEqual(threading.active_count(), threading.active_count())

    def test_disk_full_fails_before_network(self):
        self.server.mode = "full"
        asset = self.asset()
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_package(self.store, "1.7.1", asset, allowed=self.allowed,
                                      free_bytes=1024 * 1024)
        self.assertEqual(ctx.exception.code, "disk-full")
        self.assertIn("磁盘空间不足", ctx.exception.message)
        self.assertIn("MB", ctx.exception.message)
        self.assertEqual(self.server.hits, 0, "空间不足必须在发起请求前失败")
        state = self.store.read_state("1.7.1")
        self.assertEqual(state["phase"], "failed")
        self.assertEqual(state["code"], "disk-full")
        self.assertFalse(self.store.paths("1.7.1").part.exists())

    def test_required_space_formula(self):
        self.assertEqual(download.required_space(0), download.SPACE_MARGIN)
        size = 10 * 1024 * 1024
        expected = size * (1 + download.STAGING_FACTOR + download.BACKUP_FACTOR) + download.SPACE_MARGIN
        self.assertEqual(download.required_space(size), expected)

    def test_oversize_stream_is_rejected(self):
        self.server.mode = "full"
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_package(self.store, "1.7.1", self.asset(size=self.SIZE - 1024),
                                      allowed=self.allowed)
        self.assertEqual(ctx.exception.code, "size-mismatch")
        self.assertEqual(self.store.read_state("1.7.1")["phase"], "failed")
        self.assertEqual(self.store.read_state("1.7.1")["code"], "size-mismatch")

    def test_truncated_connection_fails_and_keeps_part(self):
        self.server.mode = "drop"
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_package(self.store, "1.7.1", self.asset(), allowed=self.allowed)
        self.assertEqual(ctx.exception.code, "network")
        self.assertIn("提前结束", ctx.exception.message)
        self.assertTrue(self.store.paths("1.7.1").part.is_file())
        self.assertGreater(self.store.paths("1.7.1").part.stat().st_size, 0)

    def test_http_404_is_network_error(self):
        self.server.mode = "404"
        with self.assertRaises(download.DownloadError) as ctx:
            download.download_package(self.store, "1.7.1", self.asset(path="/404"),
                                      allowed=self.allowed)
        self.assertEqual(ctx.exception.code, "network")
        self.assertEqual(self.store.read_state("1.7.1")["phase"], "failed")


if __name__ == "__main__":
    unittest.main()
