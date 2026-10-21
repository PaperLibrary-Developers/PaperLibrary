# -*- coding: utf-8 -*-
"""metadata_enricher.py — 学术题录智能丰富与自动补齐器
对标 Zotero / Paperlib 的 Metadata Resolver：
  1. 优先通过 DOI 精准查询 Crossref / OpenAlex，获取权威作者列表、期刊全名、准确年份与摘要；
  2. 若无 DOI，使用标题模糊匹配 Crossref / OpenAlex，通过字符相似度比对防误判；
  3. 支持单篇更新与后台批量自动清洗，将元数据回填至 papers.db。
"""
import os
import re
import json
import time
import socket
import urllib.parse
import urllib.request
import sqlite3
from difflib import SequenceMatcher
from typing import Dict, Any, Optional, List, Tuple

UA = "PaperLibraryApp/2.0 (mailto:scholar@example.com; Academic Research Assistant)"
TIMEOUT = 12

def _get_json(url: str, timeout: int = TIMEOUT) -> Optional[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:
        return None


def clean_title(s: str) -> str:
    if not s:
        return ""
    s = re.sub(r"\.pdf$", "", s, flags=re.I)
    s = re.sub(r"[-_]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def string_similarity(a: str, b: str) -> float:
    """计算两段字符串的归一化相似度 (0.0 ~ 1.0)"""
    def norm(x):
        return re.sub(r"[^a-zA-Z0-9\u4e00-\u9fa5]", "", x).lower()
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def fetch_from_crossref_doi(doi: str) -> Optional[Dict[str, Any]]:
    """通过 Crossref 根据已知 DOI 抓取权威元数据"""
    doi = doi.strip()
    if not doi:
        return None
    url = f"https://api.crossref.org/works/{urllib.parse.quote(doi)}"
    data = _get_json(url)
    if not data or "message" not in data:
        return None

    msg = data["message"]
    titles = msg.get("title") or []
    title = titles[0] if titles else ""

    authors = []
    for a in msg.get("author") or []:
        nm = a.get("name") or " ".join(x for x in [a.get("given"), a.get("family")] if x)
        if nm:
            authors.append(nm.strip())

    year = None
    for key in ("published-print", "published-online", "issued", "created"):
        parts = ((msg.get(key) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0] and str(parts[0]).isdigit():
            y = int(parts[0])
            if 1950 <= y <= 2026:
                year = y
                break

    journals = msg.get("container-title") or []
    journal = journals[0] if journals else ""

    return {
        "source": "crossref_doi",
        "title": title,
        "authors": authors,
        "journal": journal,
        "year": year,
        "doi": msg.get("DOI", doi),
        "abstract": msg.get("abstract", "")
    }


def fetch_from_crossref_title(query_title: str) -> Optional[Dict[str, Any]]:
    """通过标题模糊检索 Crossref 并进行相似度严格校验"""
    q = clean_title(query_title)
    if len(q) < 8:
        return None
    # 同时使用 query.title 与 query.bibliographic 提高查全率
    url = f"https://api.crossref.org/works?query.title={urllib.parse.quote(q)}&rows=5"
    data = _get_json(url)
    if not data or "message" not in data:
        return None

    items = data["message"].get("items") or []
    for it in items:
        cand_titles = it.get("title") or []
        if not cand_titles:
            continue
        cand_title = cand_titles[0]
        sim = string_similarity(q, cand_title)
        # 高置信度匹配才采纳 (阈值 0.78)
        if sim >= 0.78:
            authors = []
            for a in it.get("author") or []:
                nm = a.get("name") or " ".join(x for x in [a.get("given"), a.get("family")] if x)
                if nm:
                    authors.append(nm.strip())

            year = None
            for key in ("published-print", "published-online", "issued", "created"):
                parts = ((it.get(key) or {}).get("date-parts") or [[None]])[0]
                if parts and parts[0] and str(parts[0]).isdigit():
                    y = int(parts[0])
                    if 1950 <= y <= 2026:
                        year = y
                        break

            journals = it.get("container-title") or []
            return {
                "source": "crossref_title",
                "title": cand_title,
                "authors": authors,
                "journal": journals[0] if journals else "",
                "year": year,
                "doi": it.get("DOI", ""),
                "abstract": it.get("abstract", "")
            }
    return None


def fetch_from_openalex(query_title: str) -> Optional[Dict[str, Any]]:
    """作为 Crossref 的第二兜底：OpenAlex 检索"""
    q = clean_title(query_title)
    if len(q) < 8:
        return None
    url = f"https://api.openalex.org/works?search={urllib.parse.quote(q)}&per-page=5"
    data = _get_json(url)
    if not data or "results" not in data:
        return None

    for it in data.get("results") or []:
        cand_title = it.get("title") or ""
        sim = string_similarity(q, cand_title)
        if sim >= 0.78:
            authors = []
            for authorship in it.get("authorships") or []:
                a_name = (authorship.get("author") or {}).get("display_name")
                if a_name:
                    authors.append(a_name.strip())

            raw_doi = it.get("doi") or ""
            norm_doi = re.sub(r"^https?://doi\.org/", "", raw_doi)

            loc = it.get("primary_location") or {}
            src = loc.get("source") or {}
            journal = src.get("display_name") or ""

            return {
                "source": "openalex",
                "title": cand_title,
                "authors": authors,
                "journal": journal,
                "year": it.get("publication_year"),
                "doi": norm_doi,
                "abstract": ""
            }
    return None


def enrich_paper(paper: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    """综合多源智能解析单篇文献元数据"""
    doi = (paper.get("doi") or "").strip()
    title = (paper.get("title") or paper.get("filename") or "").strip()

    # 1. 有 DOI 优先走 DOI 精确查询
    if doi and "10." in doi:
        meta = fetch_from_crossref_doi(doi)
        if meta and meta.get("authors"):
            return True, meta

    # 2. 用标题查 Crossref
    meta = fetch_from_crossref_title(title)
    if meta and meta.get("authors"):
        return True, meta

    # 3. 兜底走 OpenAlex
    meta = fetch_from_openalex(title)
    if meta and meta.get("authors"):
        return True, meta

    return False, {}


def enrich_and_update_db(db_path: str, paper_id: str) -> Tuple[bool, str]:
    """对库中指定 paper_id 进行在线丰富并写入 DB"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
    if not row:
        conn.close()
        return False, "未找到该文献"

    p = dict(row)
    ok, meta = enrich_paper(p)
    if not ok or not meta:
        conn.close()
        return False, "未能从 Crossref/OpenAlex 检索到高匹配元数据"

    # 准备写入字段
    updates = []
    params = []

    if meta.get("authors"):
        updates.append("authors = ?")
        params.append(json.dumps(meta["authors"], ensure_ascii=False))

    if meta.get("journal"):
        updates.append("journal = ?")
        params.append(meta["journal"])

    if meta.get("doi") and not p.get("doi"):
        updates.append("doi = ?")
        params.append(meta["doi"])

    if meta.get("year") and (not p.get("year") or p.get("year") < 1970 or p.get("year") > 2026):
        updates.append("year = ?")
        params.append(meta["year"])

    if updates:
        params.append(paper_id)
        sql = f"UPDATE papers SET {', '.join(updates)} WHERE id = ?"
        conn.execute(sql, tuple(params))
        conn.commit()
        conn.close()
        au_count = len(meta.get("authors", []))
        return True, f"成功补全：{au_count} 位作者、期刊「{meta.get('journal','')}」"
    
    conn.close()
    return False, "无需更新"

