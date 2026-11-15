# -*- coding: utf-8 -*-
"""全文读取回归：正文 + 全部 SI 必须完整进入 AI 总结 / 对话的处理流程。

旧实现只取 1.2–2.4 万字节选（正文头尾、SI 前 4000 字/合计 12000 字）；这里验证：
  · 总量不大时一次完整送入，不截断；
  · 超过阈值时分段 map-reduce，逐段都调用模型，最终汇总覆盖全文；
  · 覆盖信息（bodyChars/siChars/chunks/processedChunks/attachments）如实上报；
  · SI 文本缺失时会尝试重新抽取。
"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from paperlib.api import ai as ai_api
from paperlib.api import chat as chat_api
from paperlib.domain import ai as ai_domain
from paperlib.domain import chat as chat_domain
from paperlib.adapters.llm import LLMError
from paperlib.domain import fullread
from test.test_ai import AiTestBase, FakeCtx


class RecordingLLM:
    """记录每次模型调用的假 LLM：chat 用于 map，stream 用于最终回答。"""

    def __init__(self, answer="最终回答"):
        self.settings = {"model": "fake-fullread", "promptTemplate": "请输出中文总结。"}
        self.answer = answer
        self.chat_calls = []
        self.stream_calls = []

    def readiness(self):
        return {"ready": True, "reason": ""}

    def build_user_message(self, paper, excerpt, label="正文节选（可能被截断）"):
        return ("标题：" + str(paper.get("title") or "") + "\n\n===== "
                + str(label) + "=====\n" + str(excerpt))

    def chat(self, messages=None, options=None):
        self.chat_calls.append(list(messages or []))
        return {"text": "要点 %d" % len(self.chat_calls)}

    def chat_stream(self, on_delta=None, options=None):
        return self.stream(on_delta, options)

    def stream(self, on_delta=None, options=None):
        self.stream_calls.append(list((options or {}).get("messages") or []))
        if on_delta:
            on_delta(self.answer)
        return {"text": self.answer, "usage": {"total_tokens": 1}}


class FullReadMaterialTests(AiTestBase):
    def test_split_text_covers_every_character(self):
        text = ("段落一\n\n" * 40) + ("句子内容。" * 200)
        parts = fullread.split_text(text, 120)
        self.assertEqual("".join(parts), text)
        self.assertTrue(all(len(piece) <= 120 for piece in parts))

    def test_collect_includes_body_and_all_si(self):
        services = self.make_services()
        paper = self.add_paper(title="T", attachments=[
            {"file": "si1.pdf", "originalName": "SI-1.pdf"},
            {"file": "si2.pdf", "originalName": "SI-2.pdf"},
        ])
        services.fulltext.save(paper["id"], "BODY-" * 100)
        services.db.set_text("att-si1.pdf", "SI1-" * 50)
        services.db.set_text("att-si2.pdf", "SI2-" * 30)
        collected = fullread.collect_materials(services, paper)
        self.assertEqual(collected["bodyChars"], 500)
        self.assertEqual(collected["siChars"], 200 + 120)
        self.assertEqual([a["chars"] for a in collected["attachments"]], [200, 120])
        self.assertEqual(collected["missing"], [])

    def test_missing_attachment_text_is_re_extracted(self):
        services = self.make_services()
        paper = self.add_paper(title="T", attachments=[
            {"file": "si.pdf", "originalName": "SI.pdf"}])
        Path(services.db.attachments_dir).mkdir(parents=True, exist_ok=True)
        (Path(services.db.attachments_dir) / "si.pdf").write_bytes(b"%PDF-1.4 demo")
        services.extract_attachment_text = lambda path, ext: {
            "ok": True, "text": "从 SI 重新抽出的文本" * 3, "kind": "pdf"}
        collected = fullread.collect_materials(services, paper)
        self.assertEqual(collected["missing"], [])
        self.assertEqual(collected["siChars"], len("从 SI 重新抽出的文本" * 3))
        self.assertIn("从 SI 重新抽出的文本", collected["materials"][0]["text"])


class FullReadSummaryTests(AiTestBase):
    def _paper(self, services, body="BODY-" * 100, si="SI-" * 100):
        paper = self.add_paper(title="Summary Paper", attachments=[
            {"file": "si.pdf", "originalName": "SI.pdf"}])
        services.fulltext.save(paper["id"], body)
        services.db.set_text("att-si.pdf", si)
        return paper

    def test_direct_mode_sends_full_body_and_si(self):
        services = self.make_services(api_key="sk-test")
        paper = self._paper(services)
        llm = RecordingLLM("总结文本")
        services.llm = llm
        ctx = FakeCtx(services)
        ai_domain.summarize_stream(services, paper, {}, ai_api.StreamOpener(ctx))
        frames = ctx.frames()
        start = frames[0]
        self.assertEqual(start["fullRead"]["mode"], "direct")
        self.assertEqual(start["fullRead"]["bodyChars"], 500)
        self.assertEqual(start["fullRead"]["siChars"], 300)
        self.assertEqual(start["fullRead"]["totalChars"], 800)
        self.assertEqual(start["fullRead"]["processedChunks"], 1)
        final_user = llm.stream_calls[-1][-1]["content"]
        self.assertIn("BODY-" * 100, final_user)
        self.assertIn("SI-" * 100, final_user)
        self.assertIn("全文（正文 + 全部补充材料 SI，未截断）", final_user)
        done = frames[-1]
        self.assertEqual(done["fullRead"]["totalChars"], 800)

    def test_final_summary_gets_min_output_budget(self):
        """总结最终请求至少给 SUMMARY_MIN_MAX_TOKENS，避免思考型模型把预算用光。"""
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(title="Budget Paper")
        services.fulltext.save(paper["id"], "正文内容" * 100)

        class BudgetLLM(RecordingLLM):
            def __init__(self):
                super().__init__("总结")
                self.stream_options = []

            def stream(self, on_delta=None, options=None):
                self.stream_options.append(dict(options or {}))
                return super().stream(on_delta, options)

        llm = BudgetLLM()
        services.llm = llm
        ai_domain.summarize_stream(services, paper, {}, ai_api.StreamOpener(FakeCtx(services)))
        self.assertGreaterEqual(llm.stream_options[-1]["maxTokens"],
                                ai_domain.SUMMARY_MIN_MAX_TOKENS)

    def test_map_budget_covers_reasoning_models(self):
        self.assertGreaterEqual(fullread.MAP_MAX_TOKENS, 2000)

    def test_map_retries_with_larger_budget_when_truncated(self):
        """第一次因 finish_reason=length 空回复时，第二次要用更大预算重试。"""
        class TruncatingLLM:
            def __init__(self):
                self.budgets = []

            def chat(self, messages=None, options=None):
                self.budgets.append((options or {}).get("maxTokens"))
                if len(self.budgets) == 1:
                    raise LLMError("模型输出被 max_tokens 截断（finish_reason=length）")
                return {"text": "重试后的要点"}

        llm = TruncatingLLM()
        notes = fullread.map_chunks(llm, [{"label": "正文", "text": "x"}], "sys",
                                    lambda chunk, index, count: "user")
        self.assertEqual(notes[0]["text"], "重试后的要点")
        self.assertEqual(llm.budgets[0], fullread.MAP_MAX_TOKENS)
        self.assertGreaterEqual(llm.budgets[1], 4000)

    def test_map_reduce_processes_every_chunk(self):
        services = self.make_services(api_key="sk-test")
        paper = self._paper(services, body="正文内容" * 60, si="补充材料" * 60)
        llm = RecordingLLM("汇总总结")
        services.llm = llm
        with mock.patch.object(fullread, "DIRECT_LIMIT", 100), \
                mock.patch.object(fullread, "CHUNK_CHARS", 100):
            ctx = FakeCtx(services)
            ai_domain.summarize_stream(services, paper, {}, ai_api.StreamOpener(ctx))
        frames = ctx.frames()
        start = frames[0]
        chunks = start["fullRead"]["chunks"]
        self.assertEqual(start["fullRead"]["mode"], "map-reduce")
        self.assertGreater(chunks, 1)
        self.assertEqual(len(llm.chat_calls), chunks)
        done = frames[-1]
        self.assertEqual(done["fullRead"]["processedChunks"], chunks)
        # 每一段原文都出现在某一次 map 调用里（没有漏段/截断）
        map_text = "\n".join(call[-1]["content"] for call in llm.chat_calls)
        materials = fullread.collect_materials(services, paper)["materials"]
        for material in materials:
            for piece in fullread.split_text(material["text"], 100):
                self.assertIn(piece, map_text)
        # 最终汇总收到所有分段的提炼结果
        final_user = llm.stream_calls[-1][-1]["content"]
        for index in range(1, chunks + 1):
            self.assertIn("要点 %d" % index, final_user)


class FullReadChatTests(AiTestBase):
    def _paper(self, services, body="BODY-" * 100, si="SI-" * 100):
        paper = self.add_paper(title="Chat Paper", attachments=[
            {"file": "si.pdf", "originalName": "SI.pdf"}])
        services.fulltext.save(paper["id"], body)
        services.db.set_text("att-si.pdf", si)
        return paper

    def test_direct_mode_context_has_full_body_and_si(self):
        services = self.make_services(api_key="sk-test")
        paper = self._paper(services)
        llm = RecordingLLM("答案")
        services.llm = llm
        ctx = FakeCtx(services)
        chat_domain.chat_stream(services, paper, {"question": "方法是什么？"},
                                chat_api.StreamOpener(ctx))
        frames = ctx.frames()
        self.assertEqual(frames[0]["fullRead"]["mode"], "direct")
        self.assertEqual(frames[0]["fullRead"]["processedChunks"], 1)
        self.assertTrue(frames[0]["hasBody"])
        self.assertIn("正文", frames[0]["sources"])
        self.assertIn("SI:SI.pdf", frames[0]["sources"])
        sent = llm.stream_calls[-1]
        context = sent[1]["content"]
        self.assertIn("BODY-" * 100, context)
        self.assertIn("SI-" * 100, context)
        self.assertEqual(sent[-1], {"role": "user", "content": "方法是什么？"})

    def test_map_reduce_searches_every_chunk_with_question(self):
        services = self.make_services(api_key="sk-test")
        paper = self._paper(services, body="正文内容" * 60, si="补充材料" * 60)
        llm = RecordingLLM("基于全文的答案")
        services.llm = llm
        with mock.patch.object(fullread, "DIRECT_LIMIT", 100), \
                mock.patch.object(fullread, "CHUNK_CHARS", 100):
            ctx = FakeCtx(services)
            chat_domain.chat_stream(services, paper, {"question": "实验条件是什么？"},
                                    chat_api.StreamOpener(ctx))
        frames = ctx.frames()
        chunks = frames[0]["fullRead"]["chunks"]
        self.assertGreater(chunks, 1)
        self.assertEqual(len(llm.chat_calls), chunks)
        self.assertEqual(frames[-1]["fullRead"]["processedChunks"], chunks)
        map_text = "\n".join(call[-1]["content"] for call in llm.chat_calls)
        self.assertIn("实验条件是什么？", map_text)
        materials = fullread.collect_materials(services, paper)["materials"]
        for material in materials:
            for piece in fullread.split_text(material["text"], 100):
                self.assertIn(piece, map_text)
        final_context = llm.stream_calls[-1][1]["content"]
        self.assertIn("全文分段证据", final_context)
        for index in range(1, chunks + 1):
            self.assertIn("要点 %d" % index, final_context)
        self.assertEqual(frames[-1]["text"], "基于全文的答案")


if __name__ == "__main__":
    unittest.main()
