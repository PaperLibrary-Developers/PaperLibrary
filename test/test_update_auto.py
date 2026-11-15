"""P1-04：自动检查更新的前端调度逻辑测试（调用 Node 测试脚本）。"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


@unittest.skipUnless(NODE, "未安装 node，跳过自动检查前端测试")
class UpdateAutoNodeTest(unittest.TestCase):
    def test_scheduler_suite(self):
        env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        proc = subprocess.run([NODE, str(ROOT / "test" / "js" / "update-auto.test.mjs")],
                              cwd=str(ROOT), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120, env=env)
        self.assertEqual(proc.returncode, 0, (proc.stdout or "") + (proc.stderr or ""))
        self.assertIn("11 tests passed", proc.stdout)
        self.assertIn("24 小时内再次 runNow 被节流", proc.stdout)


if __name__ == "__main__":
    unittest.main()
