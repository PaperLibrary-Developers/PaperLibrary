"""Copilot（全库智能问答助手）自测。

覆盖：问题分词与相关性排序、资料包组装、未配置模型时的「统计类问题离线回答」、
配置模型后的 SSE 帧与来源、空问题 400、overview 接口。

跑法：python -m unittest test.test_copilot -v
"""
from __future__ import annotations

import unittest

from paperlib.api import copilot as copilot_api
from paperlib.domain import copilot as copilot_domain
from test.test_ai import AiTestBase, FakeCtx, delta


class CopilotTests(AiTestBase):
    def seed(self):
        services = self.make_services()
        # 分类文件夹是独立的一张表：直接插 papers 不会自动建，这里显式建出来
        services.folders.ensure("电催化/钙钛矿")
        services.folders.ensure("电催化")
        self.cal = self.add_paper(
            title="钙钛矿太阳能电池的长期稳定性", year=2024, type="journal",
            summary="综述钙钛矿电池的降解机理与封装策略。", tags=["钙钛矿"], folder="电催化/钙钛矿")
        self.cat = self.add_paper(
            title="单原子催化剂的构效关系", year=2023, type="journal",
            summary="讨论电催化中的单原子位点。", tags=["电催化"], folder="电催化")
        self.other = self.add_paper(title="未分类的预印本", year=2025, type="preprint")
        return services

    # ---------------- 检索与排序 ----------------
    def test_title_hit_outranks_summary_hit(self):
        services = self.seed()
        built = copilot_domain.build_context(services, "钙钛矿的稳定性")
        ids = [row["id"] for row in built["sources"]]
        self.assertEqual(ids[0], self.cal["id"])
        self.assertIn(self.cal["title"], built["context"])
        self.assertIn("[1]", built["context"])

    def test_unrelated_question_has_no_sources(self):
        services = self.seed()
        built = copilot_domain.build_context(services, "量子色动力学的渐近自由")
        self.assertEqual(built["sources"], [])
        self.assertEqual(built["context"], "")

    def test_source_rows_carry_folder_for_ui(self):
        services = self.seed()
        built = copilot_domain.build_context(services, "钙钛矿")
        row = built["sources"][0]
        self.assertEqual(row["folder"], "电催化/钙钛矿")
        self.assertEqual(row["year"], 2024)
        self.assertTrue(row["title"])

    # ---------------- 统计与离线回答 ----------------
    def test_stats_snapshot_counts_and_labels(self):
        services = self.seed()
        snapshot = copilot_domain.stats_snapshot(services)
        self.assertEqual(snapshot["total"], 3)
        self.assertEqual(snapshot["tags"], 2)
        self.assertEqual(snapshot["folders"], 2)
        self.assertEqual(snapshot["rootCount"], 1)
        self.assertEqual(snapshot["yearRange"], [2023, 2025])
        text = copilot_domain.stats_markdown(snapshot)
        self.assertIn("共 **3** 篇", text)
        self.assertIn("期刊 2", text)
        self.assertIn("预印本 1", text)
        self.assertIn("#钙钛矿", text)

    def test_empty_library_stats_is_friendly(self):
        self.make_services()
        text = copilot_domain.stats_markdown(copilot_domain.stats_snapshot(self.services))
        self.assertIn("还是空的", text)

    # ---------------- 未配置模型（离线引擎） ----------------
    def test_no_key_usage_question_is_answered_offline(self):
        services = self.seed()
        ctx = FakeCtx(services)
        result = copilot_domain.ask_stream(services, {"question": "怎么导入文献？"}, copilot_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertTrue(frames[0]["offline"])
        self.assertEqual(frames[0]["engine"], "offline")
        self.assertEqual(frames[0]["intent"], "import")
        self.assertEqual(frames[-1]["type"], "done")
        self.assertIn("新增文献", frames[-1]["text"])
        # 每条离线回答都带可点的入口，新手不用自己找菜单
        self.assertIn("#/new", [item["hash"] for item in frames[-1]["actions"]])

    def test_offline_rules_cover_the_four_required_topics(self):
        services = self.seed()
        cases = {
            "怎么导入文献": "import",
            "怎么搜图表": "figures",
            "如何导出 BibTeX": "cite",
            "当前库藏数据概况": "stats",
            "PDF 阅读器怎么翻页": "pdf",
            "怎么用 #标签 检索": "search",
            "怎么备份换电脑": "backup",
        }
        for question, intent in cases.items():
            with self.subTest(question=question):
                reply = copilot_domain.offline_answer(services, question)
                self.assertEqual(reply["intent"], intent)
                self.assertTrue(reply["answer"].strip())

    def test_offline_uses_library_context_when_no_rule_matches(self):
        services = self.seed()
        reply = copilot_domain.offline_answer(services, "钙钛矿稳定性")
        self.assertEqual(reply["intent"], "library")
        self.assertTrue(reply["answer"].startswith("**库里检索到"))
        self.assertEqual([row["title"] for row in reply["sources"]],
                         ["钙钛矿太阳能电池的长期稳定性"])

    def test_offline_fallback_suggests_next_steps(self):
        services = self.seed()
        reply = copilot_domain.offline_answer(services, "今天天气怎么样")
        self.assertEqual(reply["intent"], "fallback")
        self.assertIn("导入", reply["answer"])
        self.assertTrue(reply["actions"])

    def test_no_key_but_stats_question_answers_offline(self):
        services = self.seed()
        ctx = FakeCtx(services)
        result = copilot_domain.ask_stream(services, {"question": "当前文献库的库藏概况如何？"}, copilot_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertEqual([frame["type"] for frame in frames[:2]], ["start", "delta"])
        self.assertTrue(frames[0]["offline"])
        self.assertEqual(frames[0]["sources"], [])
        self.assertEqual(frames[-1]["type"], "done")
        self.assertIn("共 **3** 篇", frames[-1]["text"])

    def test_empty_question_is_400(self):
        services = self.seed()
        ctx = FakeCtx(services)
        result = copilot_domain.ask_stream(services, {"question": "   "}, copilot_api.StreamOpener(ctx))
        self.assertEqual(result["status"], 400)
        self.assertFalse(ctx.started)

    # ---------------- 配置了模型 ----------------
    def test_stream_with_model_sends_sources_and_context(self):
        services = self.make_services(api_key="sk-test")
        self.cal = self.add_paper(title="钙钛矿太阳能电池的长期稳定性", year=2024,
                                  summary="降解机理。", tags=["钙钛矿"])
        self.fake.set({"sse": [delta("根据 [1]，"), delta("稳定性受湿度影响。")]})
        ctx = FakeCtx(services)
        result = copilot_domain.ask_stream(
            services, {"question": "钙钛矿稳定性受什么影响？"}, copilot_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertEqual(frames[0]["type"], "start")
        self.assertTrue(frames[0]["ready"])
        self.assertEqual([row["id"] for row in frames[0]["sources"]], [self.cal["id"]])
        self.assertEqual(frames[-1]["type"], "done")
        self.assertIn("稳定性受湿度影响", frames[-1]["text"])

        # 发给模型的 messages：系统提示 + 材料包 + 用户问题
        body = self.fake.server.last_body or {}
        messages = body.get("messages") or []
        self.assertEqual(messages[-1]["role"], "user")
        material = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
        self.assertIn("功能说明", material)
        self.assertIn("钙钛矿太阳能电池的长期稳定性", material)
        self.assertIn("库藏统计", material)

    def test_history_is_truncated_and_passed_through(self):
        services = self.make_services(api_key="sk-test")
        self.add_paper(title="钙钛矿太阳能电池", year=2024, summary="降解机理。")
        self.fake.set({"sse": [delta("好")]})
        history = [{"role": "user" if i % 2 == 0 else "assistant", "content": "第%d轮" % i} for i in range(20)]
        ctx = FakeCtx(services)
        copilot_domain.ask_stream(services, {"question": "钙钛矿呢？", "history": history},
                                  copilot_api.StreamOpener(ctx))
        messages = (self.fake.server.last_body or {}).get("messages") or []
        self.assertLessEqual(len(messages), 2 + copilot_domain.HISTORY_LIMIT + 1)
        self.assertIn("第19轮", [m.get("content") for m in messages])

    def test_model_error_degrades_to_offline_instead_of_failing(self):
        services = self.make_services(api_key="sk-test")
        self.add_paper(title="钙钛矿太阳能电池", year=2024)
        self.fake.set({"status": 500, "json": {"error": "boom"}})
        ctx = FakeCtx(services)
        result = copilot_domain.ask_stream(services, {"question": "怎么导入文献"}, copilot_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        # 降级：不抛 error 帧，最后给一条带离线知识库答案的 done
        self.assertNotIn("error", [frame["type"] for frame in frames])
        last = frames[-1]
        self.assertEqual(last["type"], "done")
        self.assertTrue(last["offline"])
        self.assertTrue(last["degraded"])
        self.assertIn("模型调用失败", last["text"])
        self.assertIn("新增文献", last["text"])

    # ---------------- overview ----------------
    def test_overview_reports_readiness_and_stats(self):
        services = self.seed()
        ctx = FakeCtx(services, method="GET")
        copilot_api.overview(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertFalse(ctx.payload["ready"])
        self.assertEqual(ctx.payload["engine"], "offline")
        self.assertTrue(ctx.payload["providerLabel"])
        self.assertEqual(ctx.payload["stats"]["total"], 3)
        self.assertTrue(ctx.payload["stats"]["byType"])


if __name__ == "__main__":
    unittest.main()
