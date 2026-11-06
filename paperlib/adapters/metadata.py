"""题录解析（移植自 Sci-lib server/adapters/metadata.js，410 行；契约名 metadata.resolve）。

契约名：metadata.resolve(doi | title) → Paper

  · fetch_by_doi_detailed(doi) → { paper, sources, errors, fieldsFrom, elapsedMs }
    （前端据此如实显示「题录：Crossref ✓　摘要：OpenAlex ✓」）
  · fetch_by_doi(doi) → Paper（pdf-import 等内部调用）
  · search_by_title(title, limit) → Paper[]（Crossref 模糊搜索）
  · merge_papers(canonical, supplement) → { paper, fieldsFrom }（字段级 provenance）
  · resolve_from_pdf(abs_path)（本库新增的门面：PyMuPDF 首屏字体级标题 → 标题反查题录）

本文件合并了原 server/metadata.js 与 server/doi-lookup.js 两段：一、多来源并行 + 逐字段合并；
二、对外门面。两段里有两组同义定义（TYPE_MAP 与 CROSSREF_TYPE_MAP、pick_date 与 Crossref
日期提取），按「先搬后改」保留原样，将来要统一再说。
"""
from __future__ import annotations

import os
import threading
import time

from paperlib.adapters.external_sources import common
from paperlib.util import clean_title, normalize_doi, now_iso
from shared.pure.html_text import to_plain_text

UA = "LiteratureVault/1.0 (local personal library)"
# 单源超时：两个源并行，所以用户最多等这么久（而不是 15s × 2）
SOURCE_TIMEOUT_MS = 12000
# 整体预算：>= 单源超时，两个源并行时它才是真正的上限（超过就用手上已有的结果）
TOTAL_BUDGET_MS = 13000

_sources = None


class MetadataError(Exception):
    """题录抓取失败：not_found 表示「两个源都没这个 DOI」（api 层按 404 处理）。"""

    def __init__(self, message, not_found=False, errors=None):
        super().__init__(message)
        self.message = message
        self.not_found = bool(not_found)
        self.errors = dict(errors or {})


def configure(sources_instance):
    """注入 Sources（OpenAlex key / 联系邮箱与外部检索共用同一套配置）。"""
    global _sources
    _sources = sources_instance
    common.configure(sources_instance)
    return _sources


# ---------------------------------------------------------------- 类型映射 / 日期


CROSSREF_TYPE_MAP = {
    "journal-article": "journal",
    "proceedings-article": "conference",
    "book-chapter": "book",
    "book": "book",
    "monograph": "book",
    "edited-book": "book",
    "reference-book": "book",
    "dissertation": "thesis",
    "report": "report",
    "posted-content": "preprint",
    "dataset": "other",
    "peer-review": "other",
}

OPENALEX_TYPE_MAP = {
    "article": "journal",
    "review": "journal",
    "book-chapter": "book",
    "book": "book",
    "monograph": "book",
    "dissertation": "thesis",
    "report": "report",
    "preprint": "preprint",
    "dataset": "other",
    "paratext": "other",
    "editorial": "other",
    "letter": "other",
}

# 兼容既有单测：旧名 TYPE_MAP 与 pick_date
TYPE_MAP = CROSSREF_TYPE_MAP


def pick_date(item) -> int | None:
    """Crossref 的发布日期：issued → published-print → published-online → created。"""
    item = item or {}
    for key in ("issued", "published-print", "published-online", "created"):
        parts = ((item.get(key) or {}).get("date-parts") or [[]])[0]
        if parts and parts[0]:
            return int(parts[0])
    return None


pick_crossref_date = pick_date


def is_filled(value) -> bool:
    """只保留真正有内容的字段，避免空串覆盖掉别的好数据。"""
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return len(value) > 0
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value != 0
    return True


# ---------------------------------------------------------------- 各源 → 统一 paper


