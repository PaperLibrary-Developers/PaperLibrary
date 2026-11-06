"""unit test：paperlib/domain/query.py（移植自 Sci-lib server/domain/query.js）。

覆盖：分页/排序/筛选/facets/stats/列表项/路径防护，以及两段式粗筛与布尔求值
（布尔用例在 shared.pure.boolean_query 落地后自动启用，未落地时 skip）。

跑法：python -m unittest test.test_query -v
"""
from __future__ import annotations

import os
import tempfile
import unittest

from paperlib.data.store import Store
from paperlib.domain.library import Library
from paperlib.domain.query import QueryEngine, has_or_node, needs_evaluator, parse_query

try:
    from shared.pure import boolean_query as bool_query  # noqa: F401
except Exception:  # noqa: BLE001
    bool_query = None

BOOL_READY = bool_query is not None
SKIP_BOOL = unittest.skipUnless(BOOL_READY, "shared.pure.boolean_query 未落地（T1 交付后自动启用）")


def paper(**over):
    """一张字段完整的卡片（时间字段固定，排序用例才能断言）。"""
    base = {
        "id": "p", "title": "t", "authors": [], "year": None, "type": "journal",
        "venue": "", "volume": "", "issue": "", "pages": "", "publisher": "",
        "doi": "", "url": "", "tags": [], "keywords": [], "status": "unread",
        "rating": 0, "starred": False, "starredAt": "", "summary": "", "notes": "",
        "aiSummary": "", "aiSummaryAt": "", "attachments": [], "chat": [], "pdfFile": "",
        "addedAt": "2026-10-01T00:00:00.000Z", "updatedAt": "2026-10-01T00:00:00.000Z",
    }
    base.update(over)
    return base


FIXTURE = [
    paper(id="p01", title="AAA supramolecular host-guest chemistry",
          authors=["Werner M. Nau", "Khaleel I. Assaf"], year=2015, type="journal",
          venue="Chemical Society Reviews", tags=["supramolecular", "CB"], status="read",
          rating=5, starred=True, starredAt="2026-11-01T00:00:00.000Z",
          summary="cucurbituril binding affinity " * 20, notes="要点：主客体化学",
          doi="10.1039/c4cs00273c", pdfFile="p01.pdf",
          addedAt="2026-10-01T00:00:00.000Z", updatedAt="2026-11-05T00:00:00.000Z"),
    paper(id="p02", title="catalysis inside cucurbituril", authors=["Jonathan R. Nitschke"],
          year=2020, venue="JACS", tags=["CB", "catalysis"], rating=3,
          keywords=["catalysis", "container"], status="reading",
          addedAt="2026-10-02T00:00:00.000Z", updatedAt="2026-11-04T00:00:00.000Z"),
    paper(id="p03", title="supramolecular capsules at interfaces", year=2013,
          type="conference", venue="International Conference on Supramolecular Chemistry",
          tags=["supramolecular"], updatedAt="2026-11-03T00:00:00.000Z"),
    paper(id="p04", title="adsorption of dyes on porous frameworks", year=2024,
          type="preprint", venue="ChemRxiv", rating=4, starred=True,
          starredAt="2026-10-20T00:00:00.000Z", summary="dye adsorption isotherms",
          notes="待读", attachments=[{"file": "p04-SI.pdf"}],
          chat=[{"role": "user", "content": "hi"}], updatedAt="2026-11-02T00:00:00.000Z"),
    paper(id="p05", title="超分子化学导论", authors=["张三"], year=2013, type="book",
          venue="Wiley", tags=["教材"], status="archived", updatedAt="2026-11-01T00:00:00.000Z"),
    paper(id="p06", title="untitled working notes", type="other", status="unread",
          pdfFile="missing.pdf", updatedAt="2026-10-31T00:00:00.000Z"),
    paper(id="p07", title="molecular capsule assembly", authors=["Fujita"],
          year=2018, venue="JACS", tags=["catalysis"], rating=5,
          summary="self-assembly of coordination cages", updatedAt="2026-10-30T00:00:00.000Z"),
    paper(id="p08", title="porous framework adsorption review", year=2018, type="report",
          venue="Annual Review", tags=[], status="read", updatedAt="2026-10-29T00:00:00.000Z"),
]


class QueryTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store.load()
        self.library = Library(self.store)
        self.addCleanup(self.store.repo.close)      # Windows：先关库再删临时目录
        for item in FIXTURE:
            self.store.insert_paper(dict(item))
            self.library.index.set(dict(item))

    def ids(self, result):
        return [item["id"] for item in result["items"]]

    def keys(self, params):
        return self.library.query(dict(params))["items"]


class PaginationTest(QueryTestBase):
    def test_default_page_size_is_50_and_total_is_all(self):
        result = self.library.query({})
        self.assertEqual(result["total"], len(FIXTURE))
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["pageSize"], 50)
        self.assertEqual(result["pageCount"], 1)
        self.assertEqual(result["scope"], "metadata")
        self.assertFalse(result["boolean"])

    def test_page_and_page_size_slicing(self):
        result = self.library.query({"page": 2, "pageSize": 3})
        self.assertEqual(result["page"], 2)
        self.assertEqual(result["pageSize"], 3)
        self.assertEqual(result["pageCount"], 3)
        self.assertEqual(len(result["items"]), 3)

    def test_page_size_clamped_to_1_500(self):
        self.assertEqual(self.library.query({"pageSize": 999})["pageSize"], 500)
        self.assertEqual(self.library.query({"pageSize": 0})["pageSize"], 1)
        self.assertEqual(self.library.query({"pageSize": "abc"})["pageSize"], 50)

    def test_page_clamped_to_minimum_1(self):
        self.assertEqual(self.library.query({"page": 0})["page"], 1)
        self.assertEqual(self.library.query({"page": -3})["page"], 1)

    def test_all_true_ignores_pagination(self):
        result = self.library.query({"all": "true", "pageSize": 2})
        self.assertEqual(len(result["items"]), len(FIXTURE))
        # 布尔 True 也认（api 层会把字符串 'true' 转成 True）
        self.assertEqual(len(self.library.query({"all": True, "pageSize": 2})["items"]),
                         len(FIXTURE))

    def test_page_count_at_least_1_for_empty_result(self):
        result = self.library.query({"q": "zzzznothing"})
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["pageCount"], 1)


class SortTest(QueryTestBase):
    def order(self, sort):
        return self.ids(self.library.query({"sort": sort, "all": True}))

    def test_default_sort_is_updated_at_desc(self):
        self.assertEqual(self.order(None), ["p01", "p02", "p03", "p04", "p05", "p06", "p07", "p08"])
        self.assertEqual(self.order("nonsense"), self.order(None))

    def test_added_at_desc(self):
        ids = self.order("addedAt")
        self.assertEqual(ids[0], "p02")            # 2026-10-02 最新
        self.assertEqual(sorted(ids[1:]), ["p01", "p03", "p04", "p05", "p06", "p07", "p08"])

    def test_year_desc_and_asc(self):
        ids = self.order("yearDesc")
        self.assertEqual(ids[0], "p04")           # 2024
        self.assertEqual(ids[-1], "p06")          # 无年份（0）
        ids = self.order("yearAsc")
        self.assertEqual(ids[0], "p06")

    def test_title_sort(self):
        self.assertEqual(self.order("title")[0], "p01")   # 'AAA…' 最小

    def test_rating_sort(self):
        ids = self.order("rating")
        # 5 分两名（updatedAt 更晚的 p01 在前），其次 4 分的 p04
        self.assertEqual(ids[:3], ["p01", "p07", "p04"])
        self.assertEqual(ids[-1], "p08")            # 0 分组里 updatedAt 最早

    def test_starred_at_sort(self):
        self.assertEqual(self.order("starredAt")[0], "p01")


