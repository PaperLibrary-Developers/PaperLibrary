"""api 域：外部源检索 / 网络检索 / 题录抓取 / 摘要回填（移植自 Sci-lib server/api/search.js，166 行）。

契约端点（shared/contracts/api.md §3.5 / §3.6）：
  GET  /api/external/sources            → {ok, sources, settings}
  GET  /api/external/search?q=&…        → {ok, filters, query, items, sources, page, hasMore, cached}
  POST /api/sources/openalex/test       → 恒 200（ok:true 或 ok:false + quotaExhausted/retryAfterSeconds）
  POST /api/sources/abstracts/backfill  → {ok, total, filled, results}（409 = 已有一轮在跑）
  POST /api/websearch/test              → 转调 services.websearch（T4 拥有）
  GET  /api/websearch/search | answer   → 同上
  POST /api/fetch/doi                   → {ok, paper, sources, errors, fieldsFrom, elapsedMs}
  POST /api/fetch/title                 → {ok, items}
源配置的读写（/api/sources/settings、/api/websearch/settings）在 api/settings.py。

网络失败一律 502 + 中文 error；参数缺失 400。摘要回填逻辑移植自 server/abstract-backfill.js（102 行）：
只补空摘要、PDF 本地抽取优先、OpenAlex 兜底、绝不覆盖已有摘要。
"""
from __future__ import annotations

import os

from paperlib.adapters.external_sources.common import SourceError
from paperlib.util import now_iso
from shared.pure.html_text import to_plain_text

# 同一时间只允许一次补摘要（逐篇调外部 API，并发只会被限流）
BACKFILL = {"running": False}

NO_SOURCE = "PDF 与 OpenAlex 都没有可用的摘要（这本刊/这篇可能本来就没印摘要）"
ABSTRACT_MAX = 4000


# ---------------------------------------------------------------- 外部源


def external_sources(ctx):
    """可用的外部来源清单 + 源的公开设置（密钥只回尾号）。"""
    services = ctx.services
    settings = {}
    try:
        settings = services.sources.public_settings()
    except Exception:  # noqa: BLE001 - 设置读不出来不影响清单
        settings = {}
    return ctx.json(200, {"ok": True, "sources": services.external.sources_list(), "settings": settings})


def _filters_from(ctx) -> dict:
    return {
        "yearFrom": ctx.query("yearFrom") or "",
        "yearTo": ctx.query("yearTo") or "",
        "venue": ctx.query("venue") or "",
        "author": ctx.query("author") or "",
        "minCitations": ctx.query("minCitations") or "",
        "onlyOA": ctx.query("onlyOA") == "true",
    }


def _mark_in_library(ctx, items) -> None:
    """标出哪些已经在本地库里（前端显示「已在库中」而不是「导入」）。"""
    try:
        papers = ctx.services.db.list_papers()
    except Exception:  # noqa: BLE001
        return
    by_doi = {}
    titles = set()
    for paper in papers:
        if paper.get("doi"):
            by_doi[str(paper["doi"]).lower()] = paper.get("id")
        titles.add(str(paper.get("title") or "").lower().replace("\s+", " ").strip())
    for item in items:
        key = str(item.get("doi") or "").lower()
        title_key = str(item.get("title") or "").lower().replace("\s+", " ").strip()
        item["inLibrary"] = bool((key and key in by_doi) or title_key in titles)
        item["localId"] = by_doi.get(key) if key else None


def external_search(ctx):
    """外部文献检索：跨源重复自动折叠，并标出「已在库中」。"""
    services = ctx.services
    q = str(ctx.query("q") or "").strip()
    filters = _filters_from(ctx)
    has_filter = any(value not in ("", None, False) for value in filters.values())
    if not q and not has_filter:
        return ctx.fail(400, "请提供查询词 q 或至少一个筛选条件")
    try:
        result = services.external.search(q, {
            "limit": ctx.query("limit"),
            "page": ctx.query("page"),
            "sources": [part.strip() for part in str(ctx.query("sources") or "").split(",") if part.strip()],
            "filters": filters,
        })
    except SourceError as exc:
        return ctx.fail(502, "外部检索失败：" + exc.message)
    except Exception as exc:  # noqa: BLE001 - 网络与解析异常统一按外部服务失败返回
        return ctx.fail(502, "外部检索失败：" + (str(exc) or "未知错误"))
    _mark_in_library(ctx, result.get("items") or [])
    payload = {"ok": True, "filters": filters}
    payload.update(result)
    return ctx.json(200, payload)


