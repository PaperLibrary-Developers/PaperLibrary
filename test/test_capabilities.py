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

DATA_DIR = tempfile.mkdtemp(prefix="helix-cap-")


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
    page.insert_image(pymupdf.Rect(460, 760, 570, 810), stream=_noise_png(seed=8))

    second = document.new_page()
    second.draw_rect(pymupdf.Rect(90, 100, 500, 330), color=(0, 0, 0), width=1)
    for index in range(10):
        y = 120 + index * 20
        second.draw_line(pymupdf.Point(100, y), pymupdf.Point(480, y))
    second.insert_text((90, 360), "Fig. 2. Schematic of the reaction pathway.", fontsize=10)
    cover = document.new_page()
    cover.insert_image(cover.rect, stream=_noise_png(seed=9), keep_proportion=False)

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
    def test_glossary_size_and_shape(self):
        self.assertGreaterEqual(lexicon.count(), 119)
        items, _total = lexicon.search("循环伏安", 3)
        self.assertTrue(items)
        item = items[0]
        self.assertEqual(sorted(item.keys()), ["aliases", "en", "field", "zh"])
        self.assertIsInstance(item["aliases"], list)

    def test_search_zh_en_alias_and_limit(self):
        items, total = lexicon.search("取代", 20)
        self.assertGreaterEqual(total, 1)
        self.assertIn("亲核取代", [item["zh"] for item in items])
        for query in ("nucleophilic substitution", "SN2"):
            hits, _total = lexicon.search(query, 5)
            self.assertEqual(hits[0]["zh"], "亲核取代")
        limited, total = lexicon.search("电", 3)
        self.assertLessEqual(len(limited), 3)
        self.assertGreaterEqual(total, len(limited))


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
        # 注意：合并后的卡片字段是 summary（原摘要），normalize_paper 不保留 abstract
        paper = {"id": "p1", "title": "Cu catalysis", "year": 2024, "venue": "JACS",
                 "rating": 4, "tags": ["催化"],
                 "summary": "We report a novel Cu catalyst. NMR and XPS confirm 95% yield at 25 °C."}
        analysis = deep_analysis.run(services, paper, [{"fig_index": 1}], "x" * 9000)
        self.assertEqual(sorted(analysis.keys()),
                         ["fields", "model", "oneLine", "paperId", "scores", "updatedAt"])
        self.assertEqual(analysis["model"], deep_analysis.LOCAL_MODEL)
        self.assertEqual(set(analysis["scores"].keys()),
                         {"创新性", "严谨性", "影响力", "可复现性", "可读性"})
        for value in analysis["scores"].values():
            self.assertIsInstance(value, int)
            self.assertTrue(1 <= value <= 5)

        # 22 字段保真：24 个语义槽位 + 评分理由 + 分析模式，一个都不能少
        from paperlib.legacy import deep_prompt

        self.assertGreaterEqual(len(deep_prompt.SLOT_NAMES), 22)
        self.assertGreaterEqual(len(analysis["fields"]), 22)
        self.assertEqual(set(deep_prompt.FIELDS) - set(analysis["fields"]), set(),
                         "fields 缺少语义槽位：" + ", ".join(
                             sorted(set(deep_prompt.FIELDS) - set(analysis["fields"]))))
        for name, _path, _kind in deep_prompt.FIELD_SLOTS:
            self.assertTrue(str(analysis["fields"][name]).strip(), name + " 不该为空")
        note = analysis["fields"]["分析模式"]
        self.assertIn("未配置大模型", note)
        self.assertIn("本地", note)
        self.assertIn("We report a novel Cu catalyst", analysis["fields"]["原文摘要"])