class FilterTest(QueryTestBase):
    def test_tags_multi_value_is_or(self):
        ids = self.ids(self.library.query({"tags": "supramolecular,catalysis", "all": "true"}))
        self.assertEqual(sorted(ids), ["p01", "p02", "p03", "p07"])

    def test_type_and_status_all_means_no_filter(self):
        self.assertEqual(len(self.keys({"type": "all"})), len(FIXTURE))
        self.assertEqual(self.keys({"type": "preprint"})[0]["id"], "p04")
        self.assertEqual(self.keys({"type": "book,report"})[0]["id"], "p05")
        self.assertEqual(len(self.keys({"status": "all"})), len(FIXTURE))
        self.assertEqual([p["id"] for p in self.keys({"status": "archived"})], ["p05"])

    def test_author_venue_keyword_substring(self):
        self.assertEqual([p["id"] for p in self.keys({"author": "nau"})], ["p01"])
        self.assertEqual([p["id"] for p in self.keys({"venue": "jacs"})],
                         ["p02", "p07"])
        self.assertEqual([p["id"] for p in self.keys({"keyword": "container"})], ["p02"])

    def test_has_notes_has_pdf_starred(self):
        self.assertEqual([p["id"] for p in self.keys({"hasNotes": "true"})], ["p01", "p04"])
        pdf_ids = [p["id"] for p in self.keys({"hasPdf": "true"})]
        self.assertEqual(sorted(pdf_ids), ["p01", "p06"])
        self.assertEqual(sorted(p["id"] for p in self.keys({"starred": "true"})),
                         ["p01", "p04"])

    def test_year_range_and_multi_year(self):
        self.assertEqual(sorted(p["id"] for p in self.keys({"yearFrom": 2018})),
                         ["p02", "p04", "p07", "p08"])
        self.assertEqual(sorted(p["id"] for p in self.keys({"yearTo": 2015})),
                         ["p01", "p03", "p05"])
        self.assertEqual(sorted(p["id"] for p in self.keys({"years": "2013,2018"})),
                         ["p03", "p05", "p07", "p08"])

    def test_authors_and_venues_multi_select(self):
        self.assertEqual([p["id"] for p in self.keys({"authors": "Fujita"})], ["p07"])
        self.assertEqual(sorted(p["id"] for p in self.keys({"venues": "jacs,chemrxiv"})),
                         ["p02", "p04", "p07"])

    def test_min_rating_and_exclude_id(self):
        self.assertEqual(sorted(p["id"] for p in self.keys({"minRating": 4})),
                         ["p01", "p04", "p07"])
        self.assertNotIn("p01", self.ids(self.library.query({"excludeId": "p01"})))

    def test_ids_filter_and_empty_ids_means_empty(self):
        self.assertEqual(sorted(p["id"] for p in self.keys({"ids": "p01,p03"})), ["p01", "p03"])
        self.assertEqual(self.keys({"ids": ""}), self.keys({}))
        self.assertEqual(self.library.query({"idsEmpty": True})["total"], 0)

    def test_empty_query_does_not_filter(self):
        self.assertEqual(self.library.query({"q": ""})["total"], len(FIXTURE))
        self.assertEqual(self.library.query({"q": "   "})["total"], len(FIXTURE))


