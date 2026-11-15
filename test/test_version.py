"""产品版本唯一来源测试：version.json 的解析/校验 + /api/version + 一致性检查。"""
from __future__ import annotations

import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PAPERLIB_SKIP_MIGRATE"] = "1"
os.environ["PYTHONUTF8"] = "1"

from paperlib import version as version_mod  # noqa: E402
from paperlib.app import build  # noqa: E402
from paperlib.config import Config  # noqa: E402
from paperlib.http.server import HttpServer  # noqa: E402
from tools import version_check  # noqa: E402


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class VersionModuleTest(unittest.TestCase):
    def test_repo_version_file_is_valid(self):
        info = version_mod.load(ROOT)
        self.assertEqual(info["product"], "PaperLibrary")
        version_mod.semver(info["version"])
        self.assertEqual(info["tag"], version_mod.ui_label(info["version"]))

    def test_ui_label_tag_rule(self):
        # patch=0 用短标签（兼容既有 v1.6）；patch>0 用完整 vX.Y.Z，避免同一 tag 复用于多个补丁版本
        self.assertEqual(version_mod.ui_label("1.6.0"), "v1.6")
        self.assertEqual(version_mod.ui_label("12.34.0"), "v12.34")
        self.assertEqual(version_mod.ui_label("1.7.1"), "v1.7.1")
        self.assertEqual(version_mod.ui_label("12.34.5"), "v12.34.5")

    def test_rejects_bad_records(self):
        cases = [
            ({"product": "Other", "version": "1.6.0", "tag": "v1.6"}, "product"),
            ({"product": "PaperLibrary", "version": "1.6", "tag": "v1.6"}, "x.y.z"),
            ({"product": "PaperLibrary", "version": "1.6.0", "tag": "v1.7"}, "tag"),
            ({"product": "PaperLibrary", "version": "1.7.1", "tag": "v1.7"}, "tag"),
            ({"product": "PaperLibrary", "version": "1.7.0", "tag": "v1.7.0"}, "tag"),
            ({"product": "PaperLibrary", "version": "1.6.0"}, "缺少字段"),
            ({"product": "PaperLibrary", "version": "1.6.0", "tag": "v1.6", "builtAt": "2026/11/15"},
             "builtAt"),
            ({"product": "PaperLibrary", "version": "1.6.0", "tag": "v1.6", "commit": "XYZ"},
             "commit"),
            ("not-an-object", "对象"),
        ]
        for data, needle in cases:
            with self.assertRaises(version_mod.VersionError) as ctx:
                version_mod.validate(data)
            self.assertIn(needle, str(ctx.exception), data)

    def test_release_package_requires_build_fields(self):
        with self.assertRaises(version_mod.VersionError):
            version_mod.validate({"product": "PaperLibrary", "version": "1.6.0", "tag": "v1.6"},
                                 require_build=True)

    def test_build_record_and_dumps_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "version.json").write_text(
                json.dumps({"product": "PaperLibrary", "version": "2.3.4", "tag": "v2.3.4"}),
                encoding="utf-8")
            record = version_mod.build_record(root, built_at="2026-12-01T00:00:00Z", commit="unknown")
            version_mod.validate(record, require_build=True)
            text = version_mod.dumps(record)
            self.assertTrue(text.endswith("\n"))
            self.assertEqual(list(json.loads(text).keys()),
                             ["product", "version", "tag", "builtAt", "commit"])
            (root / "version.json").write_text(text, encoding="utf-8")
            self.assertEqual(version_mod.load(root, require_build=True), record)

    def test_missing_version_file_is_explicit_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(version_mod.VersionError) as ctx:
                version_mod.load(tmp)
            self.assertIn("version.json", str(ctx.exception))


class VersionEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = Path(tempfile.mkdtemp(prefix="pl-version-"))
        (cls.data_dir / ".seeded").write_text("{}", encoding="utf-8")
        cls.config = Config(root=ROOT, data_dir=cls.data_dir, host="127.0.0.1",
                            port=free_port(), no_open=True)
        cls.config.started_at = time.time()
        cls.services, cls.router, cls.static, cls.store = build(cls.config)
        cls.server = HttpServer(cls.router, cls.static, "127.0.0.1", cls.config.port, verbose=False)
        cls.server.start_background()
        cls.base = "http://127.0.0.1:%d" % cls.config.port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.close()
        cls.store.repo.close()
        import shutil
        shutil.rmtree(cls.data_dir, ignore_errors=True)

    def get_json(self, path):
        with urllib.request.urlopen(self.base + path, timeout=10) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))

    def test_api_version_matches_version_json(self):
        status, data = self.get_json("/api/version")
        self.assertEqual(status, 200)
        info = version_mod.load(ROOT)
        self.assertEqual(data["ok"], True)
        self.assertEqual(data["product"], info["product"])
        self.assertEqual(data["version"], info["version"])
        self.assertEqual(data["tag"], info["tag"])
        self.assertEqual(data["ui"], info["tag"])
        self.assertEqual(data["builtAt"], info["builtAt"])
        self.assertEqual(data["commit"], info["commit"])


class VersionCheckTest(unittest.TestCase):
    def test_repo_passes_consistency_check(self):
        problems, info = version_check.run(ROOT)
        self.assertEqual(problems, [])
        self.assertEqual(info["tag"], version_mod.ui_label(info["version"]))

    def test_detects_hardcoded_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "version.json").write_text(
                json.dumps({"product": "PaperLibrary", "version": "1.7.0", "tag": "v1.7"}),
                encoding="utf-8")
            (root / "README.md").write_text("# PaperLibrary v1.7 Nexus\n", encoding="utf-8")
            problems, info = version_check.run(root)
            self.assertTrue(any("README.md" in item and "v1.7" in item for item in problems),
                            problems)

    def test_package_requires_build_fields_and_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            package = Path(tmp) / "pkg"
            root.mkdir()
            package.mkdir()
            (root / "version.json").write_text(
                json.dumps({"product": "PaperLibrary", "version": "1.6.0", "tag": "v1.6"}),
                encoding="utf-8")
            (package / "version.json").write_text(
                json.dumps({"product": "PaperLibrary", "version": "1.6.0", "tag": "v1.6"}),
                encoding="utf-8")
            problems, _info = version_check.run(root, package=package)
            self.assertTrue(any("builtAt" in item for item in problems), problems)
            self.assertTrue(any("release-manifest.json" in item for item in problems), problems)


if __name__ == "__main__":
    unittest.main()
