"""外部检索各来源的共用底座（移植自 Sci-lib server/adapters/external-sources/common.js，252 行）。

契约名：search.external(query, filters) → ExternalItem[]（入口在 adapters/external.py）

这里放：HTTP（urllib + 超时 + 429 退避 + 配额头记录）、凭据与礼貌头、字段下推、
来源 URL 组装、DOI 归一化、分页偏移。每个来源一个模块，只负责把该源的响应变成统一
ExternalItem（source/sourceId/title/authors/year/venue/doi/citations/type/isOpenAccess/
oaUrl/url/abstract），编排与折叠排序在 adapters/external.py。
"""
from __future__ import annotations

import datetime
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

from paperlib.adapters.external_sources import boolexpr
from paperlib.util import normalize_doi as _normalize_doi_base
from paperlib.util import now_iso

UA = "LiteratureVault/1.0 (local personal library)"
DEFAULT_JSON_TIMEOUT_MS = 15000
DEFAULT_TEXT_TIMEOUT_MS = 20000
# 429 时最多退避重试一次，且最多等这么久（避免请求挂死）
RETRY_MAX_WAIT_MS = 5000
RETRY_BACKOFF_MS = 1500
ACCEPT_JSON = "application/json"
ACCEPT_XML = "application/atom+xml, application/xml, text/xml"

# 各来源的接口根。测试用 set_endpoints() 指到本地 http.server，整条链路不联网。
ENDPOINTS = {
    "openalex": "https://api.openalex.org",
    "crossref": "https://api.crossref.org",
    "europepmc": "https://www.ebi.ac.uk/europepmc/webservices/rest",
    "arxiv": "http://export.arxiv.org",
    "semanticscholar": "https://api.semanticscholar.org",
}
_DEFAULT_ENDPOINTS = dict(ENDPOINTS)

_sources = None
# 可注入的 URL 打开函数：(url, timeout_seconds, headers) -> (status, headers_dict, body_bytes)
# 默认走 urllib；单测可注入假实现，避免联网。
_opener = None
_timeouts = {"json": DEFAULT_JSON_TIMEOUT_MS, "text": DEFAULT_TEXT_TIMEOUT_MS}


class SourceError(Exception):
    """外部源失败：带 HTTP 状态与配额信息（404=没抓到，429=限流/额度用尽）。"""

    def __init__(self, message, status=0, retry_after_seconds=None,
                 quota_exhausted=False, not_found=False):
        super().__init__(message)
        self.message = message
        self.status = int(status or 0)
        self.retry_after_seconds = retry_after_seconds
        self.quota_exhausted = bool(quota_exhausted)
        self.not_found = bool(not_found)


# ---------------------------------------------------------------- 配置


def configure(sources_instance):
    """注入 Sources（凭据 / 配额 / 公开设置）；装配在 services.create_services 里。"""
    global _sources
    _sources = sources_instance
    return _sources


def get_sources():
    return _sources


def set_opener(opener):
    """注入 URL 打开函数（单测用）；传 None 恢复 urllib。"""
    global _opener
    _opener = opener


def set_timeouts(json_ms=None, text_ms=None):
    if json_ms:
        _timeouts["json"] = int(json_ms)
    if text_ms:
        _timeouts["text"] = int(text_ms)
    return dict(_timeouts)


def set_endpoints(overrides=None):
    """把来源接口根临时指向别处（本地 http.server 冒烟）；传 None 恢复默认。"""
    ENDPOINTS.clear()
    ENDPOINTS.update(_DEFAULT_ENDPOINTS)
    if overrides:
        ENDPOINTS.update({str(k): str(v).rstrip("/") for k, v in overrides.items()})
    return dict(ENDPOINTS)


def endpoint(name, path=""):
    base = ENDPOINTS.get(str(name)) or ""
    return base + str(path)


def urlencode(value) -> str:
    return urllib.parse.quote(str(value), safe="")