class DeepAnalysisOutputBudgetTest(unittest.TestCase):
    """回归：24 槽位 JSON 的输出预算必须够（旧值 2400 会被截断 → 静默退回本地结果）。"""

    class CapturingLLM:
        settings = {"model": "fake-model"}

        def __init__(self):
            self.options = []
            self.calls = 0

        def ready(self):
            return {"ready": True}

        def chat(self, messages=None, options=None):
            self.calls += 1
            self.options.append(dict(options or {}))
            return {"text": ""}

    def test_ready_model_gets_enough_output_tokens(self):
        llm = self.CapturingLLM()
        analysis = deep_analysis.run(_fake_services(llm),
                                     {"id": "p1", "title": "T"}, [], "")
        self.assertEqual(analysis["model"], deep_analysis.LOCAL_MODEL)
        # 调用本身就失败时不必再重问一次（旧实现会白等两倍超时）
        self.assertEqual(llm.calls, 1)
        self.assertGreaterEqual(llm.options[0].get("maxTokens"), 4000)
        # 已配置模型却失败：必须说明原因，不能显示成「未配置大模型」
        self.assertIn("大模型调用失败", analysis["fields"]["分析模式"])
        self.assertIn("本地分析", analysis["fields"]["分析模式"])

    def test_truncated_json_degrades_with_reason(self):
        class TruncatedLLM:
            settings = {"model": "fake-model"}

            def ready(self):
                return {"ready": True}

            def chat(self, messages=None, options=None):
                # 与真实故障一致：JSON 在中间被截断（旧预算 2400 的典型表现）
                return {"text": '{"one_liner": "Cu 催化", "problem": "背景'}

        analysis = deep_analysis.run(_fake_services(TruncatedLLM()),
                                     {"id": "p1", "title": "T"}, [], "")
        self.assertEqual(analysis["model"], deep_analysis.LOCAL_MODEL)
        self.assertIn("大模型调用失败", analysis["fields"]["分析模式"])
        self.assertIn("本地分析", analysis["fields"]["分析模式"])


