"""shared.pure.passages 的语义单测（移植自 Sci-lib shared/pure/passages.js）。

JS 的逐例对照在 test_pure_parity.py；这里钉页码锚点、去重与预算行为。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.pure import passages as pg  # noqa: E402

# 关键词只在锚点附近出现，避免「每个 55 字都有命中 → 窗口全并成一段」的极端情况
FILLER = "The host guest chemistry was studied in detail by several groups. " * 55
TEXT = (
    "S-1 General information. " + FILLER
    + "Figure S - 55 shows the titration data for the molecular capsule. "
    + FILLER + "Table GP-2 lists the yields. " + FILLER
    + "Supporting information is available on request."
)


class AnchorAndKeywords(unittest.TestCase):
    def test_anchor_patterns_cover_spacing_variants(self):
        patterns = pg.anchor_patterns("what is in Figure S55?")
        self.assertEqual(len(patterns), 1)
        for variant in ("S55", "S-55", "S − 55", "s 55"):
            self.assertIsNotNone(patterns[0].search(variant), variant)

    def test_page_anchor_hits(self):
        found = pg.find_hits(TEXT, "What does Figure S55 show about the molecular capsule?", 60)
        self.assertGreaterEqual(found["anchors"], 1)
        self.assertTrue(found["hits"])

    def test_chinese_keywords_are_bigrams(self):
        keys = pg.keywords("分子胶囊的滴定数据")
        self.assertIn("分子", keys)
        self.assertNotIn("分", keys)          # 单字太泛，不收

    def test_stop_words_dropped(self):
        keys = pg.keywords("the binding of the guest")
        self.assertIn("binding", keys)
        self.assertNotIn("the", keys)
        self.assertNotIn("of", keys)


class WindowsAndDedup(unittest.TestCase):
    def test_merge_windows_merges_nearby(self):
        self.assertEqual(pg.merge_windows([100, 400], 700), [{"start": 0, "end": 1100}])
        self.assertEqual(pg.merge_windows([], 700), [])

    def test_merge_windows_keeps_far_apart(self):
        spans = pg.merge_windows([100, 5000], 300)
        self.assertEqual(len(spans), 2)
        self.assertEqual(spans[0], {"start": 0, "end": 400})
        self.assertEqual(spans[1], {"start": 4700, "end": 5300})

    def test_hits_deduplicated_by_position(self):
        text = "capsule capsule capsule"
        found = pg.find_hits(text, "capsule", 60)
        self.assertEqual(found["hits"], [0, 8, 16])

    def test_no_duplicate_passage_text(self):
        parts = pg.pick_passages(TEXT, "S55 titration capsule", {"budget": 1200, "headChars": 300})
        self.assertTrue(parts)
        hits = [p for p in parts if p["kind"] == "hit"]
        self.assertEqual(len(hits), len(set(p["text"] for p in hits)))


class PickPassages(unittest.TestCase):
    def test_short_text_returned_whole(self):
        parts = pg.pick_passages("short text", "anything")
        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["kind"], "head")
        self.assertEqual(parts[0]["text"], "short text")

    def test_empty_text(self):
        self.assertEqual(pg.pick_passages("", "q"), [])
        self.assertEqual(pg.pick_passages(None, "q"), [])

    def test_report_marks_truncated_and_budget(self):
        report = pg.pick_passages_report(TEXT, "S55 titration capsule", {"budget": 1200, "headChars": 300})
        self.assertTrue(report["truncated"])
        self.assertLess(report["used"], len(TEXT))
        self.assertIn("以下是与问题相关的片段", report["text"])
        self.assertGreaterEqual(report["passages"], 1)

    def test_terms_may_be_a_list(self):
        parts = pg.pick_passages(TEXT, ["S55", "titration"], {"budget": 1500, "headChars": 400})
        kinds = [p["kind"] for p in parts]
        self.assertEqual(kinds[0], "head")
        self.assertIn("hit", kinds)

    def test_kinds_and_offsets_are_consistent(self):
        parts = pg.pick_passages(TEXT, "S55", {"budget": 1200, "headChars": 300})
        for part in parts:
            self.assertIn(part["kind"], ("head", "hit", "tail"))
            self.assertEqual(TEXT[part["start"]:part["end"]].strip(), part["text"])
        self.assertLessEqual(parts[0]["start"], parts[0]["end"])

    def test_tail_included_when_requested(self):
        report = pg.pick_passages_report(
            TEXT, "S55", {"budget": 2000, "headChars": 300, "tailChars": 300}
        )
        self.assertIn("以下为文档结尾", report["text"])


if __name__ == "__main__":
    unittest.main()
