"""shared.pure.abstract / abstract_extract 的语义单测。

移植自 Sci-lib shared/pure/{abstract,abstract-extract}.js；JS 逐例对照在 test_pure_parity.py。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.pure import abstract as ab  # noqa: E402
from shared.pure import abstract_extract as axe  # noqa: E402

LONG = "Binding of the guest inside the cavity was confirmed by NMR and ITC. " * 5
SHORT = "Short note."
CHROME = "ADVERTISEMENT RETURN TO ISSUE Share on Facebook Download PDF"


class CleanSource(unittest.TestCase):
    def test_strips_jats_tags_and_entities(self):
        raw = "<jats:p>Host &amp; guest &lt;binding&gt;</jats:p>"
        out = ab.clean_source_abstract(raw)
        self.assertIn("Host & guest <binding>", out)
        self.assertNotIn("<jats:p>", out)
        self.assertNotIn("&amp;", out)

    def test_drops_publisher_chrome(self):
        self.assertTrue(ab.looks_like_chrome(CHROME))
        self.assertEqual(ab.clean_source_abstract(CHROME), "")
        self.assertFalse(ab.looks_like_chrome("A normal abstract about chemistry."))
        self.assertEqual(ab.clean_source_abstract(""), "")
        self.assertEqual(ab.clean_source_abstract(None), "")

    def test_strips_lead_label_and_repairs_text(self):
        out = ab.clean_source_abstract("Abstract: The e ffi cient synthesis is well - defined.")
        self.assertTrue(out.startswith("The efficient synthesis"))
        self.assertIn("well-defined", out)


class ThreeTierFallback(unittest.TestCase):
    def test_crossref_wins_when_long_enough(self):
        picked = ab.choose_abstract({"crossref": LONG, "pdf": {"text": LONG, "method": "heading"}})
        self.assertEqual(picked["source"], "crossref")
        self.assertNotIn("method", picked)

    def test_pdf_used_when_crossref_missing(self):
        picked = ab.choose_abstract({"pdf": {"text": LONG, "method": "heading"}})
        self.assertEqual(picked["source"], "pdf")
        self.assertEqual(picked["method"], "heading")
        self.assertEqual(ab.describe_source("pdf", "heading"), "PDF（带摘要标题的那一段）")

    def test_openalex_is_last_resort(self):
        picked = ab.choose_abstract({"crossref": SHORT, "pdf": {"text": SHORT, "method": "position"},
                                     "openalex": LONG})
        self.assertEqual(picked["source"], "openalex")

    def test_chrome_crossref_never_used(self):
        picked = ab.choose_abstract({"crossref": CHROME, "openalex": LONG})
        self.assertEqual(picked["source"], "openalex")
        self.assertEqual(ab.choose_abstract({"crossref": CHROME}), None)

    def test_short_sources_kept_as_last_resort(self):
        self.assertEqual(ab.choose_abstract({"crossref": SHORT})["source"], "crossref")
        picked = ab.choose_abstract({"pdf": {"text": SHORT, "method": "position"}})
        self.assertEqual(picked, {"text": SHORT, "source": "pdf", "method": "position"})

    def test_nothing_available(self):
        self.assertIsNone(ab.choose_abstract({}))
        self.assertIsNone(ab.choose_abstract(None))

    def test_fallback_alias_and_describe(self):
        self.assertIs(ab.build_fallback, ab.choose_abstract)
        self.assertEqual(ab.describe_source("crossref"), "Crossref")
        self.assertEqual(ab.describe_source("openalex"), "OpenAlex")
        self.assertEqual(ab.describe_source(""), "手工填写")
        self.assertEqual(ab.MIN_USEFUL, 200)


HEADING_LINES = [
    "Journal of the American Chemical Society",
    "Cucurbituril-Based Molecular Capsules",
    "John Smith, Anna Lee and Jonathan R. Nitschke",
    "Department of Chemistry, University of Cambridge",
    "ABSTRACT: The binding of guests inside the cavity of cucurbit[8]uril was studied by NMR "
    "spectroscopy and isothermal titration calorimetry. Association constants reach 10^9 M-1.",
    "1 Introduction",
    "Host guest chemistry is a broad field.",
]

POSITION_LINES = [
    "nature chemistry",
    "Article",
    "https://doi.org/10.1038/s41557-019-0123-4",
    "Supramolecular capsules assembled from cucurbituril",
    "Anna Lee, John Smith",
    "Department of Chemistry, Cambridge, UK",
    "The self-assembly of molecular capsules from cucurbit[8]uril and viologen guests was "
    "investigated in water. The capsule forms quantitatively at millimolar concentrations.",
    "Results and discussion",
    "The binding constant was determined by titration.",
]


class ExtractFromLines(unittest.TestCase):
    def test_heading_method(self):
        got = axe.extract_abstract_from_lines(HEADING_LINES, {"title": "Cucurbituril-Based Molecular Capsules"})
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "heading")
        self.assertIn("cucurbit[8]uril", got["text"])
        self.assertNotIn("Introduction", got["text"])
        self.assertEqual(got["startLine"], 4)

    def test_position_method(self):
        got = axe.extract_abstract_from_lines(
            POSITION_LINES, {"title": "Supramolecular capsules assembled from cucurbituril"}
        )
        self.assertIsNotNone(got)
        self.assertEqual(got["method"], "position")
        self.assertTrue(got["text"].startswith("The self-assembly"))

    def test_returns_none_when_nothing_found(self):
        self.assertIsNone(axe.extract_abstract_from_lines([], None))
        self.assertIsNone(axe.extract_abstract_from_lines(["just one line"], None))
        self.assertIsNone(axe.extract_abstract_from_lines(None, {}))
        self.assertIsNone(axe.extract_abstract_from_lines(["INTRODUCTION", "1 Introduction"], None))

    def test_clean_abstract_removes_watermarks(self):
        raw = ("e ffi cient catalyst was used. Downloaded via ETH Zurich. "
               "https://doi.org/10.1021/jacs.9b01234")
        out = axe.clean_abstract(raw)
        self.assertIn("efficient catalyst", out)
        self.assertNotIn("Downloaded via", out)
        self.assertNotIn("doi.org", out)

    def test_trim_to_limit_cuts_at_sentence(self):
        text = "First sentence here. Second sentence goes on. Third sentence is longer than needed."
        trimmed = axe.trim_to_limit(text, 30)
        self.assertTrue(trimmed.endswith("."))
        self.assertLessEqual(len(trimmed), 30)
        self.assertEqual(axe.trim_to_limit(text, 500), text)
        self.assertTrue(axe.trim_to_limit("no sentence break anywhere at all", 10).endswith("…"))

    def test_exports_used_by_callers(self):
        for name in ("extract_abstract_from_lines", "clean_abstract", "looks_like_prose",
                     "looks_like_author_line", "looks_like_continuation", "looks_like_heading"):
            self.assertTrue(callable(getattr(axe, name)), name)
        self.assertTrue(axe.looks_like_prose("The self-assembly of molecular capsules was investigated."))
        self.assertTrue(axe.looks_like_author_line("Anna Lee, John Smith"))
        self.assertFalse(axe.looks_like_author_line("The host guest chemistry was studied in detail."))
        self.assertTrue(axe.looks_like_continuation("and the binding constant,"))
        self.assertTrue(axe.looks_like_heading("Results and discussion"))
        self.assertFalse(axe.looks_like_heading("Results and discussion."))


if __name__ == "__main__":
    unittest.main()
