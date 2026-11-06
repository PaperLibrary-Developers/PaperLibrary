"""T5 检索适配层自测（对照 Sci-lib server/adapters/{metadata,external,sources}.js 行为）。

命令：python -m unittest test.test_search_adapters -v
全程不联网：接口根用 common.set_endpoints() 指到本进程起的本地 http.server（回放样例 JSON）；
「断网」用例指到一个刚关闭的端口，断言端点回 502（不是 500）。
"""
import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import unquote, urlsplit

from paperlib.adapters import external, metadata
from paperlib.adapters.external_sources import arxiv, boolexpr, common
from paperlib.api import search as api_search

OPENALEX_SEARCH = {"results": [
    {"id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/ABC",
     "title": "Cucurbituril host-guest chemistry &lt;i&gt;in water&lt;/i&gt;",
     "publication_year": 2019, "type": "article", "cited_by_count": 42,
     "open_access": {"is_oa": True},
     "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
     "primary_location": {"source": {"display_name": "J. Supramol. Chem."}},
     "authorships": [{"author": {"display_name": "Alice Nitschke"}},
                     {"author": {"display_name": "Bob Lee"}}],
     "abstract_inverted_index": {"host": [0], "guest": [1], "water": [2]}},
    {"id": "https://openalex.org/W2", "doi": None, "title": "Cyclodextrin aggregates",
     "publication_year": 2015, "cited_by_count": 7, "open_access": {"is_oa": False},
     "primary_location": {"source": {"display_name": "Chem. Lett."}},
     "authorships": [{"author": {"display_name": "Carol"}}]}]}
CROSSREF_SEARCH = {"message": {"items": [
    {"DOI": "10.1000/abc", "title": ["Cucurbituril host-guest chemistry in water"],
     "author": [{"given": "Alice", "family": "Nitschke"}], "issued": {"date-parts": [[2019]]},
     "container-title": ["J. Supramol. Chem."], "is-referenced-by-count": 30,
     "type": "journal-article", "URL": "https://doi.org/10.1000/abc",
     "abstract": "<jats:p>Host guest binding in water.</jats:p>"}]}}
EUROPEPMC_SEARCH = {"resultList": {"result": [
    {"id": "MED:1", "pmid": "1234567", "doi": "10.2000/xyz", "title": "Supramolecular capsules",
     "authorString": "Doe, J.; Roe, R.", "pubYear": "2020", "citedByCount": 12,
     "journalInfo": {"journal": {"title": "Chem. Sci."}}, "isOpenAccess": "Y", "pmcid": "PMC999",
     "pubType": "research-article", "abstractText": "<p>We report capsules.</p>"}]}}
S2_SEARCH = {"data": [
    {"paperId": "abc123", "title": "Supramolecular capsules", "year": 2020, "venue": "Chem. Sci.",
     "citationCount": 3, "externalIds": {"DOI": "10.2000/XYZ"},
     "openAccessPdf": {"url": "https://example.org/x.pdf"},
     "authors": [{"name": "J. Doe"}], "abstract": "Capsules abstract"}]}
ARXIV_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2101.00001v1</id>
    <published>2021-01-01T00:00:00Z</published>
    <title>Cucurbituril capsules for water</title>
    <summary>We study cucurbituril capsules.</summary>
    <author><name>Alice Nitschke</name></author>
    <author><name>Bob Lee</name></author>
    <arxiv:doi>10.3000/arxiv</arxiv:doi>
    <category term="cond-mat.soft" />
  </entry>
  <entry>
    <id>http://arxiv.org/abs/1801.00002v1</id>
    <published>2018-05-01T00:00:00Z</published>
    <title>Older preprint</title>
    <summary>old</summary>
    <author><name>Carol</name></author>
    <category term="physics.chem-ph" />
  </entry>
