"""文献端点：检索 / 建卡 / 整理 / 导出（移植自 Sci-lib server/api/papers.js，287 行）。

  GET    /api/papers?q=&tags=&…&page=&scope=metadata|fulltext|all
  POST   /api/papers
  POST   /api/papers/bulk
  GET    /api/papers/scan/pdfs
  POST   /api/papers/associate-pdf
  POST   /api/papers/cleanup-duplicates
  POST   /api/papers/tidy-pdf-names
  GET    /api/facets
  GET    /api/papers/:id
  PUT    /api/papers/:id
  DELETE /api/papers/:id
  GET    /api/export/json|bibtex|markdown

只做「校验入参 → 调 library → 组响应」；原文/附件/全文这些文件类端点见 api/files.py。
契约名：shared/contracts/api.md §3.3（papers-list / facets / export）+ §5 不变量。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from paperlib.api.paper_scope import require_paper
from paperlib.domain.query import needs_evaluator, parse_query
from paperlib.util import now_iso, safe_path_join, to_string_array

FULLTEXT_BUDGET = 60


# ------------------------------------------------------------------ 小工具
def _query_params(ctx):
    """查询串 → dict（同名参数取最后一个，与 Node 的 Object.fromEntries(entries) 一致）。"""
    params = {}
    for name in ctx._query:
        values = ctx.query_all(name)
        if values:
            params[name] = values[-1]
    return params


def _fulltext_available(fulltext):
    """依赖是否可用：`None` 表示还没探测过（与 Node 的 fulltext.stats().available 同义）。"""
    flag = getattr(fulltext, "_available", None)
    if flag is not None:
        return flag
    for name in ("availability_known", "availabilityKnown", "available"):
        fn = getattr(fulltext, name, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001
                return True
    return True


def _fulltext_stats(services):
    """全文索引统计（{available, indexed, dir, tokens}）；实现缺失时给同形兜底。"""
    fulltext = services.fulltext
    fn = getattr(fulltext, "stats", None)
    if callable(fn):
        try:
            value = fn()
            if isinstance(value, dict):
                return value
        except Exception:  # noqa: BLE001
            pass
    return {
        "available": _fulltext_available(fulltext),
        "indexed": 0,
        "dir": str(getattr(services.db, "fulltext_dir", "") or ""),
        "tokens": 0,
    }


def _fulltext_search(fulltext, terms, limit):
    fn = getattr(fulltext, "search", None)
    if not callable(fn):
        return []
    try:
        return list(fn(terms, limit) or [])
    except Exception:  # noqa: BLE001
        return []


def _fulltext_excerpts(fulltext, paper_id, query, limit):
    fn = getattr(fulltext, "excerpts", None)
    if not callable(fn):
        return []
    try:
        return list(fn(paper_id, query, limit) or [])
    except Exception:  # noqa: BLE001
        return []


def _rename_attachment_text(services, paper_id, old_file, new_file):
    """附件改名后同步抽取文本的文件名（方法名在 T3 落地前可能是两种写法）。"""
    fulltext = services.fulltext
    for name in ("rename_attachment_text", "renameAttachmentText"):
        fn = getattr(fulltext, name, None)
        if callable(fn):
            try:
                fn(paper_id, old_file, new_file)
            except Exception as exc:  # noqa: BLE001
                print("[attachments] 抽取文本改名失败：" + str(exc))
            return


def _auto_summary_ready(llm):
    try:
        settings = getattr(llm, "settings", None) or {}
        auto = settings.get("autoSummaryZh") if hasattr(settings, "get") else None
        if auto is False:
            return False
        readiness = getattr(llm, "readiness", None)
        info = readiness() if callable(readiness) else None
        return bool(isinstance(info, dict) and info.get("ready"))
    except Exception:  # noqa: BLE001
        return False


def _ensure_folder(services, payload):
    """文献写入前把 folder 归一化并确保目录存在（导入时用户可能直接选一个还没建的路径）。"""
    if not isinstance(payload, dict):
        return
    raw = payload.get("folder")
    if raw is None:
        return
    folders = getattr(services, "folders", None)
    if folders is None:
        return
    payload["folder"] = folders.ensure(raw) if str(raw).strip() else ""


def _stamp():
    """导出文件名里的日期：与 Node 的 new Date().toISOString().slice(0, 10) 一致（UTC）。"""
    return now_iso()[:10]


def _send_text(ctx, body, content_type, filename=None):
    headers = {}
    if filename:
        headers["Content-Disposition"] = 'attachment; filename="' + filename + '"'
    ctx.send_bytes(200, str(body).encode("utf-8"), content_type, headers)


# ------------------------------------------------------------------ 列表
def list_papers(ctx):
    services = ctx.services
    library = services.library
    fulltext = services.fulltext
    params = _query_params(ctx)
    params["all"] = params.get("all") == "true"
    scope = str(params.get("scope") or "metadata")

    # 全文检索：拿到命中的 id 列表后按 id 过滤
    excerpt_map = None
    fulltext_query = params.get("q")  # 生成摘录要用原查询（下面的布尔逻辑会改写 params.q）
    if params.get("q") and scope in ("fulltext", "all"):
        # 布尔/字段限定查询：拿「全部正项词」去全文索引粗筛（粗筛只看词在不在正文里，
        # 精确的字段判定由 library.query 的求值器完成）。
        query_text = params.get("q") or ""
        if needs_evaluator(query_text):
            search_terms = " ".join((parse_query(query_text) or {}).get("allTerms") or [])
        else:
            search_terms = query_text
        hits = _fulltext_search(fulltext, search_terms, 200) if search_terms else []
        excerpt_map = {}
        for hit in hits[:60]:
            snippets = _fulltext_excerpts(fulltext, hit["id"], fulltext_query, 1)
            excerpt_map[hit["id"]] = snippets[0] if snippets else ""
        if scope == "fulltext":
            # 只搜全文：命中集合已经由全文检索给出（params.ids），这里必须清掉 q，
            # 否则 library 会再拿它筛一遍题录索引（全文命中的文献会被误杀）。
            # 布尔逻辑仍然生效：library 会用保留下来的 booleanQuery 做后置过滤。
            params["booleanQuery"] = params.get("q")
            params["q"] = ""
            if hits:
                params["ids"] = ",".join(str(hit["id"]) for hit in hits)
            else:
                params.pop("ids", None)
                params["idsEmpty"] = True
        elif hits:
            # 两者都搜：题录索引照常筛（q 保留），正文里命中的文献也一并并进结果。
            params["fulltextIds"] = ",".join(str(hit["id"]) for hit in hits)

    # 布尔检索要看正文时给一个预算（每次查询最多读这么多篇的全文）。
    # 查询里显式写了 fulltext: 也要给预算——否则用户点名搜正文，却因为范围选择器
    # 还在「题录与摘要」而永远 0 条。
    field_terms = (parse_query(params.get("q") or "") or {}).get("fieldTerms") or []
    wants_fulltext_field = any((term or {}).get("field") == "fulltext" for term in field_terms)
    services.fulltext_budget["value"] = (
        FULLTEXT_BUDGET if (scope in ("fulltext", "all") or wants_fulltext_field) else 0)

    result = library.query(params)
    if excerpt_map:
        for item in result["items"]:
            snippet = excerpt_map.get(item["id"])
            if snippet:
                item["fulltextExcerpt"] = snippet
    return ctx.json(200, dict({"ok": True, "scope": scope,
                               "fulltext": _fulltext_stats(services)}, **result))


def create(ctx):
    services = ctx.services
    body = ctx.read_json() or {}
    _ensure_folder(services, body)
    paper = services.library.create(body)
    # 新增文献后自动补一份中文一句话总结（后台跑、不阻塞创建；失败不影响保存）。
    if _auto_summary_ready(services.llm):
        timer = threading.Timer(0.3, _run_auto_summary, args=(services, paper.get("id")))
        timer.daemon = True
        timer.start()
    return ctx.json(201, {"ok": True, "paper": paper})


def _run_auto_summary(services, paper_id):
    try:
        services.trigger_auto_summary_zh(paper_id)
    except Exception as exc:  # noqa: BLE001
        print("[summary-zh] 自动生成失败：" + str(exc))


def bulk(ctx):
    services = ctx.services
    body = ctx.read_json() or {}
    changed = services.library.bulk(body.get("ids"), body.get("patch") or {})
    return ctx.json(200, {"ok": True, "changed": len(changed), "papers": changed})


# ------------------------------------------------------------------ 文件整理
def scan_pdfs(ctx):
    return ctx.json(200, {"ok": True, "scan": ctx.services.library.scan_pdf_contents()})


def associate_pdf(ctx):
    body = ctx.read_json() or {}
    result = ctx.services.library.associate_pdf(body.get("paperId"), body.get("file"))
    if not result.get("ok"):
        return ctx.fail(400, result.get("error") or "关联失败")
    return ctx.json(200, {"ok": True, "paper": result.get("paper"), "file": result.get("file")})


def cleanup_duplicates(ctx):
    """把「无引用的重复副本」移到隔离区 data/backups/orphans/（不是直接删除）。"""
    services = ctx.services
    library = services.library
    body = ctx.read_json() or {}
    scan = library.scan_pdf_contents()
    safe_list = []
    safe = set()
    for group in scan.get("duplicateGroups") or []:
        for file_name in group.get("removable") or []:
            if file_name not in safe:
                safe.add(file_name)
                safe_list.append(file_name)
    wanted_files = body.get("files")
    if isinstance(wanted_files, list) and wanted_files:
        wanted = [name for name in wanted_files if name in safe]
    else:
        wanted = list(safe_list)
    if not wanted:
        return ctx.json(200, {"ok": True, "moved": 0, "files": [],
                              "hint": "没有可清理的重复副本"})
    quarantine = Path(str(services.runtime.data_dir)) / "backups" / "orphans"
    quarantine.mkdir(parents=True, exist_ok=True)
    moved = []
    for file_name in wanted:
        abs_path = safe_path_join(services.db.papers_dir, file_name)
        if not abs_path or not os.path.isfile(abs_path):
            continue
        dest = safe_path_join(quarantine, os.path.basename(file_name))
        if not dest:
            continue
        try:
            if os.path.isfile(dest):
                os.unlink(dest)
            os.replace(abs_path, dest)
            moved.append({"file": file_name, "bytes": os.stat(dest).st_size})
        except OSError as exc:
            print("[cleanup] 移走失败 " + file_name + "：" + str(exc))
    freed = sum(int(row["bytes"]) for row in moved)
    if moved:
        print("[cleanup] 已把 %d 个重复副本移到 data/backups/orphans/（释放 %.1f MB）"
              % (len(moved), freed / 1048576.0))
    return ctx.json(200, {
        "ok": True,
        "moved": len(moved),
        "bytes": freed,
        "files": [row["file"] for row in moved],
        "quarantine": "data/backups/orphans/",
        "scan": library.scan_pdf_contents(),
    })


def tidy_pdf_names(ctx):
    """重整文件名：原文 <标题>.pdf、补充材料 <标题> SI.<ext>。"""
    services = ctx.services
    pdf = services.library.tidy_pdf_names()
    attachments = services.library.tidy_attachment_names(
        lambda paper_id, old_file, new_file: _rename_attachment_text(
            services, paper_id, old_file, new_file))
    merged = dict(pdf)
    merged["attachments"] = attachments
    return ctx.json(200, {"ok": True, "result": merged, "attachmentResult": attachments})


# ------------------------------------------------------------------ facets
def facets(ctx):
    """高级搜索的联想数据：顶层是全库清单，query 是当前结果集内的 facet 计数。"""
    services = ctx.services
    library = services.library
    fulltext = services.fulltext
    params = _query_params(ctx)
    scope = str(params.get("scope") or "metadata")
    # 与 /api/papers 列表处理器**完全对齐**：布尔/字段查询先粗筛 id；
    # scope=fulltext 时必须像列表那样清掉 q，否则只出现在正文里的文献会被误杀，计数永远 0。
    if params.get("q") and scope in ("fulltext", "all"):
        query_text = params.get("q") or ""
        if needs_evaluator(query_text):
            terms = " ".join((parse_query(query_text) or {}).get("allTerms") or [])
        else:
            terms = query_text
        hits = _fulltext_search(fulltext, terms, 200) if terms else []
        if scope == "fulltext":
            params["booleanQuery"] = params.get("q")
            params["q"] = ""
            if hits:
                params["ids"] = ",".join(str(hit["id"]) for hit in hits)
            else:
                params.pop("ids", None)
                params["idsEmpty"] = True
        elif hits:
            params["fulltextIds"] = ",".join(str(hit["id"]) for hit in hits)
    services.fulltext_budget["value"] = FULLTEXT_BUDGET if scope in ("fulltext", "all") else 0
    try:
        result = library.facets(params)
    except Exception as exc:  # noqa: BLE001
        print("[facets] 计数失败：" + str(exc))
        result = None
    return ctx.json(200, {
        "ok": True,
        "authors": library.authors(params.get("authorLimit")),
        "venues": library.venues(params.get("venueLimit")),
        "tags": library.tags(),
        "years": library.stats().get("byYear") or [],
        "fulltext": _fulltext_stats(services),
        "query": result,
    })


# ------------------------------------------------------------------ 单条 CRUD
def get_one(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    services = ctx.services
    fulltext = services.fulltext
    return ctx.json(200, {
        "ok": True,
        "paper": paper,
        "pdfExists": services.library.pdf_exists(paper.get("pdfFile")),
        "fulltext": {
            "has": bool(getattr(fulltext, "has", lambda _id: False)(paper.get("id"))),
            "available": _fulltext_available(fulltext) is not False,
        },
    })


def update(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    body = ctx.read_json() or {}
    _ensure_folder(ctx.services, body)
    updated = ctx.services.library.update(ctx.params.get("id"), body)
    return ctx.json(200, {"ok": True, "paper": updated})


def remove(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    services = ctx.services
    services.library.remove(ctx.params.get("id"))
    fn = getattr(services.fulltext, "remove", None)
    if callable(fn):
        try:
            fn(ctx.params.get("id"))
        except Exception as exc:  # noqa: BLE001
            print("[papers] 删除全文失败：" + str(exc))
    return ctx.json(200, {"ok": True})


# ------------------------------------------------------------------ 导出
def export_by_kind(ctx):
    """GET /api/export/:kind（kind ∈ json|bibtex|markdown），未知 kind 回 404。"""
    services = ctx.services
    library = services.library
    ids = to_string_array(ctx.query("ids"))
    kind = (ctx.params or {}).get("kind")
    stamp = _stamp()
    if kind == "json":
        payload = json.dumps(library.export_json(), ensure_ascii=False, indent=2)
        return _send_text(ctx, payload, "application/json; charset=utf-8")
    if kind == "bibtex":
        return _send_text(ctx, library.export_bibtex(ids), "text/plain; charset=utf-8",
                          "library-" + stamp + ".bib")
    if kind == "markdown":
        return _send_text(ctx, library.export_markdown(ids), "text/markdown; charset=utf-8",
                          "library-" + stamp + ".md")
    return ctx.fail(404, "不支持的导出格式")