def crossref_to_paper(item, doi=None):
    """Crossref 的 message → 统一 paper 形状。"""
    if not isinstance(item, dict):
        return None
    authors = []
    for author in item.get("author") or []:
        if not isinstance(author, dict):
            continue
        if author.get("name"):
            authors.append(str(author["name"]))
            continue
        name = " ".join(str(part) for part in (author.get("given"), author.get("family")) if part).strip()
        if name:
            authors.append(name)
    containers = item.get("container-title") or []
    container = ""
    if isinstance(containers, list) and containers:
        container = containers[0] or ""
    if not container:
        short = item.get("short-container-title") or []
        container = (short[0] if short else "") or ""
    titles = item.get("title") or []
    title = (titles[0] if isinstance(titles, list) and titles else "") or ""
    clean = normalize_doi(item.get("DOI") or doi)
    return {
        "title": clean_title(title),
        "authors": authors,
        "year": pick_date(item),
        "type": CROSSREF_TYPE_MAP.get(item.get("type")) or "journal",
        "venue": clean_title(container),
        "volume": item.get("volume") or "",
        "issue": item.get("issue") or "",
        "pages": item.get("page") or "",
        "publisher": item.get("publisher") or "",
        "doi": clean,
        "url": item.get("URL") or (("https://doi.org/" + clean) if clean else ""),
        # 摘要可能是 JATS XML，也可能是被转义过的标签（&lt;i&gt;）：统一过公共清洗
        "summary": to_plain_text(item.get("abstract") or ""),
        "keywords": list(item.get("subject") or []),
    }


def to_paper(item, doi=None):
    """兼容入口（原 metadata.js 的 toPaper）：转调 crossref_to_paper，避免两份逻辑漂移。"""
    return crossref_to_paper(item, doi)


def rebuild_abstract(index, limit=None) -> str:
    """OpenAlex 的摘要是倒排索引（词 → 位置数组），还原成文本。"""
    max_chars = int(limit) if limit and int(limit) > 0 else 4000
    if not isinstance(index, dict):
        return ""
    slots = {}
    for word, positions in index.items():
        for pos in positions or []:
            if isinstance(pos, int) and pos >= 0:
                slots[pos] = word
    text = " ".join(str(slots[key]) for key in sorted(slots) if slots.get(key))
    return text[:max_chars]


def openalex_to_paper(work, doi=None):
    """OpenAlex 的 work → 统一 paper；这里只产出「补充」用字段，题录优先级低于 Crossref。"""
    if not isinstance(work, dict):
        return None
    primary = work.get("primary_location") or {}
    source_info = primary.get("source") or {}
    authors = []
    for authorship in work.get("authorships") or []:
        if isinstance(authorship, dict):
            name = ((authorship.get("author") or {}).get("display_name") or "").strip()
            if name:
                authors.append(name)
    abstract = (rebuild_abstract(work.get("abstract_inverted_index"))
                if work.get("abstract_inverted_index") else to_plain_text(work.get("abstract") or ""))
    work_doi = normalize_doi(work.get("doi") or doi)
    best = work.get("best_oa_location") or {}
    return {
        "title": clean_title(work.get("title") or work.get("display_name") or ""),
        "authors": authors,
        "year": work.get("publication_year") or None,
        "type": OPENALEX_TYPE_MAP.get(work.get("type")) or "journal",
        "venue": clean_title(source_info.get("display_name") or ""),
        "volume": "",
        "issue": "",
        "pages": "",
        "publisher": source_info.get("host_organization_name") or "",
        "doi": work_doi,
        "url": ("https://doi.org/" + work_doi) if work_doi else (work.get("id") or ""),
        "summary": abstract,
        "keywords": [((keyword or {}).get("display_name") or "")
                     for keyword in (work.get("keywords") or []) if isinstance(keyword, dict)],
        "citations": int(work.get("cited_by_count") or 0),
        "isOpenAccess": bool((work.get("open_access") or {}).get("is_oa")),
        "oaUrl": best.get("pdf_url") or best.get("landing_page_url") or "",
    }


# ---------------------------------------------------------------- 单源抓取


def lookup_options() -> dict:
    """给抓取用的上下文：OpenAlex key 与联系邮箱（与外部检索共用同一套配置）。"""
    helpers = common.source_helpers()
    return {"openAlexKey": helpers["openalex_key"](), "mailto": helpers["polite_mailto"]()}


