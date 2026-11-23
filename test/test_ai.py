"""T4 自测：大模型适配器 + AI 总结编排 + Exa 网络检索。

覆盖：LLM 配置/打码/readiness、strip_echoed_material、SSE 流解析（本地假 OpenAI 服务）、
错误映射（401/402/404/429/500/超时/连不上）、domain 的 summary_zh/summarize_sync/summarize_stream、
api 层的状态码与「校验先于开流」、WebSearch 的预算护栏/缓存/条目映射。
跑法：python -m unittest test.test_ai -v
"""
from __future__ import annotations

import json
import shutil
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from paperlib.adapters.llm import LLM, PRESETS, Readiness, strip_echoed_material
from paperlib.api import ai as ai_api
from paperlib.data.store import Store
from paperlib.domain import ai as ai_domain
from paperlib.domain.repository import normalize_paper
from paperlib.services import create_services


# ---------------------------------------------------------------- 本地假模型服务
def delta(text):
    return {"choices": [{"delta": {"content": text}}]}


def _sse_bytes(chunks):
    out = [b"data: " + json.dumps(c, ensure_ascii=False).encode("utf-8") + b"\n\n" for c in chunks]
    out.append(b": keep-alive\n\n")
    out.append(b"data: [DONE]\n\n")
    return b"".join(out)


class _FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.last_body = json.loads(raw.decode("utf-8") or "{}")
        self.server.last_path = self.path
        self.server.last_headers = {k.lower(): v for k, v in self.headers.items()}
        spec = self.server.spec or {}
        if spec.get("sleep"):
            time.sleep(spec["sleep"])
        if spec.get("sse") is not None:
            try:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.end_headers()
                self.wfile.write(_sse_bytes(spec["sse"]))
                self.wfile.flush()
            except OSError:
                pass
            return
        body = json.dumps(spec.get("json") or {}, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(int(spec.get("status") or 200))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        return


class FakeOpenAI:
    """一次性假模型 / 假 Exa 服务：设定 spec 后把 base_url 指过去。"""

    def __init__(self, spec=None):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
        self.server.spec = spec or {}
        self.server.last_body = None
        self.server.last_headers = None
        self.server.last_path = None
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/v1" % self.server.server_address[1]

    def set(self, spec):
        self.server.spec = spec or {}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class FakeCtx:
    """api 层测试用的最小 ctx（只实现 api 模块用到的那几件事）。"""

    def __init__(self, services, params=None, query=None, body=None, method="POST"):
        self.services = services
        self.method = method
        self.params = params or {}
        self._query = dict(query or {})
        self._body = body
        self.status = None
        self.payload = None
        self.chunks = []
        self.headers = {}
        self.started = False
        self.ended = False

    def query(self, name, default=""):
        return self._query.get(name, default)

    def has_query(self, name):
        return name in self._query

    def read_json(self, limit=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body if self._body is not None else {}

    def json(self, status, data):
        self.status = status
        self.payload = data
        return self

    def fail(self, status, message, extra=None):
        body = {"ok": False, "error": message}
        if extra:
            body.update(extra)
        return self.json(status, body)

    def start_stream(self, status, headers):
        self.started = True
        self.status = status
        self.headers = dict(headers)

    def write_chunk(self, data):
        self.chunks.append(data)

    def end_stream(self):
        self.ended = True

    def frames(self):
        out = []
        for chunk in self.chunks:
            for block in chunk.decode("utf-8").split("\n\n"):
                if block.startswith("data: "):
                    out.append(json.loads(block[6:]))
        return out


class AiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="helix-t4-ai-"))
        self.fake = FakeOpenAI()

    def tearDown(self):
        self.fake.close()
        services = getattr(self, "services", None)
        if services is not None:
            try:
                services.db.repo.close()
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_services(self, api_key="", base_url=None):
        config = SimpleNamespace(
            root=Path(__file__).resolve().parents[1], data_dir=self.tmp, port=4393,
            host="127.0.0.1", app="literature-vault", started_at=time.time())
        store = Store(config.data_dir)
        store.load()
        services = create_services(config, store)
        # 没给密钥时用「非本地」地址：本地地址（127.0.0.1/localhost）按 Node 语义免密钥，
        # 那样就测不到「还没配置大模型」的分支了。
        target = base_url or (self.fake.base_url if api_key else "https://api.example.invalid/v1")
        services.llm.update({"baseURL": target, "model": "fake-model",
                             "apiKey": api_key or None})
        self.services = services
        return services

    def add_paper(self, **fields):
        paper = normalize_paper(dict({"title": "A Paper", "authors": ["Li"], "year": 2024}, **fields))
        self.services.db.insert_paper(paper)
        self.services.library.reindex()
        return self.services.library.get(paper["id"])


