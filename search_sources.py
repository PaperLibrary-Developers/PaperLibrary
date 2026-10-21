# -*- coding: utf-8 -*-
"""search_sources.py — 多源学术检索的适配层

把 OpenAlex / Crossref / Europe PMC / arXiv / Semantic Scholar 统一成同一个形状
（指南 2.2 的 ExternalItem 契约），这样上层折叠、排序、facet、界面渲染都不用写 if-else。

设计要点（都来自指南里踩过的坑）：
  · 一个源挂了绝不能让整次搜索失败 —— 每源独立 try/except，失败只标该源 ⚠
  · 每源独立超时；并发用线程池（stdlib，无需第三方依赖）
  · 能力如实标注：不懂布尔的源只收「朴素查询」，由上层本地复核
  · 标题/期刊名必须清洗（先解实体、再剥标签、反复到稳定），否则去重会骗人
"""

import concurrent.futures
import html as _html
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

# 礼貌的 UA：OpenAlex / Crossref 都建议带联系方式，也避免被当成爬虫
UA = "PaperLibrary/1.0 (academic search; mailto:noreply@example.com)"
TIMEOUT = 20
DEFAULT_LIMIT = 10

SOURCES = [
    {"id": "openalex", "label": "OpenAlex",
     "note": "2.5 亿+ 作品，免费无需密钥，最稳（首选）"},
    {"id": "crossref", "label": "Crossref",
     "note": "出版商登记的题录，题录最权威"},
    {"id": "europepmc", "label": "Europe PMC",
     "note": "生命科学 / 医学最全"},
    {"id": "arxiv", "label": "arXiv",
     "note": "预印本，免费（无被引数据）"},
    {"id": "semanticscholar", "label": "Semantic Scholar",
     "note": "AI2 出品（Asta 的同一数据源）；匿名额度很紧，常限流"},
]
SOURCE_IDS = [s["id"] for s in SOURCES]


# ═══════════════════════════════════════════════════
# 清洗（指南 3.5：必须先解实体、再剥标签、反复到稳定）
# ═══════════════════════════════════════════════════

def clean_text(s: str) -> str:
    """清洗标题/期刊名：解 HTML 实体 → 剥标签 → 再解实体（处理双重编码）。"""
    if not s:
        return ""
    t = str(s)
    for _ in range(3):                       # 反复几轮，处理 &amp;lt; 这类双重编码
        before = t
        t = _html.unescape(t)
        t = re.sub(r"<[^>]+>", " ", t)        # 剥标签
        if t == before:
            break
    t = t.replace("\u00ad", "")               # soft hyphen
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def normalize_doi(doi: str) -> str:
    """DOI 归一化：去 https://doi.org/ 前缀、去 doi: 前缀、小写（指南坑 #4）。"""
    s = str(doi or "").strip()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s, flags=re.I)
    s = re.sub(r"^doi:\s*", "", s, flags=re.I)
    return s.strip().lower()


def _restore_abstract(inverted: dict, limit: int = 1200) -> str:
    """OpenAlex 的摘要是倒排索引 {词: [位置…]}，还原成正常文本。"""
    if not inverted or not isinstance(inverted, dict):
        return ""
    pos = {}
    for word, idxs in inverted.items():
        for i in idxs:
            pos[i] = word
    if not pos:
        return ""
    text = " ".join(pos[i] for i in sorted(pos))
    return text[:limit]


def _mk(source, source_id, title, authors, year, venue, doi, citations,
        type_, is_oa=False, oa_url="", url="", abstract="", **extra):
    """构造统一契约的一条记录（所有适配器都走这里，保证字段不缺失）"""
    item = {
        "source": source,
        "sourceId": str(source_id or ""),
        "title": clean_text(title),
        "authors": [clean_text(a) for a in (authors or []) if clean_text(a)],
        "year": int(year) if str(year or "").isdigit() else None,
        "venue": clean_text(venue),
        "doi": normalize_doi(doi),
        "citations": int(citations or 0),
        "type": type_ or "",
        "isOpenAccess": bool(is_oa),
        "oaUrl": oa_url or "",
        "url": url or "",
        "abstract": clean_text(abstract)[:1200],
    }
    item.update(extra)
    return item