</feed>
"""

CROSSREF_DOI = {"message": {
    "DOI": "10.1000/abc", "title": ["Cucurbituril host-guest chemistry in water"],
    "author": [{"given": "Alice", "family": "Nitschke"}], "issued": {"date-parts": [[2019]]},
    "container-title": ["J. Supramol. Chem."], "volume": "12", "issue": "3", "page": "100-110",
    "publisher": "Wiley", "type": "journal-article", "URL": "https://doi.org/10.1000/abc",
    "abstract": "Crossref abstract.", "subject": ["Chemistry"]}}
OPENALEX_DOI = {
    "id": "https://openalex.org/W1", "doi": "https://doi.org/10.1000/abc",
    "title": "Cucurbituril host-guest chemistry in water", "publication_year": 2019,
    "type": "article", "cited_by_count": 42, "open_access": {"is_oa": True},
    "best_oa_location": {"pdf_url": "https://example.org/paper.pdf"},
    "primary_location": {"source": {"display_name": "J. Supramol. Chem."}},
    "authorships": [{"author": {"display_name": "Alice Nitschke"}},
                    {"author": {"display_name": "Bob Lee"}}],
    "abstract_inverted_index": {"A": [0], "much": [1], "longer": [2], "abstract": [3], "here": [4]},
    "keywords": [{"display_name": "supramolecular"}]}


def _stub_server(routes):
    """routes: [(路径前缀, {"status":…, "body":…, "headers":{…}})]；最长前缀优先。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args):
            return

        def do_GET(self):  # noqa: N802 - http.server 的约定
            path = unquote(urlsplit(self.path).path)
            spec = {"status": 404, "body": {"message": "no route"}}
            for prefix, candidate in sorted(routes, key=lambda pair: -len(pair[0])):
                if path.startswith(prefix):
                    spec = candidate
                    break
            body = spec.get("body")
            raw = (json.dumps(body, ensure_ascii=False).encode("utf-8")
                   if not isinstance(body, str) else body.encode("utf-8"))
            self.send_response(int(spec.get("status") or 200))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            for name, value in (spec.get("headers") or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(raw)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    # poll_interval 调小：tearDown 里的 shutdown() 不用干等默认的 0.5 秒
    threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.05), daemon=True).start()
    return httpd


def _closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()          # 端口立刻释放 → 之后连接它必定被拒绝（模拟断网）
    return port


def _point_to(port):
    base = "http://127.0.0.1:%d" % int(port)
    common.set_endpoints({"openalex": base + "/openalex", "crossref": base + "/crossref",
                          "europepmc": base + "/europepmc", "arxiv": base + "/arxiv",
                          "semanticscholar": base + "/s2"})


class FakeSources:
    def __init__(self, key=""):   # 与 adapters.sources.Sources 同形，不落盘
        self.quota = {}
        self.settings = {"openalexApiKey": key, "contactEmail": ""}

    def openalex_key(self):
        return self.settings["openalexApiKey"]

    def has_openalex_key(self):
        return len(self.openalex_key()) > 3

    def contact_email(self):
        return ""

    def public_settings(self):
        return {"openalex": {"hasKey": self.has_openalex_key(), "keyHint": "abcd…wxyz"},
                "contactEmail": "", "note": "假设置"}


class StubCase(unittest.TestCase):
    routes = ()          # 子类给 [(路径前缀, {"status", "body", "headers"})]

    def setUp(self):
        self.httpd = _stub_server(list(self.routes))
        _point_to(self.httpd.server_address[1])
        arxiv.set_throttle_ms(0)
        common.set_timeouts(json_ms=4000, text_ms=4000)
        self.sources = FakeSources()
        external.configure(self.sources)
        metadata.configure(self.sources)
        external.clear_cache()

    def restart_stub(self, routes):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.httpd = _stub_server(list(routes))
        _point_to(self.httpd.server_address[1])

    def tearDown(self):
        common.set_endpoints(None)
        common.set_timeouts(json_ms=common.DEFAULT_JSON_TIMEOUT_MS,
                            text_ms=common.DEFAULT_TEXT_TIMEOUT_MS)
        arxiv.set_throttle_ms(3000)
        external.configure(None)
        metadata.configure(None)
        external.clear_cache()
        api_search.BACKFILL["running"] = False
        self.httpd.shutdown()
        self.httpd.server_close()


