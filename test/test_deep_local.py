"""深度分析本地降级：短摘要/无标点摘要不得 500（契约 §8 / 离线目标 G2 回归）。"""
from __future__ import annotations

import unittest

from paperlib.legacy import deep_local


class DeepLocalShortAbstractTest(unittest.TestCase):
    def _paper(self, summary):
        return {"id": "p1", "title": "A", "summary": summary, "year": 2024}

    def test_short_abstract_does_not_crash(self):
        for summary in ("摘要 A", "待补充", "见笔记", "x"):
            analysis = deep_local.analyze_local(self._paper(summary), [], "")
            self.assertIn("fields", analysis)
            self.assertGreaterEqual(len(analysis["fields"]), 22)
            self.assertIn("分析模式", analysis["fields"])
            self.assertTrue(analysis["oneLine"])

    def test_no_abstract(self):
        analysis = deep_local.analyze_local(self._paper(""), [], "")
        self.assertTrue(analysis["oneLine"])
        self.assertIn("未提及", analysis["fields"]["原文摘要"])


if __name__ == "__main__":
    unittest.main()
