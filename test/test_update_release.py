"""Release 资产命名/校验与 verify_release 离线路径测试（阶段 0 / P0-04）。"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import ed25519, manifest, release, signing  # noqa: E402

TOOL = ROOT / "tools" / "verify_release.py"
TEST_SEED = bytes(range(32))
TEST_PUBLIC = ed25519.public_key(TEST_SEED)


def make_zip(path: Path, entries: dict, version="1.6.0"):
    payload = dict(entries)
    if version is not None:
        payload.setdefault("version.json", json.dumps({"product": "PaperLibrary", "version": version}))
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in payload.items():
            zf.writestr(name, content)
    return path


class ReleaseManifestTest(unittest.TestCase):
    def test_manifest_target_never_contains_local_path(self):
        from tools import release as release_tool

        self.assertEqual(release_tool.relative_target("C:/repo", "C:/repo/dist/PK"),
                         "dist/PK")
        self.assertEqual(release_tool.relative_target("/repo", "/repo/dist/PK"), "dist/PK")
        # 仓库外的路径只保留末级目录名，绝不回退成绝对路径
        self.assertEqual(release_tool.relative_target("/repo", "D:/other/PK"), "PK")


class NamingTest(unittest.TestCase):
    def test_asset_name(self):
        self.assertEqual(release.asset_name("v1.6"), "PaperLibrary.v1.6.Nexus.zip")
        self.assertEqual(release.asset_name("v12.34"), "PaperLibrary.v12.34.Nexus.zip")
        for bad in ("1.6", "v1", "v1.6.0", "", None, "v1.6/x"):
            with self.assertRaises(ValueError):
                release.asset_name(bad)

    def test_urls(self):
        self.assertEqual(
            release.release_asset_url("v1.6"),
            "https://github.com/PaperLibrary-Developers/PaperLibrary/releases/download/v1.6/"
            "PaperLibrary.v1.6.Nexus.zip")
        self.assertEqual(
            release.stable_manifest_url(),
            "https://github.com/PaperLibrary-Developers/PaperLibrary/releases/latest/download/"
            "update-manifest.json")
        self.assertEqual(
            release.stable_asset_url("x.zip"),
            "https://github.com/PaperLibrary-Developers/PaperLibrary/releases/latest/download/x.zip")

    def test_allowed_hosts(self):
        self.assertTrue(release.allowed_download_url(
            "https://github.com/a/b/releases/download/v1.6/x.zip"))
        self.assertTrue(release.allowed_download_url(
            "https://release-assets.githubusercontent.com/github-production-release-asset/1/x"))
        for bad in ("http://github.com/a", "https://evil.example/a",
                    "https://github.com.evil.example/a", "ftp://github.com/a", ""):
            self.assertFalse(release.allowed_download_url(bad))


class ZipCheckTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_valid_zip(self):
        path = make_zip(self.dir / "ok.zip", {"PaperLibrary.exe": b"MZ", "paperlib/app.py": b"x"})
        problems, warnings = release.check_zip(path, expected_version="1.6.0",
                                               require_version_file=True)
        self.assertEqual(problems, [])
        self.assertEqual(warnings, [])

    def test_data_directory_rejected(self):
        path = make_zip(self.dir / "data.zip",
                        {"PaperLibrary.exe": b"MZ", "data/library.db": b"x"})
        problems, _ = release.check_zip(path, expected_version="1.6.0")
        self.assertTrue(any("data/" in item for item in problems), problems)

    def test_missing_launcher(self):
        path = make_zip(self.dir / "noexe.zip", {"paperlib/app.py": b"x"})
        problems, _ = release.check_zip(path, expected_version="1.6.0")
        self.assertTrue(any("PaperLibrary.exe" in item for item in problems), problems)

    def test_version_mismatch_and_missing(self):
        path = make_zip(self.dir / "old.zip", {"PaperLibrary.exe": b"MZ"}, version="1.5.0")
        problems, _ = release.check_zip(path, expected_version="1.6.0")
        self.assertTrue(any("1.5.0" in item for item in problems), problems)

        path2 = make_zip(self.dir / "noversion.zip", {"PaperLibrary.exe": b"MZ"}, version=None)
        problems2, warnings = release.check_zip(path2, expected_version="1.6.0")
        self.assertEqual(problems2, [])
        self.assertTrue(any("version.json" in item for item in warnings), warnings)
        problems3, _ = release.check_zip(path2, expected_version="1.6.0", require_version_file=True)
        self.assertTrue(any("version.json" in item for item in problems3), problems3)

    def test_traversal_and_absolute_entries(self):
        path = self.dir / "evil.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("PaperLibrary.exe", b"MZ")
            zf.writestr("../evil.txt", b"x")
            zf.writestr("/absolute.txt", b"x")
        problems, _ = release.check_zip(path, expected_version="1.6.0")
        self.assertTrue(any("绝对路径" in item for item in problems), problems)

    def test_corrupt_zip(self):
        path = self.dir / "broken.zip"
        path.write_bytes(b"not a zip")
        problems, _ = release.check_zip(path, expected_version="1.6.0")
        self.assertTrue(any("不是合法 zip" in item for item in problems), problems)


class VerifyCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.zip = make_zip(self.dir / "PaperLibrary.v1.6.Nexus.zip",
                            {"PaperLibrary.exe": b"MZ", "paperlib/app.py": b"x"})
        self.asset = {
            "url": release.release_asset_url("v1.6"),
            "size": self.zip.stat().st_size,
            "sha256": __import__("hashlib").sha256(self.zip.read_bytes()).hexdigest(),
        }
        data = {
            "schema": 1, "product": "PaperLibrary", "channel": "stable",
            "version": "1.6.0", "tag": "v1.6",
            "publishedAt": "2026-11-15T00:12:52Z",
            "releaseNotesUrl": "https://github.com/PaperLibrary-Developers/PaperLibrary/releases/tag/v1.6",
            "minWindows": "10.0.17763", "arch": "x64", "assets": {"full": self.asset},
        }
        signed = signing.sign_manifest(data, TEST_SEED)
        self.manifest_path = self.dir / "update-manifest.json"
        self.manifest_path.write_bytes(manifest.canonical_bytes(signed))
        self.pubkey = base64.b64encode(TEST_PUBLIC).decode()

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *args):
        env = dict(__import__("os").environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=str(ROOT), env=env, timeout=120)

    def test_offline_verification_passes(self):
        proc = self.run_tool("--manifest", str(self.manifest_path),
                             "--asset-path", str(self.zip), "--pubkey", self.pubkey)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("发布校验通过", proc.stdout)

    def test_legacy_flag_downgrades_missing_version(self):
        legacy = make_zip(self.dir / "legacy.zip", {"PaperLibrary.exe": b"MZ"}, version=None)
        import hashlib
        data = manifest.load_file(self.manifest_path)
        data["assets"]["full"] = {"url": release.release_asset_url("v1.6"),
                                  "size": legacy.stat().st_size,
                                  "sha256": hashlib.sha256(legacy.read_bytes()).hexdigest()}
        data.pop("signature")
        signed = signing.sign_manifest(data, TEST_SEED)
        path = self.dir / "legacy-manifest.json"
        path.write_bytes(manifest.canonical_bytes(signed))
        proc = self.run_tool("--manifest", str(path), "--asset-path", str(legacy),
                             "--pubkey", self.pubkey, "--legacy")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("WARN", proc.stdout)
        strict = self.run_tool("--manifest", str(path), "--asset-path", str(legacy),
                               "--pubkey", self.pubkey)
        self.assertEqual(strict.returncode, 1, strict.stdout + strict.stderr)

    def test_tampered_asset_fails(self):
        with open(self.zip, "ab") as fh:
            fh.write(b"x")
        proc = self.run_tool("--manifest", str(self.manifest_path),
                             "--asset-path", str(self.zip), "--pubkey", self.pubkey)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("大小", proc.stdout)

    def test_wrong_key_fails(self):
        other = base64.b64encode(ed25519.public_key(bytes(range(1, 33)))).decode()
        proc = self.run_tool("--manifest", str(self.manifest_path),
                             "--asset-path", str(self.zip), "--pubkey", other)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_open_chain_rejects_disallowed_host_without_network(self):
        from tools import verify_release

        with self.assertRaises(verify_release.VerifyError):
            verify_release.open_chain("https://evil.example/x.zip", timeout=1.0)
        with self.assertRaises(verify_release.VerifyError):
            verify_release.open_chain("http://github.com/a/b", timeout=1.0)


if __name__ == "__main__":
    unittest.main()