# 查询降级 / arXiv 解析
class BooleanDownShiftTest(unittest.TestCase):
    def test_boolean_and_field_detection(self):
        for query in ("cucurbituril AND capsule", "a || b", "甲 与 乙", '"host guest"'):
            self.assertTrue(boolexpr.has_boolean(query), query)
        self.assertFalse(boolexpr.has_boolean("cucurbituril capsule"))
        self.assertTrue(boolexpr.has_field_syntax("title:cucurbituril"))
        self.assertTrue(boolexpr.has_field_syntax("年份:2019"))
        self.assertFalse(boolexpr.has_field_syntax("10.1000/abc:def"))
        self.assertEqual(boolexpr.plain_query('title:"molecular capsule" NOT junk'), "molecular capsule")
        self.assertEqual(boolexpr.plain_query("cucurbituril AND capsule"), "cucurbituril capsule")
        self.assertEqual(boolexpr.to_arxiv_query("a AND b NOT c"), 'all:"a"+AND+all:"b"+ANDNOT+all:"c"')
        self.assertEqual(boolexpr.field_terms("title:abc AND year:2013-2018"),
                         [{"field": "title", "value": "abc"}, {"field": "year", "value": "2013-2018"}])

    def test_filter_items_local_recheck(self):
        rows = [{"t": "cucurbituril chemistry"}, {"t": "cyclodextrin only"},
                {"t": "both cucurbituril and cyclodextrin"}]
        kept = boolexpr.filter_items(rows, "cucurbituril NOT cyclodextrin", lambda item: item["t"])
        self.assertEqual([row["t"] for row in kept], ["cucurbituril chemistry"])
        fields = [{"title": "capsule", "abstract": "cyclodextrin"}]
        kept = boolexpr.filter_items(fields, "title:capsule",
                                     lambda item: item["title"] + " " + item["abstract"],
                                     lambda item, field: item.get(field) or "")
        self.assertEqual(len(kept), 1)
        self.assertTrue(boolexpr.filter_items([{"t": "2016 paper"}], "year:2013-2018", lambda i: i["t"]))
        self.assertFalse(boolexpr.filter_items([{"t": "2019 paper"}], "year:2013-2018", lambda i: i["t"]))


class ArxivFeedTest(unittest.TestCase):
    def test_parse_feed(self):
        items = arxiv.parse_arxiv_feed(ARXIV_FEED)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["source"], "arxiv")
        self.assertEqual(first["title"], "Cucurbituril capsules for water")
        self.assertEqual(first["authors"], ["Alice Nitschke", "Bob Lee"])
        self.assertEqual(first["year"], 2021)
        self.assertEqual(first["doi"], "10.3000/arxiv")
        self.assertTrue(first["isOpenAccess"])
        self.assertTrue(first["oaUrl"].endswith("/pdf/2101.00001v1"))
        self.assertEqual(first["citations"], 0)
        self.assertEqual(len(arxiv.parse_arxiv_feed(ARXIV_FEED, {"yearFrom": "2020"})), 1)


# 各来源字段统一
class SourceMappingTest(StubCase):
    routes = [
        ("/openalex/works/doi:", {"body": OPENALEX_DOI}),
        ("/openalex/works", {"body": OPENALEX_SEARCH}),
        ("/crossref/works", {"body": CROSSREF_SEARCH}),
        ("/europepmc/search", {"body": EUROPEPMC_SEARCH}),
        ("/arxiv/api/query", {"body": ARXIV_FEED}),
        ("/s2/graph/v1/paper/search", {"body": S2_SEARCH}),
    ]

    def test_openalex_page(self):
        item = external.PAGERS["openalex"]("cucurbituril", 10, 1, {})[0]
        self.assertEqual(item["doi"], "10.1000/abc")              # 去 resolver 前缀 + 小写
        self.assertEqual(item["authors"], ["Alice Nitschke", "Bob Lee"])
        self.assertEqual(item["citations"], 42)
        self.assertTrue(item["isOpenAccess"])
        self.assertEqual(item["abstract"], "host guest water")     # 倒排索引还原
        self.assertEqual(item["url"], "https://doi.org/10.1000/abc")

    def test_crossref_page(self):
        item = external.PAGERS["crossref"]("cucurbituril", 10, 1, {})[0]
        self.assertEqual(item["doi"], "10.1000/abc")
        self.assertEqual(item["year"], 2019)
        self.assertEqual(item["venue"], "J. Supramol. Chem.")
        self.assertEqual(item["abstract"], "Host guest binding in water.")  # JATS 标签清掉

    def test_europepmc_page(self):
        item = external.PAGERS["europepmc"]("capsules", 10, 1, {})[0]
        # 与 JS 版一致：authorString 按 [,;] 切分（"Doe, J.; Roe, R." → 4 段）
        self.assertEqual(item["authors"], ["Doe", "J.", "Roe", "R."])
        self.assertEqual(item["year"], 2020)
        self.assertTrue(item["isOpenAccess"])
        self.assertIn("PMC999", item["oaUrl"])

    def test_semantic_scholar_page(self):
        item = external.PAGERS["semanticscholar"]("capsules", 10, 1, {})[0]
        self.assertEqual(item["doi"], "10.2000/xyz")
        self.assertTrue(item["isOpenAccess"])

    def test_arxiv_page(self):
        # category 是自闭合标签，JS 的 pick('category') 同样取不到 → venue 用 preprint
        self.assertEqual(external.PAGERS["arxiv"]("cucurbituril", 10, 1, {})[0]["venue"],
                         "arXiv 预印本（preprint）")


