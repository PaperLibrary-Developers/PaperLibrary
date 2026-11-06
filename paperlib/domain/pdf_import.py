"""领域层 · 「上传 PDF 建档」的导入编排（移植自 server/domain/pdf-import.js，213 行；契约名：pdf.extract_metadata(path) → Parsed）。

优先级（实测自 10 篇真实 PDF 的结论）：
  ① 正文前几页里的 DOI —— 10 篇里 9 篇能扫到，是最可靠的抓手；
  ② 拿 DOI 去题录源核对/补齐（作者、年、刊、卷、期、页、摘要、关键词）；
  ③ 查不到（离线 / 太老的文献没登记）时退回 PDF 内嵌元数据 + 首页标题候选；
  ④ 完全没有 DOI 时，把期刊/标题候选交给用户在确认页点选。

分工：纯规则（DOI 扫描、标题打分、合并）在 adapters/pdf_rules.py（可离线单测）；
本文件只做 I/O 编排（读 PDF 文本层、问题录源、组装草稿、摘要三层兜底）。
**网络失败绝不抛给调用方，只降级**（题录照样能建，用户手工补）。

依赖注入（deps）：
  lookup_doi(doi) -> paper|None      默认 services.metadata.fetch_by_doi（缺失时退 resolve(doi=…)）
  fetch_openalex_abstract(doi) -> str 默认 services.external.fetch_openalex_abstract
离线单测直接传 deps，不联网。
"""
from __future__ import annotations

import importlib
import os
import re
import shutil
import time
from pathlib import Path

from paperlib.adapters import pdf_rules, pdf_text

# 摘要三层兜底用的纯函数来自 T1；未就绪时用下面的本地降级（形状一致）


def _pure(module_name, names):
    try:
        module = importlib.import_module(module_name)
    except Exception:  # noqa: BLE001
        return None
    for name in names:
        fn = getattr(module, name, None)
        if callable(fn):
            return fn
    return None


def _clean_source_abstract(value):
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def _fallback_choose_abstract(*, crossref=None, pdf=None, openalex=None):
    """降级版 choose_abstract：取最长的一份，形状与 T1 版一致。"""
    options = []
    for source, value in (("crossref", crossref), ("pdf", pdf), ("openalex", openalex)):
        text = _clean_source_abstract(value)
        if len(text) >= 40:
            options.append({"text": text, "source": source})
    if not options:
        return None
    best = max(options, key=lambda item: len(item["text"]))
    return {"text": best["text"], "source": best["source"], "method": ""}


SOURCE_LABELS = {
    "crossref": "Crossref", "openalex": "OpenAlex", "pdf": "PDF 原文",
    "pubmed": "PubMed", "semantic": "Semantic Scholar", "arxiv": "arXiv", "europepmc": "Europe PMC",
}


def _fallback_describe_source(source, method=None):
    label = SOURCE_LABELS.get(str(source or ""), "")
    if not label:
        return ""
    return label + ("（正文位置推断）" if method == "position" else "")


_CHOOSE_ABSTRACT = None
_CLEAN_SOURCE_ABSTRACT = None
_DESCRIBE_SOURCE = None
_EXTRACT_ABSTRACT_FROM_LINES = None
_resolved = False


def _tools():
    """一次性解析 T1 的纯函数（缺失则用本地降级）。"""
    global _resolved, _CHOOSE_ABSTRACT, _CLEAN_SOURCE_ABSTRACT, _DESCRIBE_SOURCE, _EXTRACT_ABSTRACT_FROM_LINES
    if _resolved:
        return
    _resolved = True
    _CHOOSE_ABSTRACT = _pure("shared.pure.abstract", ("choose_abstract",))
    _CLEAN_SOURCE_ABSTRACT = _pure("shared.pure.abstract", ("clean_source_abstract",))
    _DESCRIBE_SOURCE = _pure("shared.pure.abstract", ("describe_source",))
    _EXTRACT_ABSTRACT_FROM_LINES = _pure("shared.pure.abstract_extract", ("extract_abstract_from_lines",))


def _choose_abstract(**kwargs):
    """T1 的 choose_abstract(cands) 收一个字典；也兼容按关键字调用的实现。"""
    _tools()
    if _CHOOSE_ABSTRACT:
        for call in (lambda: _CHOOSE_ABSTRACT(kwargs), lambda: _CHOOSE_ABSTRACT(**kwargs)):
            try:
                return call()
            except TypeError:
                continue
            except Exception:  # noqa: BLE001 - 纯函数内部异常时退回本地降级
                break
    return _fallback_choose_abstract(**kwargs)


