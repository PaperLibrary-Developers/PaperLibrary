"""更新清单 schema=1 的校验与规范化 JSON 测试（阶段 0 / P0-02）。"""
from __future__ import annotations

import base64
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import manifest  # noqa: E402

EXAMPLE = ROOT / "shared" / "fixtures" / "update-manifest.example.json"


def example():
    return manifest.load_file(EXAMPLE)


class CanonicalJsonTest(unittest.TestCase):
    def test_example_is_valid(self):
        data = example()
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["product"], "PaperLibrary")
        self.assertEqual(data["channel"], "stable")
        self.assertEqual(data["version"], "1.6.0")
        self.assertEqual(data["tag"], "v1.6")
        self.assertEqual(data["arch"], "x64")
        self.assertEqual(data["assets"]["full"]["size"], 45625893)
        self.assertEqual(len(base64.b64decode(data["signature"])), 64)

    def test_canonical_bytes_reproducible(self):
        data = example()
        first = manifest.canonical_bytes(data)
        second = manifest.canonical_bytes(example())
        self.assertEqual(first, second)
        self.assertEqual(manifest.load_bytes(first), data)
        self.assertNotIn(b" ", first)
        self.assertNotIn(b"\n", first)
        self.assertTrue(first.endswith(b'}'))

    def test_field_order_is_frozen(self):
        text = manifest.canonical_bytes(example()).decode("utf-8")
        order = [text.index('"%s"' % key) for key in manifest.FIELDS]
        self.assertEqual(order, sorted(order), "顶层字段顺序必须与 FIELDS 一致")
        self.assertIn('"assets":{"full":{"url":', text)
        self.assertTrue(text.endswith('"signature":"' + example()["signature"] + '"}'))

    def test_payload_excludes_signature_and_covers_fields(self):
        data = example()
        payload = manifest.canonical_payload(data)
        self.assertNotIn(b"signature", payload)
        self.assertIn(b'"version":"1.6.0"', payload)
        changed = copy.deepcopy(data)
        changed["assets"]["full"]["sha256"] = "0" * 64
        self.assertNotEqual(manifest.canonical_payload(changed), payload)

    def test_input_key_order_does_not_change_canonical_bytes(self):
        data = example()
        shuffled = dict(reversed(list(data.items())))
        self.assertEqual(manifest.canonical_bytes(shuffled), manifest.canonical_bytes(data))


class RejectTest(unittest.TestCase):
    def assert_rejected(self, mutate, needle=""):
        data = example()
        data = mutate(data) or data
        with self.assertRaises(manifest.ManifestError) as ctx:
            manifest.validate(data)
        if needle:
            self.assertIn(needle, str(ctx.exception))

    def test_missing_each_field(self):
        for key in manifest.FIELDS:
            def mutate(data, key=key):
                data.pop(key, None)
            self.assert_rejected(mutate, "缺少字段")

    def test_unknown_top_level_field(self):
        self.assert_rejected(lambda d: d.update({"extra": 1}) or d, "未知字段")

    def test_schema_product_channel_arch(self):
        self.assert_rejected(lambda d: d.update({"schema": 2}) or d, "schema")
        self.assert_rejected(lambda d: d.update({"schema": "1"}) or d, "schema")
        self.assert_rejected(lambda d: d.update({"schema": True}) or d, "schema")
        self.assert_rejected(lambda d: d.update({"product": "Other"}) or d, "product")
        self.assert_rejected(lambda d: d.update({"channel": "beta"}) or d, "channel")
        self.assert_rejected(lambda d: d.update({"arch": "arm64"}) or d, "arch")

    def test_version_and_tag(self):
        self.assert_rejected(lambda d: d.update({"version": "1.7"}) or d, "version")
        self.assert_rejected(lambda d: d.update({"version": "1.7.0"}) or d, "tag")

    def test_published_at_and_min_windows(self):
        self.assert_rejected(lambda d: d.update({"publishedAt": "2026-11-15 00:00:00"}) or d,
                             "publishedAt")
        self.assert_rejected(lambda d: d.update({"minWindows": "10"}) or d, "minWindows")
        self.assert_rejected(lambda d: d.update({"minWindows": 10}) or d, "minWindows")

    def test_urls(self):
        def set_urls(base, full, notes=None):
            def mutate(data):
                data["assets"]["full"]["url"] = base
                data["releaseNotesUrl"] = notes or full
                return data
            return mutate
        self.assert_rejected(set_urls("http://github.com/a/b/releases/download/v1.6/x.zip",
                                      "https://github.com/a/b/releases/tag/v1.6"), "https")
        self.assert_rejected(set_urls("https://evil.example/a/b/releases/download/v1.6/x.zip",
                                      "https://github.com/a/b/releases/tag/v1.6"), "允许列表")
        self.assert_rejected(set_urls("https://github.com/a/b/raw/v1.6/x.zip",
                                      "https://github.com/a/b/releases/tag/v1.6"), "releases")
        self.assert_rejected(set_urls(
            "https://user:pass@github.com/a/b/releases/download/v1.6/x.zip",
            "https://github.com/a/b/releases/tag/v1.6"), "用户名")

    def test_assets(self):
        self.assert_rejected(lambda d: d.update({"assets": {}}) or d, "full")
        def extra_asset(data):
            data["assets"]["extra"] = {}
            return data
        self.assert_rejected(extra_asset, "未知字段")
        def drop_size(data):
            data["assets"]["full"].pop("size")
            return data
        self.assert_rejected(drop_size, "size")
        for bad in (0, -1, "12", 1.5, True):
            self.assert_rejected(
                lambda d, bad=bad: d["assets"]["full"].update({"size": bad}) or d, "size")

    def test_sha256(self):
        for bad in ("A" * 64, "0" * 63, "0" * 65, "zz" * 32, ""):
            self.assert_rejected(
                lambda d, bad=bad: d["assets"]["full"].update({"sha256": bad}) or d, "sha256")

    def test_signature(self):
        self.assert_rejected(lambda d: d.update({"signature": "not base64!!"}) or d, "base64")
        self.assert_rejected(
            lambda d: d.update({"signature": base64.b64encode(b"\x00" * 32).decode()}) or d,
            "64 字节")

    def test_oversize_and_bad_json(self):
        with self.assertRaises(manifest.ManifestError) as ctx:
            manifest.load_bytes(b" " * (manifest.MAX_BYTES + 1))
        self.assertIn("256", str(ctx.exception))
        with self.assertRaises(manifest.ManifestError):
            manifest.load_bytes(b"{not json")
        with self.assertRaises(manifest.ManifestError):
            manifest.load_bytes(b"\xff\xfe\x00")
        with self.assertRaises(manifest.ManifestError):
            manifest.load_bytes(b"[]")

    def test_missing_file(self):
        with self.assertRaises(manifest.ManifestError) as ctx:
            manifest.load_file(ROOT / "shared" / "fixtures" / "no-such-manifest.json")
        self.assertIn("找不到", str(ctx.exception))

    def test_example_is_the_canonical_artifact(self):
        raw = EXAMPLE.read_bytes()
        self.assertEqual(raw, manifest.canonical_bytes(manifest.load_bytes(raw)))
        self.assertNotIn(b"\n", raw)
        self.assertLessEqual(len(raw), manifest.MAX_BYTES)

    def test_published_manifest_parses_as_plain_json(self):
        data = json.loads(EXAMPLE.read_text(encoding="utf-8"))
        self.assertEqual(set(data), set(manifest.FIELDS))


if __name__ == "__main__":
    unittest.main()
