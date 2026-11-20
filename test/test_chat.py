"""T4 自测：文献对话 / 识图 / 抄录（domain/chat.py + api/chat.py）。

覆盖：片段挑选（本地等价实现与 T1 接口兼容）、资料包组装（题录/正文/SI/本库/联网）、
chat_stream 的 SSE 帧与落库（保留 images/usage）、vision/transcribe、api 层的
404/400 与「校验先于开流」、DELETE ?pair 的删除语义。

跑法：python -m unittest test.test_chat -v
"""
from __future__ import annotations

import unittest

from paperlib.api import chat as chat_api
from paperlib.domain import chat as chat_domain
from test.test_ai import AiTestBase, FakeCtx, delta

IMAGE = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUg=="


def chat_paper(test, **fields):
    return test.add_paper(**fields)


class PickPassagesTests(AiTestBase):
    def test_short_text_is_returned_whole(self):
        picked = chat_domain.local_pick_passages("短文本", "问题", budget=12000)
        self.assertEqual(picked["text"], "短文本")
        self.assertFalse(picked["truncated"])

    def test_long_text_keeps_head_anchor_and_tail(self):
        body = "A" * 3000 + " S − 55 图注说明 " + "B" * 3000
        picked = chat_domain.local_pick_passages(body, "S55 那一页的图", budget=3000)
        self.assertTrue(picked["text"].startswith("A"))
        self.assertIn("S − 55", picked["text"])          # 宽松匹配连字符与空格
        self.assertTrue(picked["text"].rstrip().endswith("B"))
        self.assertTrue(picked["truncated"])
        self.assertLessEqual(len(picked["text"]), 3200)
        self.assertEqual(picked["anchors"], 1)

    def test_pick_uses_shared_report_when_available(self):
        services = self.make_services()
        body = "A" * 3000 + " S − 55 图注说明 " + "B" * 3000
        picked = chat_domain.pick_passages(services, body, "S55 那一页的图", 3000, 500, 300)
        self.assertIn("S − 55", picked["text"])
        self.assertIn("……（以下是与问题相关的片段）……", picked["text"])   # 与 JS 同构的报告形态
        self.assertTrue(picked["passages"] >= 1)
        self.assertLessEqual(len(picked["text"]), 3200)

    def test_pick_degrades_to_services_then_local(self):
        services = self.make_services()
        origin = chat_domain._shared_report
        calls = []

        def fake_pick(text, question, options=None):
            calls.append((text, question, options))
            return [{"kind": "hit", "text": "片段一"}, {"kind": "hit", "text": "片段二"}]

        try:
            chat_domain._shared_report = None
            services.pick_passages = fake_pick
            picked = chat_domain.pick_passages(services, "原文", "问题", 1000, 200, 100)
            self.assertEqual(calls[0][1], "问题")
            self.assertEqual(picked["text"], "片段一\n……\n片段二")
            self.assertEqual(picked["passages"], 2)
            services.pick_passages = None            # 全都没有 → 本地实现
            self.assertEqual(chat_domain.pick_passages(services, "原文", "问题", 1000, 200, 100)["text"],
                             "原文")
        finally:
            chat_domain._shared_report = origin