def openalex_test(ctx):
    """用一个轻量请求验证 OpenAlex key 是否可用（顺带刷新配额快照）；恒 200。"""
    services = ctx.services
    try:
        result = services.external.test_openalex()
    except SourceError as exc:
        return ctx.json(200, {
            "ok": False,
            "error": exc.message,
            "quotaExhausted": bool(exc.quota_exhausted),
            "retryAfterSeconds": exc.retry_after_seconds,
            "settings": _public_settings(services),
        })
    except Exception as exc:  # noqa: BLE001
        return ctx.json(200, {
            "ok": False,
            "error": str(exc) or "未知错误",
            "quotaExhausted": False,
            "retryAfterSeconds": None,
            "settings": _public_settings(services),
        })
    payload = {"ok": True}
    payload.update(result or {})
    return ctx.json(200, payload)


def _public_settings(services) -> dict:
    try:
        return services.sources.public_settings()
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 摘要回填


def _abstract_pure():
    """摘要纯函数（T1 交付）；缺失时用本文件的本地兜底，保证回填不因依赖未就绪而挂掉。"""
    try:
        from shared.pure import abstract as pure_abstract
        from shared.pure import abstract_extract
    except Exception:  # noqa: BLE001
        return None, None
    return pure_abstract, abstract_extract


def _clean_abstract(text) -> str:
    return to_plain_text(str(text or "")).strip()[:ABSTRACT_MAX]


def _choose_abstract(pdf_text=None, openalex_text=None):
    """选一条摘要：PDF 优先，OpenAlex 兜底；PDF 只是位置推断时取更长的那份。"""
    pure_abstract, _extract = _abstract_pure()
    if pure_abstract is not None:
        try:
            picked = pure_abstract.choose_abstract({"pdf": pdf_text, "openalex": openalex_text})
            if picked:
                return dict(picked) if isinstance(picked, dict) else {"text": str(picked), "source": "pdf"}
        except Exception:  # noqa: BLE001 - 纯函数签名不一致时退回本地兜底
            pass
    pdf = _clean_abstract(pdf_text)
    openalex = _clean_abstract(openalex_text)
    if pdf:
        return {"text": pdf, "source": "pdf", "method": "position"}
    if openalex:
        return {"text": openalex, "source": "openalex"}
    return None


def _describe_source(source, method=None) -> str:
    pure_abstract, _extract = _abstract_pure()
    if pure_abstract is not None and hasattr(pure_abstract, "describe_source"):
        try:
            return str(pure_abstract.describe_source(source, method))
        except Exception:  # noqa: BLE001
            pass
    return {"pdf": "PDF 正文", "openalex": "OpenAlex"}.get(str(source or ""), str(source or ""))


def _pdf_head_text(services, paper, pages=2):
    """PDF 前几页的文本与行（T3 的文本层）；拿不到返回 (None, 原因)。"""
    pdf_file = str(paper.get("pdfFile") or "")
    if not pdf_file:
        return None, ""
    try:
        abs_path = services.db.path_for_paper_file(pdf_file)
    except Exception:  # noqa: BLE001
        return None, ""
    if not abs_path or not os.path.isfile(str(abs_path)):
        return None, "PDF 文件不存在：" + pdf_file
    try:
        from paperlib.adapters import pdf_text
    except Exception:  # noqa: BLE001
        return None, "PDF 文本层未就绪"
    try:
        data = pdf_text.extract_text(str(abs_path), {"maxPages": pages})
    except NotImplementedError:
        return None, "PDF 文本层未就绪（T3 待交付）"
    except Exception as exc:  # noqa: BLE001
        return None, "PDF 抽取失败：" + str(exc)
    text = str((data or {}).get("text") or "")
    return (text or None), ""


def _extract_abstract_from_text(text, title) -> str:
    _pure_abstract, abstract_extract = _abstract_pure()
    if abstract_extract is not None and text:
        lines = [line.strip() for line in str(text).splitlines() if line.strip()]
        try:
            found = abstract_extract.extract_abstract_from_lines(lines, {"title": title or ""})
            if isinstance(found, dict) and found.get("text"):
                return _clean_abstract(found["text"])
        except Exception:  # noqa: BLE001
            return ""
    return ""


