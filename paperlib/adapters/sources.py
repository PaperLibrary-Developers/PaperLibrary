"""外部检索源的凭据与配额状态（对应 Node server/adapters/sources.js）。

配置存 data/sources.json；密钥只在本机，不回传前端（public_settings 只给 keyHint）。
"""
from __future__ import annotations

import os
from pathlib import Path

from paperlib.adapters.secret_store import dump_with_protected_secret, load_secret_field
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
        if load_secret_field(self.settings, "openalexApiKey") and self.file.is_file():
            try:
                self.save()
            except OSError:
                pass
        return self.settings

    def save(self):
        write_json_atomic(self.file, dump_with_protected_secret(self.settings, "openalexApiKey"))

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
        quota = self.quota.get("openalex")
        return {
            "openalex": {
                "hasKey": self.has_openalex_key(),
                "keyHint": (key[:4] + "…" + key[-4:]) if key else "",
                "fromEnv": bool(str(os.environ.get("OPENALEX_API_KEY") or "").strip()),
                "quota": None if not quota else {
                    "limit": quota.get("limit"),
                    "remaining": quota.get("remaining"),
                    "exhausted": bool(quota.get("exhausted")),
                    "resetAt": quota.get("resetAt"),
                    "retryAfterSeconds": quota.get("retryAfterSeconds"),
                    "status": quota.get("status"),
                    "at": quota.get("at"),
                    "keyed": bool(quota.get("keyed")),
                },
                "signupUrl": "https://openalex.org",
            },
            "contactEmail": self.contact_email(),
            "note": ("已配置 key：额度按你的账号计算（OpenAlex 免费账号配额约为匿名的 10 倍）"
                     if self.has_openalex_key() else
                     "未配置 key：OpenAlex 额度按共享 IP 计算，别人用完你就会被限流（可注册免费账号提升 10 倍）"),
        }
