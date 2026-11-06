"""外部检索来源之一：Crossref（移植自 Sci-lib server/adapters/external-sources/crossref.js，39 行）。

契约名：search.external(query, filters) → ExternalItem[]（Crossref 分支）

出版商登记的题录（卷/期/页/出版社最权威），但**不支持布尔**，只做相关性检索；
所以查询先降级成 plainQuery，布尔与字段约束交给 adapters/external.py 的本地复核。
"""
from __future__ import annotations

import re

from paperlib.adapters.external_sources.common import (
    build_source_query,
    fetch_json,
    normalize_doi,
    offset_for_page,
    polite_mailto,
    query_for_source,
    urlencode,
    with_params,
)

SELECT = "DOI,title,author,issued,container-title,is-referenced-by-count,URL,type,abstract,link"


def _plain(text) -> str:
    """Crossref 的摘要/标题常带 JATS 类标签，先压成纯文本。"""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def search_crossref_page(query, per_page, page, filters=None) -> list:
    built = build_source_query("crossref", query_for_source("crossref", query), filters or {}, query)
    params = ["rows=" + str(int(per_page)), "offset=" + str(offset_for_page(page, per_page)), "select=" + SELECT]
    mail = polite_mailto()
    if mail:
        params.append("mailto=" + urlencode(mail))
    json_data = fetch_json(with_params(built["url"], params), 15000)
    items = []
    for work in ((json_data or {}).get("message") or {}).get("items") or []:
        if not isinstance(work, dict):
            continue
        issued = ((work.get("issued") or {}).get("date-parts") or [[]])[0]
        doi = normalize_doi(work.get("DOI"))
        links = work.get("link") if isinstance(work.get("link"), list) else []
        pdf_link = next((link for link in links if re.search(
            r"pdf", str((link or {}).get("content-type") or ""), re.I)), None)
        authors = []
        for author in work.get("author") or []:
            if not isinstance(author, dict):
                continue
            name = " ".join(part for part in (author.get("given"), author.get("family")) if part).strip()
            if name or author.get("name"):
                authors.append(name or str(author.get("name")))
        container = (work.get("container-title") or [""])[0] if isinstance(work.get("container-title"), list) else ""
        items.append({
            "source": "crossref",
            "sourceId": doi,
            "title": ((work.get("title") or ["(无标题)"])[0] or "(无标题)") if isinstance(work.get("title"), list) else "(无标题)",
            "authors": authors,
            "year": issued[0] if issued and issued[0] else None,
            "venue": container or "",
            "doi": doi,
            "citations": work.get("is-referenced-by-count") or 0,
            "type": work.get("type") or "",
            "isOpenAccess": bool(pdf_link),
            "oaUrl": (pdf_link or {}).get("URL") or "",
            "url": work.get("URL") or (("https://doi.org/" + doi) if doi else ""),
            "abstract": _plain(work.get("abstract"))[:800],
        })
    return items
