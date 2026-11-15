"""Ed25519 签名/验签与私钥管理的测试（阶段 0 / P0-03）。"""
from __future__ import annotations

import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import ed25519, keys, manifest, signing  # noqa: E402

EXAMPLE = ROOT / "shared" / "fixtures" / "update-manifest.example.json"
SIGNED = ROOT / "shared" / "fixtures" / "update-manifest.signed.json"
TOOL = ROOT / "tools" / "update_manifest.py"
TEST_SEED = bytes(range(32))
TEST_PUBLIC = ed25519.public_key(TEST_SEED)

# RFC 8032 §7.1 的 3 条测试向量（seed, public, message, signature）
RFC_VECTORS = [
    ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60",
     "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
     "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
    ("4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb",
     "3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c", "72",
     "92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613d0f11d8c387b2eaeb4302aeeb00d291612bb0c00"),
    ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7",
     "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
     "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a"),
]


def unsigned_example():
    data = manifest.load_file(EXAMPLE)
    data.pop("signature", None)
    return data


class RfcVectorTest(unittest.TestCase):
    def test_vectors(self):
        for seed_hex, public_hex, msg_hex, sig_hex in RFC_VECTORS:
            seed = bytes.fromhex(seed_hex)
            message = bytes.fromhex(msg_hex)
            self.assertEqual(ed25519.public_key(seed).hex(), public_hex)
            signature = ed25519.sign(message, seed)
            self.assertEqual(signature.hex(), sig_hex)
            self.assertTrue(ed25519.verify(signature, message, bytes.fromhex(public_hex)))

    def test_verify_rejects_bad_inputs(self):
        seed = TEST_SEED
        sig = ed25519.sign(b"msg", seed)
        self.assertFalse(ed25519.verify(sig, b"other", TEST_PUBLIC))
        tampered = bytearray(sig)
        tampered[0] ^= 1
        self.assertFalse(ed25519.verify(bytes(tampered), b"msg", TEST_PUBLIC))
        self.assertFalse(ed25519.verify(sig[:-1], b"msg", TEST_PUBLIC))
        self.assertFalse(ed25519.verify(sig, b"msg", b"\x00" * 32))
        self.assertFalse(ed25519.verify(b"\x00" * 64, b"msg", TEST_PUBLIC))
        # 非规范 S（S >= L）必须拒绝
        r = sig[:32]
        self.assertFalse(ed25519.verify(r + (2 ** 252 + 27742317777372353535851937790883648493).to_bytes(32, "little"),
                                        b"msg", TEST_PUBLIC))

    def test_signing_is_deterministic(self):
        first = ed25519.sign(b"same message", TEST_SEED)
        second = ed25519.sign(b"same message", TEST_SEED)
        self.assertEqual(first, second)

    def test_cross_check_with_cryptography_if_available(self):
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PrivateKey, Ed25519PublicKey)
        except ImportError:  # 便携运行时没有 cryptography，跳过
            self.skipTest("cryptography 未安装")
        message = b"manifest payload"
        signature = ed25519.sign(message, TEST_SEED)
        Ed25519PublicKey.from_public_bytes(TEST_PUBLIC).verify(signature, message)
        other = Ed25519PrivateKey.from_private_bytes(TEST_SEED).sign(message)
        self.assertTrue(ed25519.verify(other, message, TEST_PUBLIC))
        self.assertEqual(other, signature)


