"""外部检索来源之一：Semantic Scholar（移植自 Sci-lib server/adapters/external-sources/semanticscholar.js，36 行）。

契约名：search.external(query, filters) → ExternalItem[]（Semantic Scholar 分支）

有摘要与引用，但免费额度紧（易限流）；只做相关性检索、不支持布尔。
"""
from __future__ import annotations

import os

from paperlib.adapters.external_sources.common import (
    endpoint,
    fetch_json,
    normalize_doi,
    urlencode,
    with_params,
)

FIELDS = "title,year,venue,citationCount,externalIds,openAccessPdf,authors,abstract"
TIMEOUT_MS = 12000


def search_semantic_scholar(query, per_page, offset) -> list:
    key = str(os.environ.get("LV_S2_API_KEY") or "").strip()
    url = with_params(endpoint("semanticscholar", "/graph/v1/paper/search"), [
        "query=" + urlencode(query),
        "limit=" + str(min(max(int(per_page or 10), 1), 20)),
        "offset=" + str(max(0, int(offset or 0))),
        "fields=" + FIELDS,
    ])
    json_data = fetch_json(url, TIMEOUT_MS, {"headers": {"x-api-key": key} if key else {}})
    items = []
    for paper in (json_data or {}).get("data") or []:
        if not isinstance(paper, dict):
            continue
        doi = normalize_doi((paper.get("externalIds") or {}).get("DOI"))
        oa_pdf = (paper.get("openAccessPdf") or {}).get("url") or ""
        paper_id = str(paper.get("paperId") or "")
        items.append({
            "source": "semanticscholar",
            "sourceId": paper_id,
            "title": paper.get("title") or "(无标题)",
            "authors": [a.get("name") for a in (paper.get("authors") or [])
                        if isinstance(a, dict) and a.get("name")],
            "year": paper.get("year") or None,
            "venue": paper.get("venue") or "",
            "doi": doi,
            "citations": paper.get("citationCount") or 0,
            "type": "",
            "isOpenAccess": bool(oa_pdf),
            "oaUrl": oa_pdf,
            "url": ("https://doi.org/" + doi) if doi else
                   (("https://www.semanticscholar.org/paper/" + paper_id) if paper_id else ""),
            "abstract": str(paper.get("abstract") or "")[:800],
        })
    return items
