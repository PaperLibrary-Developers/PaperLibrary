"""大模型调用（OpenAI 兼容接口；对应 Node server/adapters/llm.js，317 行）。

契约名：llm.chat(messages) / llm.stream(messages)
配置存 data/settings.json；密钥只在本机，不回传前端（public_settings 只给 keyHint）。
"""
from __future__ import annotations

import json
from pathlib import Path

from paperlib.util import read_json, strip_bom, write_json_atomic

PRESETS = [
    {"id": "deepseek", "label": "DeepSeek（推荐，便宜、中文好）",
     "baseURL": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "docs": "https://platform.deepseek.com/api_keys"},
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


class LLM:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "settings.json"
        raw = read_json(self.file, {})
        self.settings = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            self.settings.update(raw)

    def load(self):
        raw = read_json(self.file, {})
        self.settings = dict(DEFAULT_SETTINGS)
        if isinstance(raw, dict):
            self.settings.update(raw)
        return self.settings

    def save(self):
        write_json_atomic(self.file, self.settings)

    def update(self, patch):
        for key, value in (patch or {}).items():
            if value is None:
                continue
            self.settings[key] = value
        self.save()
        return self.settings

    def public_settings(self):
        key = str(self.settings.get("apiKey") or "")
        out = dict(self.settings)
        out["apiKey"] = ""
        out["hasKey"] = bool(key.strip())
        out["keyHint"] = (key[:4] + "…" + key[-4:]) if key else ""
        return out

    def ready(self):
        return bool(str(self.settings.get("apiKey") or "").strip())

    def install_hint(self):
        return "还没有配置大模型：到「设置 → 大模型」填入 API Key（或切到本地 Ollama）"

    # ---- 真正调用模型（T4 实现；接口约定见文件头） ----
    def chat(self, messages, options=None):
        raise NotImplementedError("待实现：LLM.chat（POST {baseURL}/chat/completions）")

    def stream(self, messages, options=None):
        raise NotImplementedError("待实现：LLM.stream（流式回调）")

    def test(self):
        raise NotImplementedError("待实现：LLM.test")