def _clean(value):
    _tools()
    if _CLEAN_SOURCE_ABSTRACT:
        try:
            return _CLEAN_SOURCE_ABSTRACT(value)
        except Exception:  # noqa: BLE001
            pass
    return _clean_source_abstract(value)


def _describe(source, method=None):
    _tools()
    if _DESCRIBE_SOURCE:
        try:
            return _DESCRIBE_SOURCE(source, method)
        except TypeError:
            try:
                return _DESCRIBE_SOURCE(source)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
    return _fallback_describe_source(source, method)


def _extract_abstract(lines, title, line_y):
    _tools()
    if not _EXTRACT_ABSTRACT_FROM_LINES or not lines:
        return None
    try:
        return _EXTRACT_ABSTRACT_FROM_LINES(lines, {"title": title, "lineY": line_y})
    except TypeError:
        try:
            return _EXTRACT_ABSTRACT_FROM_LINES(lines)
        except Exception:  # noqa: BLE001
            return None
    except Exception:  # noqa: BLE001
        return None


# --------------------------------------------------------------------------- 编排


def _default_lookup_doi(services):
    metadata = getattr(services, "metadata", None)
    fetch = getattr(metadata, "fetch_by_doi", None)

    def lookup(doi):
        if callable(fetch):
            return fetch(doi)
        resolve = getattr(metadata, "resolve", None)
        if callable(resolve):
            return resolve(doi=doi)
        raise RuntimeError("题录源未装配")

    return lookup


def _default_fetch_openalex_abstract(services):
    external = getattr(services, "external", None)

    def fetch(doi):
        fn = getattr(external, "fetch_openalex_abstract", None)
        if not callable(fn):
            raise RuntimeError("OpenAlex 摘要接口未装配")
        return fn(doi)

    return fetch


def parse_pdf(services, abs_path, deps=None):
    """解析一个 PDF 文件，给出「能自动填多少题录」的结果（形状对齐 Node parsePdf）。"""
    options = deps or {}
    lookup_doi = options.get("lookup_doi") or _default_lookup_doi(services)
    max_pages = options.get("max_pages") or pdf_text.HEAD_PAGES

    base = {
        "file": os.path.basename(str(abs_path)),
        "pages": 0,
        "doi": "",
        "doiSource": "",       # 'text' | 'meta' | ''
        "doiVerified": False,  # 是否被题录源认过
        "crossref": None,      # 题录源查到的记录
        "titleCandidates": [],
        "journalCandidates": [],
        "pdfTitle": "",        # PDF 内嵌标题（清洗后可用才有值）
        "metaAuthors": [],
        "authors": [],
        "year": None,
        "warning": "",         # 给用户看的降级说明
        "ok": True,
    }

    try:
        head = pdf_text.extract_head(abs_path, max_pages)
    except Exception as exc:  # noqa: BLE001 - 打不开的 PDF 也要能保存文件
        base["ok"] = False
        base["warning"] = "打不开这个 PDF：" + str(exc)
        return base

    base["pages"] = int(head.get("pages") or 0)
    base["headLines"] = list(head.get("lines") or [])
    base["headLineY"] = list(head.get("lineY") or [])
    info = head.get("info") or {}
    xmp = head.get("xmp")
    base["pdfTitle"] = str(info.get("Title") or "").strip() if pdf_rules.usable_embedded_title(info.get("Title")) else ""
    base["metaAuthors"] = pdf_rules.authors_from_meta(info, xmp)
    base["year"] = pdf_rules.year_from_meta(info, xmp)

    # ① 正文里的 DOI（前几页），② 内嵌元数据里的 DOI；跨行拼接的结果排在前面
    text_dois = pdf_rules.extract_dois_across_lines(base["headLines"]) + pdf_rules.extract_dois(base["headLines"])
    meta_doi = pdf_rules.doi_from_meta(xmp)
    picked = pdf_rules.title_candidates(base["headLines"])
    base["titleCandidates"] = picked["candidates"]
    base["journalCandidates"] = picked["journalCandidates"]
    if base["pdfTitle"]:
        base["titleCandidates"] = [base["pdfTitle"]] + [t for t in base["titleCandidates"] if t != base["pdfTitle"]]
    if picked["journalCandidates"]:
        base["venueCandidates"] = picked["journalCandidates"]

    if meta_doi:
        tried = [meta_doi] + [d for d in text_dois if d.lower() != meta_doi.lower()]
    else:
        tried = text_dois
    if not tried:
        base["warning"] = "没能在 PDF 里找到 DOI，题录需要手工补（也可以用下面的「按标题检索」找候选）"
        return base

    # 逐个试（最多 3 个），第一个被题录源认下来的就用它
    last_error = ""
    for doi in tried[:3]:
        try:
            paper = lookup_doi(doi)
        except NotImplementedError:
            last_error = "题录接口暂不可用"
            continue
        except Exception as exc:  # noqa: BLE001
            last_error = str(exc) or exc.__class__.__name__
            continue
        if not paper:
            last_error = "题录源没有返回结果"
            continue
        finished = dict(paper) if isinstance(paper, dict) else {}
        base["doi"] = finished.get("doi") or doi
        base["doiSource"] = "meta" if doi.lower() == meta_doi.lower() else "text"
        base["doiVerified"] = bool(finished)
        base["crossref"] = finished or None
        return base

    # 题录源没认（离线、或这本刊没登记）：题录取 PDF 自身的信息
    base["doi"] = tried[0]
    base["doiSource"] = "meta" if tried[0].lower() == meta_doi.lower() else "text"
    base["doiVerified"] = False
    base["warning"] = "PDF 里的 DOI 没能在 Crossref 核对成功（" + last_error + "），下面按 PDF 自身信息填写，请核对"
    return base


