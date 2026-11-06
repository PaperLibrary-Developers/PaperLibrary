"""shared.pure.text_repair 的语义单测（移植自 Sci-lib shared/pure/text-repair.js）。

JS 的逐例对照在 test_pure_parity.py；这里钉真实 PDF 文本的修复效果。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.pure import text_repair as tr  # noqa: E402


class LigatureFragments(unittest.TestCase):
    """pdfjs 风格的连字碎片：'e ffi cient'。"""

    def test_mid_word_fragments(self):
        cases = {
            "e ffi cient": "efficient",
            "de fi ne": "define",
            "classi fi cation": "classification",
            "bene fi t": "benefit",
            "di ff erent": "different",
            "o ffi cial": "official",
        }
        for raw, want in cases.items():
            self.assertEqual(tr.repair_ligatures(raw), want, raw)

    def test_common_word_keeps_space(self):
        self.assertEqual(tr.repair_ligatures("the fi nding"), "the finding")
        self.assertEqual(tr.repair_ligatures("we fl ow"), "we flow")

    def test_plain_text_untouched(self):
        for raw in ("hello world", "", None, "cucurbituril host-guest"):
            self.assertEqual(tr.repair_ligatures(raw), "" if raw is None else raw)

    def test_ligature_chars_normalized(self):
        # PyMuPDF 会给真连字字形：ﬁ ﬂ ﬀ ﬃ ﬄ
        # 连字字形在文本层里是独立片段（两侧带空格），先归一成 ASCII 再按碎片规则接词
        self.assertEqual(tr.repair_text("e \ufb03 cient"), "efficient")
        self.assertEqual(tr.repair_text("de \ufb01 ne"), "define")
        self.assertEqual(tr.repair_text("ba \ufb04 e"), "baffle")
        self.assertEqual(tr.repair_text("\ufb02 ow"), "flow")
        self.assertEqual(tr.repair_text("\ufb00 ects"), "ffects")
        self.assertEqual(tr.normalize_ligatures("\ufb01\ufb02\ufb00\ufb03\ufb04"), "fiflffffiffl")


class HyphenBreaks(unittest.TestCase):
    """PDF 行末断词：'con- tainer'；靠文档词表判断是不是真断词。"""

    def test_joins_when_vocab_has_word(self):
        vocab = tr.build_word_set("container molecules and their container chemistry")
        self.assertEqual(tr.join_hyphen_breaks("con- tainer molecules", vocab), "container molecules")

    def test_keeps_real_compound(self):
        vocab = tr.build_word_set("container molecules")
        self.assertEqual(tr.join_hyphen_breaks("hydrogen- bonded", vocab), "hydrogen-bonded")

    def test_no_vocab_means_no_change(self):
        self.assertEqual(tr.join_hyphen_breaks("con- tainer", None), "con- tainer")
        self.assertEqual(tr.join_hyphen_breaks("con- tainer", set()), "con- tainer")

    def test_space_before_hyphen_is_not_a_break(self):
        vocab = tr.build_word_set("container")
        self.assertEqual(tr.join_hyphen_breaks("con - tainer", vocab), "con - tainer")

    def test_build_word_set_min_length(self):
        vocab = tr.build_word_set("The quick brown fox jumps")
        self.assertIn("quick", vocab)
        self.assertNotIn("the", vocab)
        self.assertNotIn("fox", vocab)


class DashesAndSpacing(unittest.TestCase):
    def test_hyphen_spaces_removed(self):
        self.assertEqual(tr.tidy_dashes("well - defined"), "well-defined")
        self.assertEqual(tr.tidy_dashes("16213 - 16219"), "16213-16219")
        self.assertEqual(tr.tidy_dashes("sp -rich"), "sp-rich")

    def test_long_dash_becomes_spaced_minus(self):
        self.assertEqual(tr.tidy_dashes("a\u2014b"), "a \u2212 b")
        self.assertEqual(tr.tidy_dashes("a \u2013 b"), "a \u2212 b")

    def test_spacing_collapse(self):
        self.assertEqual(tr.tidy_spacing("  a   b  "), "a b")
        self.assertEqual(tr.tidy_spacing("a , b ."), "a, b.")
        self.assertEqual(tr.tidy_spacing("( a )"), "(a)")


class RepairPipeline(unittest.TestCase):
    def test_repair_alias_and_pipeline(self):
        self.assertIs(tr.repair, tr.repair_text)
        self.assertEqual(tr.repair_text("e ffi cient  hydrogen - bonded"), "efficient hydrogen-bonded")
        self.assertEqual(tr.repair(""), "")
        self.assertEqual(tr.repair(None), "")

    def test_full_pdf_sentence(self):
        raw = "The  e ffi cient  synthesis of  cucurbit[8]uril  (CB[8])  is  well - known ."
        self.assertEqual(
            tr.repair(raw),
            "The efficient synthesis of cucurbit[8]uril (CB[8]) is well-known.",
        )

    def test_vocab_join_in_pipeline(self):
        vocab = tr.build_word_set("container molecules")
        self.assertEqual(tr.repair("con- tainer  molecules", vocab), "container molecules")


if __name__ == "__main__":
    unittest.main()
