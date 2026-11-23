"""开屏公告小弹窗（方案 A：本地 public/notice.json）静态资源、偏好持久化与前端单测。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PAPERLIB_SKIP_MIGRATE"] = "1"

from paperlib.app import build  # noqa: E402
from paperlib.config import Config  # noqa: E402
from paperlib.http.static import StaticHandler  # noqa: E402

NODE = shutil.which("node")


class _DummyCtx:
    def __init__(self, pathname):
        self.pathname = pathname
        self.sent_file = None
        self.sent_cache = None

    def has_query(self, _name):
        return False

    def send_file(self, abs_path, cache=None):
        self.sent_file = abs_path
        self.sent_cache = cache


class NoticeStaticAndPrefsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="paperlib-notice-"))
        data_dir = self.tmp / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / ".seeded").write_text("{}", encoding="utf-8")
        cfg = Config(root=ROOT, data_dir=data_dir, host="127.0.0.1", port=4321, no_open=True)
        self.services, _router, _static, self.store = build(cfg)

    def tearDown(self):
        self.store.repo.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_notice_json_served_with_no_cache(self):
        handler = StaticHandler(ROOT)
        ctx = _DummyCtx("/notice.json")
        handler.serve_static(ctx)
        self.assertEqual(Path(ctx.sent_file), ROOT / "public" / "notice.json")
        self.assertEqual(ctx.sent_cache, "no-cache")
        data = json.loads(Path(ctx.sent_file).read_text(encoding="utf-8"))
        self.assertTrue(data.get("id"))
        self.assertTrue(data.get("enabled"))
        self.assertIn("promo", data)
        self.assertTrue(data["promo"].get("title"))

    def test_prefs_persists_notice_dismissed_id(self):
        saved = self.services.prefs.merge({"noticeDismissedId": "2026-11-v1.9.1"})
        self.assertEqual(saved.get("noticeDismissedId"), "2026-11-v1.9.1")
        again = self.services.prefs.read()
        self.assertEqual(again.get("noticeDismissedId"), "2026-11-v1.9.1")


@unittest.skipUnless(NODE, "未安装 node，跳过开屏公告前端测试")
class NoticeNodeTest(unittest.TestCase):
    def test_notice_js_suite(self):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [NODE, "--test", str(ROOT / "test" / "js" / "notice.test.mjs")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=env,
        )
        self.assertEqual(proc.returncode, 0, (proc.stdout or "") + (proc.stderr or ""))
        self.assertIn("pass 8", proc.stdout)


if __name__ == "__main__":
    unittest.main()