def normalize_doi(value) -> str:
    """DOI 归一化：去 resolver 前缀与 doi:，并转小写（与 JS common.normalizeDoi 一致）。

    注意 shared/pure/text.py 的 normalize_doi 不转小写（那是给题录字段用的）；
    外部源聚合要按 DOI 折叠去重，所以这里必须统一大小写。
    """
    return _normalize_doi_base(value).lower()


def with_params(url, params) -> str:
    """把参数拼到 URL 上（自动处理已经带 ? 的情况，避免出现 ?&）。"""
    items = [str(p) for p in (params or []) if p]
    if not items:
        return str(url)
    sep = "" if str(url).endswith("?") else ("&" if "?" in str(url) else "?")
    return str(url) + sep + "&".join(items)


def polite_mailto() -> str:
    """礼貌标识邮箱（可选）：OpenAlex/Crossref 对带邮箱的请求更友好。"""
    sources = _sources
    mail = ""
    if sources is not None and hasattr(sources, "contact_email"):
        try:
            mail = str(sources.contact_email() or "")
        except Exception:  # noqa: BLE001
            mail = ""
    return mail if "@" in mail else ""


def openalex_key() -> str:
    """OpenAlex key（优先环境变量，由 Sources 实现）；没有则为空串。"""
    sources = _sources
    if sources is None or not hasattr(sources, "openalex_key"):
        return ""
    try:
        return str(sources.openalex_key() or "")
    except Exception:  # noqa: BLE001
        return ""


def source_helpers() -> dict:
    """给「按 DOI 抓题录」复用同一套请求 / key / 配额逻辑。"""
    return {"fetch_json": fetch_json, "openalex_key": openalex_key, "polite_mailto": polite_mailto}


# ---------------------------------------------------------------- 配额快照


def _header_get(headers, name):
    if headers is None:
        return None
    try:
        getter = getattr(headers, "get", None)
        if callable(getter):
            return getter(name)
    except Exception:  # noqa: BLE001
        return None
    try:
        return dict(headers).get(name)
    except (TypeError, ValueError):
        return None


def _retry_after_seconds(headers):
    raw = _header_get(headers, "retry-after")
    try:
        return float(str(raw).strip()) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _iso_after(seconds):
    try:
        moment = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=float(seconds))
    except (TypeError, ValueError, OverflowError):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def note_quota(source, headers, status):
    """记录一次响应里的配额头部（OpenAlex 给 X-RateLimit-* / Retry-After），界面如实展示。

    Sources 若自带 note_quota 就委托给它；否则写它的 quota 字典（两种都只在本机内存）。
    """
    sources = _sources
    if sources is None or not source:
        return None
    limit = _header_get(headers, "x-ratelimit-limit")
    remaining = _header_get(headers, "x-ratelimit-remaining")
    reset = _header_get(headers, "x-ratelimit-reset")
    retry_after = _header_get(headers, "retry-after")
    used = _header_get(headers, "x-ratelimit-credits-used")
    if limit is None and remaining is None and retry_after is None and not status:
        return None

    def _num(value):
        try:
            return float(str(value).strip()) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    wait = _num(retry_after)
    if wait is None:
        wait = _num(reset)
    snap = {
        "at": now_iso(),
        "status": int(status or 0),
        "limit": _num(limit),
        "remaining": _num(remaining),
        "creditsUsed": _num(used),
        "resetSeconds": _num(reset),
        "retryAfterSeconds": _num(retry_after),
        "resetAt": _iso_after(wait) if wait is not None else None,
        "exhausted": int(status or 0) == 429 or _num(remaining) == 0,
        "keyed": bool(openalex_key() and hasattr(sources, "has_openalex_key") and sources.has_openalex_key()),
    }
    own = getattr(sources, "note_quota", None)
    if callable(own):
        try:
            return own(source, headers, status)
        except Exception:  # noqa: BLE001 - 配额记录失败不影响检索
            pass
    quota = getattr(sources, "quota", None)
    if isinstance(quota, dict):
        quota[source] = snap
    return snap


# ---------------------------------------------------------------- HTTP


def _headers(options, accept):
    merged = {"User-Agent": UA, "Accept": accept}
    merged.update((options or {}).get("headers") or {})
    return merged


