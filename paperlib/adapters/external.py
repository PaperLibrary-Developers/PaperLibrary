"""外部文献检索的**编排入口**（移植自 Sci-lib server/adapters/external.js，332 行）。

契约名：search.external(query, filters) → ExternalItem[]

每个来源一个模块（external_sources/{openalex,crossref,europepmc,arxiv,semanticscholar}.py），
它们只负责「把该源的响应变成统一 ExternalItem」；本文件负责：
  · 并发查询（threading + urllib），单源失败不影响整体（错误随结果返回，前端如实展示）
  · 10 分钟内存缓存、跨源折叠去重、按相似度 + 被引数排序
  · 本地兜底过滤、布尔与字段复核、配额耗尽的可读提示

为什么是这几个源而不是 Google Scholar：OpenAlex / Semantic Scholar 有公开 API；
Google Scholar 没有公开 API 且条款禁止抓取，做成功能会随时失效。
"""
from __future__ import annotations

import re
import threading
import time

from paperlib.adapters.external_sources import arxiv, boolexpr, common
from paperlib.adapters.external_sources.arxiv import search_arxiv_page
from paperlib.adapters.external_sources.common import normalize_doi
from paperlib.adapters.external_sources.crossref import search_crossref_page
from paperlib.adapters.external_sources.europepmc import search_europepmc_page
from paperlib.adapters.external_sources.openalex import (
    fetch_openalex_abstract,
    search_openalex_page,
    test_openalex,
)
from paperlib.adapters.external_sources.semanticscholar import search_semantic_scholar
from paperlib.util import clean_title, now_iso

CACHE_TTL_MS = 10 * 60 * 1000
CACHE_MAX = 80
MAX_LIMIT = 50
FILTER_KEYS = ("yearFrom", "yearTo", "venue", "author", "minCitations", "onlyOA")
# 单次检索的整体预算：各源自己的 HTTP 超时是 15~20 秒，这里兜住「有的源挂死」
SEARCH_BUDGET_MS = 22000

# 可用的外部来源清单（前端用来渲染选择器）
SOURCES = [
    {"id": "openalex", "label": "OpenAlex", "note": "2.5 亿+ 作品，免费无需密钥，最稳",
     "supports": ["year", "venue", "author", "citations", "oa"]},
    {"id": "crossref", "label": "Crossref", "note": "出版商登记的题录，覆盖广（被引数据来自出版商）",
     "supports": ["year", "venue", "author"]},
    {"id": "europepmc", "label": "Europe PMC", "note": "生命科学 / 医学最全，含大量开放获取全文",
     "supports": ["year", "venue", "author"]},
    {"id": "arxiv", "label": "arXiv", "note": "预印本，免费无需密钥（没有被引数）", "supports": ["year"]},
    {"id": "semanticscholar", "label": "Semantic Scholar",
     "note": "有摘要与引用，但免费额度紧（易限流）", "supports": ["year", "venue"]},
]

_cache = {}
_cache_lock = threading.Lock()
_sources = None

_LATIN_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]{1,}")
_CJK_RE = re.compile("[\u4e00-\u9fff]")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]*|[\u4e00-\u9fff]")
_DEDUP_TITLE_RE = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def configure(sources_instance):
    """注入 Sources（凭据 / 配额 / 公开设置）。"""
    global _sources
    _sources = sources_instance
    return common.configure(sources_instance)


def source_helpers():
    return common.source_helpers()


def field_filters_for(source, query):
    return common.field_filters_for(source, query)


def sources_list() -> list:
    """可用的外部来源清单（api/external/sources 的 sources 字段）。"""
    return [dict(item) for item in SOURCES]


# ---------------------------------------------------------------- 缓存


def _cache_get(key):
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        if time.time() * 1000 - hit["at"] > CACHE_TTL_MS:
            _cache.pop(key, None)
            return None
        return hit["value"]


def _cache_set(key, value):
    with _cache_lock:
        _cache[key] = {"at": time.time() * 1000, "value": value}
        if len(_cache) > CACHE_MAX:
            oldest = min(_cache.items(), key=lambda kv: kv[1]["at"])[0]
            _cache.pop(oldest, None)


def clear_cache():
    with _cache_lock:
        _cache.clear()