class BuildContextTests(AiTestBase):
    def test_metadata_only(self):
        services = self.make_services()
        paper = self.add_paper(title="标题甲", authors=["张三"], summary="摘要乙")
        built = chat_domain.build_chat_context(services, paper, "这篇讲了什么", {})
        self.assertIn("===== 文献题录 =====", built["context"])
        self.assertIn("标题甲", built["context"])
        self.assertFalse(built["hasBody"])
        self.assertEqual(built["sources"], [])

    def test_body_and_attachment(self):
        services = self.make_services()
        paper = self.add_paper(title="带正文", attachments=[
            {"file": "si.pdf", "originalName": "SI.pdf", "kind": "pdf"}])
        services.fulltext.save(paper["id"], "正文开头 " + "C" * 5000)
        services.db.set_text("att-si.pdf", "补充材料 S55 图 3")
        paper = services.library.get(paper["id"])
        built = chat_domain.build_chat_context(services, paper, "S55 图 3 说明了什么", {})
        self.assertTrue(built["hasBody"])
        self.assertIn("===== 这篇文献的正文节选 =====", built["context"])
        self.assertIn("===== 补充材料：SI.pdf =====", built["context"])
        self.assertIn("正文", built["sources"])
        self.assertTrue(any(s.startswith("SI:SI.pdf") for s in built["sources"]))

    def test_external_search_triggered_only_when_asked(self):
        services = self.make_services()
        paper = self.add_paper()
        tried = []

        def fake_search(query, filters=None):
            tried.append(query)
            return {"items": [{"title": "另一篇", "year": 2020, "venue": "J", "doi": "10.1/x"}]}

        services.external = type("E", (), {"search": staticmethod(fake_search)})()
        built = chat_domain.build_chat_context(services, paper, "有没有相关研究", {"useExternal": True})
        self.assertEqual(tried, ["有没有相关研究"])
        self.assertIn("联网检索到的相关文献", built["context"])
        self.assertIn("联网检索", built["sources"])
        # 不勾联网就不查
        built2 = chat_domain.build_chat_context(services, paper, "有没有相关研究", {"useExternal": False})
        self.assertEqual(len(tried), 1)
        self.assertNotIn("联网检索", built2["sources"])

    def test_external_failure_is_reported_inside_context(self):
        services = self.make_services()
        paper = self.add_paper()

        def boom(query, filters=None):
            raise RuntimeError("网络断了")

        services.external = type("E", (), {"search": staticmethod(boom)})()
        built = chat_domain.build_chat_context(services, paper, "最新进展", {"useExternal": True})
        self.assertIn("联网检索失败：网络断了", built["context"])