class BooleanTest(QueryTestBase):
    @SKIP_BOOL
    def test_boolean_flag_and_not_filter(self):
        result = self.library.query({"q": "supramolecular NOT capsules", "all": "true"})
        self.assertTrue(result["boolean"])
        self.assertEqual([p["id"] for p in result["items"]], ["p01"])

    @SKIP_BOOL
    def test_or_query_uses_union_coarse_filter(self):
        # 只命中另一支的文献不能被粗筛砍掉（历史缺陷：OR 用交集粗筛只回 1 篇）
        result = self.library.query({"q": "cucurbituril OR capsule", "all": "true"})
        self.assertEqual(sorted(p["id"] for p in result["items"]), ["p01", "p02", "p07"])

    @SKIP_BOOL
    def test_field_query_title(self):
        result = self.library.query({"q": "title:capsule", "all": "true"})
        self.assertEqual(sorted(p["id"] for p in result["items"]), ["p03", "p07"])

    @SKIP_BOOL
    def test_field_query_year_range(self):
        result = self.library.query({"q": "year:2013-2018", "all": "true"})
        self.assertEqual(sorted(p["id"] for p in result["items"]),
                         ["p01", "p03", "p05", "p07", "p08"])

    @SKIP_BOOL
    def test_pure_field_query_scans_all(self):
        result = self.library.query({"q": "type:preprint", "all": "true"})
        self.assertEqual([p["id"] for p in result["items"]], ["p04"])

    @SKIP_BOOL
    def test_punctuation_only_query_is_empty_not_everything(self):
        self.assertEqual(self.library.query({"q": "---"})["total"], 0)

    @SKIP_BOOL
    def test_scope_fulltext_uses_body_and_keeps_boolean_query(self):
        # api 层在 scope=fulltext 时清空 q、保留 booleanQuery、用全文命中的 ids 收窄；
        # 这里照同一口径调用，验证求值器读的是正文而不是题录。
        body = {"p01": "cucurbituril binding in water", "p03": "molecular capsules in water"}
        self.library.fulltext_text_for = lambda pid: body.get(pid, "")
        result = self.library.query({"q": "", "booleanQuery": "water NOT capsules",
                                     "scope": "fulltext", "ids": "p01,p03", "all": "true"})
        self.assertTrue(result["boolean"])
        self.assertEqual([p["id"] for p in result["items"]], ["p01"])

    def test_searchable_text_scope_and_fields(self):
        engine = self.library.query_engine
        self.assertIsInstance(engine, QueryEngine)
        target = dict(FIXTURE[0])
        self.assertIn("Werner M. Nau", engine.searchable_text(target))
        self.assertNotIn("BODY-TOKEN", engine.searchable_text(target))
        engine.fulltextTextFor = lambda pid: "BODY-TOKEN" if pid == "p01" else ""
        self.assertIn("BODY-TOKEN", engine.searchable_text(target, "fulltext"))
        self.assertEqual(engine.field_text(target, "year"), "2015")
        self.assertEqual(engine.field_text(target, "tag"), "supramolecular CB")
        self.assertEqual(engine.field_text(target, "unknown"), "")


class FacetsTest(QueryTestBase):
    def test_per_class_condition_is_stripped(self):
        # 已选 tag=supramolecular，tags 计数仍按「不含 tag 过滤」统计
        result = self.library.facets({"tags": "supramolecular"})
        self.assertEqual(result["total"], 2)        # p01/p03 命中 tag=supramolecular
        tags = {row["value"]: row["count"] for row in result["tags"]["items"]}
        self.assertEqual(tags.get("supramolecular"), 2)
        self.assertEqual(tags.get("CB"), 2)
        self.assertEqual(tags.get("catalysis"), 2)
        self.assertEqual(result["tags"]["distinct"], 4)

    def test_year_facet_sorted_desc_and_strips_range(self):
        result = self.library.facets({"yearFrom": 2015})
        years = [row["value"] for row in result["years"]["items"]]
        self.assertEqual(years, ["2024", "2020", "2018", "2015", "2013"])
        self.assertEqual(result["total"], 5)

    def test_default_facet_limit_is_6(self):
        extra = dict(FIXTURE[0], id="p09",
                     tags=["t1", "t2", "t3", "t4", "t5", "t6", "t7", "t8"])
        self.store.insert_paper(extra)
        self.library.index.set(extra)
        result = self.library.facets({})
        self.assertEqual(len(result["tags"]["items"]), 6)      # 默认 6 项
        self.assertEqual(result["tags"]["distinct"], 12)
        self.assertEqual(len(self.library.facets({"facetLimit": 2})["tags"]["items"]), 2)

    def test_types_facet_and_distinct_counts(self):
        result = self.library.facets({})
        types = {row["value"]: row["count"] for row in result["types"]["items"]}
        self.assertEqual(types["journal"], 3)
        self.assertEqual(types["conference"], 1)
        self.assertEqual(result["authors"]["distinct"], 5)


