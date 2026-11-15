"""P2-05 POST /api/update/download：单任务编排、状态落盘、失败重试。"""
from __future__ import annotations

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
from paperlib.update import download, fetch, manifest as manifest_mod, release, signing  # noqa: E402
from paperlib.update import service as service_mod, store, verify, staging  # noqa: E402
from paperlib.version import VersionError, ui_label  # noqa: E402

TEST_SEED = bytes(range(32))
TEST_PUBLIC = __import__("paperlib.update.ed25519", fromlist=["public_key"]).public_key(TEST_SEED)


def make_root(tmp: Path, version="1.6.0") -> Path:
    (tmp / "version.json").write_text(
        json.dumps({"product": "PaperLibrary", "version": version, "tag": ui_label(version)}),
        encoding="utf-8")
    return tmp


def make_zip(version="1.7.1") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("PaperLibrary.exe", b"MZ launcher")
        zf.writestr("version.json", json.dumps({"product": "PaperLibrary", "version": version}))
        zf.writestr("paperlib/app.py", b"print('ok')\n")
    return buffer.getvalue()


def signed_manifest(version, zip_bytes, seed=TEST_SEED):
    tag = ui_label(version)
    import hashlib
    data = {
        "schema": 1, "product": "PaperLibrary", "channel": "stable",
        "version": version, "tag": tag,
        "publishedAt": "2026-11-15T13:52:55Z",
        "releaseNotesUrl": "https://github.com/%s/releases/tag/%s" % (release.REPO, tag),
        "minWindows": "10.0.17763", "arch": "x64",
        "assets": {"full": {
            "url": release.release_asset_url(tag),
            "size": len(zip_bytes),
            "sha256": hashlib.sha256(zip_bytes).hexdigest(),
        }},
    }
    return manifest_mod.canonical_bytes(signing.sign_manifest(data, seed))


class FakeDownloader:
    """把预生成的 zip 写进 .part；可阻塞/失败，用来测并发与重试。"""

    def __init__(self, zip_bytes, gate=None, fail_times=0):
        self.zip_bytes = zip_bytes
        self.gate = gate
        self.fail_times = fail_times
        self.calls = 0
        self.lock = threading.Lock()

    def __call__(self, store_obj, version, asset, **kwargs):
        with self.lock:
            self.calls += 1
            call = self.calls
        if self.gate is not None:
            if not self.gate.wait(10):
                raise RuntimeError("gate timeout")
        if call <= self.fail_times:
            raise download.DownloadError("network", "模拟下载失败")
        paths = store_obj.ensure(version)
        paths.part.write_bytes(self.zip_bytes)
        store_obj.update_state(version, phase="downloading",
                               downloadedBytes=len(self.zip_bytes),
                               totalBytes=len(self.zip_bytes))
        return {"version": version, "downloadedBytes": len(self.zip_bytes),
                "totalBytes": len(self.zip_bytes), "resumedFrom": 0,
                "partPath": str(paths.part), "sourceUrl": asset["url"]}


class CancelAwareDownloader:
    """在取消事件被设置时抛 DownloadCancelled，模拟真实下载的中途取消。"""

    def __init__(self, zip_bytes):
        self.zip_bytes = zip_bytes

    def __call__(self, store_obj, version, asset, cancel_event=None, **kwargs):
        for _ in range(500):
            if cancel_event is not None and cancel_event.is_set():
                raise download.DownloadCancelled()
            time.sleep(0.01)
        paths = store_obj.ensure(version)
        paths.part.write_bytes(self.zip_bytes)
        return {}


class ServiceDownloadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir(parents=True, exist_ok=True)
        make_root(self.root)
        self.data = Path(self.tmp.name) / "external-data"
        self.zip = make_zip()
        self.raw = signed_manifest("1.7.1", self.zip)

    def tearDown(self):
        self.tmp.cleanup()

    def make_service(self, downloader=None, raw=None):
        return service_mod.UpdateService(
            self.root, self.data,
            fetch_manifest=(lambda: raw if raw is not None else self.raw),
            public_key=TEST_PUBLIC,
            downloader=downloader or FakeDownloader(self.zip))

    def test_download_pipeline_reaches_ready(self):
        downloader = FakeDownloader(self.zip)
        svc = self.make_service(downloader)
        snap = svc.start_download()
        self.assertTrue(snap["started"])
        self.assertTrue(snap["running"])
        self.assertIn(snap["phase"], ("downloading", "verifying", "extracting"))
        self.assertTrue(svc.wait_idle(30))
        snap = svc.snapshot()
        self.assertEqual(snap["phase"], "ready")
        self.assertTrue(snap["ready"])
        self.assertEqual(snap["version"], "1.7.1")
        self.assertEqual(snap["stagingVersion"], "1.7.1")
        self.assertEqual(snap["progress"]["percent"], 100)
        self.assertEqual(downloader.calls, 1)
        paths = svc.store.paths("1.7.1")
        self.assertTrue((paths.staging / "PaperLibrary.exe").is_file())
        self.assertTrue(paths.ready.is_file())
        self.assertEqual(svc.store.read_state("1.7.1")["phase"], "ready")
        self.assertEqual(svc.store.read_manifest("1.7.1"), self.raw)

    def test_duplicate_calls_do_not_start_second_download(self):
        gate = threading.Event()
        downloader = FakeDownloader(self.zip, gate=gate)
        svc = self.make_service(downloader)
        first = svc.start_download()
        self.assertTrue(first["started"])
        second = svc.start_download()
        self.assertFalse(second.get("started"))
        self.assertTrue(second["running"])
        deadline = time.time() + 5
        while downloader.calls < 1 and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(downloader.calls, 1, "重复调用不能并发重复下载")
        gate.set()
        self.assertTrue(svc.wait_idle(30))
        self.assertEqual(svc.snapshot()["phase"], "ready")
        third = svc.start_download()
        self.assertFalse(third.get("started"))
        self.assertEqual(downloader.calls, 1, "ready 后重复调用不应重新下载")

    def test_failure_then_retry(self):
        downloader = FakeDownloader(self.zip, fail_times=1)
        svc = self.make_service(downloader)
        svc.start_download()
        self.assertTrue(svc.wait_idle(30))
        snap = svc.snapshot()
        self.assertEqual(snap["phase"], "failed")
        self.assertEqual(snap["code"], "network")
        self.assertIn("模拟下载失败", snap["error"])
        # 再次调用 = 重试（.part 不存在从头下；存在则续传）
        retry = svc.start_download()
        self.assertTrue(retry["started"])
        self.assertTrue(svc.wait_idle(30))
        self.assertEqual(svc.snapshot()["phase"], "ready")
        self.assertEqual(downloader.calls, 2)

    def test_no_update_is_rejected(self):
        raw = signed_manifest("1.6.0", self.zip)  # 与当前 1.6.0 相同
        svc = service_mod.UpdateService(self.root, self.data, fetch_manifest=lambda: raw,
                                        public_key=TEST_PUBLIC,
                                        downloader=FakeDownloader(self.zip))
        with self.assertRaises(service_mod.NoUpdateError) as ctx:
            svc.start_download()
        self.assertEqual(ctx.exception.status, 400)
        self.assertEqual(svc.snapshot()["phase"], "idle")

    def test_check_network_error_propagates(self):
        def boom():
            raise fetch.FetchError("timeout", "模拟超时")
        svc = service_mod.UpdateService(self.root, self.data, fetch_manifest=boom,
                                        public_key=TEST_PUBLIC,
                                        downloader=FakeDownloader(self.zip))
        with self.assertRaises(fetch.FetchError) as ctx:
            svc.start_download()
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertEqual(svc.snapshot()["phase"], "idle")
        self.assertFalse(svc.snapshot()["running"])

    def test_checking_phase_visible_and_duplicate_rejected(self):
        gate = threading.Event()
        started = threading.Event()

        def slow_fetch():
            started.set()
            gate.wait(10)
            return self.raw

        svc = service_mod.UpdateService(self.root, self.data, fetch_manifest=slow_fetch,
                                        public_key=TEST_PUBLIC,
                                        downloader=FakeDownloader(self.zip))
        result = {}

        def run():
            result["snap"] = svc.start_download()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        self.assertTrue(started.wait(5))
        time.sleep(0.05)
        during = svc.start_download()
        self.assertFalse(during.get("started"))
        self.assertEqual(during["phase"], "checking")
        self.assertTrue(during["running"])
        gate.set()
        thread.join(10)
        self.assertTrue(svc.wait_idle(30))
        self.assertEqual(result["snap"]["started"], True)
        self.assertEqual(svc.snapshot()["phase"], "ready")

    def test_ready_survives_restart(self):
        svc = self.make_service()
        svc.start_download()
        self.assertTrue(svc.wait_idle(30))
        restarted = service_mod.UpdateService(self.root, self.data,
                                              fetch_manifest=lambda: (_ for _ in ()).throw(
                                                  AssertionError("不应再拉清单")),
                                              public_key=TEST_PUBLIC,
                                              downloader=FakeDownloader(self.zip))
        snap = restarted.snapshot()
        self.assertEqual(snap["phase"], "ready")
        self.assertTrue(snap["ready"])
        self.assertEqual(snap["stagingVersion"], "1.7.1")

    def test_interrupted_state_marked_failed_and_resumable(self):
        st = store.UpdateStore(self.data)
        st.prepare("1.7.1")
        st.paths("1.7.1").part.write_bytes(b"partial")
        svc = self.make_service()
        snap = svc.snapshot()
        self.assertEqual(snap["phase"], "failed")
        self.assertEqual(snap["code"], "interrupted")
        self.assertIn("续传", snap["error"])
        self.assertTrue(st.paths("1.7.1").part.is_file(), "中断后 .part 必须保留以便续传")

    def test_cancel_discards_partial_files(self):
        downloader = CancelAwareDownloader(self.zip)
        svc = self.make_service(downloader)
        svc.start_download()
        time.sleep(0.05)
        snap = svc.cancel_download()
        self.assertEqual(snap["phase"], "cancelled")
        self.assertFalse(snap["running"])
        self.assertFalse(snap["ready"])
        self.assertIn("取消", snap["error"])
        paths = svc.store.paths("1.7.1")
        self.assertFalse(paths.root.exists(), "取消后该版本目录与残包必须删除（方案 B）")
        # 取消后重新下载从头开始（没有可续传残包）
        retry = service_mod.UpdateService(self.root, self.data,
                                          fetch_manifest=lambda: self.raw,
                                          public_key=TEST_PUBLIC,
                                          downloader=FakeDownloader(self.zip))
        retry.start_download()
        self.assertTrue(retry.wait_idle(30))
        self.assertEqual(retry.snapshot()["phase"], "ready")

    def test_cancel_without_running_task_is_noop(self):
        svc = self.make_service()
        snap = svc.cancel_download()
        self.assertEqual(snap["phase"], "idle")
        self.assertFalse(snap["running"])

    def test_check_with_manifest_returns_signed_raw(self):
        svc = self.make_service()
        result, data, raw = svc.checker.check_with_manifest()
        self.assertTrue(result["available"])
        self.assertEqual(data["version"], "1.7.1")
        self.assertEqual(raw, self.raw)


