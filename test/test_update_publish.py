"""发布编排脚本（P0-06）的离线测试：资产生成、签名、校验、发布前拦截。"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import ed25519, manifest, signing  # noqa: E402
from paperlib.version import load as load_version  # noqa: E402
from tools import update_publish  # noqa: E402

TOOL = ROOT / "tools" / "update_publish.py"
TEST_SEED = bytes(range(32))
TEST_PUBLIC = ed25519.public_key(TEST_SEED)
# 用仓库当前版本，避免每次升版本都要改测试
CURRENT = load_version(ROOT)
VERSION = CURRENT["version"]
TAG = CURRENT["tag"]
ASSET = "PaperLibrary.%s.Nexus.zip" % TAG


def make_zip(path: Path, version=VERSION, extra=None):
    entries = {"PaperLibrary.exe": b"MZ", "paperlib/app.py": b"x"}
    if version is not None:
        entries["version.json"] = json.dumps({"product": "PaperLibrary", "version": version})
    entries.update(extra or {})
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)
    return path


class PublishUnitTest(unittest.TestCase):
    def test_build_manifest_matches_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / ASSET)
            data = update_publish.build_manifest(
                {"product": "PaperLibrary", "version": "1.6.0", "tag": "v1.6"},
                zip_path, "10.0.17763", published_at="2026-12-01T00:00:00Z")
            self.assertEqual(data["version"], "1.6.0")
            self.assertEqual(data["assets"]["full"]["size"], zip_path.stat().st_size)
            self.assertEqual(data["assets"]["full"]["sha256"], update_publish.sha256_file(zip_path))
            self.assertEqual(data["assets"]["full"]["url"],
                             "https://github.com/PaperLibrary-Developers/PaperLibrary/"
                             "releases/download/v1.6/PaperLibrary.v1.6.Nexus.zip")
            manifest.validate(data, require_signature=False)
            signed = update_publish.sign_manifest(data, TEST_SEED, TEST_PUBLIC)
            signing.verify_manifest(signed, TEST_PUBLIC)

    def test_write_sums(self):
        with tempfile.TemporaryDirectory() as tmp:
            one = Path(tmp) / "a.bin"
            two = Path(tmp) / "b.bin"
            one.write_bytes(b"one")
            two.write_bytes(b"two")
            sums = update_publish.write_sums(Path(tmp), [one, two])
            lines = sums.read_text(encoding="utf-8").strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].endswith("  a.bin"))
            self.assertTrue(lines[1].startswith(update_publish.sha256_file(two)))

    def test_min_windows_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = make_zip(Path(tmp) / "x.zip")
            proc = subprocess.run([sys.executable, str(TOOL), "--zip", str(zip_path),
                                   "--out", tmp, "--no-build", "--skip-tests"],
                                  capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", cwd=str(ROOT), timeout=120,
                                  env=dict(os.environ, PYTHONUTF8="1"))
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("min-windows", proc.stdout + proc.stderr)


class PublishCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.zip = make_zip(self.dir / ASSET)
        self.key = self.dir / "key.json"
        signing.save_private_key(TEST_SEED, self.key)
        self.pubkey = base64.b64encode(TEST_PUBLIC).decode()
        self.out = self.dir / "out"

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        env.pop(signing.KEY_ENV, None)
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(ROOT), env=env, timeout=180)

    def base_args(self):
        return ["--zip", str(self.zip), "--no-build", "--skip-tests", "--out", str(self.out),
                "--key", str(self.key), "--pubkey", self.pubkey,
                "--min-windows", "10.0.17763"]

    def test_dry_run_produces_all_assets(self):
        proc = self.run_tool(*self.base_args())
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue((self.out / ASSET).is_file())
        self.assertTrue((self.out / "update-manifest.json").is_file())
        self.assertTrue((self.out / "SHA256SUMS").is_file())
        record = json.loads((self.out / ("release-%s.json" % TAG)).read_text(encoding="utf-8"))
        self.assertEqual(record["publish"], False)
        self.assertEqual(record["size"], self.zip.stat().st_size)
        self.assertIn("发布校验通过", proc.stdout)
        data = manifest.load_file(self.out / "update-manifest.json")
        signing.verify_manifest(data, TEST_PUBLIC)

    def test_rejects_zip_with_data_directory(self):
        bad = make_zip(self.dir / "bad.zip", extra={"data/library.json": b"{}"})
        args = self.base_args()
        args[1] = str(bad)
        proc = self.run_tool(*args)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("data/", proc.stdout + proc.stderr)

    def test_rejects_version_mismatch(self):
        bad = make_zip(self.dir / "old.zip", version="1.5.0")
        args = self.base_args()
        args[1] = str(bad)
        proc = self.run_tool(*args)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("version", proc.stdout + proc.stderr)

    def test_publish_requires_yes_and_refuses_existing_tag(self):
        args = self.base_args() + ["--publish"]
        proc = self.run_tool(*args)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("--yes", proc.stdout + proc.stderr)
        args = self.base_args() + ["--publish", "--yes"]
        proc = self.run_tool(*args)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("tag", proc.stdout + proc.stderr)

    def test_wrong_expected_pubkey_is_rejected(self):
        args = self.base_args()
        idx = args.index("--pubkey") + 1
        args[idx] = base64.b64encode(bytes(32)).decode()
        proc = self.run_tool(*args)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("不匹配", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
