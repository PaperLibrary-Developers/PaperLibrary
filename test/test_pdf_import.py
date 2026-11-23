"""T3 单测：PDF 预检编排（domain/pdf_import）与解析规则（adapters/pdf_rules）。

对应契约：pdf.extract_metadata(path) → Parsed
（Node 参考：server/domain/pdf-import.js + server/adapters/pdf-parse-rules.js）。
全程离线：题录源 / OpenAlex 摘要都注入假函数，不发起任何网络请求。
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
import time
import unittest
from collections import deque
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pymupdf

from paperlib.adapters import pdf_rules, pdf_text
from paperlib.domain import pdf_import

ROOT = Path(__file__).resolve().parent.parent
CJK_FONT = r"C:\Windows\Fonts\msyh.ttc"
SAMPLE_TITLE = "Porous Metal-Organic Cages for Gas Separation"
SAMPLE_DOI = "10.1021/jacs.3b12345"


def make_sample_pdf(path, *, chinese=True, title=SAMPLE_TITLE):
    """一页样例 PDF（英文题录 + 可选中文标题行），PyMuPDF 生成，不依赖外部文件。"""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 60), "Chemical Society Reviews", fontsize=9)
    page.insert_text((72, 96), title, fontsize=15)
    page.insert_text((72, 124), "Khaleel I. Assaf and Werner M. Nau", fontsize=10)
    page.insert_text((72, 150), "Department of Chemistry, University of Example", fontsize=8)
    page.insert_text((72, 190),
                     "Abstract: Porous metal-organic cages were synthesized and characterized "
                     "by NMR spectroscopy and gas adsorption measurements.", fontsize=9)
    page.insert_text((72, 260), "doi: " + SAMPLE_DOI, fontsize=9)
    if chinese and os.path.isfile(CJK_FONT):
        page.insert_text((72, 320), "金属有机笼：设计、合成与应用", fontsize=13,
                         fontname="cjk", fontfile=CJK_FONT)
        page.insert_text((72, 350), "摘要：本文报道了一种多孔金属有机笼的合成与表征。", fontsize=10,
                         fontname="cjk", fontfile=CJK_FONT)
    doc.set_metadata({
        "title": title,
        "author": "Khaleel I. Assaf; Werner M. Nau",
        "creationDate": "D:20230415120000",
    })
    doc.subset_fonts()  # 不子集化会整份嵌入 msyh.ttc（~19MB）
    doc.save(str(path))
    doc.close()


def fake_lookup(paper=None, error=None, calls=None):
    def lookup(doi):
        if calls is not None:
            calls.append(doi)
        if error:
            raise error
        return dict(paper or {})
    return lookup


class PdfTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t3-pdf-")
        cls.pdf = os.path.join(cls.tmp, "sample.pdf")
        make_sample_pdf(cls.pdf)

    def test_extract_text_pages_and_content(self):
        result = pdf_text.extract_text(self.pdf)
        self.assertEqual(result["pages"], 1)
        self.assertIn("Metal-Organic Cages", result["text"])
        self.assertIn("金属有机笼", result["text"])
        self.assertEqual(len(result["perPage"]), 1)
        self.assertGreater(result["perPage"][0]["chars"], 100)

    def test_extract_head_lines_and_meta(self):
        head = pdf_text.extract_head(self.pdf, 3)
        self.assertEqual(head["pages"], 1)
        self.assertEqual(head["headPages"], 1)
        self.assertEqual(len(head["lines"]), len(head["lineY"]))
        self.assertIn(SAMPLE_TITLE, head["lines"])
        self.assertEqual(head["info"]["Title"], SAMPLE_TITLE)
        self.assertIn("Assaf", head["info"]["Author"])

    def test_parse_xmp_flattens_dc_and_prism(self):
        xml = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
            '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/">'
            '<rdf:Description rdf:about="">'
            '<prism:doi>10.1021/jacs.3b12345</prism:doi>'
            '<prism:publicationDate>2023-04-15</prism:publicationDate>'
            '<dc:creator><rdf:Seq><rdf:li>Khaleel I. Assaf</rdf:li>'
            '<rdf:li>Werner M. Nau</rdf:li></rdf:Seq></dc:creator>'
            '</rdf:Description></rdf:RDF></x:xmpmeta>'
        )
        xmp = pdf_text.parse_xmp(xml)
        self.assertEqual(xmp["prism:doi"], "10.1021/jacs.3b12345")
        self.assertEqual(xmp["dc:creator"], ["Khaleel I. Assaf", "Werner M. Nau"])
        self.assertEqual(pdf_rules.doi_from_meta(xmp), "10.1021/jacs.3b12345")
        self.assertEqual(pdf_rules.year_from_meta({}, xmp), 2023)
        self.assertIsNone(pdf_text.parse_xmp(""))
        self.assertIsNone(pdf_text.parse_xmp("not xml"))

    def test_extract_page(self):
        page = pdf_text.extract_page(self.pdf, 1)
        self.assertEqual(page["page"], 1)
        self.assertIn("Metal-Organic Cages", page["text"])

    def test_extract_repairs_reversible_mojibake(self):
        with pymupdf.open() as doc:
            page = doc.new_page()
            page.insert_text((72, 80), "FranÃ§ais: spectroscopie à haute résolution")
            raw = doc.tobytes()
        result = pdf_text.extract_text(raw)
        self.assertIn("Français", result["text"])
        self.assertIn("résolution", result["text"])
        self.assertEqual(result["unreadablePages"], [])

    def test_unmapped_page_needs_ocr(self):
        from paperlib.adapters.pdf import FullText

        with patch.object(pdf_text, "page_lines", return_value=["\ufffd\ue000\ufffd" * 20]), \
             patch.object(pdf_text, "_ocr_settings", return_value=None):
            with self.assertRaisesRegex(ValueError, "第 1 页.*识图识别全文"):
                FullText(None).extract_from_file(self.pdf)

        with patch.object(pdf_text, "page_lines", return_value=["\ufffd" * 30]), \
             patch.object(pdf_text, "_recover_page", return_value="Recovered scientific text from page one."):
            result = pdf_text.extract_text(self.pdf)
        self.assertIn("Recovered scientific text", result["text"])
        self.assertEqual(result["unreadablePages"], [])


class PdfRulesTest(unittest.TestCase):
    def test_extract_dois_normalizes_and_dedupes(self):
        lines = ["doi: 10.1021/jacs.3b12345.", "see 10.1021/jacs.3b12345", "https://doi.org/10.1002/anie.202315020"]
        self.assertEqual(pdf_rules.extract_dois(lines),
                         ["10.1021/jacs.3b12345", "10.1002/anie.202315020"])

    def test_extract_dois_across_lines_repairs_wrapped_doi(self):
        lines = ["https://doi.org/10.1002/anie.2023", "15020", "1 Introduction"]
        self.assertEqual(pdf_rules.extract_dois_across_lines(lines), ["10.1002/anie.202315020"])

    def test_extract_dois_across_lines_ignores_prose_continuation(self):
        self.assertEqual(pdf_rules.extract_dois_across_lines(["doi:10.1002/anie.2023", "1 Introduction"]), [])

    def test_title_candidates_pick_title_and_journal(self):
        lines = [
            "Chemical Society Reviews",
            SAMPLE_TITLE,
            "Khaleel I. Assaf and Werner M. Nau",
            "Department of Chemistry, University of Example",
            "Abstract: Porous metal-organic cages were synthesized.",
        ]
        picked = pdf_rules.title_candidates(lines)
        self.assertEqual(picked["title"], SAMPLE_TITLE)
        self.assertNotIn("Khaleel I. Assaf and Werner M. Nau", picked["candidates"])
        self.assertEqual(picked["journalCandidates"], ["Chemical Society Reviews"])

    def test_author_line_helpers(self):
        self.assertTrue(pdf_rules.looks_like_name_list("Khaleel I. Assaf and Werner M. Nau"))
        self.assertEqual(pdf_rules.strip_author_run("Khaleel I. Assaf and Werner M. Nau"), "")
        self.assertTrue(pdf_rules.looks_like_author_line("A. B. Smith, C. D. Jones"))
        self.assertFalse(pdf_rules.looks_like_author_line(SAMPLE_TITLE))

    def test_usable_embedded_title(self):
        self.assertTrue(pdf_rules.usable_embedded_title(SAMPLE_TITLE))
        for bad in ("untitled", "ao8b02734 1..5", "shu_final_v3.pdf", "x"):
            self.assertFalse(pdf_rules.usable_embedded_title(bad), bad)

    def test_meta_helpers(self):
        info = {"Author": "Khaleel I. Assaf; Werner M. Nau", "CreationDate": "D:20230415120000"}
        self.assertEqual(pdf_rules.authors_from_meta(info, None),
                         ["Khaleel I. Assaf", "Werner M. Nau"])
        self.assertEqual(pdf_rules.authors_from_meta({}, {"dc:creator": ["Alice B", "Bob C"]}),
                         ["Alice B", "Bob C"])
        self.assertEqual(pdf_rules.year_from_meta(info, {"prism:publicationDate": "2021-05-01"}), 2021)
        self.assertEqual(pdf_rules.year_from_meta(info, None), 2023)
        self.assertIsNone(pdf_rules.year_from_meta({}, None))
        self.assertEqual(pdf_rules.doi_from_meta({"prism:doi": "10.1021/jacs.3b12345"}),
                         "10.1021/jacs.3b12345")
        self.assertEqual(pdf_rules.doi_from_meta(None), "")

    def test_title_similarity_and_file_name(self):
        self.assertAlmostEqual(
            pdf_rules.title_similarity("Metal-Organic Cages for Gas Separation",
                                        "Metal Organic Cages for Gas Separation"), 1.0)
        self.assertEqual(pdf_rules.title_similarity("alpha beta gamma", "delta epsilon zeta"), 0.0)
        self.assertEqual(pdf_rules.title_from_file_name("abcdef0123456789-Metal_Organic_Cages.pdf"),
                         "Metal Organic Cages")
        self.assertTrue(pdf_rules.is_plausible_title(SAMPLE_TITLE))
        self.assertFalse(pdf_rules.is_plausible_title("Short title"))


class ParsePdfTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t3-parse-")
        cls.pdf = os.path.join(cls.tmp, "sample.pdf")
        make_sample_pdf(cls.pdf)

    def test_parse_offline_degrades_to_pdf_facts(self):
        parsed = pdf_import.parse_pdf(None, self.pdf, {"lookup_doi": fake_lookup(error=ConnectionError("offline"))})
        self.assertTrue(parsed["ok"])
        self.assertEqual(parsed["pages"], 1)
        self.assertEqual(parsed["doi"], SAMPLE_DOI)
        self.assertEqual(parsed["doiSource"], "text")
        self.assertFalse(parsed["doiVerified"])
        self.assertIn("Crossref", parsed["warning"])
        self.assertEqual(parsed["pdfTitle"], SAMPLE_TITLE)

    def test_parse_uses_first_verified_doi(self):
        calls = []
        crossref = {"title": "Crossref Title", "doi": SAMPLE_DOI, "authors": ["A B"],
                    "year": 2023, "venue": "Chem. Soc. Rev.", "summary": "x" * 80}
        parsed = pdf_import.parse_pdf(None, self.pdf, {"lookup_doi": fake_lookup(crossref, calls=calls)})
        self.assertTrue(parsed["doiVerified"])
        self.assertEqual(parsed["crossref"]["title"], "Crossref Title")
        self.assertEqual(calls, [SAMPLE_DOI])

    def test_parse_pdf_without_doi_warns(self):
        path = os.path.join(self.tmp, "no-doi.pdf")
        # 一份没有 DOI 行的 PDF
        doc = pymupdf.open()
        page = doc.new_page()
        page.insert_text((72, 96), "Porous Metal-Organic Cages for Gas Separation", fontsize=15)
        page.insert_text((72, 124), "Khaleel I. Assaf", fontsize=10)
        doc.save(path)
        doc.close()
        parsed = pdf_import.parse_pdf(None, path, {"lookup_doi": fake_lookup(error=ConnectionError("x"))})
        self.assertEqual(parsed["doi"], "")
        self.assertIn("没能在 PDF 里找到 DOI", parsed["warning"])

    def test_parsed_for_api_shape(self):
        parsed = pdf_import.parse_pdf(None, self.pdf, {"lookup_doi": fake_lookup(error=ConnectionError("x"))})
        api = pdf_import.parsed_for_api(parsed)
        self.assertEqual(set(api), {"ok", "pages", "doi", "doiSource", "doiVerified", "pdfTitle",
                                    "titleCandidates", "journalCandidates", "metaAuthors", "warning"})

    def test_fake_services_for_preflight(self):
        services = SimpleNamespace(
            db=SimpleNamespace(list_papers=lambda: []),
            external=SimpleNamespace(),
        )
        result = pdf_import.pdf_preflight(services, self.pdf, "sample.pdf", "3",
                                          {"lookup_doi": fake_lookup(error=ConnectionError("x")),
                                           "fetch_openalex_abstract": lambda doi: ""})
        self.assertEqual(result["tempId"], "3")
        self.assertEqual(result["parsed"]["doi"], SAMPLE_DOI)
        self.assertIn("duplicate", result["draft"])
        self.assertIn("summarySource", result["draft"])


class DraftFromParsedTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t3-draft-")
        cls.pdf = os.path.join(cls.tmp, "sample.pdf")
        make_sample_pdf(cls.pdf)

    def _services(self, local=None):
        return SimpleNamespace(
            db=SimpleNamespace(list_papers=lambda: list(local or [])),
            external=SimpleNamespace(),
        )

    def test_draft_from_crossref(self):
        parsed = {
            "file": "sample.pdf", "crossref": {
                "title": "Crossref Title", "authors": ["A B", "C D"], "year": 2022,
                "type": "journal", "venue": "Chem. Soc. Rev.", "doi": SAMPLE_DOI,
                "summary": "Crossref abstract " * 5, "keywords": ["cages"],
            }, "doi": SAMPLE_DOI,
        }
        services = self._services()
        draft = pdf_import.draft_from_parsed(services, parsed, {"localPapers": []},
                                            {"fetch_openalex_abstract": lambda doi: ""})
        self.assertEqual(draft["title"], "Crossref Title")
        self.assertEqual(draft["url"], "https://doi.org/" + SAMPLE_DOI)
        self.assertEqual(draft["summarySource"], "crossref")
        self.assertTrue(draft["summarySourceLabel"])
        self.assertIsNone(draft["duplicate"])

    def test_draft_from_pdf_facts_and_duplicate_by_doi(self):
        parsed = pdf_import.parse_pdf(None, self.pdf, {"lookup_doi": fake_lookup(error=ConnectionError("x"))})
        local = [{"id": "p1", "title": "Other", "doi": SAMPLE_DOI, "pdfFile": "p1-x.pdf"}]
        draft = pdf_import.draft_from_parsed(self._services(local), parsed, {"localPapers": local},
                                            {"fetch_openalex_abstract": lambda doi: ""})
        self.assertEqual(draft["title"], SAMPLE_TITLE)
        self.assertEqual(draft["doi"], SAMPLE_DOI)
        self.assertEqual(draft["type"], "journal")
        self.assertEqual(draft["duplicate"], {"id": "p1", "title": "Other", "hasPdf": True})

    def test_draft_duplicate_by_title_similarity(self):
        parsed = {"file": "x.pdf", "crossref": {"title": SAMPLE_TITLE, "doi": ""}, "doi": ""}
        local = [{"id": "p2", "title": SAMPLE_TITLE.lower(), "pdfFile": ""}]
        draft = pdf_import.draft_from_parsed(self._services(local), parsed, {"localPapers": local},
                                            {"fetch_openalex_abstract": lambda doi: ""})
        self.assertEqual(draft["duplicate"]["id"], "p2")
        self.assertFalse(draft["duplicate"]["hasPdf"])

    def test_draft_falls_back_to_file_name(self):
        parsed = {"file": "abcdef0123456789-Metal_Organic_Cages.pdf", "crossref": None,
                  "doi": "", "titleCandidates": [], "metaAuthors": [], "year": None}
        draft = pdf_import.draft_from_parsed(self._services(), parsed, {"localPapers": []},
                                            {"fetch_openalex_abstract": lambda doi: ""})
        self.assertEqual(draft["title"], "Metal Organic Cages")

    def test_draft_summary_uses_openalex_when_nothing_else(self):
        parsed = {"file": "x.pdf", "crossref": None, "doi": SAMPLE_DOI,
                  "titleCandidates": [SAMPLE_TITLE], "metaAuthors": [], "year": None}
        long_abstract = "<p>" + "OpenAlex reports the gas adsorption behaviour of the cages. " * 6 + "</p>"
        draft = pdf_import.draft_from_parsed(
            self._services(), parsed, {"localPapers": []},
            {"fetch_openalex_abstract": lambda doi: long_abstract})
        self.assertEqual(draft["summarySource"], "openalex")
        self.assertNotIn("<p>", draft["summary"])
        self.assertGreater(len(draft["summary"]), 200)

    def test_draft_survives_openalex_failure(self):
        def boom(doi):
            raise RuntimeError("network down")

        parsed = {"file": "x.pdf", "crossref": None, "doi": SAMPLE_DOI,
                  "titleCandidates": [SAMPLE_TITLE], "metaAuthors": [], "year": None}
        draft = pdf_import.draft_from_parsed(self._services(), parsed, {"localPapers": []},
                                            {"fetch_openalex_abstract": boom})
        self.assertEqual(draft["summary"], "")


class CleanupTest(unittest.TestCase):
    def test_cleanup_removes_old_batches_only(self):
        tmp = tempfile.mkdtemp(prefix="t3-clean-")
        services = SimpleNamespace(runtime=SimpleNamespace(data_dir=tmp))
        tmp_import = os.path.join(tmp, "tmp-import")
        os.makedirs(os.path.join(tmp_import, "oldbatch"))
        fresh = os.path.join(tmp_import, "freshbatch")
        os.makedirs(fresh)
        stale = os.path.join(tmp_import, "oldbatch")
        os.utime(stale, (0, 0))
        result = pdf_import.cleanup_import_tmp(services)
        self.assertEqual(result["removed"], 1)
        self.assertTrue(os.path.isdir(fresh))
        self.assertFalse(os.path.exists(stale))

    def test_cleanup_tolerates_missing_dir(self):
        tmp = tempfile.mkdtemp(prefix="t3-clean2-")
        services = SimpleNamespace(runtime=SimpleNamespace(data_dir=tmp))
        self.assertEqual(pdf_import.cleanup_import_tmp(services), {"removed": 0})


class FulltextSearchTest(unittest.TestCase):
    """全文检索（list/facets 的 scope=fulltext 依赖 FullText.search / excerpts）。"""

    def setUp(self):
        from paperlib.config import Config
        from paperlib.data.store import Store
        from paperlib.services import create_services

        tmp = tempfile.TemporaryDirectory(prefix="t3-search-")
        self.addCleanup(tmp.cleanup)
        config = Config(root=ROOT, data_dir=Path(tmp.name), port=4392)
        config.started_at = time.time()
        self.store = Store(config.data_dir)
        self.addCleanup(self.store.repo.close)
        self.store.load()
        self.services = create_services(config, self.store)
        self.ids = {}
        for name, text in (
            ("cage", "本文研究超分子化学与金属有机笼的自组装行为"),
            ("imaging", "超分辨率成像技术用于材料表征"),
            ("gas", "金属有机笼的气体分离性能研究"),
            ("mof", "metal organic frameworks for gas uptake"),
        ):
            paper = self.services.library.create({"title": name})
            self.store.set_text(paper["id"], text)
            self.ids[name] = paper["id"]

    def hits(self, terms, limit=200):
        return [hit["id"] for hit in self.services.fulltext.search(terms, limit)]

    def test_chinese_whole_word_requires_all_bigrams(self):
        # 「超分子」的 token 是 超/超分/分/分子/子：只出现「超分」的正文不应命中
        self.assertEqual(self.hits("超分子"), [self.ids["cage"]])
        self.assertIn(self.ids["gas"], self.hits("气体分离"))
        self.assertNotIn(self.ids["imaging"], self.hits("气体分离"))

    def test_latin_terms_and_chinese_union(self):
        # 一个 term 的 token 全中才算；不同 term 之间是并集（与 Node 的全文粗筛一致）
        self.assertEqual(self.hits("metal organic frameworks"), [self.ids["mof"]])
        self.assertEqual(self.hits("cages"), [])
        self.assertIn(self.ids["mof"], self.hits("metal organic cages"))
        self.assertEqual(sorted(self.hits("金属有机笼")),
                         sorted([self.ids["cage"], self.ids["gas"]]))

    def test_attachment_text_not_searchable(self):
        self.store.set_text("att-" + self.ids["cage"] + "-SI.txt", "超分子补充材料")
        self.assertTrue(all(not str(hit).startswith("att-") for hit in self.hits("超分子")))

    def test_unknown_query_and_limit(self):
        self.assertEqual(self.hits("完全不存在的词"), [])
        self.assertEqual(self.services.fulltext.search("", 200), [])
        self.assertEqual(len(self.hits("超分", limit=1)), 1)

    def test_index_tracks_save_and_remove(self):
        paper = self.services.library.create({"title": "新文献"})
        self.services.fulltext.save(paper["id"], "这里讨论超分子识别")
        self.assertIn(paper["id"], self.hits("超分子"))
        self.services.fulltext.save(paper["id"], "改成了别的主题")
        self.assertNotIn(paper["id"], self.hits("超分子"))
        self.services.fulltext.remove(paper["id"])
        self.assertNotIn(paper["id"], self.hits("别的主题"))

    def test_startup_and_warm_search_have_no_body_selects(self):
        from paperlib.services import create_services

        conn = self.store.repo.conn
        statements = []
        conn.set_trace_callback(statements.append)
        try:
            create_services(self.services.runtime.config, self.store)
            self.assertFalse(any("paper_text" in sql for sql in statements))
            self.hits("超分子")
            statements.clear()
            self.assertEqual(self.hits("超分子"), [self.ids["cage"]])
            self.assertEqual(statements, ["PRAGMA data_version"])
        finally:
            conn.set_trace_callback(None)

    def test_incremental_index_reads_equal_length_updates_and_deletions(self):
        key = self.ids["mof"]
        body = "probe alpha"
        self.store.set_text(key, body)
        self.assertEqual(self.hits("alpha"), [key])
        repo = self.store.repo
        with patch.object(repo, "read_body_batch", wraps=repo.read_body_batch) as read:
            self.store.set_text("att-fixture.txt", "probe alpha")
            self.assertEqual(self.hits("alpha"), [key])
            read.assert_not_called()
            self.store.set_text(key, "probe omega")
            self.assertEqual(self.hits("alpha"), [])
            self.assertEqual(self.hits("omega"), [key])
            self.assertEqual([call.args[0] for call in read.call_args_list], [(key,)])
        self.services.library.remove(key)
        self.assertEqual(self.hits("omega"), [])

    def test_index_rebuilds_after_other_connection_or_expired_cursor(self):
        key = self.ids["mof"]
        repo = self.store.repo
        repo._body_changes = deque(repo._body_changes, maxlen=2)
        self.assertEqual(self.hits("frameworks"), [key])
        with closing(sqlite3.connect(repo.db_path)) as conn, conn:
            conn.execute("UPDATE paper_text SET text = replace(text, 'frameworks', 'structures') WHERE paper_id = ?",
                         (key,))
        self.assertEqual(self.hits("structures"), [key])
        self.assertEqual(self.hits("frameworks"), [])
        for name in ("mof", "cage", "gas"):
            repo.set_text(self.ids[name], "revised fixture")
        self.assertEqual(set(self.hits("revised")), {self.ids[name] for name in ("mof", "cage", "gas")})

    def test_stats_reuse_body_count_until_a_body_changes(self):
        repo = self.store.repo
        fulltext = self.services.fulltext
        self.assertEqual(fulltext.stats()["indexed"], 4)
        statements = []
        repo.conn.set_trace_callback(statements.append)
        try:
            self.store.set_text("att-count-fixture.txt", "supporting information")
            self.assertEqual(fulltext.stats()["indexed"], 4)
            self.assertFalse(any("SELECT" in sql.upper() for sql in statements))
            repo.delete_text(self.ids["mof"])
            self.assertEqual(fulltext.stats()["indexed"], 3)
        finally:
            repo.conn.set_trace_callback(None)

    def test_excerpts(self):
        fulltext = self.services.fulltext
        snippets = fulltext.excerpts(self.ids["cage"], "超分子", 1)
        self.assertEqual(len(snippets), 1)
        self.assertIn("超分子", snippets[0])
        self.assertEqual(fulltext.excerpts(self.ids["cage"], "不存在的词", 1), [])
        self.assertEqual(fulltext.excerpts("deadbeefdeadbeef", "超分子", 1), [])
        self.assertGreaterEqual(len(fulltext.excerpts(self.ids["cage"], "超分子 金属有机笼", 2)), 1)

    def test_stats_counts_indexed_and_tokens(self):
        self.hits("超分子")
        # 先显式探测一次：available 在「未探测」时按 Node 语义是 None，本用例不应依赖别的用例触发过探测
        self.services.fulltext.available()
        stats = self.services.fulltext.stats()
        self.assertEqual(stats["indexed"], 4)
        self.assertGreater(stats["tokens"], 0)
        self.assertIsNotNone(stats["available"])


class DefaultLookupBudgetTest(unittest.TestCase):
    """预检要能把「最多等多久」传给题录源，避免慢网络把单篇扫描拖到十几秒。"""

    def test_preflight_budget_reaches_multi_source_lookup(self):
        seen = {}

        class FakeMetadata:
            @staticmethod
            def fetch_by_doi_multi(doi, options=None):
                seen["doi"] = doi
                seen["options"] = options
                return {"paper": {"title": "T"}}

        services = SimpleNamespace(metadata=FakeMetadata)
        lookup = pdf_import._default_lookup_doi(services, {"budgetMs": 6000})
        self.assertEqual(lookup("10.1000/xyz"), {"title": "T"})
        self.assertEqual(seen["doi"], "10.1000/xyz")
        self.assertEqual(seen["options"], {"budgetMs": 6000})

    def test_default_lookup_falls_back_to_fetch_by_doi(self):
        class FakeMetadata:
            @staticmethod
            def fetch_by_doi(doi):
                return {"title": "from-fetch", "doi": doi}

        services = SimpleNamespace(metadata=FakeMetadata)
        self.assertEqual(pdf_import._default_lookup_doi(services)("10.1000/abc"),
                         {"title": "from-fetch", "doi": "10.1000/abc"})

    def test_parse_pdf_passes_lookup_options(self):
        seen = {}

        class FakeMetadata:
            @staticmethod
            def fetch_by_doi_multi(doi, options=None):
                seen["options"] = options
                return {"paper": None}

        services = SimpleNamespace(metadata=FakeMetadata)
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample.pdf")
            make_sample_pdf(path)
            pdf_import.parse_pdf(services, path, {"lookup_options": {"budgetMs": 6000}})
        self.assertEqual(seen.get("options"), {"budgetMs": 6000})


if __name__ == "__main__":
    unittest.main()
