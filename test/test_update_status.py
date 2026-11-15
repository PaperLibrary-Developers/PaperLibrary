"""P2-06 GET /api/update/status：字段齐全、只读、并发查询无竞态。"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.api import update as update_api  # noqa: E402
from paperlib.update import download, manifest as manifest_mod, release, signing  # noqa: E402
from paperlib.update import service as service_mod  # noqa: E402
from paperlib.version import ui_label  # noqa: E402

TEST_SEED = bytes(range(32))
TEST_PUBLIC = __import__("paperlib.update.ed25519", fromlist=["public_key"]).public_key(TEST_SEED)
KEYS = {"ok", "phase", "running", "version", "progress", "ready",
        "stagingVersion", "stagingPath", "error", "code", "updatedAt", "lastCheckAt"}
PHASES = {"idle", "checking", "downloading", "verifying", "extracting", "ready", "failed", "cancelled"}


def make_root(tmp: Path, version="1.6.0") -> Path:
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / "version.json").write_text(
        json.dumps({"product": "PaperLibrary", "version": version, "tag": ui_label(version)}),
        encoding="utf-8")
    return tmp


def make_zip(version="1.7.1") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("PaperLibrary.exe", b"MZ")
        zf.writestr("version.json", json.dumps({"product": "PaperLibrary", "version": version}))
        zf.writestr("paperlib/app.py", b"print('ok')\n")
    return buffer.getvalue()


def signed_manifest(version, zip_bytes):
    tag = ui_label(version)
    data = {
        "schema": 1, "product": "PaperLibrary", "channel": "stable",
        "version": version, "tag": tag,
        "publishedAt": "2026-11-15T13:52:55Z",
        "releaseNotesUrl": "https://github.com/%s/releases/tag/%s" % (release.REPO, tag),
        "minWindows": "10.0.17763", "arch": "x64",
        "assets": {"full": {"url": release.release_asset_url(tag),
                            "size": len(zip_bytes),
                            "sha256": hashlib.sha256(zip_bytes).hexdigest()}},
    }
    return manifest_mod.canonical_bytes(signing.sign_manifest(data, TEST_SEED))


class FakeDownloader:
    def __init__(self, zip_bytes, gate=None, fail=False):
        self.zip_bytes = zip_bytes
        self.gate = gate
        self.fail = fail
        self.calls = 0

    def __call__(self, store_obj, version, asset, **kwargs):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(10)
        if self.fail:
            raise download.DownloadError("network", "模拟失败")
        paths = store_obj.ensure(version)
        paths.part.write_bytes(self.zip_bytes)
        store_obj.update_state(version, phase="downloading",
                               downloadedBytes=len(self.zip_bytes),
                               totalBytes=len(self.zip_bytes))
        return {}


class StatusTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self.tmp.name) / "repo")
        self.data = Path(self.tmp.name) / "external-data"
        self.zip = make_zip()
        self.raw = signed_manifest("1.7.1", self.zip)

    def tearDown(self):
        self.tmp.cleanup()

    def make_service(self, downloader=None, fetch=None):
        return service_mod.UpdateService(
            self.root, self.data,
            fetch_manifest=(fetch or (lambda: self.raw)),
            public_key=TEST_PUBLIC,
            downloader=downloader or FakeDownloader(self.zip))

    def assert_snapshot(self, snap):
        self.assertTrue(KEYS.issubset(set(snap)), "缺字段：" + str(KEYS - set(snap)))
        self.assertIn(snap["phase"], PHASES)
        progress = snap["progress"]
        self.assertIn("downloadedBytes", progress)
        self.assertIn("totalBytes", progress)
        self.assertIn("percent", progress)
        self.assertIsInstance(progress["percent"], int)
        self.assertGreaterEqual(progress["percent"], 0)
        self.assertLessEqual(progress["percent"], 100)
        self.assertIsInstance(snap["ready"], bool)
        self.assertIsInstance(snap["running"], bool)

    def test_idle_status_is_read_only(self):
        def boom():
            raise AssertionError("status 不应拉取清单")
        svc = self.make_service(fetch=boom)
        snap = svc.snapshot()
        self.assert_snapshot(snap)
        self.assertEqual(snap["phase"], "idle")
        self.assertFalse(snap["running"])
        self.assertFalse(snap["ready"])
        self.assertEqual(snap["progress"]["percent"], 0)
        self.assertFalse((self.data / "updates").exists(), "status 不应创建更新目录")

    def test_ready_status_fields(self):
        svc = self.make_service()
        svc.start_download()
        self.assertTrue(svc.wait_idle(30))
        snap = svc.snapshot()
        self.assert_snapshot(snap)
        self.assertEqual(snap["phase"], "ready")
        self.assertTrue(snap["ready"])
        self.assertFalse(snap["running"])
        self.assertEqual(snap["stagingVersion"], "1.7.1")
        self.assertTrue(snap["stagingPath"])
        self.assertEqual(snap["progress"]["percent"], 100)
        self.assertEqual(snap["error"], "")
        self.assertEqual(snap["code"], "")

    def test_failed_status_fields(self):
        svc = self.make_service(downloader=FakeDownloader(self.zip, fail=True))
        svc.start_download()
        self.assertTrue(svc.wait_idle(30))
        snap = svc.snapshot()
        self.assert_snapshot(snap)
        self.assertEqual(snap["phase"], "failed")
        self.assertEqual(snap["code"], "network")
        self.assertIn("模拟失败", snap["error"])
        self.assertFalse(snap["running"])

    def test_concurrent_status_queries(self):
        gate = threading.Event()
        downloader = FakeDownloader(self.zip, gate=gate)
        svc = self.make_service(downloader)
        svc.start_download()
        errors = []
        seen = []

        def reader():
            try:
                for _ in range(40):
                    snap = svc.snapshot()
                    self.assert_snapshot(snap)
                    seen.append(snap["phase"])
                    time.sleep(0.001)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        readers = [threading.Thread(target=reader) for _ in range(12)]
        for thread in readers:
            thread.start()
        time.sleep(0.05)
        gate.set()
        for thread in readers:
            thread.join(20)
        self.assertEqual(errors, [])
        self.assertTrue(set(seen).issubset(PHASES))
        self.assertTrue(svc.wait_idle(30))
        self.assertEqual(svc.snapshot()["phase"], "ready")

    def test_status_endpoint_returns_snapshot_without_starting(self):
        calls = {"start": 0, "snapshot": 0}
        snapshot = {"ok": True, "phase": "idle", "running": False, "version": "",
                    "progress": {"downloadedBytes": 0, "totalBytes": 0, "percent": 0},
                    "ready": False, "stagingVersion": "", "stagingPath": "",
                    "error": "", "code": "", "updatedAt": "", "lastCheckAt": ""}

        class Stub:
            def snapshot(self):
                calls["snapshot"] += 1
                return dict(snapshot)

            def start_download(self):
                calls["start"] += 1

        ctx = SimpleNamespace(services=SimpleNamespace(update=Stub()), response=None)
        ctx.json = lambda status, data: setattr(ctx, "response", (status, data))
        ctx.fail = lambda status, message, extra=None: setattr(
            ctx, "response", (status, {"ok": False, "error": message}))
        update_api.status(ctx)
        status, body = ctx.response
        self.assertEqual(status, 200)
        self.assertEqual(body["phase"], "idle")
        self.assertEqual(calls["snapshot"], 1)
        self.assertEqual(calls["start"], 0, "status 不能触发下载")


if __name__ == "__main__":
    unittest.main()