class ChatStreamTests(AiTestBase):
    def test_empty_question_is_400(self):
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services)
        result = chat_domain.chat_stream(services, paper, {"question": "   "}, chat_api.StreamOpener(ctx))
        self.assertEqual(result["status"], 400)
        self.assertEqual(result["error"], "请输入问题")
        self.assertFalse(ctx.started)

    def test_no_key_is_400_and_no_stream(self):
        services = self.make_services()
        paper = self.add_paper()
        ctx = FakeCtx(services)
        result = chat_domain.chat_stream(services, paper, {"question": "问"}, chat_api.StreamOpener(ctx))
        self.assertEqual(result["status"], 400)
        self.assertIn("还没有配置大模型", result["error"])
        self.assertFalse(ctx.started)
        self.assertEqual(services.library.get(paper["id"])["chat"], [])

    def test_chat_stream_frames_and_persistence(self):
        self.fake.set({"sse": [delta("答"), delta("案"),
                               {"choices": [], "usage": {"total_tokens": 7}}]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(summary="摘要")
        services.fulltext.save(paper["id"], "正文内容")
        ctx = FakeCtx(services)
        result = chat_domain.chat_stream(services, paper, {"question": "讲了什么"},
                                         chat_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertEqual(frames[0]["type"], "start")
        self.assertEqual(frames[0]["model"], "fake-model")
        self.assertTrue(frames[0]["hasBody"])
        self.assertIn("正文", frames[0]["sources"])
        self.assertEqual([f["text"] for f in frames if f["type"] == "delta"], ["答", "案"])
        done = frames[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(done["text"], "答案")
        self.assertEqual(done["usage"], {"total_tokens": 7})
        messages = services.library.get(paper["id"])["chat"]
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["role"], "user")
        self.assertEqual(messages[0]["content"], "讲了什么")
        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[1]["content"], "答案")
        self.assertEqual(messages[1]["model"], "fake-model")
        self.assertIn("正文", messages[1]["sources"])
        # 资料包确实进了 system 消息，问题在最后一条 user 消息里
        sent = self.fake.server.last_body["messages"]
        self.assertIn("正文内容", sent[0]["content"])
        self.assertTrue(any("资料包如下" in message["content"] for message in sent[:-1]))
        self.assertEqual(sent[-1], {"role": "user", "content": "讲了什么"})

    def test_history_is_sent_to_model(self):
        self.fake.set({"sse": [delta("好")]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        services.library.update(paper["id"], {"chat": [
            {"role": "user", "content": "上一问"},
            {"role": "assistant", "content": "上一答"},
        ]})
        paper = services.library.get(paper["id"])
        ctx = FakeCtx(services)
        chat_domain.chat_stream(services, paper, {"question": "追问"}, chat_api.StreamOpener(ctx))
        sent = self.fake.server.last_body["messages"]
        contents = [m.get("content") for m in sent]
        self.assertIn("上一问", contents)
        self.assertIn("上一答", contents)
        self.assertEqual(sent[-1]["content"], "追问")
        self.assertEqual([m["role"] for m in sent[-3:]], ["user", "assistant", "user"])

    def test_model_error_emits_error_frame_and_keeps_question(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services)
        result = chat_domain.chat_stream(services, paper, {"question": "问"}, chat_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertEqual(frames[0]["type"], "start")
        self.assertEqual(frames[-1]["type"], "error")
        self.assertIn("boom", frames[-1]["message"])
        messages = services.library.get(paper["id"])["chat"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["role"], "user")

    def test_auto_extract_failure_does_not_crash(self):
        self.fake.set({"sse": [delta("好")]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(pdfFile="a.pdf")
        ctx = FakeCtx(services)
        result = chat_domain.chat_stream(services, paper, {"question": "问"}, chat_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        auto = ctx.frames()[0]["autoExtract"]
        self.assertIsNotNone(auto)
        self.assertFalse(auto["ok"])       # T3 的抽取引擎还没就绪时也要正常开流


class VisionTests(AiTestBase):
    def test_no_images_is_400(self):
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services)
        result = chat_domain.vision_stream(services, paper, {"images": []}, chat_api.StreamOpener(ctx))
        self.assertEqual(result["status"], 400)
        self.assertEqual(result["error"], "没有收到图片")
        self.assertFalse(ctx.started)

    def test_vision_stream_success(self):
        self.fake.set({"sse": [delta("这是"), delta("示意图"),
                               {"choices": [], "usage": {"total_tokens": 12}}]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(title="图文献")
        ctx = FakeCtx(services)
        result = chat_domain.vision_stream(
            services, paper,
            {"question": "这张图说明什么", "images": [IMAGE, IMAGE], "label": "图 2"},
            chat_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertEqual(frames[0]["type"], "start")
        self.assertEqual(frames[0]["images"], 2)
        self.assertEqual(frames[0]["label"], "图 2")
        done = frames[-1]
        self.assertEqual(done["type"], "done")
        self.assertEqual(len(done["messages"]), 2)
        messages = services.library.get(paper["id"])["chat"]
        self.assertEqual(messages[0]["images"], 2)
        self.assertIn("（图 2）", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "这是示意图")
        self.assertEqual(messages[1]["usage"], {"total_tokens": 12})
        self.assertEqual(messages[1]["sources"], ["图片2张"])

    def test_vision_stream_error_frame(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services)
        result = chat_domain.vision_stream(services, paper, {"images": [IMAGE]},
                                           chat_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        self.assertEqual(ctx.frames()[-1]["type"], "error")


class TranscribeTests(AiTestBase):
    def test_no_image_is_400(self):
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        result = chat_domain.transcribe(services, paper, {"image": ""})
        self.assertEqual(result["status"], 400)
        self.assertEqual(result["error"], "没有收到图片")

    def test_no_key_is_400(self):
        services = self.make_services()
        paper = self.add_paper()
        result = chat_domain.transcribe(services, paper, {"image": IMAGE})
        self.assertEqual(result["status"], 400)
        self.assertIn("还没有配置大模型", result["error"])

    def test_transcribe_success(self):
        self.fake.set({"sse": [delta("抄录"), delta("正文")]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        result = chat_domain.transcribe(services, paper, {"image": IMAGE, "page": 3})
        self.assertTrue(result["ok"])
        self.assertEqual(result["page"], 3)
        self.assertEqual(result["text"], "抄录正文")
        self.assertIn("第 3 页", self.fake.server.last_body["messages"][1]["content"][0]["text"])

    def test_transcribe_failure_is_502(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        result = chat_domain.transcribe(services, paper, {"image": IMAGE})
        self.assertEqual(result["status"], 502)
        self.assertIn("识别失败", result["error"])


class ApiChatTests(AiTestBase):
    def test_chat_get_missing_paper_is_404(self):
        services = self.make_services()
        ctx = FakeCtx(services, params={"id": "nope"})
        chat_api.chat_get(ctx)
        self.assertEqual(ctx.status, 404)
        self.assertEqual(ctx.payload["error"], "找不到这条文献")

    def test_chat_get_returns_messages(self):
        services = self.make_services()
        paper = self.add_paper()
        services.library.update(paper["id"], {"chat": [{"role": "user", "content": "问"}]})
        ctx = FakeCtx(services, params={"id": paper["id"]})
        chat_api.chat_get(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(len(ctx.payload["messages"]), 1)

    def test_chat_delete_all(self):
        services = self.make_services()
        paper = self.add_paper()
        services.library.update(paper["id"], {"chat": [
            {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]})
        ctx = FakeCtx(services, params={"id": paper["id"]})
        chat_api.chat_delete(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["messages"], [])
        self.assertEqual(services.library.get(paper["id"])["chat"], [])

    def test_chat_delete_pair(self):
        services = self.make_services()
        paper = self.add_paper()
        services.library.update(paper["id"], {"chat": [
            {"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"},
            {"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]})
        ctx = FakeCtx(services, params={"id": paper["id"]}, query={"pair": "0"})
        chat_api.chat_delete(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["removed"], 2)
        self.assertEqual([m["content"] for m in ctx.payload["messages"]], ["q2", "a2"])

    def test_chat_delete_pair_out_of_range(self):
        services = self.make_services()
        paper = self.add_paper()
        services.library.update(paper["id"], {"chat": [{"role": "user", "content": "q1"}]})
        for raw in ("5", "-1", "abc", "1.5"):
            ctx = FakeCtx(services, params={"id": paper["id"]}, query={"pair": raw})
            chat_api.chat_delete(ctx)
            self.assertEqual(ctx.status, 400, raw)
            self.assertEqual(ctx.payload["error"], "要删除的提问不存在")

    def test_chat_post_missing_paper_is_404(self):
        services = self.make_services()
        ctx = FakeCtx(services, params={"id": "nope"}, body={"question": "问"})
        chat_api.chat_post(ctx)
        self.assertEqual(ctx.status, 404)
        self.assertFalse(ctx.started)

    def test_chat_post_no_key_is_400_without_stream(self):
        services = self.make_services()
        paper = self.add_paper()
        ctx = FakeCtx(services, params={"id": paper["id"]}, body={"question": "问"})
        chat_api.chat_post(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertIn("还没有配置大模型", ctx.payload["error"])
        self.assertFalse(ctx.started)

    def test_chat_post_streams_sse(self):
        self.fake.set({"sse": [delta("答")]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services, params={"id": paper["id"]}, body={"question": "问"})
        chat_api.chat_post(ctx)
        self.assertTrue(ctx.started)
        self.assertTrue(ctx.ended)
        self.assertEqual(ctx.headers["Content-Type"], "text/event-stream; charset=utf-8")
        raw = b"".join(ctx.chunks).decode("utf-8")
        self.assertTrue(raw.endswith("\n\n"))
        for block in raw.split("\n\n"):
            if block:
                self.assertTrue(block.startswith("data: "))
                self.assertNotIn("\n", block)

    def test_vision_and_transcribe_missing_paper(self):
        services = self.make_services()
        for handler in (chat_api.vision, chat_api.transcribe):
            ctx = FakeCtx(services, params={"id": "nope"}, body={})
            handler(ctx)
            self.assertEqual(ctx.status, 404)

    def test_transcribe_endpoint_returns_json(self):
        self.fake.set({"sse": [delta("文字")]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services, params={"id": paper["id"]}, body={"image": IMAGE, "page": 1})
        chat_api.transcribe(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["text"], "文字")


if __name__ == "__main__":
    unittest.main()