def draft_from_parsed(services, parsed, context=None, deps=None):
    """把 parse_pdf 的结果整理成一份「可以直接提交的题录草稿」。

    摘要走三层兜底：题录源 → PDF 本地抽取 → OpenAlex。
    """
    options = deps or {}
    fetch_openalex = options.get("fetch_openalex_abstract")
    if fetch_openalex is None:
        fetch_openalex = _default_fetch_openalex_abstract(services)
    parsed = parsed or {}
    cr = parsed.get("crossref") or None

    candidates = list(parsed.get("titleCandidates") or [])
    best_candidate = ""
    for title in candidates:
        if pdf_rules.is_plausible_title(title):
            best_candidate = title
            break
    if cr:
        draft = dict(cr)
    else:
        draft = {
            "title": parsed.get("pdfTitle") or best_candidate or (candidates[0] if candidates else ""),
            "authors": parsed.get("metaAuthors") or [],
            "year": parsed.get("year") or None,
            "type": "journal",
            "venue": "",
            "volume": "",
            "issue": "",
            "pages": "",
            "publisher": "",
            "doi": parsed.get("doi") or "",
            "url": ("https://doi.org/" + parsed.get("doi")) if parsed.get("doi") else "",
            "summary": "",
            "keywords": [],
        }
    if not draft.get("title"):
        draft["title"] = pdf_rules.title_from_file_name(parsed.get("file"))
    if not draft.get("doi") and parsed.get("doi"):
        draft["doi"] = parsed.get("doi")
    if not draft.get("url") and draft.get("doi"):
        draft["url"] = "https://doi.org/" + str(draft.get("doi"))

    # ---------- 摘要：题录源 → PDF → OpenAlex ----------
    pdf_abstract = None
    if parsed.get("headLines"):
        pdf_abstract = _extract_abstract(parsed.get("headLines"), draft.get("title"), parsed.get("headLineY"))
    picked = _choose_abstract(crossref=draft.get("summary"), pdf=pdf_abstract)
    # PDF 的「无标题首段」只是位置推断，不如明确标注那一段可信；这时 OpenAlex 更长就换它
    if picked and picked.get("source") == "pdf" and picked.get("method") == "position" and draft.get("doi"):
        try:
            longer = _clean(fetch_openalex(str(draft["doi"])))
            if len(longer) > len(str(picked.get("text") or "")):
                picked = {"text": longer, "source": "openalex"}
        except Exception:  # noqa: BLE001 - 拿不到就算了，PDF 那份继续用
            pass
    # 前两层都没结果，才去问 OpenAlex（省额度也省时间）
    if not picked and draft.get("doi"):
        try:
            openalex = _clean(fetch_openalex(str(draft["doi"])))
            picked = _choose_abstract(crossref=draft.get("summary"), pdf=pdf_abstract, openalex=openalex)
        except Exception:  # noqa: BLE001 - 网络不通就留空，不阻断导入
            pass
    summary = str((picked or {}).get("text") or "").strip() or str(draft.get("summary") or "").strip()
    source = (picked or {}).get("source") or ("crossref" if summary else "")

    # 重复检测：同 DOI，或标题高度相似
    local = (context or {}).get("localPapers") or []
    duplicate = None
    doi_key = str(draft.get("doi") or "").lower()
    if doi_key:
        for paper in local:
            if str(paper.get("doi") or "").lower() == doi_key:
                duplicate = paper
                break
    if not duplicate:
        for paper in local:
            if pdf_rules.title_similarity(paper.get("title"), draft.get("title")) >= 0.85:
                duplicate = paper
                break

    return {
        "title": draft.get("title") or "",
        "authors": draft.get("authors") or [],
        "year": draft.get("year") or None,
        "type": draft.get("type") or "journal",
        "venue": draft.get("venue") or "",
        "volume": draft.get("volume") or "",
        "issue": draft.get("issue") or "",
        "pages": draft.get("pages") or "",
        "publisher": draft.get("publisher") or "",
        "doi": draft.get("doi") or "",
        "url": draft.get("url") or "",
        "summary": summary,
        "summarySource": source,
        "summarySourceLabel": _describe(source, (picked or {}).get("method")) if summary else "",
        "keywords": draft.get("keywords") or [],
        "duplicate": ({"id": duplicate.get("id"), "title": duplicate.get("title"),
                       "hasPdf": bool(duplicate.get("pdfFile"))} if duplicate else None),
    }