def lookup_crossref(doi, options=None):
    opts = dict(options or {})
    mail = str(opts.get("mailto") or "")
    url = common.endpoint("crossref", "/works/" + common.urlencode(doi))
    headers = {"User-Agent": UA + ((" (mailto:" + mail + ")") if mail else "")}
    try:
        json_data = common.fetch_json(url, opts.get("timeoutMs") or SOURCE_TIMEOUT_MS, {"headers": headers})
    except common.SourceError as exc:
        if exc.not_found:
            raise MetadataError("Crossref 里没有这个 DOI", not_found=True) from exc
        raise MetadataError("Crossref " + exc.message, errors={"crossref": exc.message}) from exc
    paper = crossref_to_paper((json_data or {}).get("message"), doi)
    if not paper:
        raise MetadataError("Crossref 返回内容无法解析", errors={"crossref": "无法解析"})
    return paper


def lookup_openalex(doi, options=None):
    opts = dict(options or {})
    key = str(opts.get("openAlexKey") or "")
    select = ("id,doi,title,display_name,authorships,publication_year,type,primary_location,"
              "abstract_inverted_index,cited_by_count,open_access,best_oa_location,keywords")
    params = ["select=" + select]
    mail = str(opts.get("mailto") or "")
    if mail:
        params.append("mailto=" + common.urlencode(mail))
    url = common.with_params(common.endpoint("openalex", "/works/doi:" + common.urlencode(doi)), params)
    headers = {"User-Agent": UA}
    if key:
        headers["Authorization"] = "Bearer " + key
    try:
        json_data = common.fetch_json(url, opts.get("timeoutMs") or SOURCE_TIMEOUT_MS,
                                      {"source": "openalex", "headers": headers})
    except common.SourceError as exc:
        if exc.not_found:
            raise MetadataError("OpenAlex 里没有这个 DOI", not_found=True) from exc
        raise MetadataError("OpenAlex " + exc.message, errors={"openalex": exc.message}) from exc
    paper = openalex_to_paper(json_data, doi)
    if not paper:
        raise MetadataError("OpenAlex 返回内容无法解析", errors={"openalex": "无法解析"})
    return paper


# ---------------------------------------------------------------- 合并


# 需要从补充来源填的题录字段（canonical 没有时才用）
FILL_FIELDS = ("title", "authors", "year", "venue", "volume", "issue", "pages", "publisher", "doi", "url")


def merge_papers(canonical, supplement):
    """合并两个来源：题录以 canonical（Crossref）为准，空字段才用 supplement 补。

    摘要取较长的（Crossref 常为空或很短，OpenAlex 往往更完整）；citations / isOpenAccess /
    oaUrl 这类只有 OpenAlex 才有的字段直接带上。返回 {paper, fieldsFrom}（字段级 provenance）。
    """
    base = dict(canonical or {})
    sup = supplement or {}
    from_map = {"title": "crossref"}
    for key in FILL_FIELDS:
        if not is_filled(base.get(key)) and is_filled(sup.get(key)):
            base[key] = sup.get(key)
            from_map[key] = "openalex"
    base_summary = str(base.get("summary") or "")
    sup_summary = str(sup.get("summary") or "")
    if len(sup_summary) > len(base_summary):
        base["summary"] = sup_summary
        base["summarySource"] = "openalex"
    elif base_summary:
        base["summarySource"] = "crossref"
    else:
        base["summarySource"] = ""
    keywords = []
    for keyword in list(base.get("keywords") or []) + list(sup.get("keywords") or []):
        text = str(keyword or "").strip()
        if text and not any(existing.lower() == text.lower() for existing in keywords):
            keywords.append(text)
    base["keywords"] = keywords
    if sup.get("citations") is not None:
        base["citations"] = sup.get("citations")
    if sup.get("isOpenAccess") is not None:
        base["isOpenAccess"] = sup.get("isOpenAccess")
    if sup.get("oaUrl"):
        base["oaUrl"] = sup.get("oaUrl")
    return {"paper": base, "fieldsFrom": from_map}


