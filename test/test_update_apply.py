"""P2-07 POST /api/update/apply：staging 校验、apply.json 写入、幂等与拒绝。"""
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
from paperlib.update import service as service_mod, store  # noqa: E402
from paperlib.version import VersionError, ui_label  # noqa: E402

TEST_SEED = bytes(range(32))
TEST_PUBLIC = __import__("paperlib.update.ed25519", fromlist=["public_key"]).public_key(TEST_SEED)
APPLY_KEYS = {"schema", "version", "tag", "fromVersion", "fromTag", "stagingPath",
              "stagingRel", "backupPath", "backupRel", "packageSha256", "manifestSha256",
              "files", "bytes", "createdAt"}


def write_version(root: Path, version: str):
    (root / "version.json").write_text(
        json.dumps({"product": "PaperLibrary", "version": version, "tag": ui_label(version)}),
        encoding="utf-8")


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
    def __init__(self, zip_bytes, gate=None):
        self.zip_bytes = zip_bytes
        self.gate = gate

    def __call__(self, store_obj, version, asset, **kwargs):
        if self.gate is not None:
            self.gate.wait(10)
        paths = store_obj.ensure(version)
        paths.part.write_bytes(self.zip_bytes)
        store_obj.update_state(version, phase="downloading",
                               downloadedBytes=len(self.zip_bytes),
                               totalBytes=len(self.zip_bytes))
        return {}


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "repo"
        self.root.mkdir(parents=True, exist_ok=True)
        write_version(self.root, "1.6.0")
        self.data = Path(self.tmp.name) / "external-data"
        self.zip = make_zip("1.7.1")
        self.raw = signed_manifest("1.7.1", self.zip)
        self.service = service_mod.UpdateService(
            self.root, self.data, fetch_manifest=lambda: self.raw, public_key=TEST_PUBLIC,
            downloader=FakeDownloader(self.zip))

    def tearDown(self):
        self.tmp.cleanup()

    def stage(self):
        self.service.start_download()
        self.assertTrue(self.service.wait_idle(30))
        self.assertEqual(self.service.snapshot()["phase"], "ready")

    def test_apply_writes_marker(self):
        self.stage()
        result = self.service.apply()
        self.assertTrue(result["applied"])
        self.assertFalse(result["idempotent"])
        payload = result["apply"]
        self.assertTrue(APPLY_KEYS.issubset(set(payload)), APPLY_KEYS - set(payload))
        self.assertEqual(payload["version"], "1.7.1")
        self.assertEqual(payload["tag"], "v1.7.1")
        self.assertEqual(payload["fromVersion"], "1.6.0")
        self.assertIn("updates", payload["stagingRel"].replace("\\", "/"))
        self.assertTrue(payload["stagingPath"].endswith("staging"))
        self.assertTrue(payload["backupPath"].endswith("app-1.6.0"))
        self.assertEqual(payload["packageSha256"],
                         hashlib.sha256(self.zip).hexdigest())
        self.assertTrue(payload["files"] > 0)
        apply_path = self.data / "updates" / "apply.json"
        self.assertTrue(apply_path.is_file())
        on_disk = json.loads(apply_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, payload)
        # apply 不应启动下载/修改 staging
        self.assertFalse(self.service.snapshot()["running"])
        self.assertTrue((self.data / "updates" / "1.7.1" / "staging" /
                         "PaperLibrary.exe").is_file())

    def test_repeat_apply_is_idempotent(self):
        self.stage()
        first = self.service.apply()
        path = self.data / "updates" / "apply.json"
        first_bytes = path.read_bytes()
        second = self.service.apply()
        self.assertTrue(second["idempotent"])
        self.assertEqual(second["apply"], first["apply"])
        self.assertEqual(path.read_bytes(), first_bytes, "重复 apply 不应改写标记")

    def test_apply_without_staging_rejected(self):
        with self.assertRaises(service_mod.UpdateServiceError) as ctx:
            self.service.apply()
        self.assertEqual(ctx.exception.code, "staging-missing")
        self.assertEqual(ctx.exception.status, 409)
        self.assertFalse((self.data / "updates" / "apply.json").exists())

    def test_incomplete_staging_rejected(self):
        self.stage()
        (self.data / "updates" / "1.7.1" / "staging" / "PaperLibrary.exe").unlink()
        with self.assertRaises(service_mod.UpdateServiceError) as ctx:
            self.service.apply()
        self.assertEqual(ctx.exception.code, "staging-incomplete")
        self.assertFalse((self.data / "updates" / "apply.json").exists())

    def test_staging_version_mismatch_rejected(self):
        self.stage()
        write_version(self.data / "updates" / "1.7.1" / "staging", "1.6.0")
        with self.assertRaises(service_mod.UpdateServiceError) as ctx:
            self.service.apply()
        self.assertEqual(ctx.exception.code, "version-mismatch")
        self.assertFalse((self.data / "updates" / "apply.json").exists())

    def test_same_version_rejected(self):
        self.stage()
        write_version(self.root, "1.7.1")
        with self.assertRaises(service_mod.UpdateServiceError) as ctx:
            self.service.apply()
        self.assertEqual(ctx.exception.code, "version-same")
        self.assertFalse((self.data / "updates" / "apply.json").exists())

    def test_downgrade_rejected(self):
        self.stage()
        write_version(self.root, "1.9.0")
        with self.assertRaises(service_mod.UpdateServiceError) as ctx:
            self.service.apply()
        self.assertEqual(ctx.exception.code, "downgrade")

    def test_apply_while_downloading_is_busy(self):
        gate = threading.Event()
        svc = service_mod.UpdateService(
            self.root, self.data, fetch_manifest=lambda: self.raw, public_key=TEST_PUBLIC,
            downloader=FakeDownloader(self.zip, gate=gate))
        svc.start_download()
        try:
            with self.assertRaises(service_mod.UpdateServiceError) as ctx:
                svc.apply()
            self.assertEqual(ctx.exception.code, "busy")
        finally:
            gate.set()
            self.assertTrue(svc.wait_idle(30))

    def test_new_download_clears_old_apply_marker(self):
        import shutil

        self.stage()
        self.service.apply()
        apply_path = self.data / "updates" / "apply.json"
        self.assertTrue(apply_path.is_file())
        # 模拟下一次下载：staging 不存在时不再走 ready 早返回
        shutil.rmtree(self.data / "updates" / "1.7.1" / "staging")
        self.service.store.clear_ready("1.7.1")
        self.service._ready = {}
        self.service.start_download()
        self.assertTrue(self.service.wait_idle(30))
        self.assertFalse(apply_path.exists(), "开始新下载必须清掉旧 apply.json，避免启动器装过期包")

    def test_store_apply_roundtrip_and_clear(self):
        st = store.UpdateStore(self.data)
        st.write_apply({"version": "1.7.1", "fromVersion": "1.6.0"})
        self.assertEqual(st.read_apply()["version"], "1.7.1")
        self.assertTrue(st.apply_path.is_file())
        st.clear_apply()
        self.assertEqual(st.read_apply(), {})


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


class ApiApplyHandlerTest(unittest.TestCase):
    class Stub:
        def __init__(self, result=None, error=None):
            self.result, self.error = result, error

        def apply(self):
            if self.error:
                raise self.error
            return self.result

    def call(self, stub):
        ctx = _FakeCtx(stub)
        update_api.apply(ctx)
        return ctx.response

    def test_success(self):
        status, body = self.call(self.Stub(result={"ok": True, "applied": True,
                                                   "idempotent": False, "apply": {}}))
        self.assertEqual(status, 200)
        self.assertTrue(body["applied"])

    def test_staging_error_maps_409(self):
        status, body = self.call(self.Stub(error=service_mod.UpdateServiceError(
            "staging-incomplete", "缺少文件", 409)))
        self.assertEqual(status, 409)
        self.assertEqual(body["code"], "staging-incomplete")

    def test_local_version_error_maps_500(self):
        status, body = self.call(self.Stub(error=VersionError("坏 version.json")))
        self.assertEqual(status, 500)
        self.assertIn("version.json", body["error"])


if __name__ == "__main__":
    unittest.main()
