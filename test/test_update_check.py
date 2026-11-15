"""阶段 1 / P1-02：GET /api/update/check 与清单拉取的全部错误分支。"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.api import update as update_api  # noqa: E402
from paperlib.update import check, fetch, keys, manifest, release, signing  # noqa: E402
from paperlib.version import VersionError, ui_label  # noqa: E402

TEST_SEED = bytes(range(32))
TEST_PUBLIC = __import__("paperlib.update.ed25519", fromlist=["public_key"]).public_key(TEST_SEED)


def make_root(tmp: Path, version="1.6.0", tag=None) -> Path:
    (tmp / "version.json").write_text(
        json.dumps({"product": "PaperLibrary", "version": version, "tag": tag or ui_label(version)}),
        encoding="utf-8")
    return tmp


def manifest_data(version="1.7.1", min_windows="10.0.17763", arch="x64", size=43238599):
    tag = ui_label(version)
    return {
        "schema": 1,
        "product": "PaperLibrary",
        "channel": "stable",
        "version": version,
        "tag": tag,
        "publishedAt": "2026-11-15T13:52:55Z",
        "releaseNotesUrl": "https://github.com/%s/releases/tag/%s" % (release.REPO, tag),
        "minWindows": min_windows,
        "arch": arch,
        "assets": {"full": {
            "url": release.release_asset_url(tag),
            "size": size,
            "sha256": "e844ba33ba07e44f8e7abd68e98b7f83a57f9f3d5d62212bb0789beaf0f30032",
        }},
    }


def signed(version="1.7.1", seed=TEST_SEED, **kwargs):
    return manifest.canonical_bytes(signing.sign_manifest(manifest_data(version, **kwargs), seed))


def checker(tmp, raw=None, fetch_func=None, **kwargs):
    kwargs.setdefault("public_key", TEST_PUBLIC)
    kwargs.setdefault("now", lambda: 0)
    if fetch_func is None:
        payload = raw
        fetch_func = lambda: payload
    return check.UpdateChecker(tmp, fetch_manifest=fetch_func, **kwargs)


class CompareTest(unittest.TestCase):
    def test_compare(self):
        self.assertGreater(check.compare_versions("1.7.1", "1.6.0"), 0)
        self.assertEqual(check.compare_versions("1.7.1", "1.7.1"), 0)
        self.assertLess(check.compare_versions("1.6.9", "1.7.0"), 0)
        self.assertEqual(check.compare_versions("1.0.10", "1.0.9"), 0 + 1)

    def test_invalid_version(self):
        with self.assertRaises(check.CheckError) as ctx:
            check.compare_versions("1.7", "1.6.0")
        self.assertEqual(ctx.exception.code, "invalid-version")


class CheckerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = make_root(Path(self.tmp.name), "1.6.0")

    def tearDown(self):
        self.tmp.cleanup()

    def test_newer(self):
        result = checker(self.root, signed("1.7.1")).check()
        self.assertTrue(result["ok"])
        self.assertTrue(result["available"])
        self.assertEqual(result["reason"], "newer")
        self.assertEqual(result["current"]["tag"], "v1.6")
        self.assertEqual(result["latest"]["tag"], "v1.7.1")
        self.assertEqual(result["latest"]["size"], 43238599)
        self.assertEqual(result["checkedAt"], "1970-01-01T00:00:00Z")
        self.assertIn("v1.7.1", result["message"])

    def test_current(self):
        result = checker(self.root, signed("1.6.0")).check()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "current")

    def test_older_is_never_offered(self):
        root = make_root(Path(self.tmp.name), "1.7.0")
        result = checker(root, signed("1.6.0")).check()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "older")
        self.assertIn("不降级", result["message"])

    def test_unsupported_windows(self):
        result = checker(self.root, signed("1.7.1"), platform_version="10.0.10240").check()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "unsupported-win")
        self.assertIn("10.0.17763", result["message"])

    def test_unsupported_arch(self):
        result = checker(self.root, signed("1.7.1"), arch="arm64").check()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "unsupported-arch")

    def test_invalid_json(self):
        with self.assertRaises(check.CheckError) as ctx:
            checker(self.root, b"{not json").check()
        self.assertEqual(ctx.exception.code, "invalid-manifest")

    def test_invalid_schema(self):
        import base64

        data = manifest_data()
        data["product"] = "Other"
        data["signature"] = base64.b64encode(b"\x00" * 64).decode("ascii")
        raw = json.dumps(data).encode("utf-8")
        with self.assertRaises(check.CheckError) as ctx:
            checker(self.root, raw).check()
        self.assertEqual(ctx.exception.code, "invalid-manifest")

    def test_bad_signature(self):
        raw = signed("1.7.1", seed=bytes(range(1, 33)))
        with self.assertRaises(check.CheckError) as ctx:
            checker(self.root, raw).check()
        self.assertEqual(ctx.exception.code, "signature")

    def test_tampered_payload_signature_fails(self):
        raw = json.loads(signed("1.7.1").decode("utf-8"))
        raw["assets"]["full"]["size"] += 1
        with self.assertRaises(check.CheckError) as ctx:
            checker(self.root, json.dumps(raw).encode()).check()
        self.assertEqual(ctx.exception.code, "signature")

    def test_oversize_manifest(self):
        with self.assertRaises(check.CheckError) as ctx:
            checker(self.root, b" " * (manifest.MAX_BYTES + 1)).check()
        self.assertEqual(ctx.exception.code, "invalid-manifest")
        self.assertIn("256", ctx.exception.message)

    def test_real_embedded_key_fixture(self):
        # shared/fixtures/update-manifest.signed.json 由内置私钥签名；当前 root 是 1.6.0 → 1.6.0 为同一版本
        raw = (ROOT / "shared" / "fixtures" / "update-manifest.signed.json").read_bytes()
        result = check.UpdateChecker(self.root, fetch_manifest=lambda: raw, now=lambda: 0).check()
        self.assertFalse(result["available"])
        self.assertEqual(result["reason"], "current")
        self.assertEqual(result["latest"]["tag"], "v1.6")

    def test_fetch_errors_pass_through(self):
        for code in ("timeout", "network", "no-manifest"):
            def boom(code=code):
                raise fetch.FetchError(code, "模拟失败：" + code)
            with self.assertRaises(fetch.FetchError) as ctx:
                checker(self.root, fetch_func=boom).check()
            self.assertEqual(ctx.exception.code, code)

    def test_current_version_file_broken(self):
        (Path(self.tmp.name) / "version.json").write_text("{bad", encoding="utf-8")
        with self.assertRaises(VersionError):
            checker(self.root, signed("1.7.1")).check()


class FetchTest(unittest.TestCase):
    class Resp:
        def __init__(self, status=200, body=b"{}", location="", headers=None):
            self.status = status
            self._body = body
            self.headers = headers if headers is not None else {"Location": location}
            self.closed = False

        def read(self, n=-1):
            return self._body if n < 0 else self._body[:n]

        def close(self):
            self.closed = True

    def test_success(self):
        calls = []

        def request_once(url, timeout):
            calls.append(url)
            return self.Resp(body=b'{"ok":1}')

        raw = fetch.fetch_manifest(request_once=request_once, sleep=lambda s: None)
        self.assertEqual(raw, b'{"ok":1}')
        self.assertEqual(calls, [release.stable_manifest_url()])

    def test_allowed_redirect(self):
        calls = []

        def request_once(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                return self.Resp(status=302, location="https://release-assets.githubusercontent.com/x")
            return self.Resp(body=b"ok")

        raw = fetch.fetch_manifest(request_once=request_once, sleep=lambda s: None)
        self.assertEqual(raw, b"ok")
        self.assertEqual(len(calls), 2)

    def test_disallowed_redirect(self):
        calls = []

        def request_once(url, timeout):
            calls.append(url)
            return self.Resp(status=302, location="https://evil.example/x")

        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_manifest(request_once=request_once, sleep=lambda s: None)
        self.assertEqual(ctx.exception.code, "redirect-host")
        self.assertEqual(len(calls), 1)

    def test_404_no_retry(self):
        calls = []

        def request_once(url, timeout):
            calls.append(url)
            return self.Resp(status=404)

        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_manifest(request_once=request_once, retries=2, sleep=lambda s: None)
        self.assertEqual(ctx.exception.code, "no-manifest")
        self.assertEqual(len(calls), 1)

    def test_timeout_retries(self):
        calls, sleeps = [], []

        def request_once(url, timeout):
            calls.append(url)
            raise socket.timeout("timed out")

        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_manifest(request_once=request_once, retries=2, sleep=sleeps.append)
        self.assertEqual(ctx.exception.code, "timeout")
        self.assertEqual(len(calls), 3)
        self.assertEqual(len(sleeps), 2)

    def test_retry_then_success(self):
        calls = []

        def request_once(url, timeout):
            calls.append(url)
            if len(calls) == 1:
                raise urllib.error.URLError("temporary")
            return self.Resp(body=b"ok")

        raw = fetch.fetch_manifest(request_once=request_once, retries=2, sleep=lambda s: None)
        self.assertEqual(raw, b"ok")
        self.assertEqual(len(calls), 2)

    def test_oversize_body(self):
        big = b"x" * (manifest.MAX_BYTES + 100)

        def request_once(url, timeout):
            return self.Resp(body=big)

        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_manifest(request_once=request_once, sleep=lambda s: None)
        self.assertEqual(ctx.exception.code, "manifest-too-large")

    def test_http_url_rejected_before_network(self):
        calls = []

        def request_once(url, timeout):
            calls.append(url)
            return self.Resp()

        with self.assertRaises(fetch.FetchError) as ctx:
            fetch.fetch_manifest("http://github.com/x", request_once=request_once)
        self.assertEqual(ctx.exception.code, "bad-url")
        self.assertEqual(calls, [])


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


class ApiHandlerTest(unittest.TestCase):
    class Stub:
        def __init__(self, result=None, error=None):
            self.result, self.error = result, error

        def check(self):
            if self.error:
                raise self.error
            return self.result

    def call(self, stub):
        ctx = _FakeCtx(stub)
        update_api.check(ctx)
        return ctx.response

    def test_success(self):
        status, body = self.call(self.Stub(result={"ok": True, "available": True}))
        self.assertEqual(status, 200)
        self.assertTrue(body["available"])

    def test_fetch_errors_map_to_502_with_code(self):
        for code in ("timeout", "network", "no-manifest", "redirect-host", "bad-url",
                     "manifest-too-large"):
            status, body = self.call(self.Stub(error=fetch.FetchError(code, "失败：" + code)))
            self.assertEqual(status, 502)
            self.assertEqual(body["code"], code)
            self.assertIn("失败", body["error"])

    def test_check_errors_map_to_502_with_code(self):
        for code in ("invalid-manifest", "signature", "invalid-version"):
            status, body = self.call(self.Stub(error=check.CheckError(code, "失败：" + code)))
            self.assertEqual(status, 502)
            self.assertEqual(body["code"], code)

    def test_local_version_error_maps_to_500(self):
        status, body = self.call(self.Stub(error=VersionError("坏 version.json")))
        self.assertEqual(status, 500)
        self.assertIn("version.json", body["error"])


if __name__ == "__main__":
    unittest.main()
