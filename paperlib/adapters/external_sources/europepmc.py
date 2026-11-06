"""外部检索来源之一：Europe PMC（移植自 Sci-lib server/adapters/external-sources/europepmc.js，34 行）。

契约名：search.external(query, filters) → ExternalItem[]（Europe PMC 分支）

生命科学 / 医学最全，原生支持布尔与括号，所以查询直接透传（下推在 common.build_source_query）。
"""
from __future__ import annotations

import re

from paperlib.adapters.external_sources.common import (
    build_source_query,
    fetch_json,
    normalize_doi,
    query_for_source,
    with_params,
)


def _plain(text) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(text or ""))).strip()


def _split_authors(author_string) -> list:
    raw = str(author_string or "")
    if not raw.strip():
        return []
    return [part.strip() for part in re.split(r"[,;]\s*", raw) if part.strip()]


def search_europepmc_page(query, per_page, page, filters=None) -> list:
    built = build_source_query("europepmc", query_for_source("europepmc", query), filters or {}, query)
    params = ["format=json", "pageSize=" + str(int(per_page)), "page=" + str(int(page)), "resultType=core"]
    json_data = fetch_json(with_params(built["url"], params), 15000, {"source": "europepmc"})
    hits = ((json_data or {}).get("resultList") or {}).get("result") or []
    items = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue
        doi = normalize_doi(hit.get("doi"))
        is_oa = str(hit.get("isOpenAccess") or "").upper() == "Y"
        journal = ((hit.get("journalInfo") or {}).get("journal") or {}).get("title") or ""
        pmcid = str(hit.get("pmcid") or "")
        pmid = str(hit.get("pmid") or "")
        items.append({
            "source": "europepmc",
            "sourceId": hit.get("id") or doi,
            "title": hit.get("title") or "(无标题)",
            "authors": _split_authors(hit.get("authorString")),
            "year": int(hit["pubYear"]) if str(hit.get("pubYear") or "").isdigit() else None,
            "venue": journal,
            "doi": doi,
            "citations": int(hit["citedByCount"]) if str(hit.get("citedByCount") or "").isdigit() else 0,
            "type": hit.get("pubType") or "",
            "isOpenAccess": is_oa,
            "oaUrl": ("https://www.ebi.ac.uk/europepmc/webservices/rest/" + pmcid + "/fullTextPDF")
                     if (is_oa and pmcid) else "",
            "url": ("https://doi.org/" + doi) if doi else
                   (("https://pubmed.ncbi.nlm.nih.gov/" + pmid + "/") if pmid else ""),
            "abstract": _plain(hit.get("abstractText"))[:800],
        })
    return items