class LlmReadinessTest(unittest.TestCase):
    """真实 LLM 适配器未配置 Key 时，compare/deep 必须本地降级且**不触网**（离线目标 G2）。"""

    def test_unconfigured_llm_is_never_called(self):
        from paperlib.adapters.llm import LLM

        llm = LLM(tempfile.mkdtemp(prefix="helix-llm-"))
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
            import json as _json

            # 原始 deep_analysis SYSTEM 的 JSON 结构（英文键 + experiments/applicability 嵌套）
            payload = {
                "one_liner": "Cu 催化不对称氢化 95% 产率",
                "original_abstract": "We report ...",
                "zh_overview": "中文概述",
                "problem": "背景问题",
                "method_core": "核心方法",
                "method_steps": ["步骤一", "步骤二"],
                "formulas": [{"latex": "ee=(R-S)/(R+S)", "meaning": "对映体过量"}],
                "experiments": {"systems": "酮类底物", "baselines": "无催化对照",
                                "conditions": "25 °C, 12 h, 甲苯", "metrics": "产率/ee",
                                "main_results": "95% 产率, 99% ee", "ablations": "重复三次"},
                "key_points": ["要点1", "要点2"],
                "contributions": ["贡献1"],
                "author_claims": ["主张1"],
                "evidence_judgement": "证据足够",
                "strengths": ["优势1"],
                "limitations_stated": ["底物范围窄"],
                "limitations_inferred": ["未做放大"],
                "applicability": {"when": "小试", "when_not": "吨级"},
                "related": ["相关工作1"],
                "open_questions": ["问题1"],
                "scores": {"novelty": {"score": 8, "reason": "新反应"},
                           "technical": {"score": 7, "reason": "表征全"},
                           "evidence": {"score": 6, "reason": "缺对照"},
                           "writing": {"score": 9, "reason": "表达清楚"},
                           "usefulness": {"score": 7, "reason": "可落地"}},
            }
            return {"text": _json.dumps(payload, ensure_ascii=False),
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def test_deep_uses_llm_values(self):
        from paperlib.legacy import deep_prompt

        analysis = deep_analysis.run(_fake_services(self.ReadyLLM()),
                                     {"id": "p1", "title": "T"}, [], "")
        self.assertEqual(analysis["model"], "fake-model")
        self.assertEqual(set(deep_prompt.FIELDS) - set(analysis["fields"]), set())
        self.assertEqual(analysis["oneLine"], "Cu 催化不对称氢化 95% 产率")
        self.assertEqual(analysis["fields"]["研究背景"], "背景问题")
        self.assertEqual(analysis["fields"]["方法步骤"], "步骤一；步骤二")
        self.assertEqual(analysis["fields"]["实验条件"], "25 °C, 12 h, 甲苯")
        self.assertEqual(analysis["fields"]["适用条件"], "小试")
        self.assertIn("ee=(R-S)/(R+S)", analysis["fields"]["关键公式"])
        self.assertIn("新反应", analysis["fields"]["评分理由"])
        self.assertIn("大模型分析", analysis["fields"]["分析模式"])
        # 原提示词 0-10 分 → 契约 1-5 分（8→4、9→5 四舍五入）
        self.assertEqual(analysis["scores"]["创新性"], 4)
        self.assertEqual(analysis["scores"]["可读性"], 5)
        self.assertEqual(analysis["scores"]["严谨性"], 4)

    class CompareReadyLLM:
        """compare 的 Map 步骤：每篇返回一组字段值 JSON（键名 = 模板 field key）。"""

        settings = {"model": "fake-model"}

        def ready(self):
            return {"ready": True}

        def chat(self, messages=None, options=None):
            return {"text": '{"substrate": "Cu(OTf)2", "yield": "95%",'
                            ' "conditions": "25 °C, 12 h"}',
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    def test_compare_uses_llm_values(self):
        result = compare_engine.run(_fake_services(self.CompareReadyLLM()),
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
    def test_distance_and_greedy_assignment(self):
        below = caption_matcher.compute_bbox_distance((100, 100, 400, 300), (100, 320, 400, 340))
        above = caption_matcher.compute_bbox_distance((100, 100, 400, 300), (100, 40, 400, 80))
        far = caption_matcher.compute_bbox_distance((100, 100, 400, 300), (100, 900, 400, 920))
        self.assertGreater(below, above)
        self.assertLess(far, 0)
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
        directory = Path(tempfile.mkdtemp(prefix="helix-fig-"))
        pdf_path = directory / "demo.pdf"
        pdf_path.write_bytes(build_fixture_pdf())
        result = legacy_figures.extract_pdf_figures(pdf_path, "paper-x", directory)
        self.assertTrue(result["ok"])
        rows = result["figures"]
        self.assertEqual(len(rows), 2)
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
        directory = Path(tempfile.mkdtemp(prefix="helix-fig2-"))
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
        second = extracted["figures"][1]
        status, detail = self.get_json("/api/papers/%s?figure=%d" % (self.paper_a["id"], second["id"]))
        self.assertEqual(status, 200)
        self.assertEqual(detail["figureLocation"]["page"], 2)
        self.assertTrue(0.3 < detail["figureLocation"]["y"] < 0.6)
        with self.store.repo.conn:
            self.store.repo.conn.execute("UPDATE figures SET pdf_location = '' WHERE id = ?", (second["id"],))
        _, legacy_detail = self.get_json("/api/papers/%s?figure=%d" % (self.paper_a["id"], second["id"]))
        self.assertEqual(legacy_detail["figureLocation"], detail["figureLocation"])

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
        status, headers, head_body = self.call("HEAD", "/api/figures/%d/image" % figure["id"])
        self.assertEqual(status, 200)
        self.assertEqual(head_body, b"")

        status, paged = self.get_json("/api/figures?page=2&pageSize=1")
        self.assertEqual((status, paged["page"], paged["pageSize"]), (200, 2, 1))
        self.assertLessEqual(len(paged["items"]), 1)
        self.assertGreaterEqual(paged["pageCount"], 2)

        status, payload = self.get_json("/api/figures/999999/image")
        self.assertEqual((status, payload["error"]), (404, "图表不存在"))
        status, payload = self.post_json("/api/papers/%s/figures/extract" % self.paper_b["id"], {})
        self.assertEqual((status, payload["error"]), (400, "这条文献没有关联 PDF"))

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

        # 校验：少 / 多 / 不存在
        status, payload = self.post_json("/api/compare", {"paperIds": [self.paper_a["id"]]})
        self.assertEqual(status, 400)
        self.assertIn("至少", payload["error"])
        status, payload = self.post_json(
            "/api/compare", {"paperIds": ["paper-%d" % index for index in range(9)]})
        self.assertEqual(status, 400)
        self.assertIn("最多", payload["error"])
        status, payload = self.post_json(
            "/api/compare", {"paperIds": [self.paper_a["id"], "not-a-paper"]})
        self.assertEqual((status, payload["error"]), (404, "找不到这条文献"))

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
        # 22 字段保真：24 语义槽位 + 评分理由 + 分析模式都要在端点响应里
        from paperlib.legacy import deep_prompt

        self.assertEqual(set(deep_prompt.FIELDS) - set(analysis["fields"]), set())
        self.assertGreaterEqual(len(analysis["fields"]), 22)
        self.assertIn("未配置大模型", analysis["fields"]["分析模式"])
        self.assertIn("未配置大模型", json.dumps(analysis["fields"], ensure_ascii=False))

        status, cached = self.get_json(path)
        self.assertEqual(status, 200)
        self.assertEqual(cached["analysis"]["updatedAt"], analysis["updatedAt"])
        status, payload = self.get_json("/api/papers/nope/deep-analysis")
        self.assertEqual((status, payload["error"]), (404, "找不到这条文献"))

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