# 折叠 / 排序 / 过滤
class FoldRankFilterTest(unittest.TestCase):
    def test_merge_by_doi_and_title(self):
        lists = [
            [{"source": "openalex", "title": "Cucurbituril capsules", "doi": "10.1000/abc",
              "citations": 5, "authors": ["A"], "isOpenAccess": True},
             {"source": "openalex", "title": "Cyclodextrin  only", "citations": 1}],
            [{"source": "crossref", "title": "Different title", "doi": "10.1000/ABC",
              "citations": 12, "authors": ["A", "B"]},
             {"source": "crossref", "title": "cyclodextrin only", "citations": 3}],
        ]
        merged = external.merge_results(lists)
        self.assertEqual(len(merged), 2)                      # DOI 与归一化标题各折叠一条
        by_doi = [item for item in merged if item.get("doi")]
        self.assertEqual(len(by_doi), 1)
        self.assertEqual(by_doi[0]["citations"], 12)           # 被引数取大
        self.assertEqual(by_doi[0]["authors"], ["A", "B"])     # 作者取更全
        self.assertEqual(sorted(by_doi[0]["sources"]), ["crossref", "openalex"])
        self.assertTrue(by_doi[0]["isOpenAccess"])

    def test_rank_prefers_similarity_then_citations(self):
        items = [{"title": "cyclodextrin aggregates", "citations": 500},
                 {"title": "cucurbituril host-guest chemistry", "citations": 2}]
        ranked = external.rank_by_similarity(items, "cucurbituril host guest")
        self.assertEqual(ranked[0]["title"], "cucurbituril host-guest chemistry")
        self.assertGreater(ranked[0]["relevance"], ranked[1]["relevance"])
        self.assertNotIn("_score", ranked[0])

    def test_apply_local_filters(self):
        items = [
            {"title": "a", "year": 2010, "citations": 1, "isOpenAccess": True, "venue": "Chem",
             "authors": ["Ann"]},
            {"title": "b", "year": 2020, "citations": 90, "isOpenAccess": False, "venue": "Nature",
             "authors": ["Bob"]}]
        self.assertEqual(len(external.apply_local_filters(items, {"yearFrom": "2015"})), 1)
        self.assertEqual(len(external.apply_local_filters(items, {"onlyOA": True})), 1)
        self.assertEqual(len(external.apply_local_filters(items, {"minCitations": 50})), 1)
        self.assertEqual(len(external.apply_local_filters(items, {"venue": "nat"})), 1)
        self.assertEqual(len(external.apply_local_filters(items, {"author": "ann"})), 1)


