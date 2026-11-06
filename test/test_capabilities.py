# -*- coding: utf-8 -*-
"""T6 PaperLibrary 能力移植的单元/端点测试（stdlib unittest + urllib，不装第三方包）。

覆盖：10 条合并新增端点的契约形状、状态码与错误文案，图注 bbox 匹配、PyMuPDF 图表提取、
无 Key 时 compare / deep 的本地降级（G2 离线目标）、119+ 条中英术语检索、Obsidian zip 导出。

运行：python -m unittest test.test_capabilities -v
"""
from __future__ import annotations

import io
import json
import random
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperlib.config import Config  # noqa: E402
from paperlib.data.store import Store  # noqa: E402
from paperlib.http.router import Router  # noqa: E402
from paperlib.http.server import HttpServer  # noqa: E402
from paperlib.http.static import StaticHandler  # noqa: E402
from paperlib.legacy import caption_matcher, compare_engine, deep_analysis, lexicon  # noqa: E402
from paperlib.legacy import figures as legacy_figures  # noqa: E402
from paperlib.routes import ROUTES  # noqa: E402
from paperlib.services import create_services  # noqa: E402

DATA_DIR = tempfile.mkdtemp(prefix="nexus-cap-")


def _noise_png(width=420, height=300, seed=7):
    """造一张有噪声的 PNG（体积远大于 3072 字节，避免被当成装饰图过滤）。"""
    from PIL import Image

    rnd = random.Random(seed)
    image = Image.new("RGB", (width, height))
    image.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                   for _ in range(width * height)])
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def build_fixture_pdf():
    """一页位图 + 一页矢量图，各带一条学术图注（Fig. 1 / Fig. 2）。"""
    import pymupdf

    document = pymupdf.open()
    page = document.new_page()
    page.insert_image(pymupdf.Rect(72, 72, 522, 402), stream=_noise_png())
    page.insert_text((72, 430), "Fig. 1. SEM image of the Cu catalyst surface.", fontsize=10)

    second = document.new_page()
    second.draw_rect(pymupdf.Rect(90, 100, 500, 330), color=(0, 0, 0), width=1)
    for index in range(10):
        y = 120 + index * 20
        second.draw_line(pymupdf.Point(100, y), pymupdf.Point(480, y))
    second.insert_text((90, 360), "Fig. 2. Schematic of the reaction pathway.", fontsize=10)

    blob = document.tobytes()
    document.close()
    return blob


class ServerCase(unittest.TestCase):
    """起一个真实的 HTTP 服务（端口 0 由系统分配，避免占用 4321/4395）。"""

    @classmethod
    def setUpClass(cls):
        config = Config(root=ROOT, data_dir=Path(DATA_DIR), host="127.0.0.1",
                        port=0, no_open=True)
        config.started_at = time.time()
        store = Store(config.data_dir)
        store.load()
        services = create_services(config, store)
        cls.services = services
        cls.store = services.db
        static = StaticHandler(ROOT, config.public_dir, config.shared_dir, config.vendor_dir)
        router = Router(ROUTES, services)
        cls.server = HttpServer(router, static, "127.0.0.1", 0, verbose=False)
        services.runtime.on_shutdown = cls.server.shutdown
        services.runtime.timing["bootMs"] = 1
        cls.server.start_background()
        cls.port = cls.server.httpd.server_address[1]

        cls.pdf = build_fixture_pdf()
        cls.paper_a = services.library.create({
            "title": "Cu-catalyzed asymmetric hydrogenation of ketones",
            "authors": ["A. Author", "B. Author"], "year": 2024, "venue": "JACS",
            "doi": "10.1021/jacs.4b00001", "tags": ["催化", "不对称"],
            "abstract": ("We report a novel Cu-catalyzed asymmetric hydrogenation. The reaction "
                         "runs in toluene at 25 °C for 12 h and gives 95% yield with 99% ee. "
                         "NMR and XPS confirm the product; the mechanism involves a Cu-H "
                         "intermediate. A limitation is the narrow substrate scope."),
        })
        cls.paper_b = services.library.create({
            "title": "Ni-catalyzed cross-coupling of aryl chlorides",
            "authors": ["C. Author"], "year": 2023, "venue": "Nature Chemistry",
            "doi": "10.1038/s41557-023-00001", "tags": ["催化"],
            "abstract": ("A Ni-catalyzed cross-coupling is described. Conditions: THF, 60 °C, "
                         "8 h, yield up to 88%. DFT supports a radical pathway."),
        })
        cls.store.attach_file(cls.paper_a["id"], cls.pdf, {"fileName": "paper-a.pdf"})
        cls.paper_a = cls.store.find_paper(cls.paper_a["id"])

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.close()
        except Exception:  # noqa: BLE001
            pass
        cls.store.repo.close()

    # ---------- HTTP 小工具 ----------
    def call(self, method, path, payload=None, headers=None):
        url = "http://127.0.0.1:%d%s" % (self.port, path)
        data = None
        merged = dict(headers or {})
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            merged.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, method=method, headers=merged)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def get_json(self, path):
        status, _headers, body = self.call("GET", path)
        return status, json.loads(body.decode("utf-8"))

    def post_json(self, path, payload=None):
        status, _headers, body = self.call("POST", path, payload if payload is not None else {})
        return status, json.loads(body.decode("utf-8"))