# ---------------------------------------------------------------- 相似度


def _tokenize_loose(text) -> dict:
    s = str(text or "").lower()
    latin = _LATIN_RE.findall(s)
    cjk = _CJK_RE.findall(s)
    return {"latin": latin, "cjk": cjk, "all": latin + cjk}


def _bigrams_of(text) -> list:
    words = _WORD_RE.findall(str(text or "").lower())
    return [words[i] + " " + words[i + 1] for i in range(len(words) - 1)]


def similarity(query, item) -> float:
    """查询与文献的文本相似度（0~100）：标题覆盖为主，摘要、bigram、整串包含加权。"""
    q = _tokenize_loose(query)
    words = list(dict.fromkeys(q["all"]))
    if not words:
        return 0.0

    def in_set(tokens, word):
        return any(token == word or token.startswith(word) or word.startswith(token) for token in tokens)

    title = _tokenize_loose((item or {}).get("title"))
    abstract = _tokenize_loose((item or {}).get("abstract") or "")
    title_hits = 0
    abstract_hits = 0
    for word in words:
        if in_set(title["all"], word):
            title_hits += 1
        elif in_set(abstract["all"], word):
            abstract_hits += 1
    title_coverage = title_hits / float(len(words))
    abstract_coverage = abstract_hits / float(len(words))

    q_bigrams = _bigrams_of(query)
    title_bigrams = set(_bigrams_of((item or {}).get("title")))
    abstract_bigrams = set(_bigrams_of((item or {}).get("abstract") or ""))
    bigram_hit = (sum(1 for b in q_bigrams if b in title_bigrams or b in abstract_bigrams) / float(len(q_bigrams))
                  if q_bigrams else 0.0)

    q_norm = re.sub(r"\s+", " ", str(query).lower()).strip()
    t_norm = re.sub(r"\s+", " ", str((item or {}).get("title") or "").lower()).strip()
    phrase_bonus = 1.0 if (len(q_norm) >= 4 and q_norm and q_norm in t_norm) else 0.0

    score = 100.0 * (0.50 * title_coverage + 0.20 * abstract_coverage +
                     0.20 * bigram_hit + 0.10 * phrase_bonus)
    return round(score * 10) / 10.0


# ---------------------------------------------------------------- 合并 / 排序


def _dedup_key(item) -> str:
    if item.get("doi"):
        return "doi:" + str(item["doi"]).lower()
    return "title:" + _DEDUP_TITLE_RE.sub(" ", str(item.get("title") or "").lower()).strip()


def merge_results(lists) -> list:
    """把所有来源的结果折叠重复项：先按 DOI，其次按归一化标题。"""
    by_key = {}
    for group in lists or []:
        for item in group or []:
            key = _dedup_key(item)
            existing = by_key.get(key)
            if existing is None:
                merged = dict(item)
                merged["sources"] = [item.get("source")]
                by_key[key] = merged
                continue
            existing["citations"] = max(int(existing.get("citations") or 0), int(item.get("citations") or 0))
            if len(item.get("authors") or []) > len(existing.get("authors") or []):
                existing["authors"] = list(item.get("authors") or [])
            for field in ("venue", "year", "oaUrl", "doi", "url", "abstract"):
                if not existing.get(field) and item.get(field):
                    existing[field] = item[field]
            if existing.get("isOpenAccess") or item.get("isOpenAccess"):
                existing["isOpenAccess"] = True
            if item.get("source") not in existing["sources"]:
                existing["sources"].append(item.get("source"))
    return list(by_key.values())


def rank_by_similarity(items, query) -> list:
    """按「与查询的文本相似度」排序：相似度按 4 分一档，档内按被引数。"""
    bucket = 4

    def sort_key(item):
        relevance = item.get("relevance") or 0
        return (round(relevance / bucket), int(item.get("citations") or 0), relevance)

    scored = []
    for item in items or []:
        row = dict(item)
        row["relevance"] = similarity(query, item)
        row.pop("_score", None)
        row.pop("_rank", None)
        scored.append(row)
    return sorted(scored, key=sort_key, reverse=True)