def _network_error(exc):
    reason = getattr(exc, "reason", exc)
    detail = str(reason or "").strip() or exc.__class__.__name__
    if isinstance(exc, (socket.timeout, TimeoutError)) or "timed out" in detail.lower():
        return SourceError("请求超时（外部服务无响应）")
    return SourceError("网络请求失败：" + detail)


def _open(url, timeout_seconds, headers):
    opener = _opener
    if opener is not None:
        try:
            return opener(url, timeout_seconds, headers)
        except SourceError:
            raise
        except Exception as exc:  # noqa: BLE001 - 注入的打开函数失败也按网络错误处理
            raise _network_error(exc) from exc
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return int(getattr(response, "status", 200) or 200), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read() or b""
        except Exception:  # noqa: BLE001
            body = b""
        finally:
            try:
                exc.close()
            except Exception:  # noqa: BLE001
                pass
        return int(exc.code), dict(exc.headers.items()) if exc.headers else {}, body
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise _network_error(exc) from exc


def _error_detail(body):
    text = ""
    try:
        text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    except Exception:  # noqa: BLE001
        text = ""
    detail = text[:200]
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            if parsed.get("message"):
                detail = str(parsed["message"])
            elif parsed.get("error"):
                detail = str(parsed["error"])
    except ValueError:
        pass
    return detail


def fetch_json(url, timeout_ms=None, options=None):
    """GET 一个 JSON 接口；失败抛 SourceError（带 status / retry_after_seconds / quota_exhausted）。"""
    options = dict(options or {})
    timeout_seconds = (timeout_ms or _timeouts["json"]) / 1000.0
    status, headers, body = _open(url, timeout_seconds, _headers(options, ACCEPT_JSON))
    if options.get("source"):
        note_quota(options["source"], headers, status)

    if status == 429 and not options.get("_retried"):
        wait = _retry_after_seconds(headers)
        wait_ms = int(wait * 1000) if wait is not None else RETRY_BACKOFF_MS
        if wait_ms <= RETRY_MAX_WAIT_MS:
            import time as _time

            _time.sleep(max(0.3, wait_ms / 1000.0))
            retried = dict(options)
            retried["_retried"] = True
            return fetch_json(url, timeout_ms, retried)

    if status < 200 or status >= 300:
        detail = _error_detail(body)
        error = SourceError("HTTP %d%s" % (status, ("：" + detail) if detail else ""), status=status)
        if status == 429:
            error.retry_after_seconds = _retry_after_seconds(headers)
            error.quota_exhausted = True
        if status == 404:
            error.not_found = True
        raise error
    try:
        return json.loads(body.decode("utf-8", "replace"))
    except (ValueError, AttributeError) as exc:
        raise SourceError("来源返回的不是合法 JSON：" + str(exc)) from exc


def fetch_text(url, timeout_ms=None):
    """GET 一个 XML/Atom 接口（arXiv）；返回文本，失败抛 SourceError。"""
    timeout_seconds = (timeout_ms or _timeouts["text"]) / 1000.0
    status, headers, body = _open(url, timeout_seconds, {"User-Agent": UA, "Accept": ACCEPT_XML})
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body or "")
    if status < 200 or status >= 300:
        error = SourceError("HTTP %d%s" % (status, ("：" + text[:120]) if text else ""), status=status)
        if status == 404:
            error.not_found = True
        raise error
    return text


# ---------------------------------------------------------------- 查询下推


def offset_for_page(page, per_page) -> int:
    return max(0, (int(page or 1) - 1)) * int(per_page or 0)


