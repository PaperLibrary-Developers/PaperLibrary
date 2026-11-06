"""shared.pure.boolean_query 的语义单测（移植自 Sci-lib shared/pure/boolean-query.js）。

JS 的逐例对照在 test_pure_parity.py（用 node 跑 JS 模块比输出）；这里钉语义与 AST 结构。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.pure import boolean_query as bq  # noqa: E402

FIELDS = ("title", "author", "abstract", "journal", "year", "type",
          "tag", "doi", "keyword", "note", "fulltext")


class ModulesImportable(unittest.TestCase):
    def test_all_pure_modules_import(self):
        from shared.pure import abstract, abstract_extract, boolean_query, passages, text_repair  # noqa: F401
        self.assertTrue(callable(boolean_query.parse))
        self.assertTrue(callable(passages.pick_passages))
        self.assertTrue(callable(text_repair.repair))
        self.assertTrue(callable(abstract.choose_abstract))
        self.assertTrue(callable(abstract_extract.extract_abstract_from_lines))


class AstShape(unittest.TestCase):
    def test_and_or_not_nodes(self):
        ast = bq.parse("a AND NOT b")
        self.assertEqual(ast["type"], "and")
        self.assertEqual(ast["left"], {"type": "term", "value": "a", "phrase": False, "field": ""})
        self.assertEqual(ast["right"]["type"], "not")
        self.assertEqual(ast["right"]["child"], {"type": "term", "value": "b", "phrase": False, "field": ""})

    def test_implicit_and_and_parens(self):
        ast = bq.parse("(a OR b) AND c")
        self.assertEqual(ast["type"], "and")
        self.assertEqual(ast["left"]["type"], "or")
        self.assertEqual(bq.parse("a b")["type"], "and")

    def test_empty_and_noise(self):
        self.assertIsNone(bq.parse(""))
        self.assertIsNone(bq.parse("   "))
        self.assertTrue(bq.evaluate(None, "anything"))

    def test_bundle_keys_are_node_identical(self):
        bundle = bq.parse_query("cucurbituril NOT cyclodextrin")
        self.assertEqual(
            sorted(bundle),
            ["allTerms", "ast", "error", "fieldTerms", "hasBoolean", "needsEvaluator", "negatedTerms", "terms"],
        )
        self.assertEqual(bundle["terms"], ["cucurbituril"])
        self.assertEqual(bundle["negatedTerms"], ["cyclodextrin"])
        self.assertTrue(bundle["hasBoolean"])     # NOT 也算用了布尔语法（与 JS 一致）


class Operators(unittest.TestCase):
    def test_english_operators(self):
        self.assertTrue(bq.matches("a b", "a AND b"))
        self.assertFalse(bq.matches("a c", "a AND b"))
        self.assertTrue(bq.matches("b", "a OR b"))
        self.assertFalse(bq.matches("c", "a OR b"))
        self.assertTrue(bq.matches("a c", "a NOT b"))
        self.assertFalse(bq.matches("a b", "a NOT b"))

    def test_symbol_operators(self):
        self.assertTrue(bq.matches("a b", "a && b"))
        self.assertTrue(bq.matches("b", "a || b"))
        self.assertTrue(bq.matches("a c", "a !b"))

    def test_chinese_operators(self):
        self.assertTrue(bq.matches("a b", "a 与 b"))
        self.assertTrue(bq.matches("b", "a 或 b"))
        self.assertTrue(bq.matches("a c", "a 非 b"))
        self.assertTrue(bq.has_boolean("a 与 b"))

    def test_parentheses_change_priority(self):
        self.assertTrue(bq.matches("b c", "(a OR b) AND c"))
        self.assertFalse(bq.matches("b d", "(a OR b) AND c"))
        self.assertTrue(bq.matches("a d", "a OR (b AND c)"))

    def test_phrases_quote_styles(self):
        self.assertTrue(bq.matches("host  guest", '"host guest"'))
        self.assertFalse(bq.matches("host and guest", '"host guest"'))
        self.assertTrue(bq.matches("分子胶囊", "“分子胶囊”"))
        self.assertTrue(bq.matches("分子  胶囊", "“分子 胶囊”"))   # 短语内的空白可浮动
        self.assertFalse(bq.matches("胶囊分子", "“分子胶囊”"))

    def test_lowercase_and_is_a_word(self):
        # AND/OR/NOT 只有大写才算布尔（与 JS 一致）
        self.assertFalse(bq.has_boolean("A and b"))
        self.assertEqual(bq.parse("A and b")["type"], "and")


class FieldTerms(unittest.TestCase):
    def test_all_eleven_fields(self):
        for field in FIELDS:
            ast = bq.parse(field + ":x")
            self.assertEqual(ast["type"], "term", field)
            self.assertEqual(ast["field"], field)
            self.assertEqual(ast["value"], "x")

    def test_aliases_collapse(self):
        self.assertEqual(bq.normalize_field("venue"), "journal")
        self.assertEqual(bq.normalize_field("authors"), "author")
        self.assertEqual(bq.normalize_field("摘要"), "abstract")
        self.assertEqual(bq.normalize_field("全文"), "fulltext")
        self.assertEqual(bq.normalize_field("unknown"), "")

    def test_field_phrase_and_empty_colon(self):
        ast = bq.parse('title:"molecular capsule"')
        self.assertEqual((ast["value"], ast["phrase"], ast["field"]), ("molecular capsule", True, "title"))
        self.assertIsNone(bq.parse("title:"))   # 冒号后没内容：整项忽略

    def test_doi_and_url_are_not_fields(self):
        self.assertFalse(bq.has_field_syntax("http://example.com"))
        ast = bq.parse("doi:10.1021/jacs.9b01234")
        self.assertEqual((ast["field"], ast["value"]), ("doi", "10.1021/jacs.9b01234"))

    def test_field_terms_not_in_positive_terms(self):
        ast = bq.parse("title:capsule year:2013-2018")
        self.assertEqual(bq.positive_terms(ast), [])
        self.assertEqual(bq.all_positive_terms(ast), ["capsule", "2013-2018"])
        self.assertTrue(bq.has_field_terms(ast))
        self.assertEqual(bq.field_terms(ast), [{"field": "title", "value": "capsule"},
                                               {"field": "year", "value": "2013-2018"}])

    def test_positive_and_negated_terms(self):
        ast = bq.parse("cucurbituril AND host NOT cyclodextrin")
        self.assertEqual(bq.positive_terms(ast), ["cucurbituril", "host"])
        self.assertEqual(bq.negated_terms(ast), ["cyclodextrin"])
        self.assertFalse(bq.has_field_terms(ast))
        self.assertFalse(bq.needs_evaluator("cucurbituril host"))
        self.assertTrue(bq.needs_evaluator("a OR b"))
        self.assertTrue(bq.needs_evaluator("title:x"))


class YearQueries(unittest.TestCase):
    def assertYear(self, spec, year, expected):
        ast = bq.parse("year:" + spec)
        self.assertEqual(
            bq.evaluate(ast, lambda: "unused", {"field_text": lambda _f: str(year)}),
            expected,
            spec + " vs " + str(year),
        )

    def test_single_closed_open_ranges(self):
        self.assertYear("2013", 2013, True)
        self.assertYear("2013", 2014, False)
        self.assertYear("2013-2018", 2015, True)
        self.assertYear("2013-2018", 2020, False)
        self.assertYear("2013-", 2020, True)
        self.assertYear("-2018", 2010, True)
        self.assertYear("-2018", 2020, False)

    def test_paper_level_year_matching(self):
        paper = {"title": "Cucurbituril capsules", "year": 2015}
        self.assertTrue(bq.matches(paper, "title:cucurbituril AND year:2013-2018"))
        self.assertFalse(bq.matches(paper, "year:2020"))


class EvaluateEntrypoints(unittest.TestCase):
    def test_haystack_forms(self):
        ast = bq.parse("a AND b")
        self.assertTrue(bq.evaluate(ast, "a b"))
        self.assertTrue(bq.evaluate(ast, lambda: "a b"))          # 0 参文本提供者
        self.assertFalse(bq.evaluate(ast, lambda: "a c"))

    def test_matcher_two_args(self):
        seen = []

        def matcher(field, value):
            seen.append((field, value))
            return (field, value) == ("title", "capsule")

        self.assertTrue(bq.evaluate(bq.parse("title:capsule"), matcher))
        self.assertEqual(seen, [("title", "capsule")])

    def test_matcher_single_node(self):
        seen = []

        def matcher(node):
            seen.append(node["value"])
            return node["value"] == "a"

        self.assertFalse(bq.evaluate(bq.parse("a AND b"), matcher))
        self.assertEqual(seen, ["a", "b"])

    def test_paper_field_extraction(self):
        paper = {
            "title": "Molecular capsules",
            "authors": ["Nitschke J.", "Smith A."],
            "summary": "Host guest binding inside the cavity.",
            "journal": "JACS",
            "year": 2019,
            "tags": ["cucurbituril"],
        }
        self.assertEqual(bq.paper_field(paper, "author"), "Nitschke J. Smith A.")
        self.assertIn("cucurbituril", bq.paper_text(paper))
        self.assertTrue(bq.matches(paper, "abstract:host guest"))
        self.assertFalse(bq.matches(paper, "abstract:cyclodextrin"))


class ExternalSources(unittest.TestCase):
    def test_plain_query_keeps_field_words(self):
        self.assertEqual(bq.plain_query("title:cucurbituril AND NOT cyclodextrin"), "cucurbituril")
        self.assertEqual(bq.plain_query("a AND b"), "a b")
        self.assertEqual(bq.plain_query("AND OR NOT"), "")

    def test_arxiv_query(self):
        self.assertEqual(bq.to_arxiv_query("a AND b"), 'all:"a"+AND+all:"b"')
        self.assertEqual(bq.to_arxiv_query("a NOT b"), 'all:"a"+ANDNOT+all:"b"')
        self.assertIn("all:", bq.to_arxiv_query("title:capsule"))

    def test_support_matrix(self):
        for source in ("openalex", "europepmc", "arxiv"):
            self.assertTrue(bq.supports_boolean(source), source)
        for source in ("crossref", "semanticscholar", "", None):
            self.assertFalse(bq.supports_boolean(source), source)

    def test_filter_items(self):
        items = [{"t": "a c"}, {"t": "b"}]
        self.assertEqual(len(bq.filter_items(items, "plain keywords", lambda it: it["t"])), 2)
        self.assertEqual(bq.filter_items(items, "a AND c", lambda it: it["t"]), [{"t": "a c"}])
        fields = [{"t": "", "f": {"title": "molecular capsule"}}, {"t": "capsule", "f": {"title": "x"}}]
        kept = bq.filter_items(fields, "title:capsule", lambda it: it["t"],
                               {"field_text": lambda it, f: it["f"].get(f, "")})
        self.assertEqual(kept, [{"t": "", "f": {"title": "molecular capsule"}}])


if __name__ == "__main__":
    unittest.main()