class TermsTest(unittest.TestCase):
    def test_glossary_size(self):
        self.assertGreaterEqual(lexicon.count(), 119)

    def test_search_chinese_substitution(self):
        items, total = lexicon.search("取代", 20)
        self.assertGreaterEqual(total, 1)
        names = [item["zh"] for item in items]
        self.assertIn("亲核取代", names)

    def test_search_english_and_alias(self):
        items, _total = lexicon.search("nucleophilic substitution", 5)
        self.assertEqual(items[0]["zh"], "亲核取代")
        items, _total = lexicon.search("SN2", 5)
        self.assertEqual(items[0]["zh"], "亲核取代")

    def test_item_shape(self):
        items, _total = lexicon.search("循环伏安", 3)
        self.assertTrue(items)
        item = items[0]
        self.assertEqual(sorted(item.keys()), ["aliases", "en", "field", "zh"])
        self.assertIsInstance(item["aliases"], list)

    def test_limit_applied(self):
        items, total = lexicon.search("电", 3)
        self.assertLessEqual(len(items), 3)
        self.assertGreaterEqual(total, len(items))


class CompareEngineTest(unittest.TestCase):
    def test_templates_shape(self):
        templates = compare_engine.templates_public()
        self.assertGreaterEqual(len(templates), 4)
        ids = [template["id"] for template in templates]
        self.assertIn("synthesis", ids)
        for template in templates:
            self.assertEqual(sorted(template.keys()), ["desc", "fields", "id", "name"])
            self.assertIsInstance(template["fields"], list)
            self.assertGreaterEqual(len(template["fields"]), 4)

    def test_local_degrade_without_key(self):
        class FakeLLM:
            def ready(self):
                return False

        services = _fake_services(FakeLLM())
        papers = [
            {"id": "p1", "title": "Cu catalysis", "abstract":
             "The reaction runs at 25 °C for 12 h and gives 95% yield with 99% ee. NMR confirms it."},
            {"id": "p2", "title": "Ni catalysis", "abstract":
             "Conditions: THF, 60 °C, 8 h, yield 88%."},
        ]
        result = compare_engine.run(services, papers, "synthesis")
        self.assertEqual(sorted(result.keys()), ["oneLine", "sections", "template", "usage"])
        self.assertEqual(result["template"], "synthesis")
        self.assertEqual(result["usage"], {"prompt_tokens": 0, "completion_tokens": 0})
        self.assertIn("未配置大模型", result["oneLine"])
        self.assertTrue(result["sections"])
        section = result["sections"][0]
        self.assertEqual(sorted(section.keys()), ["rows", "title"])
        self.assertEqual(sorted(section["rows"][0].keys()), ["paperId", "value"])

    def test_unknown_template_falls_back_to_general(self):
        self.assertEqual(compare_engine.get_template("不存在")["id"], "general")


class DeepAnalysisTest(unittest.TestCase):
    def test_local_analysis_shape(self):
        class FakeLLM:
            settings = {"model": "test"}

            def ready(self):
                return False

        services = _fake_services(FakeLLM())
        paper = {"id": "p1", "title": "Cu catalysis", "year": 2024, "venue": "JACS",
                 "rating": 4, "tags": ["催化"],
                 "abstract": "We report a novel Cu catalyst. NMR and XPS confirm 95% yield at 25 °C."}
        analysis = deep_analysis.run(services, paper, [{"fig_index": 1}], "x" * 9000)
        self.assertEqual(sorted(analysis.keys()),
                         ["fields", "model", "oneLine", "paperId", "scores", "updatedAt"])
        self.assertEqual(analysis["model"], deep_analysis.LOCAL_MODEL)
        self.assertEqual(set(analysis["scores"].keys()),
                         {"创新性", "严谨性", "影响力", "可复现性", "可读性"})
        for value in analysis["scores"].values():
            self.assertIsInstance(value, int)
            self.assertTrue(1 <= value <= 5)
        note = " ".join(analysis["fields"].values())
        self.assertIn("未配置大模型", note)