# ---------------------------------------------------------------- LLM 配置
class LLMSettingsTests(AiTestBase):
    def test_public_settings_masks_key(self):
        llm = LLM(self.tmp)
        public = llm.public_settings()
        self.assertEqual(public["hasKey"], False)
        self.assertEqual(public["keyHint"], "")
        self.assertNotIn("apiKey", public)
        self.assertEqual(len(public["presets"]), len(PRESETS))
        self.assertIs(public["isLocal"], False)
        self.assertIs(public["autoSummaryZh"], True)

    def test_update_semantics_and_persistence(self):
        llm = LLM(self.tmp)
        llm.update({"apiKey": "sk-1234567890abcd", "model": "deepseek-chat"})
        public = llm.public_settings()
        self.assertTrue(public["hasKey"])
        self.assertEqual(public["keyHint"], "sk-1…abcd")
        # 空串=不改；打码值也不能覆盖
        llm.update({"model": "", "apiKey": "sk-1…abcd", "baseURL": ""})
        self.assertEqual(llm.settings["model"], "deepseek-chat")
        self.assertEqual(llm.settings["apiKey"], "sk-1234567890abcd")
        # None=清空，落盘
        llm.update({"apiKey": None})
        self.assertEqual(llm.settings["apiKey"], "")
        on_disk = json.loads((self.tmp / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["apiKey"], "")

        # 清空后兜底补回默认值，模型不至于彻底不可用
        llm.update({"model": None, "baseURL": None})
        self.assertEqual(llm.settings["model"], "deepseek-chat")
        self.assertEqual(llm.settings["baseURL"], "https://api.deepseek.com/v1")

    def test_anthropic_preset_is_offered(self):
        ids = [item["id"] for item in PRESETS]
        self.assertIn("anthropic", ids)
        self.assertIn("deepseek", ids)


class AnthropicProtocolTests(AiTestBase):
    """Claude 走的是 /v1/messages + x-api-key，不是 OpenAI 的 chat/completions。"""

    def make_llm(self):
        llm = LLM(self.tmp)
        llm.update({
            "provider": "anthropic",
            "baseURL": self.fake.base_url,
            "model": "claude-test",
            "apiKey": "sk-ant-123",
        })
        return llm

    def test_stream_moves_system_to_top_level_and_parses_native_events(self):
        self.fake.set({"sse": [
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "你好"}},
            {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "，世界"}},
        ]})
        llm = self.make_llm()
        seen = []
        result = llm.stream(seen.append, {"messages": [
            {"role": "system", "content": "规则 A"},
            {"role": "system", "content": "材料 B"},
            {"role": "assistant", "content": "上一轮回答"},
            {"role": "user", "content": "这一轮问题"},
        ]})
        self.assertEqual(result["text"], "你好，世界")
        self.assertEqual(seen, ["你好", "，世界"])

        self.assertEqual(self.fake.server.last_path, "/v1/messages")
        headers = self.fake.server.last_headers
        self.assertEqual(headers.get("x-api-key"), "sk-ant-123")
        self.assertTrue(headers.get("anthropic-version"))
        self.assertNotIn("authorization", headers)

        body = self.fake.server.last_body
        self.assertIn("规则 A", body["system"])
        self.assertIn("材料 B", body["system"])
        # messages 里不能再有 system；开头若是 assistant 会被丢掉（Anthropic 要求 user 开头）
        self.assertEqual([m["role"] for m in body["messages"]], ["user"])
        self.assertEqual(body["messages"][0]["content"], "这一轮问题")
        self.assertTrue(body["stream"])

    def test_non_stream_reads_content_blocks(self):
        self.fake.set({"json": {"content": [{"type": "text", "text": "连接正常"}],
                                "usage": {"input_tokens": 3, "output_tokens": 2}}})
        llm = self.make_llm()
        result = llm.chat([{"role": "user", "content": "在吗"}])
        self.assertEqual(result["text"], "连接正常")
        self.assertEqual(result["usage"]["input_tokens"], 3)

    def test_openai_provider_still_uses_chat_completions(self):
        self.fake.set({"sse": [delta("好")]})
        llm = LLM(self.tmp)
        llm.update({"provider": "deepseek", "baseURL": self.fake.base_url,
                    "model": "deepseek-chat", "apiKey": "sk-test"})
        llm.stream(None, {"messages": [{"role": "user", "content": "在吗"}]})
        self.assertEqual(self.fake.server.last_path, "/v1/chat/completions")

    def test_readiness(self):
        llm = LLM(self.tmp)
        self.assertIsInstance(llm.readiness(), Readiness)
        self.assertFalse(llm.readiness()["ready"])
        self.assertEqual(llm.readiness()["reason"], "还没有填写 API 密钥")
        llm.update({"baseURL": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"})
        self.assertTrue(llm.readiness()["ready"])      # 本地 Ollama 不需要密钥
        self.assertTrue(llm.ready()["ready"])          # 前端按 ready.ready 读

    def test_build_user_message(self):
        llm = LLM(self.tmp)
        text = llm.build_user_message({"title": "T", "authors": ["A", "B"], "year": 2020}, "正文")
        self.assertIn("标题：T", text)
        self.assertIn("A, B", text)
        self.assertIn("===== 正文节选（可能被截断）=====", text)
        self.assertIn("（没有 PDF 正文，仅依据上面的题录与摘要总结）",
                      llm.build_user_message({"title": "T"}, ""))


class StripEchoedMaterialTests(unittest.TestCase):
    def test_removes_material_sections(self):
        text = "\n".join(["标题：X", "===== 原文摘要 =====", "摘要内容", "===== 正文节选 =====",
                          "一大段 PDF 原文", "（中间略）", "===== 补充材料：SI.pdf =====", "SI 原文"])
        out = strip_echoed_material(text)
        self.assertIn("摘要内容", out)
        self.assertNotIn("一大段 PDF 原文", out)
        self.assertNotIn("SI 原文", out)
        self.assertNotIn("（中间略）", out)
        self.assertEqual(strip_echoed_material("  只有总结  "), "只有总结")
        self.assertEqual(strip_echoed_material(None), "")

class LLMCallTests(AiTestBase):
    def client(self, api_key="sk-test"):
        llm = LLM(self.tmp)
        llm.update({"baseURL": self.fake.base_url, "model": "fake-model", "apiKey": api_key})
        return llm

    def test_stream_collects_deltas_and_usage(self):
        self.fake.set({"sse": [delta("你"), delta("好"),
                               {"choices": [], "usage": {"prompt_tokens": 3, "total_tokens": 5}}]})
        seen = []
        result = self.client().stream(seen.append, {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(seen, ["你", "好"])
        self.assertEqual(result["text"], "你好")
        self.assertEqual(result["usage"], {"prompt_tokens": 3, "total_tokens": 5})
        body = self.fake.server.last_body
        self.assertEqual((body["model"], body["stream"], body["messages"]),
                         ("fake-model", True, [{"role": "user", "content": "hi"}]))
        self.assertEqual(self.fake.server.last_headers.get("authorization"), "Bearer sk-test")
        # 兼容 stream(messages) 的旧调用形式
        self.fake.set({"sse": [delta("ok")]})
        self.assertEqual(self.client().stream([{"role": "user", "content": "hi"}])["text"], "ok")

    def test_chat_non_stream(self):
        self.fake.set({"json": {"choices": [{"message": {"content": "非流式"}}],
                                "usage": {"total_tokens": 2}}})
        result = self.client().chat([{"role": "user", "content": "hi"}])
        self.assertEqual((result["text"], result["usage"]["total_tokens"]), ("非流式", 2))
        self.assertIs(self.fake.server.last_body["stream"], False)

    def test_error_mapping(self):
        llm = self.client()
        cases = [
            (401, {"error": {"message": "Invalid API key"}}, "密钥无效或未授权（HTTP 401）：Invalid API key"),
            (402, {"error": {"message": "no money"}}, "账户余额不足（HTTP 402）：no money"),
            (404, {"error": {"message": "model not found"}}, "接口或模型名不对（HTTP 404）：model not found"),
            (429, {"error": {"message": "rate limit"}}, "请求太频繁或超出限额（HTTP 429）：rate limit"),
            (500, {"error": {"message": "boom"}}, "模型接口返回 500：boom"),
        ]
        for status, body, expected in cases:
            self.fake.set({"status": status, "json": body})
            with self.assertRaises(Exception) as ctx:
                llm.stream(None, {"messages": []})
            self.assertEqual(str(ctx.exception), expected)

    def test_timeout_and_connection_errors(self):
        self.fake.set({"sleep": 2, "json": {}})
        with self.assertRaises(Exception) as ctx:
            self.client().stream(None, {"messages": [], "timeoutMs": 1})
        self.assertIn("请求超时", str(ctx.exception))
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()
        llm = self.client()
        llm.update({"baseURL": "http://127.0.0.1:%d/v1" % dead_port})
        with self.assertRaises(Exception) as ctx:
            llm.stream(None, {"messages": []})
        self.assertIn("连不上模型接口", str(ctx.exception))

    def test_empty_reply_with_length_is_actionable(self):
        """思考型模型把输出预算用光时，要给出可诊断原因，不能只回空字符串。"""
        self.fake.set({"json": {"choices": [{
            "finish_reason": "length",
            "message": {"role": "assistant", "content": "",
                        "reasoning_content": "想了很多，但预算用完了"}}]}})
        with self.assertRaises(Exception) as ctx:
            self.client().chat([{"role": "user", "content": "hi"}])
        self.assertIn("max_tokens", str(ctx.exception))
        self.assertIn("截断", str(ctx.exception))

    def test_empty_reply_with_reasoning_only_is_actionable(self):
        self.fake.set({"json": {"choices": [{
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": "",
                        "reasoning_content": "只有思考"}}]}})
        with self.assertRaises(Exception) as ctx:
            self.client().chat([{"role": "user", "content": "hi"}])
        self.assertIn("思考内容", str(ctx.exception))

    def test_chat_reports_finish_reason(self):
        self.fake.set({"json": {"choices": [{
            "finish_reason": "stop", "message": {"content": "正文"}}]}})
        result = self.client().chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["text"], "正文")

    def test_stream_empty_with_length_is_actionable(self):
        self.fake.set({"sse": [
            {"choices": [{"delta": {"reasoning_content": "想了很久"}}]},
            {"choices": [{"delta": {}, "finish_reason": "length"}]},
        ]})
        with self.assertRaises(Exception) as ctx:
            self.client().stream(None, {"messages": [{"role": "user", "content": "hi"}]})
        self.assertIn("max_tokens", str(ctx.exception))

    def test_test_endpoint_helper(self):
        self.fake.set({"sse": [delta("连接正常")]})
        result = self.client().test()
        self.assertTrue(result["ok"])
        self.assertIn("连接正常", result["message"])
        self.assertEqual(result["model"], "fake-model")
        bare = LLM(self.tmp)
        bare.update({"apiKey": None, "baseURL": "https://api.example.invalid/v1"})
        self.assertFalse(bare.test()["ok"])            # 没配置 → 不联网，直接给原因
        self.assertIn("API 密钥", bare.test()["message"])


# ---------------------------------------------------------------- domain/ai
class DomainAiTests(AiTestBase):
    def test_summary_zh_requires_llm_but_chinese_needs_no_model(self):
        services = self.make_services()
        blocked = ai_domain.summary_zh(services, self.add_paper(summary="An English abstract."), {})
        self.assertEqual((blocked["ok"], blocked["status"]), (False, 400))
        self.assertIn("还没有配置大模型", blocked["error"])
        paper = self.add_paper(summary="这是一段中文摘要。")
        result = ai_domain.summary_zh(services, paper, {"mode": "translate"})
        self.assertTrue(result["ok"])
        self.assertFalse(result["translated"])
        self.assertEqual(result["reason"], "原文已经是中文")
        saved = services.library.get(paper["id"])
        self.assertEqual(saved["summaryZh"], "这是一段中文摘要。")
        self.assertEqual(saved["summaryZhModel"], "（原文已是中文）")

    def test_summary_zh_translate_and_strip(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        bad = ai_domain.summary_zh(services, self.add_paper(summary="An English abstract."),
                                   {"mode": "translate"})
        self.assertEqual(bad["status"], 502)          # 模型失败 → 502
        self.assertIn("翻译失败", bad["error"])
        no_title = ai_domain.summary_zh(services, {"id": "x", "title": ""}, {"mode": "generate"})
        self.assertEqual(no_title["status"], 400)     # 无题录素材 → 400
        self.assertIn("还没有标题", no_title["error"])
        self.fake.set({"sse": [delta("中文翻译"), delta("如下")]})
        paper = self.add_paper(summary="An English abstract.")
        result = ai_domain.summary_zh(services, paper, {"mode": "translate"})
        self.assertTrue(result["translated"])
        self.assertEqual((result["mode"], result["chars"]), ("translate", len("中文翻译如下")))
        saved = services.library.get(paper["id"])
        self.assertEqual((saved["summaryZh"], saved["summaryZhModel"]), ("中文翻译如下", "fake-model"))
        self.assertIn("原文摘要", self.fake.server.last_body["messages"][1]["content"])



    def test_summarize_stream_frames_and_save(self):
        self.fake.set({"sse": [delta("总"), delta("结"), {"choices": [], "usage": {"total_tokens": 4}}]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(summary="abs")
        services.fulltext.save(paper["id"], "正文内容" * 100)
        ctx = FakeCtx(services)
        self.assertTrue(ai_domain.summarize_stream(
            services, paper, {}, ai_api.StreamOpener(ctx))["ok"])
        frames = ctx.frames()
        self.assertEqual(frames[0]["type"], "start")
        self.assertTrue(frames[0]["usedFulltext"])
        self.assertGreater(frames[0]["chars"], 0)
        self.assertEqual([f["text"] for f in frames if f["type"] == "delta"], ["总", "结"])
        done = [f for f in frames if f["type"] == "done"][0]
        self.assertEqual((done["text"], done["saved"], done["usage"]),
                         ("总结", True, {"total_tokens": 4}))
        self.assertEqual(services.library.get(paper["id"])["aiSummary"], "总结")

    def test_summarize_stream_error_frame(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services)
        ai_domain.summarize_stream(services, paper, {}, ai_api.StreamOpener(ctx))
        frames = ctx.frames()
        self.assertEqual((frames[0]["type"], frames[-1]["type"]), ("start", "error"))
        self.assertIn("boom", frames[-1]["message"])
        self.assertIsNone(services.library.get(paper["id"])["aiSummary"] or None)

    def test_summarize_stream_no_key_does_not_open_stream(self):
        services = self.make_services()
        ctx = FakeCtx(services)
        result = ai_domain.summarize_stream(services, self.add_paper(), {},
                                            ai_api.StreamOpener(ctx))
        self.assertEqual(result["status"], 400)
        self.assertFalse(ctx.started)

    def test_summarize_sync(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        self.assertEqual(ai_domain.summarize_sync(services, self.add_paper(), {})["status"], 502)
        self.fake.set({"sse": [delta("同步总结")]})
        paper = self.add_paper(title="Sync")
        services.fulltext.save(paper["id"], "正文")
        result = ai_domain.summarize_sync(services, paper, {})
        self.assertEqual((result["text"], result["usedFulltext"], result["usage"]),
                         ("同步总结", True, None))
        self.assertEqual(services.library.get(paper["id"])["aiSummary"], "同步总结")
        other = self.add_paper(title="No Save")       # save=false 不落库
        self.assertTrue(ai_domain.summarize_sync(services, other, {"save": False})["ok"])
        self.assertFalse(services.library.get(other["id"])["aiSummary"])


    def test_summary_zh_batch(self):
        services = self.make_services()
        self.add_paper(summary="abs")
        self.assertEqual(ai_domain.summary_zh_batch(services)["status"], 400)   # 未配置
        self.fake.set({"sse": [delta("中文总结")]})
        services = self.make_services(api_key="sk-test")
        self.add_paper(summary="abs")
        self.add_paper(title="Has zh", summary="abs", summaryZh="已有")
        result = ai_domain.summary_zh_batch(services)
        self.assertEqual((result["total"], result["succeeded"], result["failed"]), (2, 2, 0))
        self.assertNotIn("Has zh", [r["title"] for r in result["results"]])   # 已有中文的跳过

class ApiAiTests(AiTestBase):
    def test_summarize_exit_codes(self):
        services = self.make_services()
        probe = FakeCtx(services)
        ai_api.test(probe)
        self.assertEqual((probe.status, probe.payload["ok"]), (400, False))
        missing = FakeCtx(services, body={"paperId": "nope"})
        ai_api.summarize(missing)
        self.assertEqual((missing.status, missing.payload["error"], missing.started),
                         (404, "找不到这条文献", False))
        no_key = FakeCtx(services, body={"paperId": self.add_paper()["id"]})
        ai_api.summarize_sync(no_key)
        self.assertEqual(no_key.status, 400)
        self.assertIn("还没有配置大模型", no_key.payload["error"])

    def test_summarize_streams_sse_frames(self):
        self.fake.set({"sse": [delta("A"), delta("B")]})
        services = self.make_services(api_key="sk-test")
        ctx = FakeCtx(services, body={"paperId": self.add_paper()["id"]})
        ai_api.summarize(ctx)
        self.assertTrue(ctx.started and ctx.ended)
        self.assertEqual(ctx.headers["Content-Type"], "text/event-stream; charset=utf-8")
        raw = b"".join(ctx.chunks).decode("utf-8")
        self.assertEqual(raw.count("data: "), 4)      # start + 2 delta + done
        self.assertTrue(raw.endswith("\n\n"))
        for block in raw.split("\n\n"):
            if block:
                self.assertTrue(block.startswith("data: "))
                json.loads(block[6:])

    def test_summary_zh_exit_codes(self):
        services = self.make_services()
        missing = FakeCtx(services, params={"id": "nope"}, body={})
        ai_api.summary_zh(missing)
        self.assertEqual(missing.status, 404)
        # 坏 JSON 被吞成 {}（Node 的 .catch(() => ({}))），没配置 → 400 而不是 500
        bad = FakeCtx(services, params={"id": self.add_paper(summary="An English abstract.")["id"]},
                      body=ValueError("坏 JSON"))
        ai_api.summary_zh(bad)
        self.assertEqual(bad.status, 400)
        self.assertIn("还没有配置大模型", bad.payload["error"])
        # 原文已是中文：不调模型，直接回 200 + translated:false
        zh = FakeCtx(services, params={"id": self.add_paper(summary="中文摘要")["id"]},
                     body={"mode": "translate"})
        ai_api.summary_zh(zh)
        self.assertEqual((zh.status, zh.payload["ok"], zh.payload["translated"]), (200, True, False))
        batch = FakeCtx(services)
        ai_api.summary_zh_batch(batch)
        self.assertEqual(batch.status, 400)



# ---------------------------------------------------------------- WebSearch（Exa）
class WebSearchTests(AiTestBase):
    def setUp(self):
        super().setUp()
        from paperlib.adapters import websearch as websearch_module
        self.module = websearch_module
        self.origin_base = websearch_module.API_BASE
        websearch_module.API_BASE = self.fake.base_url[: -len("/v1")]
        self.ws = websearch_module.WebSearch(self.tmp)
        self.ws.update({"exaApiKey": "exa-secret-key"})

    def tearDown(self):
        self.module.API_BASE = self.origin_base
        super().tearDown()

    @staticmethod
    def results_body():
        return {"results": [
            {"title": "Paper A", "url": "https://www.example.org/a", "score": 0.8123,
             "publishedDate": "2025-03-04T00:00:00.000Z", "author": "Li",
             "highlights": ["片段一", "", "片段二", "片段三"]},
            {"title": "", "url": "https://b.example.org/b", "highlights": []},
            {"title": "没有 url 的条目", "url": ""},
        ]}

    def test_public_settings_and_price(self):
        public = self.ws.public_settings()
        self.assertTrue(public["hasKey"])
        self.assertEqual(public["keyHint"], "exa-…-key")
        self.assertNotIn("exaApiKey", public)
        self.assertEqual((public["useInChat"], public["allowDeep"], public["numResults"]),
                         (True, False, 4))
        self.assertEqual(public["price"]["perSearchEstimate"], 0.011)
        self.assertEqual(public["usage"]["searches"], 0)
        self.assertEqual(len(public["usage"]["month"]), 7)
        self.assertTrue(self.ws.readiness()["ready"])

    def test_update_clamps_and_masks(self):
        self.assertFalse(self.module.WebSearch(self.tmp / "empty2").ready()["ready"])
        self.ws.update({"numResults": 99, "monthlyCapUsd": 1000})
        self.assertEqual((self.ws.settings["numResults"], self.ws.settings["monthlyCapUsd"]), (10, 100))
        self.ws.update({"numResults": 0, "monthlyCapUsd": 0})
        self.assertEqual((self.ws.settings["numResults"], self.ws.settings["monthlyCapUsd"]), (4, 3))
        self.ws.update({"exaApiKey": "exa…-key"})      # 打码值不覆盖
        self.assertEqual(self.ws.settings["exaApiKey"], "exa-secret-key")
        self.ws.update({"exaApiKey": None})            # None 清空
        self.assertEqual(self.ws.settings["exaApiKey"], "")


    def test_search_maps_items_and_records_usage(self):
        empty = self.module.WebSearch(self.tmp / "nokey")
        self.assertIn("还没有配置 Exa API Key", empty.search("晶体", {})["error"])
        self.assertEqual(self.ws.search("   ", {})["error"], "查询词是空的")
        self.fake.set({"json": self.results_body()})
        result = self.ws.search("超分子", {"numResults": 4})
        self.assertTrue(result["ok"])
        self.assertFalse(result["cached"])
        items = result["items"]
        self.assertEqual(len(items), 2)                    # 没有 url 的条目被丢掉
        self.assertEqual(items[0]["domain"], "example.org")  # www. 去掉
        self.assertEqual((items[0]["score"], items[0]["published"]), (0.81, "2025-03-04"))
        self.assertEqual(items[0]["highlights"], ["片段一", "片段二", "片段三"])
        self.assertEqual(items[1]["title"], "https://b.example.org/b")   # 空标题回退成 url
        body = self.fake.server.last_body
        self.assertEqual((body["query"], body["numResults"]), ("超分子", 4))
        self.assertIn("highlights", body["contents"])
        self.assertNotIn("text", body["contents"])
        self.assertEqual(self.fake.server.last_headers.get("x-api-key"), "exa-secret-key")
        usage = self.ws.public_settings()["usage"]
        self.assertEqual((usage["searches"], usage["pages"], usage["estCostUsd"]), (1, 2, 0.009))

    def test_cache_and_budget_guard_skip_calls(self):
        self.fake.set({"json": self.results_body()})
        self.assertTrue(self.ws.search("超分子", {"numResults": 2})["ok"])
        self.fake.server.last_body = None
        self.assertTrue(self.ws.search("超分子", {"numResults": 2})["cached"])
        self.assertIsNone(self.fake.server.last_body)      # 命中缓存，没有再发请求
        self.assertEqual(self.ws.public_settings()["usage"]["searches"], 1)
        self.ws.update({"monthlyCapUsd": 0.1})
        self.ws.record_usage(200)                          # 估算花费已超上限
        self.fake.server.last_body = None
        result = self.ws.search("超分子", {})
        self.assertFalse(result["ok"])
        self.assertIn("本月 Exa 估算花费已达上限", result["error"])
        self.assertIsNone(self.fake.server.last_body)

    def test_answer_default_off_then_enabled(self):
        off = self.ws.answer("超分子进展")
        self.assertFalse(off["ok"])
        self.assertIn("默认关闭", off["error"])
        self.ws.update({"allowDeep": True})
        self.fake.set({"json": {"answer": "  有进展  ", "citations": [
            {"title": "c1", "url": "https://www.x.org/p", "publishedDate": "2024-01-02"},
            {"url": ""}]}})
        deep = self.ws.answer("超分子进展")
        self.assertEqual(deep["answer"], "有进展")
        citation = deep["citations"][0]
        self.assertEqual((citation["title"], citation["domain"], citation["published"]),
                         ("c1", "x.org", "2024-01-02"))
        self.assertTrue(self.ws.answer("超分子进展")["cached"])

    def test_http_error_mapping_and_test_helper(self):
        for status, hint in ((401, "API Key 不对或没有权限"), (429, "额度用尽或触发限流")):
            self.fake.set({"status": status, "json": {"error": "nope"}})
            result = self.ws.search("超分子", {})
            self.assertIn("Exa 返回 HTTP %d" % status, result["error"])
            self.assertIn(hint, result["error"])
        self.fake.set({"json": self.results_body()})
        ok = self.ws.test()
        self.assertTrue(ok["ok"])
        self.assertIn("返回 2 条示例结果", ok["message"])
        self.assertEqual(len(ok["sample"]), 2)


if __name__ == "__main__":
    unittest.main()