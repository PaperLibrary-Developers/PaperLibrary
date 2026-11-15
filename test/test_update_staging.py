"""P2-04 解压与 staging 安全校验测试（恶意 zip / 缺文件 / 版本不符 / zip bomb）。"""
from __future__ import annotations

import hashlib
import io
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import manifest as manifest_mod  # noqa: E402
from paperlib.update import release, signing, staging, store  # noqa: E402
from paperlib.version import ui_label  # noqa: E402

TEST_SEED = bytes(range(32))
TEST_PUBLIC = __import__("paperlib.update.ed25519", fromlist=["public_key"]).public_key(TEST_SEED)


def make_zip(entries) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in entries:
            if isinstance(content, tuple) and content and content[0] == "symlink":
                info = zipfile.ZipInfo(name)
                info.external_attr = (0o120777 << 16)
                zf.writestr(info, content[1])
            elif isinstance(content, tuple) and content and content[0] == "big":
                zf.writestr(name, b"\x00" * content[1])
            else:
                zf.writestr(name, content)
    return buffer.getvalue()


def good_entries(version="1.7.1"):
    return [
        ("PaperLibrary.exe", b"MZ fake launcher"),
        ("version.json", json.dumps({"product": "PaperLibrary", "version": version})),
        ("paperlib/app.py", b"print('hello')\n"),
        ("public/index.html", b"<html></html>"),
    ]


def build_manifest(version, size, sha):
    tag = ui_label(version)
    data = {
        "schema": 1, "product": "PaperLibrary", "channel": "stable",
        "version": version, "tag": tag,
        "publishedAt": "2026-11-15T13:52:55Z",
        "releaseNotesUrl": "https://github.com/%s/releases/tag/%s" % (release.REPO, tag),
        "minWindows": "10.0.17763", "arch": "x64",
        "assets": {"full": {"url": release.release_asset_url(tag), "size": size, "sha256": sha}},
    }
    return manifest_mod.canonical_bytes(signing.sign_manifest(data, TEST_SEED))


class StagingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.UpdateStore(Path(self.tmp.name) / "external-data")
        self.store.prepare("1.7.1")

    def tearDown(self):
        self.tmp.cleanup()

    def prepare_package(self, zip_bytes, version="1.7.1"):
        self.store.write_manifest(version, build_manifest(version, len(zip_bytes),
                                                          hashlib.sha256(zip_bytes).hexdigest()))
        self.store.paths(version).package.write_bytes(zip_bytes)
        self.store.update_state(version, phase="verified", totalBytes=len(zip_bytes),
                                sha256=hashlib.sha256(zip_bytes).hexdigest())

    def extract(self, **kwargs):
        return staging.extract_package(self.store, "1.7.1", public_key=TEST_PUBLIC, **kwargs)

    def test_success_extracts_and_writes_ready(self):
        zip_bytes = make_zip(good_entries())
        self.prepare_package(zip_bytes)
        ready = self.extract()
        paths = self.store.paths("1.7.1")
        self.assertTrue((paths.staging / "PaperLibrary.exe").is_file())
        self.assertTrue((paths.staging / "paperlib" / "app.py").is_file())
        self.assertEqual(ready["version"], "1.7.1")
        self.assertEqual(ready["files"], len(good_entries()))
        self.assertTrue(ready["stagingPath"])
        self.assertEqual(ready["packageSha256"], hashlib.sha256(zip_bytes).hexdigest())
        self.assertEqual(self.store.read_state("1.7.1")["phase"], "ready")
        self.assertTrue(paths.ready.is_file())

    def test_retry_clears_old_staging_and_ready(self):
        zip_bytes = make_zip(good_entries())
        self.prepare_package(zip_bytes)
        self.extract()
        stale = self.store.paths("1.7.1").staging / "stale.txt"
        stale.write_text("old", encoding="utf-8")
        self.extract()
        self.assertFalse(stale.exists(), "重试必须从干净 staging 开始")

    def test_traversal_entries_rejected(self):
        cases = ["../evil.txt", "a/../../evil.txt", "..\\evil.txt", "/abs.txt",
                 "C:/evil.txt", "C:\\evil.txt"]
        for name in cases:
            with self.subTest(name=name):
                self.store.prepare("1.7.1")
                entries = good_entries() + [(name, b"x")]
                self.prepare_package(make_zip(entries))
                with self.assertRaises(staging.StagingError) as ctx:
                    self.extract()
                self.assertEqual(ctx.exception.code, "unsafe-path")
                paths = self.store.paths("1.7.1")
                self.assertFalse(paths.staging.exists())
                self.assertFalse(paths.ready.exists())
                self.assertEqual(self.store.read_state("1.7.1")["phase"], "failed")

    def test_symlink_rejected(self):
        entries = good_entries() + [("link.txt", ("symlink", "C:/Windows/win.ini"))]
        self.prepare_package(make_zip(entries))
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "symlink")
        self.assertFalse(self.store.paths("1.7.1").staging.exists())

    def test_zip_bomb_ratio_rejected(self):
        entries = good_entries() + [("big.bin", ("big", 25 * 1024 * 1024))]
        zip_bytes = make_zip(entries)
        self.prepare_package(zip_bytes)
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "zip-bomb")
        self.assertFalse(self.store.paths("1.7.1").staging.exists())

    def test_too_many_files_rejected(self):
        entries = good_entries() + [("extra_%d.txt" % i, b"x") for i in range(5)]
        self.prepare_package(make_zip(entries))
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract(limits=staging.ZipLimits(max_files=3))
        self.assertEqual(ctx.exception.code, "too-many-files")

    def test_too_large_file_rejected(self):
        entries = good_entries() + [("huge.bin", b"x" * 4096)]
        self.prepare_package(make_zip(entries))
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract(limits=staging.ZipLimits(max_file_bytes=1024))
        self.assertEqual(ctx.exception.code, "too-large")

    def test_top_level_data_rejected(self):
        entries = good_entries() + [("data/library.db", b"user data")]
        self.prepare_package(make_zip(entries))
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "data-directory")

    def test_missing_exe_rejected(self):
        entries = [e for e in good_entries() if e[0] != "PaperLibrary.exe"]
        self.prepare_package(make_zip(entries))
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "missing-exe")

    def test_missing_version_file_rejected(self):
        entries = [e for e in good_entries() if e[0] != "version.json"]
        self.prepare_package(make_zip(entries))
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "missing-version")

    def test_version_mismatch_rejected(self):
        entries = good_entries(version="1.6.0")
        self.prepare_package(make_zip(entries))
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "version-mismatch")
        self.assertFalse(self.store.paths("1.7.1").staging.exists())

    def test_invalid_zip_rejected(self):
        self.prepare_package(b"not a zip")
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "invalid-zip")
        self.assertFalse(self.store.paths("1.7.1").staging.exists())

    def test_failure_after_success_clears_ready(self):
        zip_bytes = make_zip(good_entries())
        self.prepare_package(zip_bytes)
        self.extract()
        paths = self.store.paths("1.7.1")
        self.assertTrue(paths.ready.exists())
        # 让 package 与清单不一致，再次解压必须失败并清理旧 ready/staging
        paths.package.write_bytes(b"x" * 10)
        with self.assertRaises(staging.StagingError) as ctx:
            self.extract()
        self.assertEqual(ctx.exception.code, "size-mismatch")
        self.assertFalse(paths.staging.exists())
        self.assertFalse(paths.ready.exists())
        self.assertEqual(self.store.read_state("1.7.1")["phase"], "failed")

    def test_inspect_entries_reports_stats(self):
        zip_bytes = make_zip(good_entries())
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            stats = staging.inspect_entries(zf.infolist())
        self.assertEqual(stats["files"], len(good_entries()))
        self.assertGreater(stats["totalBytes"], 0)


if __name__ == "__main__":
    unittest.main()