# ═══════════════════════════════════════════════════
# HTTP
# ═══════════════════════════════════════════════════

def _fetch(url, accept="application/json", timeout=TIMEOUT):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


class SourceError(Exception):
    """单源失败；带 status 便于界面区分 429 / 5xx / 超时"""
    def __init__(self, msg, status=0):
        super().__init__(msg)
        self.status = status


def _get_json(url, timeout=TIMEOUT):
    try:
        raw = _fetch(url)
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} {e.reason}", e.code)
    except socket.timeout:
        raise SourceError("请求超时", 0)
    except Exception as e:
        raise SourceError(f"{type(e).__name__}: {e}", 0)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except Exception as e:
        raise SourceError(f"返回内容无法解析：{e}", 0)


# ═══════════════════════════════════════════════════
# 各源适配器
# ═══════════════════════════════════════════════════

def search_openalex(q, limit=DEFAULT_LIMIT, page=1, filters=None,
                    api_key="", timeout=TIMEOUT):
    """OpenAlex：覆盖最广、免费、支持原生布尔。"""
    f = filters or {}
    params = {"search": q, "per-page": min(limit, 200),
              "page": max(page, 1)}
    extra = []
    if f.get("yearFrom") or f.get("yearTo"):
        lo = f.get("yearFrom") or "1800"
        hi = f.get("yearTo") or "2100"
        extra.append(f"publication_year:{lo}-{hi}")
    if f.get("onlyOA"):
        extra.append("is_oa:true")
    if f.get("minCitations"):
        extra.append(f"cited_by_count:>{int(f['minCitations'])}")
    if extra:
        params["filter"] = ",".join(extra)
    if api_key:
        params["api_key"] = api_key
    data = _get_json("https://api.openalex.org/works?" + urllib.parse.urlencode(params),
                     timeout=timeout)

    out = []
    for w in data.get("results", []):
        pl = w.get("primary_location") or {}
        src = pl.get("source") or {}
        oa = w.get("open_access") or {}
        authors = []
        for a in w.get("authorships", []):
            nm = ((a.get("author") or {}).get("display_name") or "").strip()
            if nm:
                authors.append(nm)
        out.append(_mk(
            "openalex", w.get("id"), w.get("title") or w.get("display_name"),
            authors, w.get("publication_year"), src.get("display_name"),
            w.get("doi"), w.get("cited_by_count"), w.get("type"),
            oa.get("is_oa"), oa.get("oa_url") or "",
            pl.get("landing_page_url") or w.get("doi") or "",
            _restore_abstract(w.get("abstract_inverted_index")),
        ))
    return out, int((data.get("meta") or {}).get("count") or 0)