class ListItemStatsTest(QueryTestBase):
    def test_list_item_shape(self):
        item = self.library.to_list_item(dict(FIXTURE[0]))
        for key in ("id", "title", "authors", "year", "type", "venue", "tags", "status",
                    "rating", "starred", "starredAt", "doi", "url", "pdfFile",
                    "pdfDisplayName", "hasPdf", "summary", "hasAiSummary", "aiSummaryAt",
                    "attachments", "chatCount", "notesLength", "addedAt", "updatedAt"):
            self.assertIn(key, item)
        self.assertNotIn("score", item)
        self.assertLessEqual(len(item["summary"]), 200)
        self.assertEqual(item["chatCount"], 0)
        self.assertEqual(self.library.to_list_item(dict(FIXTURE[3]))["chatCount"], 1)

    def test_score_rounded_to_one_decimal(self):
        item = self.library.to_list_item(dict(FIXTURE[0]), 12.345)
        self.assertEqual(item["score"], 12.3)

    def test_pdf_display_name_strips_id_prefix(self):
        item = self.library.to_list_item(paper(id="x", pdfFile="abcdef12-capsule.pdf"))
        self.assertEqual(item["pdfDisplayName"], "capsule.pdf")

    def test_has_pdf_reflects_disk(self):
        path = os.path.join(self.store.papers_dir, "p01.pdf")
        self.assertFalse(self.library.query({"ids": "p01"})["items"][0]["hasPdf"])
        with open(path, "wb") as fh:
            fh.write(b"%PDF-1.4")
        self.assertTrue(self.library.query({"ids": "p01"})["items"][0]["hasPdf"])

    def test_pdf_exists_rejects_traversal(self):
        self.assertFalse(self.library.pdf_exists("../outside.pdf"))
        self.assertFalse(self.library.pdf_exists(""))
        self.assertFalse(self.library.pdf_exists("nope.pdf"))

    def test_stats_totals(self):
        stats = self.library.stats()
        self.assertEqual(stats["total"], 8)
        self.assertEqual(stats["withPdf"], 2)
        self.assertEqual(stats["starred"], 2)
        self.assertEqual(stats["pdfMissing"], 2)      # 两个 pdfFile，一个在盘上吗？见下
        self.assertEqual(stats["avgRating"], 4.3)     # (5+3+4+5)/4
        self.assertEqual(stats["byYear"][0], {"year": 2024, "count": 1})
        self.assertEqual(stats["byType"][0]["count"], 3)
        self.assertEqual(stats["tags"], 4)

    def test_tags_authors_venues_lists(self):
        tags = self.library.tags()
        self.assertEqual(tags[0], {"name": "catalysis", "count": 2})
        self.assertEqual({row["name"]: row["count"] for row in tags},
                         {"catalysis": 2, "CB": 2, "supramolecular": 2, "教材": 1})
        authors = self.library.authors()
        self.assertEqual(authors[0]["name"], "Fujita")
        self.assertEqual(len(self.library.authors(1)), 1)
        venues = self.library.venues(2)
        self.assertEqual(len(venues), 2)
        self.assertEqual(venues[0], {"name": "JACS", "count": 2})


class HelperTest(unittest.TestCase):
    def test_has_or_node_walks_nested(self):
        node = {"type": "and", "left": {"type": "term", "value": "a", "phrase": False, "field": ""},
                "right": {"type": "or", "left": None, "right": None}}
        self.assertTrue(has_or_node(node))
        self.assertFalse(has_or_node({"type": "not", "child": {"type": "term", "value": "b"}}))
        self.assertFalse(has_or_node(None))

    @SKIP_BOOL
    def test_needs_evaluator_and_parse_bundle(self):
        self.assertFalse(needs_evaluator("cucurbituril binding"))
        self.assertTrue(needs_evaluator("a AND b"))
        self.assertTrue(needs_evaluator('title:"molecular capsule"'))
        bundle = parse_query("title:capsule OR cucurbituril")
        self.assertTrue(bundle["needsEvaluator"])
        self.assertEqual(bundle["terms"], ["cucurbituril"])
        self.assertEqual(bundle["allTerms"], ["capsule", "cucurbituril"])
        self.assertEqual(bundle["fieldTerms"], [{"field": "title", "value": "capsule"}])


if __name__ == "__main__":
    unittest.main()
