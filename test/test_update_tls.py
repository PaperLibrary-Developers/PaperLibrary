"""更新 HTTPS 上下文测试：保持证书/主机名校验，并启用 cross-signed root 的 partial-chain。"""
from __future__ import annotations

import ssl
import sys
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update import download, fetch, tls  # noqa: E402


class UpdateTlsTest(unittest.TestCase):
    def test_context_keeps_verification_and_enables_partial_chain(self):
        ctx = tls.create_https_context()
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)
        flag = getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
        self.assertTrue(ctx.verify_flags & flag)

    def test_update_openers_use_verified_context(self):
        for opener in (fetch._OPENER, download._OPENER):
            handlers = [h for h in opener.handlers if isinstance(h, urllib.request.HTTPSHandler)]
            self.assertEqual(len(handlers), 1)
            ctx = handlers[0]._context
            self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
            self.assertTrue(ctx.check_hostname)
            self.assertTrue(ctx.verify_flags & getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0))


if __name__ == "__main__":
    unittest.main()