def search_crossref(q, limit=DEFAULT_LIMIT, page=1, filters=None,
                    api_key="", timeout=TIMEOUT):
    """Crossref：题录最权威（卷/期/页码），但只做相关性检索、不支持布尔。"""
    f = filters or {}
    params = {"query.bibliographic": q, "rows": min(limit, 100),
              "offset": (max(page, 1) - 1) * min(limit, 100)}
    if f.get("yearFrom") or f.get("yearTo"):
        lo = f.get("yearFrom") or "1800"
        hi = f.get("yearTo") or "2100"
        params["filter"] = f"from-pub-date:{lo}-01-01,until-pub-date:{hi}-12-31"
    data = _get_json("https://api.crossref.org/works?" + urllib.parse.urlencode(params),
                     timeout=timeout)
    msg = data.get("message") or {}
    out = []
    for it in msg.get("items", []):
        # 过滤掉 component（补充材料条目）、没有标题的
        if (it.get("type") or "") == "component":
            continue
        titles = it.get("title") or []
        if not titles or not (titles[0] or "").strip():
            continue
        authors = []
        for a in it.get("author") or []:
            nm = a.get("name") or " ".join(x for x in [a.get("given"), a.get("family")] if x)
            if nm:
                authors.append(nm)
        year = None
        for key in ("issued", "published-print", "published-online", "created"):
            parts = ((it.get(key) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                year = parts[0]
                break
        ctypes = it.get("container-title") or []
        out.append(_mk(
            "crossref", it.get("DOI"), titles[0], authors, year,
            ctypes[0] if ctypes else "", it.get("DOI"),
            it.get("is-referenced-by-count"), it.get("type"),
            False, "", it.get("URL") or "", it.get("abstract") or "",
        ))
    return out, int(msg.get("total-results") or 0)


def search_europepmc(q, limit=DEFAULT_LIMIT, page=1, filters=None,
                     api_key="", timeout=TIMEOUT):
    """Europe PMC：生命科学/医学最全，原生布尔，返回结构干净。"""
    f = filters or {}
    params = {"query": q, "format": "json", "pageSize": min(limit, 100),
              "page": max(page, 1), "resultType": "core"}
    data = _get_json("https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
                     + urllib.parse.urlencode(params), timeout=timeout)
    rl = data.get("resultList") or {}
    out = []
    for r in rl.get("result", []):
        authors = []
        for a in (r.get("authorList") or {}).get("author", []):
            nm = a.get("fullName") or a.get("lastName") or ""
            if nm:
                authors.append(nm)
        if not authors and r.get("authorString"):
            authors = [x.strip() for x in r["authorString"].rstrip(".").split(",") if x.strip()]
        doi = r.get("doi") or ""
        out.append(_mk(
            "europepmc", r.get("id") or doi, r.get("title"), authors,
            r.get("pubYear"), r.get("journalTitle") or r.get("bookOrReportDetails") or "",
            doi, r.get("citedByCount"), (r.get("pubType") or ""),
            False, "", f"https://europepmc.org/article/{r.get('source','')}/{r.get('id','')}"
            if r.get("id") else "", r.get("abstractText") or "",
        ))
    return out, int(data.get("hitCount") or 0)


def search_arxiv(q, limit=DEFAULT_LIMIT, page=1, filters=None,
                 api_key="", timeout=TIMEOUT):
    """arXiv：Atom XML，要自己解析；不支持布尔 OR，只收朴素查询。"""
    f = filters or {}
    start = (max(page, 1) - 1) * min(limit, 100)
    params = {"search_query": f"all:{q}", "start": start,
              "max_results": min(limit, 100)}
    try:
        raw = _fetch("http://export.arxiv.org/api/query?"
                     + urllib.parse.urlencode(params),
                     accept="application/atom+xml")
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} {e.reason}", e.code)
    except socket.timeout:
        raise SourceError("请求超时", 0)
    except Exception as e:
        raise SourceError(f"{type(e).__name__}: {e}", 0)

    ns = {"a": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(raw)
    except Exception as e:
        raise SourceError(f"XML 解析失败：{e}", 0)

    out = []
    for e in root.findall("a:entry", ns):
        title = (e.findtext("a:title", "", ns) or "").strip()
        summ = (e.findtext("a:summary", "", ns) or "").strip()
        pub = (e.findtext("a:published", "", ns) or "")[:4]
        link = ""
        for ln in e.findall("a:link", ns):
            if ln.get("rel") == "alternate":
                link = ln.get("href") or ""
        doi = ""
        jr = e.find("{http://arxiv.org/schemas/atom}journal_ref")
        if jr is not None and jr.text:
            jr = jr.text.strip()
        else:
            jr = ""
        authors = [a.findtext("a:name", "", ns).strip()
                   for a in e.findall("a:author", ns)]
        out.append(_mk(
            "arxiv", e.findtext("a:id", "", ns), title, authors, pub,
            jr or "arXiv", doi, 0, "preprint", True, link, link, summ,
        ))
    total = 0
    try:
        total = int(root.find("{http://a9.com/-/spec/opensearch/1.1/}totalResults").text)
    except Exception:
        total = len(out)
    return out, total


def search_semanticscholar(q, limit=DEFAULT_LIMIT, page=1, filters=None,
                           api_key="", timeout=TIMEOUT):
    """Semantic Scholar（AI2，Asta 的同一数据源）。

    匿名额度只有约 1 请求/秒且共享，常年限流；配了 key 会好很多。
    """
    f = filters or {}
    params = {"query": q, "limit": min(limit, 100),
              "offset": (max(page, 1) - 1) * min(limit, 100),
              "fields": "title,authors,year,venue,externalIds,citationCount,"
                        "abstract,openAccessPdf,isOpenAccess,publicationTypes"}
    if f.get("yearFrom") or f.get("yearTo"):
        lo = f.get("yearFrom") or "1800"
        hi = f.get("yearTo") or "2100"
        params["year"] = f"{lo}-{hi}"
    headers = {}
    if api_key:
        headers["x-api-key"] = api_key
    url = "https://api.semanticscholar.org/graph/v1/paper/search?" \
          + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA, **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        raise SourceError(f"HTTP {e.code} {e.reason}", e.code)
    except socket.timeout:
        raise SourceError("请求超时", 0)
    except Exception as e:
        raise SourceError(f"{type(e).__name__}: {e}", 0)

    out = []
    for p in data.get("data", []):
        ext = p.get("externalIds") or {}
        oap = p.get("openAccessPdf") or {}
        out.append(_mk(
            "semanticscholar", p.get("paperId"), p.get("title"),
            [a.get("name") for a in (p.get("authors") or [])],
            p.get("year"), p.get("venue"), ext.get("DOI"),
            p.get("citationCount"), ",".join(p.get("publicationTypes") or []),
            p.get("isOpenAccess"), oap.get("url") or "",
            f"https://www.semanticscholar.org/paper/{p.get('paperId')}",
            p.get("abstract") or "",
        ))
    return out, int(data.get("total") or 0)


PAGERS = {
    "openalex": search_openalex,
    "crossref": search_crossref,
    "europepmc": search_europepmc,
    "arxiv": search_arxiv,
    "semanticscholar": search_semanticscholar,
}

# 原生懂得布尔的源（指南 2.4 第 3 层：能力如实标注）
BOOLEAN_CAPABLE = {"openalex", "europepmc"}


def plain_query(q: str) -> str:
    """给不懂布尔的源用的朴素查询：去掉 AND/OR/NOT 与括号引号。"""
    s = re.sub(r'\b(AND|OR|NOT)\b', " ", str(q or ""), flags=re.I)
    s = re.sub(r'[()"]', " ", s)
    return re.sub(r"\s+", " ", s).strip()


def query_for(source: str, q: str) -> str:
    """按源能力下推查询；不支持的降级为朴素查询（由上层本地复核兜底）。"""
    return q if source in BOOLEAN_CAPABLE else plain_query(q)


def search(query, options=None):
    """在多个源上并行检索（指南 2.3）。

    返回 {
      'items': [...],           # 已按源合并（未跨源折叠）
      'sources': {id: {...}},   # 每源状态：ok/数量 或 error/status
      'hasMore': bool,
      'total': int,
    }
    一个源失败只影响它自己 —— 绝不让整次搜索失败。
    """
    opts = options or {}
    limit = int(opts.get("limit") or DEFAULT_LIMIT)
    page = int(opts.get("page") or 1)
    filters = opts.get("filters") or {}
    keys = opts.get("api_keys") or {}
    wanted = [s for s in (opts.get("sources") or SOURCE_IDS) if s in PAGERS]
    if not wanted:
        wanted = list(SOURCE_IDS)

    results = {}

    def run(sid):
        try:
            items, total = PAGERS[sid](query_for(sid, query), limit=limit, page=page,
                                       filters=filters, api_key=keys.get(sid, ""))
            return sid, {"ok": True, "count": len(items), "total": total}, items
        except SourceError as e:
            st = {"ok": False, "error": str(e), "status": e.status}
            if e.status == 429:
                st["hint"] = "该来源正在限流，稍后重试或只勾选其它来源"
            return sid, st, []
        except Exception as e:
            return sid, {"ok": False, "error": f"{type(e).__name__}: {e}", "status": 0}, []

    out_items = []
    sources = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=len(wanted)) as pool:
        for sid, st, items in pool.map(run, wanted):
            sources[sid] = st
            out_items.extend(items)

    raw_counts = [st.get("count", 0) for st in sources.values() if st.get("ok")]
    return {
        "items": out_items,
        "sources": sources,
        "hasMore": any(c >= limit for c in raw_counts),
        "total": sum(st.get("total", 0) for st in sources.values() if st.get("ok")),
    }
