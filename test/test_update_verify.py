"""P2-03 下载后校验测试：大小、SHA-256、清单签名与失败清理。"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import manifest as manifest_mod  # noqa: E402
from paperlib.update import release, signing, store, verify  # noqa: E402
from paperlib.version import ui_label  # noqa: E402

TEST_SEED = bytes(range(32))
OTHER_SEED = bytes(range(1, 33))
TEST_PUBLIC = __import__("paperlib.update.ed25519", fromlist=["public_key"]).public_key(TEST_SEED)


def build_manifest(version="1.7.1", size=0, sha="0" * 64, seed=TEST_SEED, tag=None):
    tag = tag or ui_label(version)
    data = {
        "schema": 1,
        "product": "PaperLibrary",
        "channel": "stable",
        "version": version,
        "tag": tag,
        "publishedAt": "2026-11-15T13:52:55Z",
        "releaseNotesUrl": "https://github.com/%s/releases/tag/%s" % (release.REPO, tag),
        "minWindows": "10.0.17763",
        "arch": "x64",
        "assets": {"full": {
            "url": release.release_asset_url(tag),
            "size": size,
            "sha256": sha,
        }},
    }
    return manifest_mod.canonical_bytes(signing.sign_manifest(data, seed))


class VerifyTest(unittest.TestCase):
    BODY = bytes(range(256)) * (8 * 1024)  # 2 MB

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = store.UpdateStore(Path(self.tmp.name) / "external-data")
        self.store.prepare("1.7.1")
        self.size = len(self.BODY)
        self.sha = hashlib.sha256(self.BODY).hexdigest()

    def tearDown(self):
        self.tmp.cleanup()

    def write_manifest(self, **kwargs):
        raw = build_manifest(size=self.size, sha=self.sha, **kwargs)
        self.store.write_manifest("1.7.1", raw)
        return raw

    def write_part(self, body=None):
        self.store.paths("1.7.1").part.write_bytes(self.BODY if body is None else body)

    def assert_cleaned(self):
        paths = self.store.paths("1.7.1")
        self.assertFalse(paths.part.exists(), "失败后必须删除 .part")
        self.assertFalse(paths.package.exists(), "失败后必须删除 package.zip")

    def test_success_renames_part(self):
        self.write_manifest()
        self.write_part()
        info = verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        paths = self.store.paths("1.7.1")
        self.assertFalse(paths.part.exists())
        self.assertTrue(paths.package.is_file())
        self.assertEqual(info["size"], self.size)
        self.assertEqual(info["sha256"], self.sha)
        state = self.store.read_state("1.7.1")
        self.assertEqual(state["phase"], "verified")
        self.assertEqual(state["sha256"], self.sha)
        self.assertEqual(state["downloadedBytes"], self.size)

    def test_size_mismatch_cleans_up(self):
        self.write_manifest()
        self.write_part(self.BODY[:-1024])
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "size-mismatch")
        self.assert_cleaned()
        self.assertEqual(self.store.read_state("1.7.1")["phase"], "failed")
        self.assertEqual(self.store.read_state("1.7.1")["code"], "size-mismatch")

    def test_sha256_mismatch_cleans_up(self):
        self.write_manifest()
        tampered = bytearray(self.BODY)
        tampered[100] ^= 0xFF
        self.write_part(bytes(tampered))
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "sha256-mismatch")
        self.assert_cleaned()
        self.assertEqual(self.store.read_state("1.7.1")["code"], "sha256-mismatch")

    def test_signature_failure_cleans_up(self):
        self.write_manifest(seed=OTHER_SEED)
        self.write_part()
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "signature")
        self.assert_cleaned()
        self.assertEqual(self.store.read_state("1.7.1")["code"], "signature")

    def test_invalid_manifest_cleans_up(self):
        self.store.write_manifest("1.7.1", b"{not json")
        self.write_part()
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "invalid-manifest")
        self.assert_cleaned()

    def test_missing_manifest(self):
        self.write_part()
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "manifest-missing")
        self.assert_cleaned()

    def test_version_mismatch(self):
        self.write_manifest(version="1.7.0")
        self.write_part()
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "version-mismatch")
        self.assert_cleaned()

    def test_content_length_mismatch(self):
        self.write_manifest()
        self.write_part()
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC,
                                   content_length=self.size + 1)
        self.assertEqual(ctx.exception.code, "size-mismatch")
        self.assert_cleaned()

    def test_state_total_size_mismatch(self):
        self.write_manifest()
        self.write_part()
        self.store.update_state("1.7.1", totalBytes=self.size + 1)
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "size-mismatch")
        self.assert_cleaned()

    def test_missing_part(self):
        self.write_manifest()
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "missing-package")
        self.assert_cleaned()

    def test_sha256_file_matches_hashlib(self):
        path = Path(self.tmp.name) / "x.bin"
        path.write_bytes(self.BODY)
        self.assertEqual(verify.sha256_file(path), hashlib.sha256(self.BODY).hexdigest())

    def test_manifest_bytes_are_validated_not_trusted(self):
        # 篡改清单里的 sha256 但不重新签名 → 验签失败
        raw = json.loads(build_manifest(size=self.size, sha=self.sha).decode("utf-8"))
        raw["assets"]["full"]["sha256"] = "0" * 64
        self.store.write_manifest("1.7.1", json.dumps(raw).encode("utf-8"))
        self.write_part()
        with self.assertRaises(verify.VerifyError) as ctx:
            verify.verify_download(self.store, "1.7.1", public_key=TEST_PUBLIC)
        self.assertEqual(ctx.exception.code, "signature")


if __name__ == "__main__":
    unittest.main()
