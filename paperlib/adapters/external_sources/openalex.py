"""外部检索来源之一：OpenAlex（移植自 Sci-lib server/adapters/external-sources/openalex.js，113 行）。

契约名：search.external(query, filters) → ExternalItem[]（OpenAlex 分支）

2.5 亿+ 作品、免费无需密钥、有被引数与（倒排索引形式的）摘要，是最稳的一路。
对外：search_openalex_page（分页检索）、test_openalex（轻量验证 key 并刷新配额）、
fetch_openalex_abstract（按 DOI 取摘要，摘要兜底的第三层）。
"""
from __future__ import annotations

import datetime
import time

from paperlib.adapters.external_sources.common import (
    SourceError,
    build_source_query,
    endpoint,
    fetch_json,
    get_sources,
    normalize_doi,
    openalex_key,
    polite_mailto,
    query_for_source,
    urlencode,
    with_params,
)

SELECT_FIELDS = ("id,doi,title,display_name,authorships,publication_year,type,primary_location,"
                 "abstract_inverted_index,cited_by_count,open_access,best_oa_location,keywords")


def rebuild_abstract(index, limit=None) -> str:
    """OpenAlex 的摘要是倒排索引（词 → 位置数组），还原成正常文本。

    limit 默认 800：列表里的摘要只要一小段；「补摘要」会显式传更大的值（4000）。
    """
    max_chars = int(limit) if limit and int(limit) > 0 else 800
    if not isinstance(index, dict):
        return ""
    slots = {}
    for word, positions in index.items():
        for pos in positions or []:
            if isinstance(pos, int) and pos >= 0:
                slots[pos] = word
    text = " ".join(str(slots[key]) for key in sorted(slots) if slots.get(key))
    return text[:max_chars]


def _quota_snapshot():
    sources = get_sources()
    if sources is None:
        return None
    own = getattr(sources, "quota_of", None)
    if callable(own):
        try:
            return own("openalex")
        except Exception:  # noqa: BLE001
            return None
    quota = getattr(sources, "quota", None)
    if isinstance(quota, dict):
        return quota.get("openalex")
    return None


def _ms_until(reset_at):
    """配额重置还剩多少毫秒（解析不出返回 None）。"""
    if not reset_at:
        return None
    try:
        moment = datetime.datetime.fromisoformat(str(reset_at).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.timestamp() * 1000.0 - time.time() * 1000.0


def _known_exhausted_error():
    """已知额度用尽（且没到重置时间）时直接给出可读原因，别浪费一次请求。

    必须核对凭据：匿名共享 IP 用尽 ≠ 你自己的 key 没额度，拿匿名快照挡带 key 的请求
    会让刚填好的 key 白等一天。
    """
    sources = get_sources()
    snap = _quota_snapshot()
    if not snap:
        return None
    has_key = bool(openalex_key())
    if bool(snap.get("keyed")) != has_key:
        return None
    left = _ms_until(snap.get("resetAt"))
    if not (snap.get("exhausted") and left is not None and left > 0):
        return None
    minutes = max(1, int(round(left / 60000.0)))
    wait = ("%.1f 小时" % (minutes / 60.0)) if minutes >= 60 else ("%d 分钟" % minutes)
    message = "OpenAlex 今日额度已用尽，约 " + wait + "后重置"
    if not has_key:
        message += "（未配置 key，额度按共享 IP 计算；在「设置 → API服务 → 学术检索来源」填一个免费 key 可提升 10 倍）"
    error = SourceError(message, status=429, quota_exhausted=True)
    error.quota_reset_at = snap.get("resetAt")
    return error


def search_openalex_page(query, per_page, page, filters=None) -> list:
    """检索一页 OpenAlex 作品，返回统一 ExternalItem 列表。"""
    blocked = _known_exhausted_error()
    if blocked is not None:
        raise blocked
    built = build_source_query("openalex", query_for_source("openalex", query), filters or {}, query)
    params = ["per-page=" + str(int(per_page)), "page=" + str(int(page))]
    mail = polite_mailto()
    if mail:
        params.append("mailto=" + urlencode(mail))
    key = openalex_key()
    json_data = fetch_json(with_params(built["url"], params), 15000, {
        "source": "openalex",
        "headers": {"Authorization": "Bearer " + key} if key else {},
    })
    items = []
    for work in (json_data or {}).get("results") or []:
        if not isinstance(work, dict):
            continue
        primary = work.get("primary_location") or {}
        source_info = primary.get("source") or {}
        best = work.get("best_oa_location") or {}
        oa_url = best.get("pdf_url") or best.get("landing_page_url") or ""
        doi = normalize_doi(work.get("doi"))
        items.append({
            "source": "openalex",
            "sourceId": work.get("id"),
            "title": work.get("title") or work.get("display_name") or "(无标题)",
            "authors": [a.get("author", {}).get("display_name") for a in (work.get("authorships") or [])
                        if isinstance(a, dict) and isinstance(a.get("author"), dict)
                        and a["author"].get("display_name")],
            "year": work.get("publication_year") or None,
            "venue": source_info.get("display_name") or "",
            "doi": doi,
            "citations": work.get("cited_by_count") or 0,
            "type": work.get("type") or "",
            "isOpenAccess": bool((work.get("open_access") or {}).get("is_oa")),
            "oaUrl": oa_url,
            "url": ("https://doi.org/" + doi) if doi else (work.get("id") or ""),
            "abstract": rebuild_abstract(work.get("abstract_inverted_index")),
        })
    return items


def test_openalex() -> dict:
    """用一个轻量请求验证 key 是否可用，并刷新配额快照（只查 1 条）。"""
    key = openalex_key()
    url = with_params(endpoint("openalex", "/works"),
                      ["per-page=1", "select=id,display_name", "search=cucurbituril"])
    json_data = fetch_json(url, 15000, {
        "source": "openalex",
        "headers": {"Authorization": "Bearer " + key} if key else {},
    })
    results = (json_data or {}).get("results") or []
    sample = (results[0] or {}).get("display_name") if results and isinstance(results[0], dict) else ""
    sources = get_sources()
    quota = _quota_snapshot()
    settings = {}
    if sources is not None and hasattr(sources, "public_settings"):
        try:
            settings = sources.public_settings()
        except Exception:  # noqa: BLE001
            settings = {}
    return {"hasKey": bool(key), "sample": sample or "", "quota": quota, "settings": settings}


def fetch_openalex_abstract(doi):
    """按 DOI 取 OpenAlex 摘要（第三层兜底）；取不到返回 None。"""
    clean = normalize_doi(doi)
    if not clean:
        return None
    key = openalex_key()
    url = with_params(endpoint("openalex", "/works/doi:" + urlencode(clean)),
                      ["select=id,doi,abstract_inverted_index"])
    json_data = fetch_json(url, 15000, {
        "source": "openalex",
        "headers": {"Authorization": "Bearer " + key} if key else {},
    })
    text = rebuild_abstract((json_data or {}).get("abstract_inverted_index"), 4000)
    return text or None