def apply_local_filters(items, filters) -> list:
    """在本页内做保守过滤（下推给来源之后，这里再兜一次底）。"""
    f = filters or {}
    out = []
    for item in items or []:
        if f.get("yearFrom") and (not item.get("year") or int(item["year"]) < int(f["yearFrom"])):
            continue
        if f.get("yearTo") and (not item.get("year") or int(item["year"]) > int(f["yearTo"])):
            continue
        if f.get("onlyOA") and not item.get("isOpenAccess"):
            continue
        if f.get("minCitations") and int(item.get("citations") or 0) < int(f["minCitations"]):
            continue
        if f.get("venue") and str(f["venue"]).lower() not in str(item.get("venue") or "").lower():
            continue
        if f.get("author") and str(f["author"]).lower() not in " ".join(item.get("authors") or []).lower():
            continue
        out.append(item)
    return out


# ---------------------------------------------------------------- 并发


PAGERS = {
    "openalex": lambda query, per_page, page, filters: search_openalex_page(query, per_page, page, filters),
    "crossref": lambda query, per_page, page, filters: search_crossref_page(query, per_page, page, filters),
    "europepmc": lambda query, per_page, page, filters: search_europepmc_page(query, per_page, page, filters),
    "arxiv": lambda query, per_page, page, filters: search_arxiv_page(query, per_page, page, filters),
    # Semantic Scholar 用 offset 分页（不是 page）
    "semanticscholar": lambda query, per_page, page, filters: search_semantic_scholar(
        query, per_page, common.offset_for_page(page, per_page)),
}


def _run_parallel(names, query, limit, page, filters, budget_ms=None):
    """并发跑各来源：单源异常（含超时）只记在它自己名下，不影响整体。"""
    results = {}
    threads = []

    def worker(name):
        try:
            results[name] = {"ok": True, "items": PAGERS[name](query, limit, page, filters)}
        except common.SourceError as exc:
            results[name] = {"ok": False, "error": exc.message, "status": exc.status,
                             "retryAfterSeconds": exc.retry_after_seconds,
                             "quotaExhausted": exc.quota_exhausted}
        except Exception as exc:  # noqa: BLE001 - 单源失败不影响整体
            results[name] = {"ok": False, "error": str(exc) or exc.__class__.__name__, "status": 0}

    for name in names:
        thread = threading.Thread(target=worker, args=(name,), daemon=True)
        threads.append((name, thread))
        thread.start()
    budget = budget_ms or SEARCH_BUDGET_MS
    deadline = time.time() + (budget / 1000.0)
    for _name, thread in threads:
        thread.join(max(0.0, deadline - time.time()))
    for name, _thread in threads:
        if name not in results:
            results[name] = {"ok": False, "status": 0,
                             "error": "请求超时（整体预算 %d 秒）" % int(budget / 1000)}
    return results


def _quota_of(name):
    sources = _sources
    if sources is None:
        return None
    own = getattr(sources, "quota_of", None)
    if callable(own):
        try:
            return own(name)
        except Exception:  # noqa: BLE001
            return None
    quota = getattr(sources, "quota", None)
    return quota.get(name) if isinstance(quota, dict) else None


# ---------------------------------------------------------------- 对外入口


def _split_options(options, filters):
    """兼容两种调用：search(q, {limit,page,sources,filters}) 与 search(q, filters)。"""
    opts = dict(options or {})
    if filters is not None:
        return opts, dict(filters)
    inner = opts.get("filters")
    if isinstance(inner, dict):
        opts.pop("filters", None)
        return opts, dict(inner)
    if opts and set(opts).issubset(set(FILTER_KEYS)):
        return {}, opts
    return opts, {}


