"""网络检索（Exa）。

移植自 Sci-lib server/adapters/websearch.js（316 行）。
契约名：websearch.exa(query)（本库独有，PaperLibrary 没有这条通道）

分工：学术题录仍走 OpenAlex / Crossref / Europe PMC（结构化、免费、有被引数），
Exa 只负责「网络资料」——语义检索并直接返回网页正文片段（highlights），
正好补上「对话里只能说『有这些相关文献』却拿不到正文」的短板；/answer 直接给带引用的答案。

配置存 data/websearch.json，密钥只在本机、不回传前端（public_settings 只给 keyHint）。
计费：Search $7/1k（含 ≤10 条）+ Contents $1/1k 页，所以我们只要 highlights，
不请求 text/summary（另两档）；本地记账 + monthlyCapUsd 预算护栏，超限直接拒绝。
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from paperlib.util import read_json, write_json_atomic

API_BASE = "https://api.exa.ai"
CACHE_TTL_SECONDS = 30 * 60          # 同一组查询词不重复花钱
CACHE_MAX_ENTRIES = 60
PRICE = {"searchPerRequest": 0.007, "contentPerPage": 0.001}
DEFAULTS = {
    "exaApiKey": "",
    "useInChat": True,
    "numResults": 4,
    "allowDeep": False,
    "monthlyCapUsd": 3,
    "usage": {"month": "", "searches": 0, "pages": 0, "estCostUsd": 0},
}


class WebSearchError(RuntimeError):
    """Exa 调用失败：消息已是中文可读文案。"""


class Readiness(dict):
    """{ready, reason}：前端按 ready.ready 读，旧调用点按布尔判断也行。"""

    def __bool__(self):
        return bool(self.get("ready"))


def _num(value, fallback=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def domain_of(url):
    """从 url 取域名（去 www.），失败返回空串。"""
    try:
        host = urlsplit(str(url)).hostname or ""
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


class WebSearch:
    def __init__(self, data_dir):
        self.file = Path(data_dir) / "websearch.json"
        self.settings = json.loads(json.dumps(DEFAULTS))
        self.cache = {}
        self.load()

    # ---------- 配置 ----------
    def load(self):
        raw = read_json(self.file, {})
        self.settings = json.loads(json.dumps(DEFAULTS))
        if isinstance(raw, dict):
            for key, value in raw.items():
                self.settings[key] = value
            usage = dict(DEFAULTS["usage"])
            if isinstance(raw.get("usage"), dict):
                usage.update(raw["usage"])
            self.settings["usage"] = usage
        return self.settings

    def save(self):
        try:
            write_json_atomic(self.file, self.settings)
        except OSError as exc:
            print("[websearch] 配置保存失败：" + str(exc))

    def public_settings(self):
        """给前端看的配置：绝不回传密钥本身；带上本月用量与估算价。"""
        key = str(self.settings.get("exaApiKey") or "")
        usage = self.current_usage()
        num = int(_num(self.settings.get("numResults"), DEFAULTS["numResults"]) or DEFAULTS["numResults"])
        return {
            "hasKey": bool(key),
            "keyHint": (key[:4] + "…" + key[-4:]) if key else "",
            "useInChat": self.settings.get("useInChat") is not False,
            "allowDeep": self.settings.get("allowDeep") is True,
            "numResults": num,
            "monthlyCapUsd": _num(self.settings.get("monthlyCapUsd"), DEFAULTS["monthlyCapUsd"]),
            "usage": {
                "month": usage.get("month") or "",
                "searches": int(_num(usage.get("searches"), 0)),
                "pages": int(_num(usage.get("pages"), 0)),
                "estCostUsd": round(_num(usage.get("estCostUsd"), 0), 3),
            },
            "price": {
                "searchPerRequest": PRICE["searchPerRequest"],
                "contentPerPage": PRICE["contentPerPage"],
                "perSearchEstimate": round(
                    PRICE["searchPerRequest"] + num * PRICE["contentPerPage"], 3),
            },
        }

    def current_usage(self):
        """本月用量（跨月自动清零）。"""
        month = time.strftime("%Y-%m", time.gmtime())
        usage = self.settings.get("usage") or {}
        if not usage.get("month") or usage.get("month") != month:
            self.settings["usage"] = {"month": month, "searches": 0, "pages": 0, "estCostUsd": 0}
            return self.settings["usage"]
        return {
            "month": usage.get("month"),
            "searches": _num(usage.get("searches"), 0),
            "pages": _num(usage.get("pages"), 0),
            "estCostUsd": _num(usage.get("estCostUsd"), 0),
        }

    def record_usage(self, pages):
        usage = self.current_usage()
        usage["searches"] = _num(usage.get("searches"), 0) + 1
        usage["pages"] = _num(usage.get("pages"), 0) + _num(pages, 0)
        usage["estCostUsd"] = round(
            _num(usage.get("estCostUsd"), 0) + PRICE["searchPerRequest"] + _num(pages, 0) * PRICE["contentPerPage"],
            4)
        self.settings["usage"] = usage
        self.save()

    def over_budget(self):
        """本月估算花费是否已达上限。"""
        cap = _num(self.settings.get("monthlyCapUsd"), DEFAULTS["monthlyCapUsd"]) or DEFAULTS["monthlyCapUsd"]
        usage = self.current_usage()
        spent = _num(usage.get("estCostUsd"), 0)
        return {"over": spent >= cap, "cap": cap, "spent": round(spent, 3)}

    def update(self, patch):
        """更新配置；exaApiKey 传 None=清空，传空串=不改。"""
        patch = patch or {}
        nxt = dict(self.settings)
        if "exaApiKey" in patch:
            value = patch.get("exaApiKey")
            if value is None:
                nxt["exaApiKey"] = ""
            elif isinstance(value, str) and value.strip() and "…" not in value:
                nxt["exaApiKey"] = value.strip()
        if patch.get("useInChat") is not None:
            nxt["useInChat"] = bool(patch.get("useInChat"))
        if patch.get("allowDeep") is not None:
            nxt["allowDeep"] = bool(patch.get("allowDeep"))
        if patch.get("numResults") is not None:
            nxt["numResults"] = min(10, max(1, int(_num(patch.get("numResults"), DEFAULTS["numResults"])
                                                   or DEFAULTS["numResults"])))
        if patch.get("monthlyCapUsd") is not None:
            nxt["monthlyCapUsd"] = min(100, max(0.1, _num(patch.get("monthlyCapUsd"), DEFAULTS["monthlyCapUsd"])
                                                or DEFAULTS["monthlyCapUsd"]))
        if patch.get("resetUsage") is True:
            nxt["usage"] = {"month": "", "searches": 0, "pages": 0, "estCostUsd": 0}
        self.settings = nxt
        self.save()
        self.cache.clear()          # 换了 key/配置，旧缓存作废
        return self.public_settings()

    def readiness(self):
        if not str(self.settings.get("exaApiKey") or ""):
            return Readiness(ready=False, reason="还没有填写 Exa API Key")
        return Readiness(ready=True, reason="")

    def ready(self):
        """api/settings.py 直接把它塞进响应（前端按 ready.ready 读）。"""
        return self.readiness()

    # ---------- HTTP ----------
    def _request(self, endpoint, body):
        key = str(self.settings.get("exaApiKey") or "")
        if not key:
            raise WebSearchError("还没有配置 Exa API Key（到「设置 → 网络检索」里填写）")
        data = json.dumps(body or {}, ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "x-api-key": key,               # Exa 官方用 x-api-key
            "Authorization": "Bearer " + key,   # 顺手带 Bearer，兼容更广
        }
        request = urllib.request.Request(API_BASE + endpoint, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                text = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                text = exc.read().decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                text = ""
            parsed = None
            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = None
            detail = ""
            if isinstance(parsed, dict):
                detail = parsed.get("error") or parsed.get("message") or parsed.get("detail") or ""
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
            if not detail:
                detail = text[:200]
            hint = ""
            if exc.code in (401, 403):
                hint = "（API Key 不对或没有权限）"
            elif exc.code in (402, 429):
                hint = "（额度用尽或触发限流）"
            raise WebSearchError("Exa 返回 HTTP %d%s：%s" % (exc.code, hint, str(detail)[:180])) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise WebSearchError("Exa 请求超时") from None
            raise WebSearchError("连不上 Exa：" + str(reason)) from None
        except (socket.timeout, TimeoutError):
            raise WebSearchError("Exa 请求超时") from None
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        return parsed if isinstance(parsed, dict) else {}

    # ---------- 缓存 ----------
    def cache_get(self, key):
        hit = self.cache.get(key)
        if not hit:
            return None
        if time.time() - hit["at"] > CACHE_TTL_SECONDS:
            self.cache.pop(key, None)
            return None
        return hit["value"]

    def cache_set(self, key, value):
        self.cache[key] = {"at": time.time(), "value": value}
        while len(self.cache) > CACHE_MAX_ENTRIES:
            self.cache.pop(next(iter(self.cache)), None)

    # ---------- 检索 ----------
    def search(self, query, options=None):
        """检索网络资料（只走最便宜的一档：Search + highlights）。

        返回 {ok, items[], cached?} 或 {ok:false, items:[], error}。
        """
        opts = options or {}
        q = str(query or "").strip()
        if not q:
            return {"ok": False, "items": [], "error": "查询词是空的"}
        num = min(10, max(1, int(_num(opts.get("numResults"), 0)
                                or _num(self.settings.get("numResults"), 0) or 4)))
        category = str(opts.get("category") or "")
        include = [str(d) for d in (opts.get("includeDomains") or [])]
        cache_key = "|".join(["search", q, str(num), category, ",".join(include)])
        cached = self.cache_get(cache_key)
        if cached is not None:
            return {"ok": True, "items": cached, "cached": True}

        budget = self.over_budget()
        if budget["over"]:
            return {
                "ok": False,
                "items": [],
                "error": "本月 Exa 估算花费已达上限（约 $%s / 上限 $%s）。"
                         "可在「设置 → 网络检索」里调高上限或重置用量。"
                         % (budget["spent"], budget["cap"]),
            }

        body = {
            "query": q,
            "type": "auto",
            "numResults": num,
            # 只取命中片段：Contents 里最便宜的一档（不请求 text / summary）
            "contents": {"highlights": {"numSentences": 3, "highlightsPerUrl": 2}},
        }
        if category:
            body["category"] = category
        if include:
            body["includeDomains"] = include
        try:
            data = self._request("/search", body)
        except WebSearchError as exc:
            return {"ok": False, "items": [], "error": str(exc)}
        items = []
        for raw in data.get("results") or []:
            if not isinstance(raw, dict):
                continue
            url = str(raw.get("url") or "")
            if not url:
                continue
            score = raw.get("score")
            highlights = [str(h) for h in (raw.get("highlights") or []) if h][:3]
            items.append({
                "title": str(raw.get("title") or url or "（无标题）"),
                "url": url,
                "domain": domain_of(url),
                "published": str(raw.get("publishedDate") or "")[:10],
                "author": str(raw.get("author") or ""),
                "score": round(float(score) * 100) / 100 if isinstance(score, (int, float)) else None,
                "highlights": highlights,
                "summary": str(raw.get("summary") or ""),
            })
        # 计费按「返回了几页的 contents」算
        self.record_usage(len(items))
        self.cache_set(cache_key, items)
        return {"ok": True, "items": items, "cached": False}

    def answer(self, query):
        """深度检索：让 Exa 直接给一段带引用的答案（默认关闭，按 /1k 请求起计费）。"""
        q = str(query or "").strip()
        if not q:
            return {"ok": False, "error": "查询词是空的"}
        if self.settings.get("allowDeep") is not True:
            return {"ok": False, "error": "深度检索（Exa Answer）默认关闭：它按 /1k 请求起计费，比基础检索贵。"}
        cache_key = "answer|" + q
        cached = self.cache_get(cache_key)
        if cached is not None:
            out = dict(cached)
            out["ok"] = True
            out["cached"] = True
            return out
        try:
            data = self._request("/answer", {"query": q, "text": True})
        except WebSearchError as exc:
            return {"ok": False, "error": str(exc)}
        value = {
            "answer": str(data.get("answer") or "").strip(),
            "citations": [{
                "title": str(c.get("title") or c.get("url") or ""),
                "url": str(c.get("url") or ""),
                "domain": domain_of(c.get("url")),
                "published": str(c.get("publishedDate") or "")[:10],
            } for c in (data.get("citations") or []) if isinstance(c, dict) and c.get("url")],
        }
        self.cache_set(cache_key, value)
        return {"ok": True, "cached": False, **value}

    def test(self):
        """「保存并测试连接」用：跑一次最小的检索。"""
        result = self.search("supramolecular chemistry", {"numResults": 2})
        if not result.get("ok"):
            return {"ok": False, "message": result.get("error") or "Exa 不可用"}
        items = result.get("items") or []
        return {"ok": True, "message": "Exa 可用，返回 %d 条示例结果" % len(items), "sample": items[:2]}