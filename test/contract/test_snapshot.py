"""契约快照测试：跑 tools/contract_check.py，断言 0 FAIL（network 行 SKIP 不计）。"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class ContractSnapshotTest(unittest.TestCase):
    def test_snapshot_zero_fail(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "contract_check.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=300,
            env={**__import__("os").environ, "PYTHONUTF8": "1"},
        )
        output = (proc.stdout or "") + (proc.stderr or "")
        match = re.search(r"PASS (\d+) / FAIL (\d+) / SKIP (\d+)", output)
        self.assertIsNotNone(match, output[-2000:])
        passed, failed, skipped = (int(x) for x in match.groups())
        self.assertEqual(failed, 0, "契约失败：\n" + output[-4000:])
        self.assertGreaterEqual(passed, 50, output[-4000:])


if __name__ == "__main__":
    unittest.main()