def _run_parallel(jobs, budget_ms):
    """并发跑多个抓取任务：单源失败只记在自己名下（不重演「一个失败把另一个也带崩」）。"""
    results = {}
    threads = []

    def worker(name, fn):
        try:
            results[name] = {"ok": True, "value": fn()}
        except MetadataError as exc:
            results[name] = {"ok": False, "error": exc.message, "not_found": exc.not_found}
        except Exception as exc:  # noqa: BLE001
            results[name] = {"ok": False, "error": str(exc) or exc.__class__.__name__}

    for name, fn in jobs.items():
        thread = threading.Thread(target=worker, args=(name, fn), daemon=True)
        threads.append((name, thread))
        thread.start()
    deadline = time.time() + (budget_ms / 1000.0)
    for _name, thread in threads:
        thread.join(max(0.0, deadline - time.time()))
    for name, _thread in threads:
        if name not in results:
            results[name] = {"ok": False, "error": "请求超时（%d 秒）" % int(budget_ms / 1000)}
    return results


def fetch_by_doi_multi(doi_input, options=None) -> dict:
    """抓一个 DOI 的题录：Crossref 与 OpenAlex 并行，逐字段合并，返回 provenance。"""
    opts = dict(options or {})
    doi = normalize_doi(doi_input)
    if not doi:
        raise MetadataError("DOI 不能为空")
    started = time.time()
    jobs = {"crossref": lambda: lookup_crossref(doi, opts)}
    if not opts.get("skipOpenAlex"):
        jobs["openalex"] = lambda: lookup_openalex(doi, opts)
    settled = _run_parallel(jobs, int(opts.get("budgetMs") or TOTAL_BUDGET_MS))
    elapsed = int((time.time() - started) * 1000)

    sources_map = {"crossref": "skip", "openalex": "skip"}
    errors = {}
    papers = {}
    for name in ("crossref", "openalex"):
        result = settled.get(name)
        if not result:
            continue
        if result.get("ok"):
            sources_map[name] = "ok"
            papers[name] = result.get("value")
            continue
        message = str(result.get("error") or "未知原因")
        if "skipped" in message:
            sources_map[name] = "skip"
            continue
        sources_map[name] = "error"
        errors[name] = message

    if not papers:
        detail = "；".join(errors[name] for name in ("crossref", "openalex") if errors.get(name))
        raise MetadataError("两个来源都没抓到这个 DOI：" + detail, not_found=True, errors=errors)

    crossref = papers.get("crossref")
    openalex = papers.get("openalex")
    # 题录以 Crossref 为准；Crossref 挂了就退化成 OpenAlex 的题录
    merged = merge_papers(crossref or openalex, openalex if crossref else None)
    paper = merged["paper"]
    paper["bibliographicSource"] = "crossref" if crossref else "openalex"
    return {
        "doi": doi,
        "paper": paper,
        "fieldsFrom": merged["fieldsFrom"],
        "sources": sources_map,
        "errors": errors,
        "found": True,
        "elapsedMs": elapsed,
        "overBudget": elapsed > TOTAL_BUDGET_MS,
    }


def fetch_by_doi_detailed(doi_input) -> dict:
    """多来源抓取（api/fetch/doi 用它）：含 sources / errors / fieldsFrom / elapsedMs。"""
    return fetch_by_doi_multi(doi_input, lookup_options())


def fetch_by_doi(doi_input):
    """按 DOI 抓题录，只要 paper（pdf-import 等内部调用）。"""
    return fetch_by_doi_detailed(doi_input)["paper"]


def search_by_title(query, limit=None) -> list:
    """按标题在 Crossref 里做一次模糊搜索，返回候选列表。"""
    q = str(query or "").strip()
    if not q:
        raise MetadataError("搜索词不能为空")
    rows = min(max(int(limit or 5), 1), 10)
    url = common.with_params(common.endpoint("crossref", "/works"), [
        "rows=" + str(rows),
        "query.bibliographic=" + common.urlencode(q),
    ])
    mail = common.polite_mailto()
    headers = {"User-Agent": UA + (" (mailto:" + mail + ")" if mail else "")}
    try:
        json_data = common.fetch_json(url, 15000, {"headers": headers})
    except common.SourceError as exc:
        raise MetadataError("Crossref " + exc.message) from exc
    items = ((json_data or {}).get("message") or {}).get("items") or []
    papers = []
    for item in items:
        paper = crossref_to_paper(item, (item or {}).get("DOI"))
        if paper:
            papers.append(paper)
    return papers


