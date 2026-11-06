"""网络检索（Exa；对应 Node server/adapters/websearch.js，316 行）。

契约名：websearch.exa(query)
配置存 data/websearch.json；密钥只在本机，不回传前端（public_settings 只给 keyHint）。
"""
from __future__ import annotations

from pathlib import Path

from paperlib.util import read_json, write_json_atomic

API_BASE = "https://api.exa.ai"
PRICE = {"searchPerRequest": 0.007, "contentPerPage": 0.001}
DEFAULTS = {
    "exaApiKey": "",
    "useInChat": True,
    "numResults": 4,
    "allowDeep": False,
    "monthlyCapUsd": 3,
    "usage": {"month": "", "searches": 0, "pages": 0, "estCostUsd": 0},
}


class WebSearch:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "websearch.json"
        self.settings = dict(DEFAULTS)
        self.cache = {}
        self.load()

    def load(self):
        raw = read_json(self.file, {})
        self.settings = dict(DEFAULTS)
        if isinstance(raw, dict):
            self.settings.update(raw)
            usage = dict(DEFAULTS["usage"])
            usage.update(raw.get("usage") or {})
            self.settings["usage"] = usage
        return self.settings

    def save(self):
        write_json_atomic(self.file, self.settings)

    def public_settings(self):
        key = str(self.settings.get("exaApiKey") or "")
        usage = self.settings.get("usage") or dict(DEFAULTS["usage"])
        return {
            "hasKey": bool(key),
            "keyHint": (key[:4] + "…" + key[-4:]) if key else "",
            "useInChat": self.settings.get("useInChat") is not False,
            "allowDeep": self.settings.get("allowDeep") is True,
            "numResults": int(self.settings.get("numResults") or DEFAULTS["numResults"]),
            "monthlyCapUsd": float(self.settings.get("monthlyCapUsd") or DEFAULTS["monthlyCapUsd"]),
            "usage": {
                "month": usage.get("month") or "",
                "searches": int(usage.get("searches") or 0),
                "pages": int(usage.get("pages") or 0),
                "estCostUsd": round(float(usage.get("estCostUsd") or 0), 3),
            },
        }

    def update(self, patch):
        patch = patch or {}
        for key in ("exaApiKey", "useInChat", "numResults", "allowDeep", "monthlyCapUsd"):
            if patch.get(key) is not None:
                self.settings[key] = patch.get(key)
        self.save()
        return self.public_settings()

    def ready(self):
        return bool(str(self.settings.get("exaApiKey") or "").strip())

    # ---- 真正联网（T4 实现） ----
    def search(self, query, options=None):
        raise NotImplementedError("待实现：WebSearch.search（Exa /search + highlights）")

    def answer(self, query):
        raise NotImplementedError("待实现：WebSearch.answer（Exa /answer）")

    def test(self):
        raise NotImplementedError("待实现：WebSearch.test")