class LlmReadinessTest(unittest.TestCase):
    """真实 LLM 适配器未配置 Key 时，compare/deep 必须本地降级且**不触网**（离线目标 G2）。"""

    def test_unconfigured_llm_is_never_called(self):
        from paperlib.adapters.llm import LLM

        llm = LLM(tempfile.mkdtemp(prefix="nexus-llm-"))
        calls = []
        llm.chat = lambda *args, **kwargs: calls.append(1) or {"text": "{}"}
        services = _fake_services(llm)

        result = compare_engine.run(services, [{"id": "p1", "title": "A"}, {"id": "p2", "title": "B"}],
                                    "general")
        self.assertIn("未配置大模型", result["oneLine"])
        analysis = deep_analysis.run(services, {"id": "p1", "title": "A"}, [], "")
        self.assertEqual(analysis["model"], deep_analysis.LOCAL_MODEL)
        self.assertEqual(calls, [])


class LlmPathTest(unittest.TestCase):
    """配置了模型（ready=True）时，走 LLM 结果，并把 usage 累加到响应里。"""

    class ReadyLLM:
        settings = {"model": "fake-model"}

        def ready(self):
            return {"ready": True}

        def chat(self, messages=None, options=None):
            return {"text": '{"substrate": "Cu(OTf)2", "yield": "95%",'
                            ' "研究问题": "q", "scores": {"创新性": 5},'
                            ' "oneLine": "模型一句话"}',
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def test_deep_uses_llm_values(self):
        analysis = deep_analysis.run(_fake_services(self.ReadyLLM()),
                                     {"id": "p1", "title": "T"}, [], "")
        self.assertEqual(analysis["model"], "fake-model")
        self.assertEqual(analysis["fields"]["研究问题"], "q")
        self.assertEqual(analysis["oneLine"], "模型一句话")
        self.assertEqual(analysis["scores"]["创新性"], 5)

    def test_compare_uses_llm_values(self):
        result = compare_engine.run(_fake_services(self.ReadyLLM()),
                                    [{"id": "p1", "title": "A"}, {"id": "p2", "title": "B"}],
                                    "synthesis")
        self.assertNotIn("未配置大模型", result["oneLine"])
        self.assertEqual(result["usage"], {"prompt_tokens": 20, "completion_tokens": 10})
        first = result["sections"][0]
        self.assertEqual(first["title"], "原料")
        self.assertEqual(first["rows"][0]["value"], "Cu(OTf)2")


def _fake_services(llm):
    class FakeRepo:
        @staticmethod
        def list_figures(paper_id=None):
            return []

    class FakeDB:
        repo = FakeRepo()

        @staticmethod
        def get_text(paper_id):
            return ""

    class Services:
        pass

    services = Services()
    services.llm = llm
    services.db = FakeDB()
    return services


class CaptionMatcherTest(unittest.TestCase):
    def test_bbox_distance_below_is_best(self):
        below = caption_matcher.compute_bbox_distance((100, 100, 400, 300), (100, 320, 400, 340))
        above = caption_matcher.compute_bbox_distance((100, 100, 400, 300), (100, 40, 400, 80))
        far = caption_matcher.compute_bbox_distance((100, 100, 400, 300), (100, 900, 400, 920))
        self.assertGreater(below, above)
        self.assertLess(far, 0)

    def test_greedy_assignment(self):
        captions = [
            {"bbox": (100, 320, 400, 340), "text": "Fig. 1. First.", "label": "1", "is_head": True},
            {"bbox": (100, 720, 400, 740), "text": "Fig. 2. Second.", "label": "2", "is_head": True},
        ]
        boxes = [(11, (100, 100, 400, 300)), (22, (100, 500, 400, 700))]
        assigned = caption_matcher.match_boxes_with_captions(boxes, captions)
        self.assertEqual(assigned[11], "Fig. 1. First.")
        self.assertEqual(assigned[22], "Fig. 2. Second.")


class FigureExtractionTest(unittest.TestCase):
    def test_extract_from_pdf(self):
        directory = Path(tempfile.mkdtemp(prefix="nexus-fig-"))
        pdf_path = directory / "demo.pdf"
        pdf_path.write_bytes(build_fixture_pdf())
        result = legacy_figures.extract_pdf_figures(pdf_path, "paper-x", directory)
        self.assertTrue(result["ok"])
        rows = result["figures"]
        self.assertGreaterEqual(len(rows), 2)
        types = {row["figureType"] for row in rows}
        self.assertIn("raster", types)
        self.assertIn("vector_render", types)
        for row in rows:
            self.assertTrue(row["imagePath"].startswith("images/paper-x/"))
            resolved = legacy_figures.resolve_image_path(directory, row["imagePath"])
            self.assertTrue(resolved and Path(resolved).is_file())
        captions = " ".join(row["caption"] for row in rows)
        self.assertIn("Fig. 1", captions)
        self.assertIn("Fig. 2", captions)

    def test_resolve_rejects_escape(self):
        directory = Path(tempfile.mkdtemp(prefix="nexus-fig2-"))
        self.assertIsNone(legacy_figures.resolve_image_path(directory, "../etc/passwd"))


class EndpointTest(ServerCase):
    """10 条端点的契约形状、状态码与错误文案。"""

    def test_figures_flow(self):
        status, payload = self.get_json("/api/figures")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(sorted(payload.keys()),
                         ["items", "ok", "page", "pageCount", "pageSize", "total"])
        self.assertEqual(payload["page"], 1)
        self.assertEqual(payload["pageSize"], 48)

        status, extracted = self.post_json("/api/papers/%s/figures/extract" % self.paper_a["id"], {})
        self.assertEqual(status, 200)
        self.assertTrue(extracted["ok"])
        self.assertGreaterEqual(extracted["extracted"], 2)
        figure = extracted["figures"][0]
        self.assertEqual(sorted(figure.keys()),
                         ["aiConclusion", "aiDescription", "caption", "contextText", "figIndex",
                          "figureType", "hasImage", "id", "imagePath", "paperId"])
        self.assertTrue(figure["hasImage"])

        status, gallery = self.get_json("/api/figures")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(gallery["total"], 2)
        self.assertIn("paperTitle", gallery["items"][0])
        self.assertEqual(gallery["items"][0]["paperTitle"], self.paper_a["title"])

        status, scoped = self.get_json("/api/papers/%s/figures" % self.paper_a["id"])
        self.assertEqual(status, 200)
        self.assertEqual(sorted(scoped.keys()), ["figures", "ok", "paperId"])
        self.assertNotIn("paperTitle", scoped["figures"][0])

        # 中文查询经术语表扩展后能命中英文图注（SEM）
        status, zh = self.get_json("/api/figures?q=%E6%89%AB%E6%8F%8F%E7%94%B5%E9%95%9C")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(zh["total"], 1)

        # 图片流 + Range
        status, headers, body = self.call("GET", "/api/figures/%d/image" % figure["id"])
        self.assertEqual(status, 200)
        self.assertIn("image/png", headers.get("Content-Type", ""))
        self.assertGreater(len(body), 100)
        status, headers, part = self.call("GET", "/api/figures/%d/image" % figure["id"],
                                          headers={"Range": "bytes=0-99"})
        self.assertEqual(status, 206)
        self.assertEqual(len(part), 100)

    def test_figure_image_head_and_paging(self):
        status, payload = self.post_json("/api/papers/%s/figures/extract" % self.paper_a["id"], {})
        self.assertEqual(status, 200)
        figure_id = payload["figures"][0]["id"]
        status, headers, body = self.call("HEAD", "/api/figures/%d/image" % figure_id)
        self.assertEqual(status, 200)
        self.assertIn("image/", headers.get("Content-Type", ""))
        self.assertEqual(body, b"")

        status, payload = self.get_json("/api/figures?page=2&pageSize=1")
        self.assertEqual(status, 200)
        self.assertEqual(payload["page"], 2)
        self.assertEqual(payload["pageSize"], 1)
        self.assertLessEqual(len(payload["items"]), 1)
        self.assertGreaterEqual(payload["pageCount"], 2)

    def test_figure_image_404(self):
        status, payload = self.get_json("/api/figures/999999/image")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "图表不存在")

    def test_extract_without_pdf(self):
        status, payload = self.post_json("/api/papers/%s/figures/extract" % self.paper_b["id"], {})
        self.assertEqual(status, 400)
        self.assertEqual(payload["error"], "这条文献没有关联 PDF")

    def test_compare_templates_and_run(self):
        status, payload = self.get_json("/api/compare/templates")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertGreaterEqual(len(payload["templates"]), 4)
        self.assertEqual(sorted(payload["templates"][0].keys()), ["desc", "fields", "id", "name"])

        status, payload = self.post_json("/api/compare", {
            "paperIds": [self.paper_a["id"], self.paper_b["id"]], "template": "synthesis"})
        self.assertEqual(status, 200)
        result = payload["result"]
        self.assertEqual(sorted(result.keys()), ["oneLine", "sections", "template", "usage"])
        self.assertEqual(result["template"], "synthesis")
        self.assertIn("未配置大模型", result["oneLine"])
        self.assertEqual(len(result["sections"][0]["rows"]), 2)
        self.assertEqual(result["sections"][0]["rows"][0]["paperId"], self.paper_a["id"])

    def test_compare_validation(self):
        status, payload = self.post_json("/api/compare", {"paperIds": [self.paper_a["id"]]})
        self.assertEqual(status, 400)
        self.assertIn("至少", payload["error"])

        many = ["paper-%d" % index for index in range(9)]
        status, payload = self.post_json("/api/compare", {"paperIds": many})
        self.assertEqual(status, 400)
        self.assertIn("最多", payload["error"])

        status, payload = self.post_json("/api/compare", {
            "paperIds": [self.paper_a["id"], "not-a-paper"]})
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "找不到这条文献")

    def test_deep_analysis_offline(self):
        path = "/api/papers/%s/deep-analysis" % self.paper_a["id"]
        status, payload = self.get_json(path)
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertIn("analysis", payload)

        status, payload = self.post_json(path, {"force": True})
        self.assertEqual(status, 200)
        analysis = payload["analysis"]
        self.assertTrue(payload["saved"])
        self.assertEqual(sorted(analysis.keys()),
                         ["fields", "model", "oneLine", "paperId", "scores", "updatedAt"])
        self.assertEqual(analysis["paperId"], self.paper_a["id"])
        self.assertEqual(set(analysis["scores"].keys()),
                         {"创新性", "严谨性", "影响力", "可复现性", "可读性"})
        self.assertIn("未配置大模型", json.dumps(analysis["fields"], ensure_ascii=False))

        status, cached = self.get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(cached["analysis"]["updatedAt"], analysis["updatedAt"])

    def test_deep_analysis_404(self):
        status, payload = self.get_json("/api/papers/nope/deep-analysis")
        self.assertEqual(status, 404)
        self.assertEqual(payload["error"], "找不到这条文献")

    def test_terms_endpoint(self):
        status, payload = self.get_json("/api/terms/search?q=%E5%8F%96%E4%BB%A3")
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(sorted(payload.keys()), ["items", "ok", "query", "total"])
        self.assertEqual(payload["query"], "取代")
        self.assertGreaterEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["zh"], "取代反应")

        status, payload = self.get_json("/api/terms/search")
        self.assertEqual(status, 400)
        self.assertTrue(payload["error"])

    def test_export_obsidian_zip(self):
        # 先确保有图表可导出（本用例不依赖其它用例的执行顺序）
        self.post_json("/api/papers/%s/figures/extract" % self.paper_a["id"], {})
        status, headers, body = self.call("GET", "/api/export/obsidian")
        self.assertEqual(status, 200)
        disposition = headers.get("Content-Disposition", "")
        self.assertIn("attachment", disposition)
        self.assertIn("paperlibrary-obsidian-", disposition)
        self.assertTrue(disposition.rstrip('"').endswith(".zip"))
        archive = zipfile.ZipFile(io.BytesIO(body))
        names = archive.namelist()
        notes = [name for name in names if name.endswith(".md")]
        self.assertEqual(len(notes), 2)
        media = [name for name in names if name.startswith("media/")]
        self.assertGreaterEqual(len(media), 1)
        text = archive.read(notes[0]).decode("utf-8")
        self.assertIn("document_id:", text)
        self.assertIn("title:", text)

    def test_routes_registered(self):
        paths = [route["pattern"] for route in ROUTES]
        for pattern in ("/api/figures", "/api/figures/:id/image", "/api/papers/:id/figures",
                        "/api/papers/:id/figures/extract", "/api/compare/templates",
                        "/api/compare", "/api/papers/:id/deep-analysis",
                        "/api/terms/search", "/api/export/obsidian"):
            self.assertIn(pattern, paths)


if __name__ == "__main__":
    unittest.main(verbosity=2)
