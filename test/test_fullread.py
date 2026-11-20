"""回归：同一文献的总结与问答应在单次请求中共享正文和全部可读取的 SI。"""
from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from paperlib.api import ai as ai_api
from paperlib.api import chat as chat_api
from paperlib.domain import fullread
from test.test_ai import AiTestBase, FakeCtx, delta


class FullReadMaterialTests(AiTestBase):
    def test_collect_includes_body_and_all_si(self):
        services = self.make_services()
        paper = self.add_paper(attachments=[
            {"file": "si1.pdf", "originalName": "SI-1.pdf"},
            {"file": "si2.pdf", "originalName": "SI-2.pdf"},
        ])
        services.fulltext.save(paper["id"], "BODY-" * 100)
        services.fulltext.save_attachment_text(paper["id"], "si1.pdf", "SI1-" * 50)
        services.fulltext.save_attachment_text(paper["id"], "si2.pdf", "SI2-" * 30)
        collected = fullread.collect_materials(services, paper)
        self.assertEqual(collected["bodyChars"], 500)
        self.assertEqual(collected["siChars"], 320)
        self.assertEqual([a["chars"] for a in collected["attachments"]], [200, 120])
        self.assertEqual(collected["missing"], [])

    def test_missing_attachment_text_is_re_extracted(self):
        services = self.make_services()
        paper = self.add_paper(attachments=[{"file": "si.pdf", "originalName": "SI.pdf"}])
        directory = Path(services.db.attachments_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "si.pdf").write_bytes(b"%PDF-1.4 demo")
        text = "从 SI 重新抽出的文本" * 3
        services.extract_attachment_text = lambda path, ext: {"ok": True, "text": text, "kind": "pdf"}
        collected = fullread.collect_materials(services, paper)
        self.assertEqual(collected["siChars"], len(text))
        self.assertEqual(collected["materials"][0]["text"], text)
        self.assertEqual(services.fulltext.read_attachment(paper["id"], "si.pdf"), text)

    def test_unreadable_material_is_reported(self):
        services = self.make_services()
        paper = self.add_paper(attachments=[{"file": "si.pdf", "originalName": "缺失 SI.pdf"}])
        services.fulltext.save(paper["id"], "正文")
        prepared = fullread.prepare(services, paper)
        self.assertEqual(prepared["coverage"]["missing"], ["缺失 SI.pdf"])
        self.assertIn("未提取到文字的 SI", fullread.paper_reference(prepared)["content"])


class FullReadRequestTests(AiTestBase):
    def _ask(self, services, paper, question):
        ctx = FakeCtx(services, params={"id": paper["id"]}, body={
            "question": question, "useLocal": False, "useExternal": False,
        })
        chat_api.chat_post(ctx)
        return ctx.frames()

    def test_long_summary_and_chat_share_full_material_in_one_request_each(self):
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(attachments=[
            {"file": "first.txt", "originalName": "SI-1.txt"},
            {"file": "second.txt", "originalName": "SI-2.txt"},
        ])
        body = "正文开头\n" + "数据表与实验方法。" * 7000 + "\n正文末尾 QZ731"
        si1 = "SI 1 开头\n" + "补充结果与方法。" * 7000 + "\nSI 1 末尾"
        si2 = "SI 2 完整内容\n结尾 数据 17.4"
        services.fulltext.save(paper["id"], body)
        services.fulltext.save_attachment_text(paper["id"], "first.txt", si1)
        services.fulltext.save_attachment_text(paper["id"], "second.txt", si2)

        with mock.patch.object(services.llm, "_open", wraps=services.llm._open) as request:
            self.fake.set({"sse": [delta("已保存的 AI 总结")]})
            summary = FakeCtx(services, body={"paperId": paper["id"]})
            ai_api.summarize(summary)
            self.fake.set({"sse": [delta("基于全文的回答")]})
            first = self._ask(services, paper, "正文的关键数据？")
            second = self._ask(services, paper, "补充材料呢？")

        self.assertEqual(request.call_count, 3)
        summary_request, first_request, second_request = [call.args[0] for call in request.call_args_list]
        for payload in (summary_request, first_request, second_request):
            self.assertTrue(payload["stream"])
            self.assertEqual(payload["max_tokens"], 128000)
            self.assertEqual(payload["messages"][0], summary_request["messages"][0])
        reference = summary_request["messages"][0]["content"]
        self.assertTrue(reference.endswith(
            "===== 论文正文 =====\n" + body
            + "\n\n===== 补充材料：SI-1.txt =====\n" + si1
            + "\n\n===== 补充材料：SI-2.txt =====\n" + si2,
        ))
        for frames in (summary.frames(), first, second):
            self.assertEqual([frame["type"] for frame in frames], ["start", "delta", "done"])
            coverage = frames[-1]["fullRead"]
            self.assertEqual(coverage["mode"], "direct")
            self.assertEqual(coverage["processedChunks"], 1)
            self.assertIsNone(coverage["directLimit"])
            self.assertEqual(coverage["bodyChars"], len(body))
            self.assertEqual(coverage["siChars"], len(si1) + len(si2))
        self.assertIn("已保存的 AI 总结", first_request["messages"][2]["content"])
        self.assertEqual(second_request["messages"][-3:], [
            {"role": "user", "content": "正文的关键数据？"},
            {"role": "assistant", "content": "基于全文的回答"},
            {"role": "user", "content": "补充材料呢？"},
        ])
        current = services.library.get(paper["id"])
        self.assertEqual(current["aiSummary"], "已保存的 AI 总结")
        self.assertEqual(len(current["chat"]), 4)

    def test_sync_summary_and_chat_use_saved_budget(self):
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        services.llm.update({"maxTokens": 1300})
        self.fake.set({"sse": [delta("答案")]})
        summary = FakeCtx(services, body={"paperId": paper["id"]})
        ai_api.summarize_sync(summary)
        self.assertTrue(summary.payload["ok"])
        self.assertEqual(self.fake.server.last_body["max_tokens"], 1300)
        self.assertEqual(self._ask(services, paper, "什么结论？")[-1]["type"], "done")
        self.assertEqual(self.fake.server.last_body["max_tokens"], 1300)

    def test_summary_can_use_metadata_without_reading_fulltext(self):
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        self.fake.set({"sse": [delta("题录总结")]})
        ctx = FakeCtx(services, body={"paperId": paper["id"], "useFulltext": False})
        with mock.patch.object(fullread, "collect_materials") as collect:
            ai_api.summarize(ctx)
        collect.assert_not_called()
        self.assertEqual(ctx.frames()[-1]["fullRead"]["mode"], "none")


if __name__ == "__main__":
    unittest.main()