def backfill_abstracts(services) -> dict:
    """给「摘要为空」的文献补摘要；只补空的，绝不覆盖已有内容。"""
    targets = [paper for paper in services.library.all() if not str(paper.get("summary") or "").strip()]
    results = []
    for paper in targets:
        entry = {
            "id": paper.get("id"),
            "title": paper.get("title") or "",
            "venue": paper.get("venue") or "",
            "ok": False,
            "source": "",
            "sourceLabel": "",
            "chars": 0,
            "error": "",
        }
        try:
            # ① PDF 本地抽取
            pdf_text, pdf_note = _pdf_head_text(services, paper)
            pdf_abstract = _extract_abstract_from_text(pdf_text, paper.get("title")) if pdf_text else ""
            picked = _choose_abstract(pdf_text=pdf_abstract or None)

            # ② OpenAlex 兜底
            doi = str(paper.get("doi") or "").strip()
            if doi:
                try:
                    remote = _clean_abstract(services.external.fetch_openalex_abstract(doi))
                    if not picked:
                        picked = _choose_abstract(openalex_text=remote or None)
                    elif picked.get("source") == "pdf" and picked.get("method") == "position" \
                            and len(remote) > len(picked.get("text") or ""):
                        picked = {"text": remote, "source": "openalex"}
                except Exception as exc:  # noqa: BLE001 - 单篇失败不影响整体
                    entry["error"] = str(exc) or "OpenAlex 取摘要失败"

            if picked and picked.get("text"):
                text = _clean_abstract(picked["text"])
                services.library.update(paper.get("id"), {"summary": text, "summarySource": picked.get("source")})
                entry.update({"ok": True, "source": picked.get("source") or "",
                              "sourceLabel": _describe_source(picked.get("source"), picked.get("method")),
                              "chars": len(text), "error": ""})
            else:
                entry["error"] = entry["error"] or pdf_note or NO_SOURCE
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc) or "未知错误"
        results.append(entry)
    return {"total": len(targets), "filled": sum(1 for row in results if row["ok"]),
            "results": results, "updatedAt": now_iso()}


def sources_backfill(ctx):
    """给原文摘要为空的文献补摘要（409 = 已有一轮在跑）。"""
    if BACKFILL["running"]:
        return ctx.fail(409, "正在补摘要，请稍候")
    BACKFILL["running"] = True
    try:
        report = backfill_abstracts(ctx.services)
        payload = {"ok": True}
        payload.update(report)
        return ctx.json(200, payload)
    except Exception as exc:  # noqa: BLE001
        return ctx.fail(500, str(exc) or "摘要回填失败")
    finally:
        BACKFILL["running"] = False


# ---------------------------------------------------------------- 网络检索（T4 的适配器）


def websearch_test(ctx):
    result = ctx.services.websearch.test()
    return ctx.json(200 if result.get("ok") else 400, result)


def websearch_search(ctx):
    result = ctx.services.websearch.search(ctx.query("q"), {
        "numResults": ctx.query("num"),
        "category": ctx.query("category"),
    })
    return ctx.json(200 if result.get("ok") else 400, result)


def websearch_answer(ctx):
    result = ctx.services.websearch.answer(ctx.query("q"))
    return ctx.json(200 if result.get("ok") else 400, result)


# ---------------------------------------------------------------- 抓取题录


def fetch_doi(ctx):
    """多来源合并（Crossref 题录为准 + OpenAlex 补摘要/被引），返回 provenance 与每源错误。"""
    body = ctx.read_json() or {}
    doi = body.get("doi") if isinstance(body, dict) else None
    try:
        found = ctx.services.metadata.fetch_by_doi_detailed(doi)
    except Exception as exc:  # noqa: BLE001 - 404（没抓到）/ 502（外部服务失败）
        not_found = bool(getattr(exc, "not_found", False))
        return ctx.fail(404 if not_found else 502, str(exc) or "题录抓取失败")
    return ctx.json(200, {
        "ok": True,
        "paper": found.get("paper"),
        "sources": found.get("sources"),
        "errors": found.get("errors"),
        "fieldsFrom": found.get("fieldsFrom"),
        "elapsedMs": found.get("elapsedMs"),
    })


def fetch_title(ctx):
    """按标题在 Crossref 模糊搜索（前端「按标题抓题录」）。"""
    body = ctx.read_json() or {}
    title = body.get("title") if isinstance(body, dict) else None
    limit = body.get("limit") if isinstance(body, dict) else None
    try:
        items = ctx.services.metadata.search_by_title(title, limit)
    except Exception as exc:  # noqa: BLE001
        return ctx.fail(502, str(exc) or "题录抓取失败")
    return ctx.json(200, {"ok": True, "items": items})


__all__ = [
    "external_sources", "external_search", "sources_backfill", "openalex_test",
    "websearch_test", "websearch_search", "websearch_answer", "fetch_doi", "fetch_title",
    "backfill_abstracts", "NO_SOURCE",
]
