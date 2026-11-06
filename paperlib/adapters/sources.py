"""外部检索源的凭据与配额状态（对应 Node server/adapters/sources.js）。

配置存 data/sources.json；密钥只在本机，不回传前端（public_settings 只给 keyHint）。
"""
from __future__ import annotations

import os
from pathlib import Path

from paperlib.util import read_json, write_json_atomic

DEFAULTS = {"openalexApiKey": "", "contactEmail": ""}


class Sources:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.file = self.dir / "sources.json"
        self.settings = dict(DEFAULTS)
        self.quota = {}
        self.load()

    def load(self):
        raw = read_json(self.file, {})
        self.settings = dict(DEFAULTS)
        if isinstance(raw, dict):
            self.settings.update(raw)
        return self.settings

    def save(self):
        write_json_atomic(self.file, self.settings)

    def update(self, patch):
        patch = patch or {}
        before = self.settings.get("openalexApiKey") or ""
        if patch.get("openalexApiKey") is not None:
            self.settings["openalexApiKey"] = str(patch.get("openalexApiKey")).strip()
        if patch.get("contactEmail") is not None:
            self.settings["contactEmail"] = str(patch.get("contactEmail")).strip()
        if self.settings.get("openalexApiKey") != before:
            self.quota.pop("openalex", None)
        self.save()
        return self.settings

    def openalex_key(self):
        env = str(os.environ.get("OPENALEX_API_KEY") or "").strip()
        return env or str(self.settings.get("openalexApiKey") or "").strip()

    def has_openalex_key(self):
        return len(self.openalex_key()) > 3

    def contact_email(self):
        mail = str(self.settings.get("contactEmail") or os.environ.get("LV_CONTACT_EMAIL") or "").strip()
        return mail if "@" in mail else ""

    def public_settings(self):
        key = self.openalex_key()
        return {
            "hasOpenAlexKey": bool(key),
            "openalexKeyHint": (key[:4] + "…" + key[-4:]) if key else "",
            "contactEmail": self.contact_email(),
            "quota": self.quota,
        }