class ManifestSigningTest(unittest.TestCase):
    def setUp(self):
        self.signed = signing.sign_manifest(unsigned_example(), TEST_SEED)

    def test_sign_adds_valid_signature(self):
        self.assertNotEqual(self.signed["signature"], "")
        self.assertIn("signature", self.signed)
        signing.verify_manifest(self.signed, TEST_PUBLIC)
        # payload 不含 signature，签名前后一致
        self.assertEqual(manifest.canonical_payload(self.signed),
                         manifest.canonical_payload(unsigned_example()))

    def test_sign_is_deterministic_and_canonical(self):
        again = signing.sign_manifest(unsigned_example(), TEST_SEED)
        self.assertEqual(manifest.canonical_bytes(again), manifest.canonical_bytes(self.signed))

    def test_tampered_payload_fields_are_rejected(self):
        cases = [
            ("publishedAt", "2026-11-15T00:12:53Z"),
            ("minWindows", "10.0.19045"),
            ("releaseNotesUrl", "https://github.com/PaperLibrary-Developers/PaperLibrary/releases/tag/v1.6-other"),
            ("assets", {"full": dict(self.signed["assets"]["full"], size=45625894)}),
            ("assets", {"full": dict(self.signed["assets"]["full"], sha256="0" * 64)}),
            ("assets", {"full": dict(self.signed["assets"]["full"],
                                     url="https://github.com/PaperLibrary-Developers/PaperLibrary/releases/download/v1.6/Other.zip")}),
        ]
        for key, value in cases:
            data = copy.deepcopy(self.signed)
            data[key] = value
            with self.assertRaises(signing.ManifestSignatureError, msg=key):
                signing.verify_manifest(data, TEST_PUBLIC)

    def test_wrong_key_and_signature_are_rejected(self):
        other_public = ed25519.public_key(bytes(range(1, 33)))
        with self.assertRaises(signing.ManifestSignatureError):
            signing.verify_manifest(self.signed, other_public)
        data = copy.deepcopy(self.signed)
        raw = bytearray(base64.b64decode(data["signature"]))
        raw[-1] ^= 1
        data["signature"] = base64.b64encode(bytes(raw)).decode("ascii")
        with self.assertRaises(signing.ManifestSignatureError):
            signing.verify_manifest(data, TEST_PUBLIC)

    def test_missing_signature_is_structural_error(self):
        data = copy.deepcopy(self.signed)
        data.pop("signature")
        with self.assertRaises(manifest.ManifestError):
            signing.verify_manifest(data, TEST_PUBLIC)

    def test_embedded_key_verifies_real_fixture(self):
        data = manifest.load_file(SIGNED)
        signing.verify_manifest(data)  # 默认使用内置公钥
        self.assertEqual(keys.PUBLIC_KEY_ID, signing.key_id(keys.public_key()))
        tampered = copy.deepcopy(data)
        tampered["assets"]["full"]["size"] += 1
        with self.assertRaises(signing.ManifestSignatureError):
            signing.verify_manifest(tampered)

    def test_example_fixture_signed_with_embedded_key_matches(self):
        # signed 样例除 signature 外与 example 完全一致
        example = manifest.load_file(EXAMPLE)
        example.pop("signature")
        signed = manifest.load_file(SIGNED)
        signed.pop("signature")
        self.assertEqual(example, signed)


class PrivateKeyTest(unittest.TestCase):
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.json"
            info = signing.save_private_key(TEST_SEED, path)
            self.assertEqual(info["keyId"], signing.key_id(TEST_PUBLIC))
            self.assertNotIn("seed", {key: "" for key in info})
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("seed", record)
            self.assertEqual(signing.load_private_key(path), TEST_SEED)
            self.assertEqual(signing.load_private_key(env={signing.KEY_ENV: base64.b64encode(TEST_SEED).decode()}),
                             TEST_SEED)

    def test_missing_and_corrupt_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(signing.SigningError):
                signing.load_private_key(Path(tmp) / "nope.json")
            path = Path(tmp) / "bad.json"
            path.write_text(json.dumps({"schema": 1, "seed": base64.b64encode(b"short").decode()}),
                            encoding="utf-8")
            with self.assertRaises(signing.SigningError):
                signing.load_private_key(path)
            path.write_text(json.dumps({"schema": 1, "seed": base64.b64encode(TEST_SEED).decode(),
                                        "publicKey": base64.b64encode(b"\x00" * 32).decode()}),
                            encoding="utf-8")
            with self.assertRaises(signing.SigningError):
                signing.load_private_key(path)

    def test_env_value_validation(self):
        with self.assertRaises(signing.SigningError):
            signing.load_private_key(env={signing.KEY_ENV: "not-base64!!"})
        with self.assertRaises(signing.SigningError):
            signing.load_private_key(env={signing.KEY_ENV: base64.b64encode(b"x" * 31).decode()})


class CliTest(unittest.TestCase):
    def run_tool(self, *args):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, str(TOOL), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=str(ROOT), env=env, timeout=120)

    def test_keygen_refuses_repo_path_and_existing_file(self):
        inside = ROOT / "tools" / "_test_should_not_exist.json"
        proc = self.run_tool("keygen", "--key", str(inside))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertFalse(inside.exists())

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "key.json"
            first = self.run_tool("keygen", "--key", str(path))
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertTrue(path.exists())
            self.assertNotIn('"seed"', first.stdout)
            second = self.run_tool("keygen", "--key", str(path))
            self.assertEqual(second.returncode, 1)
            seed = signing.load_private_key(path)
            public_b64 = base64.b64encode(ed25519.public_key(seed)).decode()

            manifest_path = Path(tmp) / "manifest.json"
            manifest_path.write_bytes(manifest.canonical_payload(unsigned_example()))
            sign = self.run_tool("sign", "--in", str(manifest_path), "--key", str(path))
            self.assertEqual(sign.returncode, 0, sign.stdout + sign.stderr)
            verify = self.run_tool("verify", "--in", str(manifest_path), "--pubkey", public_b64)
            self.assertEqual(verify.returncode, 0, verify.stdout + verify.stderr)
            # 换内置公钥验签必须失败（CI 自检不会误用生产公钥）
            bad = self.run_tool("verify", "--in", str(manifest_path))
            self.assertEqual(bad.returncode, 1)
            self.assertIn("验签失败", bad.stdout + bad.stderr)


if __name__ == "__main__":
    unittest.main()