# 编排：并发 / 缓存 / 容错
class ExternalSearchTest(StubCase):
    routes = [
        ("/openalex/works", {"body": OPENALEX_SEARCH}),
        ("/crossref/works", {"body": CROSSREF_SEARCH}),
        ("/europepmc/search", {"status": 500, "body": {"message": "boom"}}),
        ("/arxiv/api/query", {"body": ARXIV_FEED}),
        ("/s2/graph/v1/paper/search", {"status": 429, "body": {"message": "rate limited"},
                                       "headers": {"Retry-After": "0"}}),
    ]

    def test_search_folds_and_tolerates_single_source_failure(self):
        result = external.search("cucurbituril host guest", {"limit": 10, "page": 1})
        self.assertEqual(result["query"], "cucurbituril host guest")
        self.assertFalse(result["cached"])
        self.assertEqual(set(result["sources"]),
                         {"openalex", "crossref", "europepmc", "arxiv", "semanticscholar"})
        self.assertTrue(result["sources"]["openalex"]["ok"])
        self.assertFalse(result["sources"]["europepmc"]["ok"])    # 单源 500 不影响整体
        self.assertIn("HTTP 500", result["sources"]["europepmc"]["error"])
        # OpenAlex 与 Crossref 的同一篇（DOI 大小写不同）折叠成一条
        doi_items = [item for item in result["items"] if item.get("doi") == "10.1000/abc"]
        self.assertEqual(len(doi_items), 1)
        self.assertEqual(sorted(doi_items[0]["sources"]), ["crossref", "openalex"])
        self.assertIn("Cucurbituril host-guest chemistry in water", result["items"][0]["title"])
        self.assertIn("fetchedAt", result)
        self.assertIsInstance(result["hasMore"], bool)

    def test_rate_limit_reports_hint(self):
        entry = external.search("capsules", {"sources": ["semanticscholar"]})["sources"]["semanticscholar"]
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["status"], 429)
        self.assertIn("hint", entry)

    def test_cache_empty_query_and_sources_list(self):
        self.assertEqual(external.search("", {})["items"], [])
        external.search("cucurbituril", {"sources": ["openalex"]})
        self.assertTrue(external.search("cucurbituril", {"sources": ["openalex"]})["cached"])
        self.assertEqual([item["id"] for item in external.sources_list()],
                         ["openalex", "crossref", "europepmc", "arxiv", "semanticscholar"])


# 题录抓取
class MetadataTest(StubCase):
    routes = [
        ("/crossref/works/10.1000/none", {"status": 404, "body": {"message": "not found"}}),
        ("/openalex/works/doi:10.1000/none", {"status": 404, "body": {"message": "missing"}}),
        ("/crossref/works/10.1000/abc", {"body": CROSSREF_DOI}),
        ("/openalex/works/doi:", {"body": OPENALEX_DOI}),
        ("/crossref/works", {"body": CROSSREF_SEARCH}),
    ]

    def test_fetch_by_doi_detailed_merges(self):
        found = metadata.fetch_by_doi_detailed("https://doi.org/10.1000/abc")
        # 与 JS 一致：只去 resolver 前缀，不改大小写（小写在外部源侧统一）
        self.assertEqual(metadata.normalize_doi("https://doi.org/10.1000/ABC"), "10.1000/ABC")
        self.assertEqual(found["doi"], "10.1000/abc")
        self.assertEqual(found["sources"], {"crossref": "ok", "openalex": "ok"})
        self.assertEqual(found["errors"], {})
        paper = found["paper"]
        self.assertEqual(paper["title"], "Cucurbituril host-guest chemistry in water")
        self.assertEqual(paper["volume"], "12")
        self.assertEqual(paper["citations"], 42)
        self.assertTrue(paper["isOpenAccess"])
        self.assertEqual(paper["bibliographicSource"], "crossref")
        self.assertEqual(paper["summarySource"], "openalex")       # 更长的摘要取 OpenAlex
        self.assertEqual(found["fieldsFrom"]["title"], "crossref")
        self.assertIsInstance(found["elapsedMs"], int)

    def test_fetch_by_doi_falls_back_to_openalex(self):
        self.restart_stub([
            ("/crossref/works/10.1000/abc", {"status": 500, "body": "down"}),
            ("/openalex/works/doi:10.1000/abc", {"body": OPENALEX_DOI})])
        found = metadata.fetch_by_doi_detailed("10.1000/abc")
        self.assertEqual(found["sources"]["crossref"], "error")
        self.assertIn("crossref", found["errors"])
        self.assertEqual(found["paper"]["bibliographicSource"], "openalex")
        self.assertEqual(found["paper"]["title"], "Cucurbituril host-guest chemistry in water")

    def test_both_sources_missing_is_not_found(self):
        with self.assertRaises(metadata.MetadataError) as ctx:
            metadata.fetch_by_doi_detailed("10.1000/none")
        self.assertTrue(ctx.exception.not_found)
        self.assertIn("两个来源都没抓到这个 DOI", str(ctx.exception))

    def test_merge_papers_fields_from(self):
        merged = metadata.merge_papers({"authors": ["A"], "doi": "10.1/x"},
                                       {"title": "T", "venue": "V", "summary": "long abstract"})
        self.assertEqual(merged["paper"]["title"], "T")
        self.assertEqual(merged["fieldsFrom"], {"title": "openalex", "venue": "openalex"})
        self.assertEqual(merged["paper"]["summarySource"], "openalex")

    def test_search_by_title_and_resolve(self):
        items = metadata.search_by_title("cucurbituril host guest", 3)
        self.assertEqual(items[0]["doi"], "10.1000/abc")
        self.assertEqual(metadata.resolve(title="cucurbituril")["items"][0]["year"], 2019)
        self.assertEqual(metadata.resolve(doi="10.1000/abc")["paper"]["doi"], "10.1000/abc")
        with self.assertRaises(metadata.MetadataError):
            metadata.resolve()
        self.assertEqual(metadata.pick_date({"issued": {"date-parts": [[2013]]}}), 2013)
        self.assertIsNone(metadata.pick_date({}))
        paper = metadata.to_paper(CROSSREF_DOI["message"])
        self.assertEqual(paper["type"], "journal")
        self.assertEqual(paper["pages"], "100-110")
        self.assertEqual(metadata.rebuild_abstract({"b": [1], "a": [0]}), "a b")