def parsed_for_api(parsed):
    """parse_pdf 结果 → 预检响应里的 parsed 块（键与 Node api/import.js 一致）。"""
    parsed = parsed or {}
    return {
        "ok": bool(parsed.get("ok")),
        "pages": int(parsed.get("pages") or 0),
        "doi": parsed.get("doi") or "",
        "doiSource": parsed.get("doiSource") or "",
        "doiVerified": bool(parsed.get("doiVerified")),
        "pdfTitle": parsed.get("pdfTitle") or "",
        "titleCandidates": list(parsed.get("titleCandidates") or []),
        "journalCandidates": list(parsed.get("journalCandidates") or []),
        "metaAuthors": list(parsed.get("metaAuthors") or []),
        "warning": parsed.get("warning") or "",
    }


def pdf_preflight(services, file_path, name, temp_id="", deps=None):
    """临时文件 → {parsed, draft, tempId}（网络/解析失败都降级，不抛给调用方）。"""
    try:
        parsed = parse_pdf(services, file_path, deps)
    except Exception as exc:  # noqa: BLE001
        parsed = {"ok": False, "warning": "解析失败：" + str(exc), "pages": 0, "doi": "",
                  "doiSource": "", "doiVerified": False, "titleCandidates": []}
    context = {"localPapers": services.db.list_papers()}
    draft = draft_from_parsed(services, parsed, context, deps)
    return {"parsed": parsed_for_api(parsed), "draft": draft, "tempId": str(temp_id or ""),
            "file": os.path.basename(str(file_path))}


def title_from_file_name(name):
    return pdf_rules.title_from_file_name(name)


def cleanup_import_tmp(services, max_age_ms=None):
    """启动时删掉「超过 6 小时」的旧批次（昨天没做完的），保留刚刚的。

    以免用户停在确认页时被一次重启清空；用户取消 / 导入完成后由 removeBatch 精确删除。
    """
    tmp_dir = Path(services.runtime.data_dir) / "tmp-import"
    removed = 0
    try:
        if not tmp_dir.is_dir():
            return {"removed": 0}
        limit = 6 * 3600 * 1000 if max_age_ms is None else int(max_age_ms)
        now = time.time() * 1000
        for entry in tmp_dir.iterdir():
            try:
                age = now - entry.stat().st_mtime * 1000
                if not entry.is_dir() or age > limit:
                    if entry.is_dir():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                continue
        return {"removed": removed}
    except OSError as exc:
        print("[import] 清理临时目录失败：%s" % exc)
        return {"removed": 0}


__all__ = ["parse_pdf", "draft_from_parsed", "parsed_for_api", "pdf_preflight",
           "title_from_file_name", "cleanup_import_tmp"]
