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

from paperlib.api.annotations import attachment_annotation_kind
from paperlib.api.paper_scope import require_paper
from paperlib.assets import parent_path, unique_path
from paperlib.domain.folders import FolderError
from paperlib.domain.query import parse_query
from paperlib.legacy.figures import locate_legacy_caption
from paperlib.util import now_iso, safe_path_join, to_string_array

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


def _rename_attachment_references(services, paper_id, old_file, new_file):
    """附件改名后同步批注与抽取文本的文件名。"""
    if old_file.lower().endswith(".pdf"):
        services.db.repo.rename_analysis(paper_id, attachment_annotation_kind(old_file),
                                         attachment_annotation_kind(new_file))
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
    params = _query_params(ctx)
    scope = str(params.get("scope") or "metadata")
    query = params.get("q") or ""
    result = services.library.query(params)
    if query:
        parsed = parse_query(query) or {}
        terms = [term["value"] for term in parsed.get("fieldTerms", []) if term["field"] == "fulltext"]
        if scope in ("fulltext", "all"):
            terms.extend(parsed.get("terms", []) if parsed.get("needsEvaluator") else [query])
        if terms:
            excerpt_query = " ".join(terms)
            for item in result["items"]:
                snippets = services.fulltext.excerpts(item["id"], excerpt_query, 1)
                if snippets:
                    item["fulltextExcerpt"] = snippets[0]
    return ctx.json(200, {"ok": True, **result, "fulltext": _fulltext_stats(services)})


def create(ctx):
    services = ctx.services
    body = ctx.read_json() or {}
    if not isinstance(body, dict):
        return ctx.fail(400, "文献字段须为对象")
    try:
        if body.get("pdfFile"):
            folder = parent_path(body["pdfFile"])
            body["folder"] = folder if services.folders.exists(folder) else ""
        else:
            _ensure_folder(services, body)
    except FolderError as exc:
        return ctx.fail(exc.status, str(exc))
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


def sync_pdfs(ctx):
    return ctx.json(200, {"ok": True, **ctx.services.library.sync_pdf_directory(ctx.services)})


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
    with services.db.paper_writes():
        return _cleanup_duplicates(ctx, services, library, body)


def _cleanup_duplicates(ctx, services, library, body):
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
        abs_path = services.db.path_for_paper_file(file_name)
        if not abs_path or not os.path.isfile(abs_path):
            continue
        dest = safe_path_join(quarantine, os.path.basename(file_name))
        if not dest:
            continue
        try:
            dest = unique_path(Path(dest))
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
        lambda paper_id, old_file, new_file: _rename_attachment_references(
            services, paper_id, old_file, new_file))
    merged = dict(pdf)
    merged["attachments"] = attachments
    return ctx.json(200, {"ok": True, "result": merged, "attachmentResult": attachments})


# ------------------------------------------------------------------ facets
def facets(ctx):
    """高级搜索的联想数据：顶层是全库清单，query 是当前结果集内的 facet 计数。"""
    services = ctx.services
    library = services.library
    params = _query_params(ctx)
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
        "years": library.years(),
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
    result = {
        "ok": True,
        "paper": paper,
        "pdfExists": services.library.pdf_exists(paper.get("pdfFile")),
        "fulltext": {
            "has": bool(getattr(fulltext, "has", lambda _id: False)(paper.get("id"))),
            "available": _fulltext_available(fulltext) is not False,
        },
    }
    figure_id = ctx.query("figure", "")
    if figure_id:
        result["figureLocation"] = None
        if result["pdfExists"] and figure_id.isascii() and figure_id.isdecimal() and len(figure_id) <= 18:
            figure = services.db.repo.get_figure(paper["id"], int(figure_id))
            if figure:
                try:
                    location = json.loads(figure["pdf_location"] or "{}")
                    path = services.db.path_for_paper_file(paper["pdfFile"])
                    result["figureLocation"] = location or locate_legacy_caption(path, figure["caption"])
                except (OSError, RuntimeError, ValueError):
                    pass
    return ctx.json(200, result)


def update(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    body = ctx.read_json() or {}
    if not isinstance(body, dict):
        return ctx.fail(400, "文献字段须为对象")
    services = ctx.services
    try:
        with services.db.paper_writes():
            latest = services.library.get(paper["id"])
            if latest is None:
                return ctx.fail(404, "找不到这条文献")
            file = latest.get("pdfFile") or ""
            requested = body.get("pdfFile")
            if requested is not None and requested != file and requested:
                folder = parent_path(requested)
                body["folder"] = folder if services.folders.exists(folder) else ""
            else:
                _ensure_folder(services, body)
                if file and requested != "" and "folder" in body and body["folder"] != parent_path(file):
                    services.folders.move_papers([paper["id"]], body["folder"])
                    body.pop("pdfFile", None)
            updated = services.library.update(paper["id"], body)
    except FolderError as exc:
        return ctx.fail(exc.status, str(exc))
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