def field_filters_for(source, query) -> list:
    """字段限定项 → 各来源自己的过滤语法；下推不了的一律返回空（交给本地复核）。"""
    out = []
    for item in boolexpr.field_terms(query):
        field = item.get("field")
        value = str(item.get("value") or "").replace('"', "").strip()
        if not value:
            continue
        if source == "openalex":
            if field == "title":
                out.append("title.search:" + value)
            elif field == "abstract":
                out.append("abstract.search:" + value)
            elif field == "author":
                out.append("raw_author_name.search:" + value)
            elif field == "journal":
                out.append("primary_location.source.display_name.search:" + value)
            elif field == "doi":
                out.append("doi:" + value.lower())
            elif field == "type":
                out.append("type:" + value)
            elif field == "year":
                match = re.match(r"^(\d{4})?\s*[-\u2013\u2014]\s*(\d{4})?$", value)
                if match:
                    if match.group(1):
                        out.append("from_publication_date:" + match.group(1) + "-01-01")
                    if match.group(2):
                        out.append("to_publication_date:" + match.group(2) + "-12-31")
                elif value.isdigit() and len(value) == 4:
                    out.append("publication_year:" + value)
        elif source == "europepmc":
            mapped = {"title": "TITLE", "author": "AUTH", "abstract": "ABSTRACT", "journal": "JOURNAL"}
            if field in mapped:
                out.append(mapped[field] + ':"' + value + '"')
            elif field == "year" and value.isdigit() and len(value) == 4:
                out.append("PUB_YEAR:" + value)
    return out


def build_source_query(source, query, filters, original_query=""):
    """把筛选条件翻译成各来源自己的语法（能下推就下推，拿回的就是筛过的结果）。"""
    f = filters or {}
    field_filters = field_filters_for(source, original_query or "")
    if source == "openalex":
        parts = []
        if query:
            parts.append("search=" + urlencode(query))
        pushed = list(field_filters)
        if f.get("yearFrom"):
            pushed.append("from_publication_date:" + str(f["yearFrom"]) + "-01-01")
        if f.get("yearTo"):
            pushed.append("to_publication_date:" + str(f["yearTo"]) + "-12-31")
        if f.get("onlyOA"):
            pushed.append("is_oa:true")
        if f.get("minCitations"):
            pushed.append("cited_by_count:>" + str(int(f["minCitations"]) - 1))
        if pushed:
            parts.append("filter=" + urlencode(",".join(pushed)))
        return {"url": with_params(endpoint("openalex", "/works"), parts), "kind": "openalex"}
    if source == "crossref":
        params = []
        if query:
            params.append("query.bibliographic=" + urlencode(query))
        pushed = []
        if f.get("yearFrom"):
            pushed.append("from-pub-date:" + str(f["yearFrom"]) + "-01-01")
        if f.get("yearTo"):
            pushed.append("until-pub-date:" + str(f["yearTo"]) + "-12-31")
        if pushed:
            params.append("filter=" + urlencode(",".join(pushed)))
        return {"url": with_params(endpoint("crossref", "/works"), params), "kind": "crossref"}
    if source == "europepmc":
        bits = []
        if query:
            bits.append("(" + str(query) + ")")
        for extra in field_filters:
            bits.append("(" + extra + ")")
        if f.get("yearFrom") or f.get("yearTo"):
            bits.append("(PUB_YEAR:[" + str(f.get("yearFrom") or "1800") + " TO " +
                        str(f.get("yearTo") or "2100") + "])")
        if f.get("onlyOA"):
            bits.append("(OPEN_ACCESS:y)")
        return {"url": with_params(endpoint("europepmc", "/search"), ["query=" + urlencode(" AND ".join(bits))]),
                "kind": "europepmc"}
    return {"url": None, "kind": str(source)}


def query_for_source(source, query) -> str:
    """各源不支持的语法如实降级：openalex/europepmc 原样透传，arxiv 换缩写，其余取朴素查询。"""
    raw = "" if query is None else str(query)
    if not boolexpr.has_field_syntax(raw) and not boolexpr.has_boolean(raw):
        return raw
    if boolexpr.has_field_syntax(raw):
        # 字段限定：search= 只放词，字段约束靠 field_filters_for 下推，推不了的交本地复核。
        plain = boolexpr.plain_query(raw)
        return plain or " ".join(boolexpr.all_terms(raw)) or raw
    if source in ("openalex", "europepmc"):
        return raw
    if source == "arxiv":
        return boolexpr.to_arxiv_query(raw)
    return boolexpr.plain_query(raw)