def _int_or(value, fallback):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def search(query, options=None, filters=None) -> dict:
    """同时查多个来源，单源失败不影响整体；跨源重复折叠成一条。

    options: {limit, page, sources:[...], filters:{yearFrom,yearTo,venue,author,minCitations,onlyOA}}
    返回 {query, boolean, fieldQuery, items, sources, page, hasMore, cached, fetchedAt}
    """
    q = str(query or "").strip()
    opts, filters = _split_options(options, filters)
    has_filter = any(value not in ("", None, False) for value in (filters or {}).values())
    if not q and not has_filter:
        return {"query": "", "boolean": False, "fieldQuery": False, "items": [], "sources": {},
                "page": 1, "hasMore": False, "cached": False, "fetchedAt": now_iso()}

    limit = min(max(_int_or(opts.get("limit"), 10), 1), MAX_LIMIT)
    page = max(1, _int_or(opts.get("page"), 1))
    requested = [str(name).strip() for name in (opts.get("sources") or []) if str(name).strip()]
    wanted = [name for name in requested if name in PAGERS] or [item["id"] for item in SOURCES
                                                               if item["id"] in PAGERS]

    cache_key = "|".join([q.lower(), str(limit), str(page), ",".join(wanted),
                          str(sorted((filters or {}).items()))])
    cached = _cache_get(cache_key)
    if cached is not None:
        value = dict(cached)
        value["cached"] = True
        return value

    settled = _run_parallel(wanted, q, limit, page, filters or {})
    sources_report = {}
    lists = []
    for name in wanted:
        result = settled.get(name) or {"ok": False, "error": "未执行", "status": 0}
        if result.get("ok"):
            items = apply_local_filters(result.get("items") or [], filters or {})
            sources_report[name] = {"ok": True, "count": len(items)}
            lists.append(items)
            continue
        entry = {"ok": False, "error": result.get("error") or "未知错误",
                 "status": int(result.get("status") or 0)}
        if entry["status"] == 429:
            snap = _quota_of(name)
            entry["quota"] = snap
            entry["keyless"] = (name == "openalex" and not common.openalex_key())
            entry["hint"] = ("该来源今日额度已用尽（免费接口的每日配额），稍后重试或换其它来源"
                             if (snap or {}).get("exhausted")
                             else "该来源正在限流，稍后重试或只选其它来源")
        sources_report[name] = entry

    rank_query = q or " ".join(str(value) for value in (filters or {}).values() if value)
    merged = rank_by_similarity(merge_results(lists), rank_query)
    # 标题与期刊名先清成纯文本：外部源常给实体形式的排版标签（如 &lt;i&gt;d-&lt;/i&gt;），
    # 不清的话界面会原样显示。放在合并后、复核前，保证排序 / 去重键 / 匹配都用干净文本。
    for item in merged:
        if item.get("title"):
            item["title"] = clean_title(item["title"])
        if item.get("venue"):
            item["venue"] = clean_title(item["venue"])

    def text_of(item):
        return " \n ".join(str(part) for part in (
            item.get("title"), " ".join(item.get("authors") or []), item.get("venue"), item.get("abstract")
        ) if part)

    def field_text_of(item, field):
        field = str(field or "")
        if field == "title":
            return str(item.get("title") or "")
        if field == "author":
            return " ".join(item.get("authors") or [])
        if field == "abstract":
            return str(item.get("abstract") or "")
        if field == "journal":
            return str(item.get("venue") or "")
        if field == "year":
            return str(item.get("year") or "")
        if field == "type":
            return str(item.get("type") or "")
        if field == "doi":
            return str(item.get("doi") or "")
        return ""

    # 布尔复核：Crossref / Semantic Scholar 只做相关性检索，会把 NOT 的词也带回来；
    # 字段限定（title:… / author:…）也在这里落地——外部源大多没有字段检索。
    checked = boolexpr.filter_items(merged, q, text_of, field_text_of)
    value = {
        "query": q,
        "boolean": boolexpr.has_boolean(q),
        "fieldQuery": boolexpr.has_field_syntax(q),
        "items": checked,
        "sources": sources_report,
        "page": page,
        "hasMore": any(result.get("ok") and len(result.get("items") or []) >= limit
                       for result in settled.values()),
        "fetchedAt": now_iso(),
    }
    if lists:
        _cache_set(cache_key, value)
    final = dict(value)
    final["cached"] = False
    return final


def parse_arxiv_feed(xml, filters=None):
    return arxiv.parse_arxiv_feed(xml, filters)


__all__ = [
    "SOURCES", "configure", "sources_list", "search", "merge_results", "rank_by_similarity",
    "apply_local_filters", "similarity", "normalize_doi", "test_openalex", "fetch_openalex_abstract",
    "source_helpers", "field_filters_for", "parse_arxiv_feed", "clear_cache", "PAGERS",
]