class _FakeCtx:
    def __init__(self, update):
        self.services = SimpleNamespace(update=update)
        self.response = None

    def json(self, status, data):
        self.response = (status, data)

    def fail(self, status, message, extra=None):
        body = {"ok": False, "error": message}
        if extra:
            body.update(extra)
        self.response = (status, body)


class ApiDownloadHandlerTest(unittest.TestCase):
    class Stub:
        def __init__(self, result=None, error=None):
            self.result, self.error = result, error

        def start_download(self):
            if self.error:
                raise self.error
            return self.result

    def call(self, stub):
        ctx = _FakeCtx(stub)
        update_api.download(ctx)
        return ctx.response

    def test_success(self):
        status, body = self.call(self.Stub(result={"ok": True, "started": True, "phase": "downloading"}))
        self.assertEqual(status, 200)
        self.assertTrue(body["started"])

    def test_no_update_maps_400(self):
        status, body = self.call(self.Stub(error=service_mod.NoUpdateError(
            "current", "已是最新版本")))
        self.assertEqual(status, 400)
        self.assertEqual(body["code"], "current")

    def test_fetch_error_maps_502(self):
        status, body = self.call(self.Stub(error=fetch.FetchError("no-manifest", "缺清单")))
        self.assertEqual(status, 502)
        self.assertEqual(body["code"], "no-manifest")

    def test_check_error_maps_502(self):
        from paperlib.update import check

        status, body = self.call(self.Stub(error=check.CheckError("signature", "签名失败")))
        self.assertEqual(status, 502)
        self.assertEqual(body["code"], "signature")

    def test_local_version_error_maps_500(self):
        status, body = self.call(self.Stub(error=VersionError("坏 version.json")))
        self.assertEqual(status, 500)
        self.assertIn("version.json", body["error"])


if __name__ == "__main__":
    unittest.main()
