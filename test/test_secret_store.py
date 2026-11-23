"""Windows DPAPI 密钥落盘加密与旧明文配置平滑迁移回归测试。"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from paperlib.adapters import secret_store
from paperlib.adapters.llm import LLM
from paperlib.adapters.sources import Sources
from paperlib.adapters.websearch import WebSearch


class SecretStoreUnitTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI 仅在 Windows 生效")
    def test_dpapi_roundtrip_randomization_and_corrupt_handling(self):
        self.assertTrue(secret_store.is_dpapi_available())
        for sample in ("sk-live-secret-1234567890", "中文密钥-αβγ-🔐", "k" * 256):
            c1 = secret_store.protect_secret(sample)
            c2 = secret_store.protect_secret(sample)
            self.assertTrue(c1.startswith(secret_store.DPAPI_PREFIX))
            self.assertNotIn(sample, c1)
            self.assertNotEqual(c1, c2)  # DPAPI 每次加盐随机化
            plain1, migrate1 = secret_store.unprotect_secret(c1)
            plain2, migrate2 = secret_store.unprotect_secret(c2)
            self.assertEqual((plain1, migrate1), (sample, False))
            self.assertEqual((plain2, migrate2), (sample, False))
            # 幂等：对已加密值再次 protect 不重复包裹
            self.assertEqual(secret_store.protect_secret(c1), c1)

        # 空串保持空串
        self.assertEqual(secret_store.protect_secret(""), "")
        self.assertEqual(secret_store.unprotect_secret(""), ("", False))

        # 损坏密文安全降级为空串，不抛异常
        self.assertEqual(secret_store.unprotect_secret("dpapi:v1:!!!not-base64!!!"), ("", False))
        self.assertEqual(secret_store.unprotect_secret("dpapi:v1:QUFBQUFB"), ("", False))

    def test_non_windows_transparent_fallback(self):
        with mock.patch.object(secret_store, "is_dpapi_available", return_value=False):
            self.assertEqual(secret_store.protect_secret("sk-plain"), "sk-plain")
            self.assertEqual(secret_store.unprotect_secret("sk-plain"), ("sk-plain", False))
            self.assertEqual(secret_store.unprotect_secret("dpapi:v1:QUFB"), ("", False))


class AdapterSecretPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="helix-dpapi-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI 仅在 Windows 生效")
    def test_llm_websearch_sources_encrypt_at_rest_and_reload(self):
        # 1) LLM
        llm = LLM(self.tmp)
        llm.update({"apiKey": "sk-llm-super-secret-9999"})
        raw_settings = (self.tmp / "settings.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-llm-super-secret-9999", raw_settings)
        self.assertTrue(json.loads(raw_settings)["apiKey"].startswith(secret_store.DPAPI_PREFIX))
        reloaded_llm = LLM(self.tmp)
        self.assertEqual(reloaded_llm.settings["apiKey"], "sk-llm-super-secret-9999")
        self.assertEqual(reloaded_llm.public_settings()["keyHint"], "sk-l…9999")

        # 2) WebSearch (Exa)
        ws = WebSearch(self.tmp)
        ws.update({"exaApiKey": "exa-secret-abcdef1234"})
        raw_ws = (self.tmp / "websearch.json").read_text(encoding="utf-8")
        self.assertNotIn("exa-secret-abcdef1234", raw_ws)
        self.assertTrue(json.loads(raw_ws)["exaApiKey"].startswith(secret_store.DPAPI_PREFIX))
        reloaded_ws = WebSearch(self.tmp)
        self.assertEqual(reloaded_ws.settings["exaApiKey"], "exa-secret-abcdef1234")

        # 3) Sources (OpenAlex)
        src = Sources(self.tmp)
        src.update({"openalexApiKey": "oa-secret-xyz7890", "contactEmail": "user@example.org"})
        raw_src = (self.tmp / "sources.json").read_text(encoding="utf-8")
        self.assertNotIn("oa-secret-xyz7890", raw_src)
        self.assertTrue(json.loads(raw_src)["openalexApiKey"].startswith(secret_store.DPAPI_PREFIX))
        reloaded_src = Sources(self.tmp)
        self.assertEqual(reloaded_src.settings["openalexApiKey"], "oa-secret-xyz7890")
        self.assertEqual(reloaded_src.contact_email(), "user@example.org")

    @unittest.skipUnless(sys.platform == "win32", "Windows DPAPI 仅在 Windows 生效")
    def test_legacy_plaintext_files_auto_migrate_on_load(self):
        (self.tmp / "settings.json").write_text(
            json.dumps({"provider": "deepseek", "apiKey": "sk-legacy-plain-1111"}),
            encoding="utf-8",
        )
        (self.tmp / "websearch.json").write_text(
            json.dumps({"exaApiKey": "exa-legacy-plain-2222"}),
            encoding="utf-8",
        )
        (self.tmp / "sources.json").write_text(
            json.dumps({"openalexApiKey": "oa-legacy-plain-3333"}),
            encoding="utf-8",
        )

        llm = LLM(self.tmp)
        ws = WebSearch(self.tmp)
        src = Sources(self.tmp)

        # 内存中立即可用明文
        self.assertEqual(llm.settings["apiKey"], "sk-legacy-plain-1111")
        self.assertEqual(ws.settings["exaApiKey"], "exa-legacy-plain-2222")
        self.assertEqual(src.settings["openalexApiKey"], "oa-legacy-plain-3333")

        # 磁盘上已自动平滑迁移为 DPAPI 密文
        for fname, field, secret in (
            ("settings.json", "apiKey", "sk-legacy-plain-1111"),
            ("websearch.json", "exaApiKey", "exa-legacy-plain-2222"),
            ("sources.json", "openalexApiKey", "oa-legacy-plain-3333"),
        ):
            raw = (self.tmp / fname).read_text(encoding="utf-8")
            self.assertNotIn(secret, raw)
            self.assertTrue(json.loads(raw)[field].startswith(secret_store.DPAPI_PREFIX))


if __name__ == "__main__":
    unittest.main()