def resolve(doi=None, title=None, limit=None):
    """门面：有 DOI 走多源合并，只有标题走 Crossref 模糊搜索。"""
    if str(doi or "").strip():
        return fetch_by_doi_detailed(doi)
    if str(title or "").strip():
        return {"items": search_by_title(title, limit)}
    raise MetadataError("需要 DOI 或标题")


# ---------------------------------------------------------------- 从 PDF 反查


def pdf_title_candidates(abs_path, pages=2) -> tuple:
    """PDF 首屏的字体级标题候选（PyMuPDF）：字号最大的那几行 + 内嵌元数据标题。

    返回 (candidates, info, warning)。启发式而非解析器：够用就好，
    T6 的 legacy/title_extract 到位后可替换这里。
    """
    try:
        import pymupdf
    except ImportError as exc:  # pragma: no cover - PyMuPDF 随包内置
        return [], {}, "缺少 PyMuPDF，无法读取 PDF：" + str(exc)
    path = str(abs_path or "")
    if not path or not os.path.isfile(path):
        return [], {}, "PDF 文件不存在：" + os.path.basename(path)
    candidates = []
    info = {}
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # noqa: BLE001
        return [], {}, "打不开这个 PDF：" + str(exc)
    try:
        info = dict(doc.metadata or {})
        for index in range(min(int(pages), doc.page_count)):
            page = doc.load_page(index)
            height = float(page.rect.height or 0)
            blocks = (page.get_text("dict") or {}).get("blocks") or []
            for block in blocks:
                for line in block.get("lines") or []:
                    spans = [span for span in (line.get("spans") or [])
                             if str(span.get("text") or "").strip()]
                    if not spans:
                        continue
                    text = "".join(str(span.get("text")) for span in spans).strip()
                    if len(text) < 8 or len(text) > 250:
                        continue
                    bbox = line.get("bbox") or (0, 0, 0, 0)
                    top = float(bbox[1] or 0)
                    if height and top > height * 0.72:
                        continue  # 页脚不要
                    size = max(float(span.get("size") or 0) for span in spans)
                    candidates.append({"text": clean_title(text), "size": round(size, 1),
                                       "page": index + 1, "line": index})
    finally:
        doc.close()
    embedded = clean_title(info.get("Title") or "")
    seen = set()
    ordered = []
    if embedded and len(embedded) >= 8:
        ordered.append({"text": embedded, "size": 0.0, "page": 0, "line": -1, "from": "metadata"})
        seen.add(embedded.lower())
    for item in sorted(candidates, key=lambda row: (-row["size"], row["page"], row["line"])):
        key = item["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        item["from"] = "font"
        ordered.append(item)
    return ordered, info, ""


def resolve_from_pdf(abs_path, fallback_title=None, limit=None) -> dict:
    """从 PDF 首屏取标题，再用 Crossref 反查题录（T3 的 PDF 建档与摘要回填可用）。"""
    candidates, info, warning = pdf_title_candidates(abs_path)
    titles = [item["text"] for item in candidates if item.get("text")]
    if fallback_title and str(fallback_title).strip():
        titles.insert(0, clean_title(fallback_title))
    result = {
        "ok": False,
        "file": os.path.basename(str(abs_path or "")),
        "title": "",
        "doi": normalize_doi((info or {}).get("doi") or ""),
        "candidates": [item["text"] for item in candidates][:8],
        "items": [],
        "paper": None,
        "warning": warning,
        "resolvedAt": now_iso(),
    }
    if not titles:
        if not result["warning"]:
            result["warning"] = "这个 PDF 前两页里没有可识别的标题（可能是扫描件），请手动填写"
        return result
    result["title"] = titles[0]
    try:
        items = search_by_title(titles[0], limit or 5)
    except MetadataError as exc:
        result["warning"] = result["warning"] or ("标题反查失败：" + exc.message)
        return result
    result["items"] = items
    if items:
        result["paper"] = items[0]
        result["ok"] = True
    else:
        result["warning"] = result["warning"] or "Crossref 里没搜到这个标题，请手动确认"
    return result