# api 层：状态码
class FakeCtx:
    def __init__(self, query=None, body=None, method="GET"):
        self.method = method
        self._query = dict(query or {})
        self._body = body
        self.services = None
        self.status = None
        self.payload = None

    def query(self, name, default=""):
        value = self._query.get(name)
        return default if value is None else value

    def read_json(self, limit=None):
        return self._body

    def json(self, status, data):
        self.status = status
        self.payload = data

    def fail(self, status, message, extra=None):
        data = {"ok": False, "error": message}
        if extra:
            data.update(extra)
        self.status = status
        self.payload = data


class ApiSearchTest(StubCase):
    routes = [
        ("/openalex/works/doi:10.1000/none", {"status": 404, "body": {"message": "missing"}}),
        ("/crossref/works/10.1000/none", {"status": 404, "body": {"message": "not found"}}),
        ("/openalex/works/doi:", {"body": OPENALEX_DOI}),
        ("/openalex/works", {"body": OPENALEX_SEARCH}),
        ("/crossref/works", {"body": CROSSREF_SEARCH}),
    ]

    def _services(self, papers=None, updates=None):
        papers = list(papers or [])
        updates = updates if updates is not None else {}

        def update(paper_id, patch):
            updates[paper_id] = patch
            return dict(patch)

        db = SimpleNamespace(list_papers=lambda: list(papers), path_for_paper_file=lambda name: None)
        library = SimpleNamespace(all=lambda: list(papers), update=update)
        return SimpleNamespace(db=db, library=library, sources=self.sources,
                               external=external, metadata=metadata)

    def test_external_sources_shape(self):
        ctx = FakeCtx()
        ctx.services = self._services()
        api_search.external_sources(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(set(ctx.payload), {"ok", "sources", "settings"})

    def test_external_search_requires_query_or_filter(self):
        ctx = FakeCtx()
        ctx.services = self._services()
        api_search.external_search(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertIn("请提供查询词", ctx.payload["error"])

    def test_external_search_marks_in_library(self):
        papers = [{"id": "p1", "doi": "10.1000/abc",
                   "title": "Cucurbituril host-guest chemistry in water"}]
        ctx = FakeCtx({"q": "cucurbituril", "sources": "openalex"})
        ctx.services = self._services(papers=papers)
        api_search.external_search(ctx)
        self.assertEqual(ctx.status, 200)
        for key in ("ok", "filters", "query", "items", "sources", "page", "hasMore", "cached"):
            self.assertIn(key, ctx.payload)
        in_lib = [item for item in ctx.payload["items"] if item.get("inLibrary")]
        self.assertEqual(len(in_lib), 1)
        self.assertEqual(in_lib[0]["localId"], "p1")
        self.assertEqual(ctx.payload["filters"]["onlyOA"], False)

    def test_external_search_offline_keeps_per_source_errors(self):
        """与 Node 一致：单源失败不抛错，200 + 每个来源的 error（不是 500）。"""
        _point_to(_closed_port())
        ctx = FakeCtx({"q": "cucurbituril", "sources": "openalex"})
        ctx.services = self._services()
        api_search.external_search(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertFalse(ctx.payload["sources"]["openalex"]["ok"])
        self.assertTrue(ctx.payload["sources"]["openalex"]["error"])
        self.assertEqual(ctx.payload["items"], [])

    def test_openalex_test_always_200(self):
        ctx = FakeCtx(body={}, method="POST")
        ctx.services = self._services()
        api_search.openalex_test(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertTrue(ctx.payload["ok"])
        self.assertIn("quota", ctx.payload)

        _point_to(_closed_port())          # 断网也回 200，但在体里如实报失败
        ctx = FakeCtx(body={}, method="POST")
        ctx.services = self._services()
        api_search.openalex_test(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertFalse(ctx.payload["ok"])
        for key in ("error", "quotaExhausted", "retryAfterSeconds", "settings"):
            self.assertIn(key, ctx.payload)

    def test_fetch_doi_status_codes(self):
        ctx = FakeCtx(body={"doi": "10.1000/abc"}, method="POST")
        ctx.services = self._services()
        api_search.fetch_doi(ctx)
        self.assertEqual(ctx.status, 200)
        for key in ("ok", "paper", "sources", "errors", "fieldsFrom", "elapsedMs"):
            self.assertIn(key, ctx.payload)

        ctx = FakeCtx(body={"doi": "10.1000/none"}, method="POST")
        ctx.services = self._services()
        api_search.fetch_doi(ctx)
        self.assertEqual(ctx.status, 404)

        _point_to(_closed_port())          # 断网 → 502（不是 500，也不是 404）
        ctx = FakeCtx(body={"doi": "10.1000/abc"}, method="POST")
        ctx.services = self._services()
        api_search.fetch_doi(ctx)
        self.assertEqual(ctx.status, 502)
        self.assertTrue(ctx.payload["error"])

    def test_fetch_title_ok_and_502(self):
        ctx = FakeCtx(body={"title": "cucurbituril host guest"}, method="POST")
        ctx.services = self._services()
        api_search.fetch_title(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["items"][0]["doi"], "10.1000/abc")

        _point_to(_closed_port())
        ctx = FakeCtx(body={"title": "x"}, method="POST")
        ctx.services = self._services()
        api_search.fetch_title(ctx)
        self.assertEqual(ctx.status, 502)

    def test_backfill_only_fills_empty_abstracts(self):
        papers = [
            {"id": "p1", "title": "no abstract", "doi": "10.1000/abc", "summary": ""},
            {"id": "p2", "title": "has abstract", "doi": "10.1000/abc", "summary": "已有摘要"}]
        updates = {}
        ctx = FakeCtx(body={}, method="POST")
        ctx.services = self._services(papers=papers, updates=updates)
        api_search.sources_backfill(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["total"], 1)          # 只挑空摘要那篇
        self.assertEqual(ctx.payload["filled"], 1)
        entry = ctx.payload["results"][0]
        self.assertEqual(entry["id"], "p1")
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["source"], "openalex")
        self.assertTrue(updates["p1"]["summary"])
        self.assertNotIn("p2", updates)

    def test_backfill_conflict_and_no_source(self):
        api_search.BACKFILL["running"] = True
        ctx = FakeCtx(body={}, method="POST")
        ctx.services = self._services()
        api_search.sources_backfill(ctx)
        self.assertEqual(ctx.status, 409)
        api_search.BACKFILL["running"] = False

        papers = [{"id": "p3", "title": "x", "doi": "", "summary": ""}]
        ctx = FakeCtx(body={}, method="POST")
        ctx.services = self._services(papers=papers)
        api_search.sources_backfill(ctx)
        self.assertEqual(ctx.payload["filled"], 0)
        self.assertEqual(ctx.payload["results"][0]["error"], api_search.NO_SOURCE)
