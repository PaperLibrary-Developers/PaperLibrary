"""T4 自测：AI 总结（adapters/llm.py、domain/ai.py、api/ai.py）。

覆盖：配置读写与打码、readiness、strip_echoed_material、SSE 流解析（本地假 OpenAI 服务）、
错误映射（401/404/429/500/超时/连不上）、domain 的 summary_zh/summarize_sync/summarize_stream、
api 层的状态码与「参数校验先于开流」。

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


# ---------------------------------------------------------------- 本地假 OpenAI 服务
def _sse_bytes(chunks):
    out = []
    for chunk in chunks:
        out.append(b"data: " + json.dumps(chunk, ensure_ascii=False).encode("utf-8") + b"\n\n")
    out.append(b": keep-alive\n\n")
    out.append(b"data: [DONE]\n\n")
    return b"".join(out)


class _FakeHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        self.server.last_body = json.loads(raw.decode("utf-8") or "{}")
        self.server.last_headers = {k.lower(): v for k, v in self.headers.items()}
        spec = self.server.spec or {}
        if spec.get("sleep"):
            time.sleep(spec["sleep"])
        if spec.get("sse") is not None:
            payload = _sse_bytes(spec["sse"])
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.end_headers()
            try:
                self.wfile.write(payload)
                self.wfile.flush()
            except OSError:
                pass
            return
        body = json.dumps(spec.get("json") or {}, ensure_ascii=False).encode("utf-8")
        self.send_response(int(spec.get("status") or 200))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except OSError:
            pass

    def log_message(self, *args):
        return


class FakeOpenAI:
    """一次性的假模型服务：设定 spec 后拿 base_url 指过去。"""

    def __init__(self, spec=None):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeHandler)
        self.server.spec = spec or {}
        self.server.last_body = None
        self.server.last_headers = None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self):
        return "http://127.0.0.1:%d/v1" % self.server.server_address[1]

    def set(self, spec):
        self.server.spec = spec or {}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def free_port():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def delta(text):
    return {"choices": [{"delta": {"content": text}}]}


# ---------------------------------------------------------------- 测试地基
class AiTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="nexus-t4-ai-"))
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
        services.llm.update({
            "baseURL": target,
            "model": "fake-model",
            "apiKey": api_key or None,
        })
        self.services = services
        self.store = store
        return services

    def add_paper(self, **fields):
        paper = normalize_paper(dict({"title": "A Paper", "authors": ["Li"], "year": 2024}, **fields))
        self.services.db.insert_paper(paper)
        self.services.library.reindex()
        return self.services.library.get(paper["id"])


class FakeCtx:
    """api 层测试用的最小 ctx（只实现 api 模块用到的那几件事）。"""

    def __init__(self, services, params=None, query=None, body=None):
        self.services = services
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
            text = chunk.decode("utf-8")
            for block in text.split("\n\n"):
                if block.startswith("data: "):
                    out.append(json.loads(block[6:]))
        return out


# ---------------------------------------------------------------- 配置
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

    def test_update_semantics(self):
        llm = LLM(self.tmp)
        llm.update({"apiKey": "sk-1234567890abcd", "model": "deepseek-chat"})
        public = llm.public_settings()
        self.assertTrue(public["hasKey"])
        self.assertEqual(public["keyHint"], "sk-1…abcd")
        # 空串=不改；打码值也不能覆盖
        llm.update({"model": "", "apiKey": "sk-1…abcd", "baseURL": ""})
        self.assertEqual(llm.settings["model"], "deepseek-chat")
        self.assertEqual(llm.settings["apiKey"], "sk-1234567890abcd")
        # None=清空
        llm.update({"apiKey": None})
        self.assertEqual(llm.settings["apiKey"], "")
        # 落盘
        on_disk = json.loads((self.tmp / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(on_disk["apiKey"], "")

    def test_update_restores_defaults(self):
        llm = LLM(self.tmp)
        llm.update({"model": None, "baseURL": None})
        self.assertEqual(llm.settings["model"], "deepseek-chat")
        self.assertEqual(llm.settings["baseURL"], "https://api.deepseek.com/v1")

    def test_readiness(self):
        llm = LLM(self.tmp)
        self.assertIsInstance(llm.readiness(), Readiness)
        self.assertFalse(llm.readiness()["ready"])
        self.assertEqual(llm.readiness()["reason"], "还没有填写 API 密钥")
        # 本地 Ollama 不需要密钥
        llm.update({"baseURL": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"})
        self.assertTrue(llm.readiness()["ready"])
        self.assertTrue(bool(llm.ready()))
        # 前端按 ready.ready 读，所以 ready() 必须是对象
        self.assertTrue(llm.ready()["ready"])

    def test_build_user_message(self):
        llm = LLM(self.tmp)
        text = llm.build_user_message({"title": "T", "authors": ["A", "B"], "year": 2020}, "正文")
        self.assertIn("标题：T", text)
        self.assertIn("A, B", text)
        self.assertIn("===== 正文节选（可能被截断）=====", text)
        empty = llm.build_user_message({"title": "T"}, "")
        self.assertIn("（没有 PDF 正文，仅依据上面的题录与摘要总结）", empty)


class StripEchoedMaterialTests(unittest.TestCase):
    def test_removes_material_sections(self):
        text = "\n".join([
            "标题：X", "===== 原文摘要 =====", "摘要内容", "===== 正文节选 =====",
            "一大段 PDF 原文", "（中间略）", "===== 补充材料：SI.pdf =====", "SI 原文",
        ])
        out = strip_echoed_material(text)
        self.assertIn("摘要内容", out)
        self.assertNotIn("一大段 PDF 原文", out)
        self.assertNotIn("SI 原文", out)
        self.assertNotIn("（中间略）", out)

    def test_keeps_plain_text(self):
        self.assertEqual(strip_echoed_material("  只有总结  "), "只有总结")
        self.assertEqual(strip_echoed_material(None), "")


# ---------------------------------------------------------------- 流式与错误映射
class LLMCallTests(AiTestBase):
    def client(self, api_key="sk-test"):
        llm = LLM(self.tmp)
        llm.update({"baseURL": self.fake.base_url, "model": "fake-model", "apiKey": api_key})
        return llm

    def test_stream_collects_deltas_and_usage(self):
        self.fake.set({"sse": [delta("你"), delta("好"),
                               {"choices": [], "usage": {"prompt_tokens": 3, "total_tokens": 5}}]})
        llm = self.client()
        seen = []
        result = llm.stream(seen.append, {"messages": [{"role": "user", "content": "hi"}]})
        self.assertEqual(seen, ["你", "好"])
        self.assertEqual(result["text"], "你好")
        self.assertEqual(result["usage"], {"prompt_tokens": 3, "total_tokens": 5})
        body = self.fake.server.last_body
        self.assertEqual(body["model"], "fake-model")
        self.assertIs(body["stream"], True)
        self.assertEqual(body["messages"], [{"role": "user", "content": "hi"}])
        self.assertEqual(self.fake.server.last_headers.get("authorization"), "Bearer sk-test")

    def test_stream_accepts_messages_as_first_arg(self):
        self.fake.set({"sse": [delta("ok")]})
        llm = self.client()
        result = llm.stream([{"role": "user", "content": "hi"}])
        self.assertEqual(result["text"], "ok")

    def test_chat_non_stream(self):
        self.fake.set({"json": {"choices": [{"message": {"content": "非流式"}}],
                                "usage": {"total_tokens": 2}}})
        llm = self.client()
        result = llm.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(result["text"], "非流式")
        self.assertEqual(result["usage"]["total_tokens"], 2)
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

    def test_timeout_message(self):
        self.fake.set({"sleep": 2, "json": {}})
        llm = self.client()
        with self.assertRaises(Exception) as ctx:
            llm.stream(None, {"messages": [], "timeoutMs": 1})
        self.assertIn("请求超时", str(ctx.exception))

    def test_connection_error_message(self):
        llm = self.client()
        llm.update({"baseURL": "http://127.0.0.1:%d/v1" % free_port()})
        with self.assertRaises(Exception) as ctx:
            llm.stream(None, {"messages": []})
        self.assertIn("连不上模型接口", str(ctx.exception))

    def test_test_endpoint_helper(self):
        self.fake.set({"sse": [delta("连接正常")]})
        llm = self.client()
        result = llm.test()
        self.assertTrue(result["ok"])
        self.assertIn("连接正常", result["message"])
        self.assertEqual(result["model"], "fake-model")
        # 没配置 → 不联网，直接给原因
        bare = LLM(self.tmp)
        bare.update({"apiKey": None, "baseURL": "https://api.example.invalid/v1"})
        self.assertFalse(bare.test()["ok"])
        self.assertIn("API 密钥", bare.test()["message"])


# ---------------------------------------------------------------- domain
class DomainAiTests(AiTestBase):
    def test_summary_zh_requires_llm(self):
        services = self.make_services()
        paper = self.add_paper(summary="An English abstract.")
        result = ai_domain.summary_zh(services, paper, {})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 400)
        self.assertIn("还没有配置大模型", result["error"])

    def test_summary_zh_chinese_source_needs_no_model(self):
        services = self.make_services()   # 没配置：仍应直接返回原文
        paper = self.add_paper(summary="这是一段中文摘要。")
        result = ai_domain.summary_zh(services, paper, {"mode": "translate"})
        self.assertTrue(result["ok"])
        self.assertFalse(result["translated"])
        self.assertEqual(result["reason"], "原文已经是中文")
        saved = services.library.get(paper["id"])
        self.assertEqual(saved["summaryZh"], "这是一段中文摘要。")
        self.assertEqual(saved["summaryZhModel"], "（原文已是中文）")

    def test_summary_zh_translate_and_strip(self):
        self.fake.set({"sse": [delta("中文翻译"), delta("如下"),
                               {"choices": [], "usage": {"total_tokens": 9}}]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(summary="An English abstract.")
        result = ai_domain.summary_zh(services, paper, {"mode": "translate"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["translated"])
        self.assertEqual(result["mode"], "translate")
        self.assertEqual(result["chars"], len("中文翻译如下"))
        saved = services.library.get(paper["id"])
        self.assertEqual(saved["summaryZh"], "中文翻译如下")
        self.assertEqual(saved["summaryZhModel"], "fake-model")
        self.assertIn("原文摘要", self.fake.server.last_body["messages"][1]["content"])

    def test_summary_zh_without_title_is_400(self):
        services = self.make_services(api_key="sk-test")
        result = ai_domain.summary_zh(services, {"id": "x", "title": ""}, {"mode": "generate"})
        self.assertEqual(result["status"], 400)
        self.assertIn("还没有标题", result["error"])

    def test_summary_zh_model_failure_is_502(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(summary="An English abstract.")
        result = ai_domain.summary_zh(services, paper, {"mode": "translate"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 502)
        self.assertIn("翻译失败", result["error"])

    def test_summarize_stream_frames_and_save(self):
        self.fake.set({"sse": [delta("总"), delta("结"), {"choices": [], "usage": {"total_tokens": 4}}]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper(summary="abs")
        services.fulltext.save(paper["id"], "正文内容" * 100)
        ctx = FakeCtx(services)
        opener = ai_api.StreamOpener(ctx)
        result = ai_domain.summarize_stream(services, paper, {}, opener)
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertEqual(frames[0]["type"], "start")
        self.assertTrue(frames[0]["usedFulltext"])
        self.assertGreater(frames[0]["chars"], 0)
        deltas = [f["text"] for f in frames if f["type"] == "delta"]
        self.assertEqual(deltas, ["总", "结"])
        done = [f for f in frames if f["type"] == "done"][0]
        self.assertEqual(done["text"], "总结")
        self.assertTrue(done["saved"])
        self.assertEqual(done["usage"], {"total_tokens": 4})
        self.assertEqual(services.library.get(paper["id"])["aiSummary"], "总结")

    def test_summarize_stream_error_frame(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services)
        result = ai_domain.summarize_stream(services, paper, {}, ai_api.StreamOpener(ctx))
        self.assertTrue(result["ok"])
        frames = ctx.frames()
        self.assertEqual(frames[0]["type"], "start")
        self.assertEqual(frames[-1]["type"], "error")
        self.assertIn("boom", frames[-1]["message"])
        self.assertIsNone(services.library.get(paper["id"])["aiSummary"] or None)

    def test_summarize_stream_no_key_does_not_open_stream(self):
        services = self.make_services()
        paper = self.add_paper()
        ctx = FakeCtx(services)
        result = ai_domain.summarize_stream(services, paper, {}, ai_api.StreamOpener(ctx))
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 400)
        self.assertFalse(ctx.started)

    def test_summarize_sync(self):
        self.fake.set({"sse": [delta("同步总结")]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        services.fulltext.save(paper["id"], "正文")
        result = ai_domain.summarize_sync(services, paper, {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "同步总结")
        self.assertTrue(result["usedFulltext"])
        self.assertEqual(result["usage"], None)
        self.assertEqual(services.library.get(paper["id"])["aiSummary"], "同步总结")
        # save=false 不落库
        other = self.add_paper(title="No Save")
        saved = ai_domain.summarize_sync(services, other, {"save": False})
        self.assertTrue(saved["ok"])
        self.assertFalse(services.library.get(other["id"])["aiSummary"])

    def test_summarize_sync_failure_is_502(self):
        self.fake.set({"status": 500, "json": {"error": {"message": "boom"}}})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        result = ai_domain.summarize_sync(services, paper, {})
        self.assertEqual(result["status"], 502)

    def test_summary_zh_batch_requires_llm(self):
        services = self.make_services()
        self.add_paper(summary="abs")
        result = ai_domain.summary_zh_batch(services)
        self.assertEqual(result["status"], 400)

    def test_summary_zh_batch_runs_pending(self):
        self.fake.set({"sse": [delta("中文总结")]})
        services = self.make_services(api_key="sk-test")
        self.add_paper(summary="abs")
        self.add_paper(title="Has zh", summary="abs", summaryZh="已有")
        result = ai_domain.summary_zh_batch(services)
        self.assertTrue(result["ok"])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["succeeded"], 1)
        self.assertEqual(result["failed"], 0)


# ---------------------------------------------------------------- api 层
class ApiAiTests(AiTestBase):
    def test_test_endpoint(self):
        services = self.make_services()
        ctx = FakeCtx(services)
        ai_api.test(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertFalse(ctx.payload["ok"])

    def test_summarize_missing_paper_is_404(self):
        services = self.make_services()
        ctx = FakeCtx(services, body={"paperId": "nope"})
        ai_api.summarize(ctx)
        self.assertEqual(ctx.status, 404)
        self.assertEqual(ctx.payload["error"], "找不到这条文献")
        self.assertFalse(ctx.started)

    def test_summarize_sync_no_key_is_400(self):
        services = self.make_services()
        paper = self.add_paper()
        ctx = FakeCtx(services, body={"paperId": paper["id"]})
        ai_api.summarize_sync(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertIn("还没有配置大模型", ctx.payload["error"])

    def test_summarize_streams_sse_frames(self):
        self.fake.set({"sse": [delta("A"), delta("B")]})
        services = self.make_services(api_key="sk-test")
        paper = self.add_paper()
        ctx = FakeCtx(services, body={"paperId": paper["id"]})
        ai_api.summarize(ctx)
        self.assertTrue(ctx.started)
        self.assertTrue(ctx.ended)
        self.assertEqual(ctx.headers["Content-Type"], "text/event-stream; charset=utf-8")
        raw = b"".join(ctx.chunks).decode("utf-8")
        self.assertEqual(raw.count("data: "), 4)          # start + 2 delta + done
        self.assertTrue(raw.endswith("\n\n"))
        for block in raw.split("\n\n"):
            if block:
                self.assertTrue(block.startswith("data: "))
                json.loads(block[6:])

    def test_summary_zh_missing_paper_is_404(self):
        services = self.make_services()
        ctx = FakeCtx(services, params={"id": "nope"}, body={})
        ai_api.summary_zh(ctx)
        self.assertEqual(ctx.status, 404)

    def test_summary_zh_tolerates_bad_json_body(self):
        services = self.make_services()          # 没配置 → 400（不是 500）
        paper = self.add_paper(summary="An English abstract.")
        ctx = FakeCtx(services, params={"id": paper["id"]}, body=ValueError("坏 JSON"))
        ai_api.summary_zh(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertIn("还没有配置大模型", ctx.payload["error"])

    def test_summary_zh_chinese(self):
        services = self.make_services()
        paper = self.add_paper(summary="中文摘要")
        ctx = FakeCtx(services, params={"id": paper["id"]}, body={"mode": "translate"})
        ai_api.summary_zh(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertTrue(ctx.payload["ok"])
        self.assertFalse(ctx.payload["translated"])

    def test_summary_zh_batch_no_key_is_400(self):
        services = self.make_services()
        ctx = FakeCtx(services)
        ai_api.summary_zh_batch(ctx)
        self.assertEqual(ctx.status, 400)


if __name__ == "__main__":
    unittest.main()