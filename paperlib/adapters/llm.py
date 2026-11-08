"""大模型调用（OpenAI 兼容接口）。

移植自 Sci-lib server/adapters/llm.js（317 行）。
契约名：llm.chat(messages) / llm.stream(messages)

设计要点（与 Node 版逐条对齐）：
  · 默认走「OpenAI 兼容」接口（DeepSeek / OpenAI / Kimi / 通义 / 本地 Ollama 都是这个格式），
    换模型只改 baseURL + 模型名；provider=anthropic 时切到 Claude 原生协议（/v1/messages、x-api-key）；
  · 密钥存 data/settings.json，只在本机；public_settings 绝不回传密钥（只给 hasKey/keyHint）；
  · stream() 用 urllib 直连 SSE，逐块回调 on_delta，usage 原样带回；chat() 是非流式版本；
  · 错误一律抛 LLMError，消息是中文可读文案（401/402/404/429 各有专门说法），
    api 层把它折成 502（模型调用失败）/ 400（未配置）。
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path

from paperlib.util import read_json, write_json_atomic

PRESETS = [
    {"id": "deepseek", "label": "DeepSeek（推荐，便宜、中文好）",
     "baseURL": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "docs": "https://platform.deepseek.com/api_keys"},
    {"id": "anthropic", "label": "Claude（Anthropic，长文与推理强）",
     "baseURL": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-5",
     "docs": "https://console.anthropic.com/settings/keys"},
    {"id": "openai", "label": "OpenAI", "baseURL": "https://api.openai.com/v1",
     "model": "gpt-4o-mini", "docs": "https://platform.openai.com/api-keys"},
    {"id": "moonshot", "label": "Kimi（月之暗面）", "baseURL": "https://api.moonshot.cn/v1",
     "model": "moonshot-v1-8k", "docs": "https://platform.moonshot.cn/console/api-keys"},
    {"id": "dashscope", "label": "通义千问（阿里云）",
     "baseURL": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "model": "qwen-plus", "docs": "https://bailian.console.aliyun.com/"},
    {"id": "ollama", "label": "本地 Ollama（无需密钥、不联网）",
     "baseURL": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b",
     "docs": "https://ollama.com/"},
]

DEFAULT_SETTINGS = {
    "provider": "deepseek",
    "baseURL": "https://api.deepseek.com/v1",
    "apiKey": "",
    "model": "deepseek-chat",
    "temperature": 0.3,
    "maxTokens": 1200,
    "promptTemplate": "\n".join([
        "你是一名严谨的科研助手。请阅读下面这篇文献的信息与正文节选，输出结构化的中文总结。",
        "",
        "要求：",
        "1. 用 Markdown，包含四个小节：**研究问题**、**方法**、**主要结论**、**对我的价值**；",
        "2. 每节 2-4 条要点，尽量具体（写清方法名、数据、指标），不要空话；",
        "3. 如果正文节选信息不足，就基于标题与摘要总结，并在开头一句说明「仅依据摘要」；",
        "4. 不要编造原文没有的数据、结论或引用。",
    ]),
}

DEFAULT_TIMEOUT_MS = 180000
TIMEOUT_MESSAGE = "请求超时（可以到设置里调小 max_tokens，或换更快的模型）"
LOCAL_URL_RE = re.compile(r"^https?://(127\.0\.0\.1|localhost)", re.I)
ANTHROPIC_VERSION = "2023-06-01"


class LLMError(RuntimeError):
    """模型调用失败：消息已是中文可读文案，上层直接拼进 {ok:false,error}。"""


class Readiness(dict):
    """{ready, reason}：前端要对象（ready.ready），旧调用点按布尔判断也行。"""

    def __bool__(self):
        return bool(self.get("ready"))


def _num(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _detail_of(text):
    """从错误响应体里抠出可读的 detail（与 Node 的 j.error.message || j.error.type 一致）。"""
    raw = str(text or "")
    try:
        parsed = json.loads(raw)
    except ValueError:
        return raw[:300]
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or raw[:300])
        if isinstance(err, str) and err:
            return err[:300]
    return raw[:300]


def _status_message(status, text):
    detail = _detail_of(text)
    if status == 401:
        return "密钥无效或未授权（HTTP 401）：" + detail
    if status == 402:
        return "账户余额不足（HTTP 402）：" + detail
    if status == 404:
        return "接口或模型名不对（HTTP 404）：" + detail
    if status == 429:
        return "请求太频繁或超出限额（HTTP 429）：" + detail
    return "模型接口返回 %d：%s" % (status, detail)


def _delta_text(chunk):
    """一条 SSE JSON 里的增量文本：优先 choices[0].delta.content，退到 message.content。"""
    if not isinstance(chunk, dict):
        return ""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    choice = choices[0]
    if not isinstance(choice, dict):
        return ""
    delta = choice.get("delta")
    if isinstance(delta, dict):
        return delta.get("content") or ""
    message = choice.get("message")
    if isinstance(message, dict):
        return message.get("content") or ""
    return ""


class LLM:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "settings.json"
        self.settings = dict(DEFAULT_SETTINGS)
        self.load()

    # ---------- 配置 ----------
    def load(self):
        raw = read_json(self.file, {})
        self.settings = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            self.settings.update(raw)
        return self.settings

    def save(self):
        write_json_atomic(self.file, self.settings)

    def public_settings(self):
        """给前端用的配置：密钥打码，只回传是否已配置。"""
        key = str(self.settings.get("apiKey") or "")
        base_url = str(self.settings.get("baseURL") or "")
        return {
            "provider": self.settings.get("provider"),
            "baseURL": base_url,
            "model": self.settings.get("model"),
            "temperature": self.settings.get("temperature"),
            "maxTokens": self.settings.get("maxTokens"),
            "promptTemplate": self.settings.get("promptTemplate"),
            "autoSummaryZh": self.settings.get("autoSummaryZh") is not False,
            "hasKey": bool(key),
            "keyHint": (key[:4] + "…" + key[-4:]) if key else "",
            "presets": PRESETS,
            "isLocal": bool(LOCAL_URL_RE.match(base_url)),
        }

    def update(self, patch):
        """更新配置；apiKey 传空串=不改，传 null(None)=清空。

        provider/baseURL/model/promptTemplate 的空串也按「不改」处理：设置页加载失败时表单
        是空的，用户点「保存并测试」会把它们整体写空，模型直接不可用（Node 版踩过这个坑）。
        """
        patch = patch or {}
        nxt = dict(self.settings)
        for key in ("provider", "baseURL", "model", "promptTemplate"):
            if key not in patch:
                continue
            value = patch.get(key)
            if value is None:
                nxt[key] = ""
            elif str(value).strip() != "":
                nxt[key] = str(value)
        if patch.get("temperature") is not None:
            nxt["temperature"] = _num(patch.get("temperature"), nxt.get("temperature") or 0.3)
        if patch.get("maxTokens") is not None:
            nxt["maxTokens"] = int(_num(patch.get("maxTokens"), nxt.get("maxTokens") or 1200))
        if patch.get("autoSummaryZh") is not None:
            nxt["autoSummaryZh"] = bool(patch.get("autoSummaryZh"))
        if "apiKey" in patch:
            value = patch.get("apiKey")
            if value is None:
                nxt["apiKey"] = ""
            elif isinstance(value, str) and value.strip() and "…" not in value:
                nxt["apiKey"] = value.strip()
        # 兜底：历史数据被写空时补回默认值，避免模型彻底不可用
        for key in ("provider", "baseURL", "model", "promptTemplate"):
            if not nxt.get(key):
                nxt[key] = DEFAULT_SETTINGS[key]
        self.settings = nxt
        self.save()
        return self.public_settings()

    def readiness(self):
        """配置是否齐全（本地模型不需要密钥）。"""
        base_url = str(self.settings.get("baseURL") or "")
        model = str(self.settings.get("model") or "")
        if not base_url or not model:
            return Readiness(ready=False, reason="还没有填写接口地址或模型名称")
        if not LOCAL_URL_RE.match(base_url) and not str(self.settings.get("apiKey") or ""):
            return Readiness(ready=False, reason="还没有填写 API 密钥")
        return Readiness(ready=True, reason="")

    def ready(self):
        """api/settings.py 直接把它塞进响应（前端按 ready.ready 读）。"""
        return self.readiness()

    def install_hint(self):
        return "还没有配置大模型：到「设置 → 大模型」填入 API Key（或切到本地 Ollama）"

    # ---------- 协议分支：OpenAI 兼容 / Anthropic ----------
    def _is_anthropic(self):
        """只按 provider 判定，不动历史配置：老数据没有这个字段，仍走 OpenAI 兼容。"""
        return str(self.settings.get("provider") or "").strip().lower() == "anthropic"

    def _anthropic_parts(self, messages):
        """Anthropic 的 system 是顶层字段，且 messages 必须 user 开头、角色交替。"""
        system = []
        chat = []
        for item in messages or []:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "user")
            content = item.get("content")
            if content is None:
                continue
            if role == "system":
                system.append(str(content))
                continue
            if role not in ("user", "assistant"):
                role = "user"
            if chat and chat[-1]["role"] == role:
                chat[-1]["content"] = str(chat[-1]["content"]) + "\n\n" + str(content)
            else:
                chat.append({"role": role, "content": content})
        while chat and chat[0]["role"] != "user":
            chat.pop(0)
        return "\n\n".join(system), chat

    # ---------- 拼消息 ----------
    def build_user_message(self, paper, excerpt):
        """拼给模型的用户消息（题录 + 正文节选）。"""
        paper = paper or {}
        head = [
            "标题：" + str(paper.get("title") or "(无)"),
            "作者：" + (", ".join(paper.get("authors") or []) or "(未填)"),
            "年份：" + str(paper.get("year") or "(未填)"),
            "期刊/会议：" + str(paper.get("venue") or "(未填)"),
            "DOI：" + str(paper.get("doi") or "(未填)"),
            ("原文关键词：" + ", ".join(paper.get("keywords") or [])) if paper.get("keywords") else "",
            ("我写的一句话总结：" + str(paper.get("summary"))) if paper.get("summary") else "",
            ("我的笔记：\n" + str(paper.get("notes"))[:2000]) if paper.get("notes") else "",
        ]
        text = "\n".join([line for line in head if line])
        if excerpt:
            body = "\n\n===== 正文节选（可能被截断）=====\n" + str(excerpt)
        else:
            body = "\n\n（没有 PDF 正文，仅依据上面的题录与摘要总结）"
        return text + body

    def _payload(self, opts, messages, stream):
        temperature = _num(self.settings.get("temperature"), 0.3)
        max_tokens = _num(opts.get("maxTokens"), 0) or _num(self.settings.get("maxTokens"), 0) or 1200
        if self._is_anthropic():
            system, chat = self._anthropic_parts(messages)
            payload = {
                "model": self.settings.get("model"),
                "stream": bool(stream),
                # Anthropic 的 temperature 只接受 0–1：设置里填 1.5 这类值直接 clamp
                "temperature": min(1.0, max(0.0, temperature if temperature else 0.3)),
                "max_tokens": int(max_tokens),
                "messages": chat,
            }
            if system:
                payload["system"] = system
            return payload
        return {
            "model": self.settings.get("model"),
            "stream": bool(stream),
            "temperature": temperature if temperature else 0.3,
            "max_tokens": int(max_tokens),
            "messages": list(messages or []),
        }

    def _open(self, payload, timeout_ms):
        """POST 到对应协议的对话端点，返回可读的响应对象；错误统一折成 LLMError。"""
        base = str(self.settings.get("baseURL") or "").rstrip("/")
        key = str(self.settings.get("apiKey") or "")
        if self._is_anthropic():
            url = base + "/messages"
            headers = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
            if key:
                headers["x-api-key"] = key
        else:
            url = base + "/chat/completions"
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = "Bearer " + key
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        timeout = max(1.0, _num(timeout_ms, DEFAULT_TIMEOUT_MS) / 1000.0)
        try:
            return urllib.request.urlopen(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                body = ""
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            raise LLMError(_status_message(exc.code, body)) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise LLMError(TIMEOUT_MESSAGE) from None
            raise LLMError("连不上模型接口：" + str(reason) + "（请检查 baseURL 与网络）") from None
        except (socket.timeout, TimeoutError):
            raise LLMError(TIMEOUT_MESSAGE) from None

    # ---------- 调用 ----------
    def _delta_of(self, chunk):
        """一条 SSE JSON 里的增量文本（两种协议）。"""
        if self._is_anthropic():
            if not isinstance(chunk, dict) or chunk.get("type") != "content_block_delta":
                return ""
            delta = chunk.get("delta")
            return str(delta.get("text") or "") if isinstance(delta, dict) else ""
        return _delta_text(chunk)

    def stream(self, on_delta=None, options=None):
        """流式对话：on_delta(text_chunk) 反复调用；返回 {"text", "usage"}。

        兼容旧的 stream(messages) 调用形式（第一个参数是消息列表时自动识别）。
        """
        opts = dict(options or {})
        if isinstance(on_delta, (list, tuple)):
            opts.setdefault("messages", list(on_delta))
            on_delta = None
        payload = self._payload(opts, opts.get("messages"), True)
        response = self._open(payload, opts.get("timeoutMs") or DEFAULT_TIMEOUT_MS)
        pieces = []
        usage = None
        try:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip() if isinstance(raw, (bytes, bytearray)) else str(raw).strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                chunk_text = line[5:].strip()
                if not chunk_text or chunk_text == "[DONE]":
                    continue
                try:
                    chunk = json.loads(chunk_text)
                except ValueError:
                    continue
                delta = self._delta_of(chunk)
                if delta:
                    pieces.append(delta)
                    if on_delta:
                        on_delta(delta)
                if isinstance(chunk, dict) and chunk.get("usage"):
                    usage = chunk["usage"]
        except (socket.timeout, TimeoutError):
            raise LLMError(TIMEOUT_MESSAGE) from None
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass
        return {"text": "".join(pieces), "usage": usage}

    def chat(self, messages=None, options=None):
        """非流式对话（脚本/测试用）：返回 {"text", "usage"}。"""
        opts = dict(options or {})
        msgs = messages if messages is not None else (opts.get("messages") or [])
        payload = self._payload(opts, msgs, False)
        response = self._open(payload, opts.get("timeoutMs") or DEFAULT_TIMEOUT_MS)
        try:
            raw = response.read()
        except (socket.timeout, TimeoutError):
            raise LLMError(TIMEOUT_MESSAGE) from None
        finally:
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass
        text = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else str(raw)
        try:
            data = json.loads(text)
        except ValueError:
            raise LLMError("模型接口返回的内容不是 JSON：" + text[:200]) from None
        choice = {}
        choices = data.get("choices") if isinstance(data, dict) else None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            choice = choices[0]
        content = ""
        if self._is_anthropic() and isinstance(data, dict):
            for block in data.get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    content += str(block.get("text") or "")
        message = choice.get("message")
        if isinstance(message, dict) and not content:
            content = message.get("content") or ""
        if not content:
            content = choice.get("text") or ""
        return {"text": str(content), "usage": (data or {}).get("usage") if isinstance(data, dict) else None}

    def chat_stream(self, on_delta=None, options=None):
        """与文献对话用的别名（Node 的 chatStream 就是 stream）。"""
        return self.stream(on_delta, options)

    def test(self):
        """保存并测试连接：发一句最短的话，看能否拿到回复。"""
        ready = self.readiness()
        if not ready["ready"]:
            return {"ok": False, "message": ready["reason"]}
        started = time.time()
        try:
            result = self.stream(None, {
                "messages": [
                    {"role": "system", "content": "你是一个测试用的助手，只回复四个字。"},
                    {"role": "user", "content": "请回复：连接正常"},
                ],
                "timeoutMs": 30000,
            })
            return {
                "ok": True,
                "message": "连接正常，模型回复：" + str(result.get("text") or "").strip()[:60],
                "ms": int((time.time() - started) * 1000),
                "model": self.settings.get("model"),
            }
        except LLMError as exc:
            return {"ok": False, "message": str(exc), "ms": int((time.time() - started) * 1000)}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "message": str(exc), "ms": int((time.time() - started) * 1000)}


# 分节头：总结/翻译时喂给模型的「资料」段（不是它该输出的内容）
MATERIAL_SECTION = re.compile(r"^={3,}\s*(?:正文节选|补充材料)", re.M)
SECTION_HEAD = re.compile(r"^={3,}\s*\S")


def strip_echoed_material(text):
    """去掉模型「把资料原文抄回来」的那一段（正文节选 / 补充材料），保留题录与原文摘要。"""
    s = "" if text is None else str(text)
    if not MATERIAL_SECTION.search(s):
        return s.strip()
    out = []
    skipping = False
    for line in s.split("\n"):
        if MATERIAL_SECTION.search(line):
            skipping = True
            continue
        if skipping and SECTION_HEAD.match(line):
            skipping = False
        if not skipping:
            out.append(line)
    joined = "\n".join(out)
    joined = re.sub(r"\n*…*（中间略）…*\s*$", "", joined)
    joined = re.sub(r"\n{3,}", "\n\n", joined)
    return joined.strip()