"""外部检索来源之一：arXiv（移植自 Sci-lib server/adapters/external-sources/arxiv.js，94 行）。

契约名：search.external(query, filters) → ExternalItem[]（arXiv 分支）

官方 Atom 接口，免费无需密钥；只给题录级信息（没有被引数）。官方要求请求间隔 ≥3 秒，
这里做全局节流（可被测试关掉），多源并发时也不会把 arXiv 打爆。
"""
from __future__ import annotations

import re
import threading
import time

from paperlib.adapters.external_sources import boolexpr
from paperlib.adapters.external_sources.common import (
    endpoint,
    fetch_text,
    normalize_doi,
    with_params,
)

THROTTLE_MS = 3000
_last_at = 0.0
_lock = threading.Lock()
_ENVELOPE = re.compile(r"<entry>([\s\S]*?)</entry>")


def set_throttle_ms(value) -> int:
    """调整 arXiv 的请求间隔（单测设为 0，避免每条用例都等 3 秒）。"""
    global THROTTLE_MS
    THROTTLE_MS = max(0, int(value or 0))
    return THROTTLE_MS


def _throttle():
    global _last_at
    with _lock:
        wait = THROTTLE_MS / 1000.0 - (time.time() - _last_at)
        if wait > 0:
            time.sleep(wait)
        _last_at = time.time()


def decode_xml(value) -> str:
    """arXiv 的 Atom 里的实体手写解码（不引第三方 XML 库，与 JS 版一致）。"""
    return (str(value or "")
            .replace("&lt;", "<").replace("&gt;", ">")
            .replace("&quot;", '"').replace("&#39;", "'").replace("&apos;", "'")
            .replace("&amp;", "&"))


def _pick(entry, tag) -> str:
    match = re.search(r"<" + re.escape(tag) + r"[^>]*>([\s\S]*?)</" + re.escape(tag) + r">", entry)
    return re.sub(r"\s+", " ", decode_xml(match.group(1))).strip() if match else ""


def parse_arxiv_feed(xml, filters=None) -> list:
    """arXiv 返回的是 Atom XML：只取需要的字段，转成统一 ExternalItem。"""
    f = filters or {}
    items = []
    for match in _ENVELOPE.finditer(str(xml or "")):
        entry = match.group(1)
        authors = []
        for author in re.finditer(r"<author>[\s\S]*?<name>([\s\S]*?)</name>[\s\S]*?</author>", entry):
            name = re.sub(r"\s+", " ", decode_xml(author.group(1))).strip()
            if name:
                authors.append(name)
        published = _pick(entry, "published")
        doi_match = re.search(r"<arxiv:doi[^>]*>([\s\S]*?)</arxiv:doi>", entry)
        doi = normalize_doi(decode_xml(doi_match.group(1)).strip() if doi_match else "")
        id_url = _pick(entry, "id")
        title = _pick(entry, "title")
        if not title:
            continue
        item = {
            "source": "arxiv",
            "sourceId": id_url or title,
            "title": title,
            "authors": authors,
            "year": int(published[:4]) if published[:4].isdigit() else None,
            "venue": "arXiv 预印本（" + (_pick(entry, "category") or "preprint") + "）",
            "doi": doi,
            "citations": 0,
            "type": "preprint",
            "isOpenAccess": True,
            "oaUrl": id_url.replace("/abs/", "/pdf/") if id_url else "",
            "url": id_url or (("https://doi.org/" + doi) if doi else ""),
            "doiUrl": ("https://doi.org/" + doi) if doi else "",
            "abstract": _pick(entry, "summary")[:800],
        }
        if f.get("yearFrom") and (not item["year"] or item["year"] < int(f["yearFrom"])):
            continue
        if f.get("yearTo") and (not item["year"] or item["year"] > int(f["yearTo"])):
            continue
        items.append(item)
    return items


def _search_expression(query) -> str:
    raw = str(query or "").strip()
    if boolexpr.has_boolean(raw):
        return boolexpr.to_arxiv_query(raw)
    terms = [term for term in raw.split() if term]
    if len(terms) > 1:
        return "+AND+".join('all:"' + term.replace('"', "") + '"' for term in terms)
    return "all:" + re.sub(r"\s+", "+", raw)


def search_arxiv_page(query, per_page, page, filters=None) -> list:
    _throttle()
    start = (max(1, int(page or 1)) - 1) * int(per_page)
    expression = _search_expression(query)
    url = with_params(endpoint("arxiv", "/api/query"), [
        "search_query=" + boolexpr_quote(expression),
        "start=" + str(start),
        "max_results=" + str(int(per_page)),
        "sortBy=relevance",
    ])
    xml = fetch_text(url, 20000)
    return parse_arxiv_feed(xml, filters)


def boolexpr_quote(value) -> str:
    """arXiv 要的是「编码后把 %2B 还原成 +」的形式。"""
    from urllib.parse import quote

    return quote(str(value), safe="").replace("%2B", "+")
