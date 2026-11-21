"""原文 / 全文 / 附件 / 引用 / PDF（移植自 server/api/files.js，272 行；契约名：pdf.extract_text(path) → Text）。

  POST   /api/papers/upload               （原始文件流 + X-Filename / X-Paper-Id）
  POST   /api/papers/fulltext/batch       给「有 PDF 但还没提取」的文献补提取
  POST   /api/papers/:id/fulltext         从 PDF 提取正文并建索引
  GET    /api/papers/:id/fulltext         读已提取的正文
  POST   /api/papers/:id/fulltext/text    { text } 把识别出来的文字存成正文
  GET    /api/papers/:id/attachments      列出补充材料（SI）
  POST   /api/papers/:id/attachments      （原始文件流 + X-Filename）
  GET    /api/papers/:id/attachments/:file 下载 / 预览某个附件
  DELETE /api/papers/:id/attachments/:file 删除某个附件
  GET    /api/papers/:id/pdf              （Range）原文预览
  GET    /api/papers/:id/cite?format=     引用格式化（gb|apa|mla|bibtex|markdown）

这一层只管 HTTP 与文件落盘；卡片字段的写入一律走 library（最终由 store 访问器落盘）。
正文与附件文本写 store.set_text（SQLite 是唯一真源），不再以 data/fulltext/*.txt 为准。

注意两处与 api.md 的差异（以 Node 实现为准）：
  · 上传体超限回 413（Node 内部抛错会被兜底成 500，这里按 §1.1「413 上传体超限」给）。
  · 附件下载发 inline disposition（Node sendFile({inline: originalName})；契约勘误已记档）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote

from paperlib.api.annotations import attachment_annotation_kind
from paperlib.api.paper_scope import require_paper
from paperlib.domain import naming
from paperlib.util import ensure_dir, now_iso, safe_path_join, sanitize_file_name
from shared.pure import citation

PDF_LIMIT = 300 * 1024 * 1024          # 原文 PDF 上限
ATTACHMENT_LIMIT = 200 * 1024 * 1024   # 单个补充材料上限
FULLTEXT_READ_LIMIT = 200000           # GET fulltext 的截断长度
FULLTEXT_SAVE_LIMIT = 8 * 1024 * 1024


def _header(ctx, name):
    try:
        return ctx.headers.get(name)
    except Exception:  # noqa: BLE001
        return None


def _paper_attachments(paper):
    return list((paper or {}).get("attachments") or [])


# --------------------------------------------------------------------------- 上传原文


def upload(ctx):
    services = ctx.services
    store = services.db
    original_name = unquote(str(_header(ctx, "X-Filename") or "document.pdf"))
    if not re.search(r"\.pdf$", original_name, re.I):
        return ctx.fail(400, "目前只支持上传 PDF 文件")
    paper_id = str(_header(ctx, "X-Paper-Id") or "").strip()
    id_safe = re.sub(r"[^a-f0-9]", "", paper_id, flags=re.I) or "unlinked"
    content_type = str(_header(ctx, "Content-Type") or "")
    try:
        buf = ctx.read_body(PDF_LIMIT)
    except ValueError:
        return ctx.fail(413, "PDF 超过 300MB 上限")
    looks_pdf = bytes(buf[:5]) == b"%PDF-"
    if not looks_pdf and "pdf" not in content_type.lower():
        return ctx.fail(400, "这个文件看起来不是 PDF")

    # 文件名优先用文献标题（更易读），取不到标题时退回原文件名
    paper = services.library.get(paper_id) if paper_id else None
    if paper and paper.get("title"):
        display = sanitize_file_name(paper.get("title"), 70)
    else:
        stem = os.path.splitext(os.path.basename(original_name))[0]
        display = sanitize_file_name(stem, 60)
    file_name = id_safe + "-" + display + ".pdf"
    with store.paper_writes():
        paper = services.library.get(paper_id) if paper_id else None
        folder = (paper or {}).get("folder") or ""
        if folder:
            file_name = folder + "/" + file_name
        if not store.path_for_paper_file(file_name):
            return ctx.fail(400, "文件名不合法")
        store.attach_file(paper.get("id") if paper else "", buf, {"fileName": file_name})

    # 上传完顺手把正文提取出来：AI 总结马上就能用上正文，不用用户再点一次「提取全文」
    extract = None
    if paper:
        try:
            extract = services.extract_paper_fulltext(services.library.get(paper.get("id")))
        except Exception as exc:  # noqa: BLE001
            extract = {"ok": False, "error": str(exc)}
    ctx.json(201, {"ok": True, "pdfFile": file_name, "size": len(buf), "extract": extract})


# --------------------------------------------------------------------------- 全文


def fulltext_batch(ctx):
    services = ctx.services
    fulltext = services.fulltext
    if not fulltext.available():
        return ctx.fail(501, fulltext.install_hint())
    pending = [p for p in services.db.list_papers()
               if p.get("pdfFile") and not fulltext.has(p.get("id"))]
    results = []
    for paper in pending:
        result = services.extract_paper_fulltext(paper) or {}
        results.append({
            "id": paper.get("id"),
            "title": paper.get("title") or "",
            "ok": bool(result.get("ok")),
            "chars": int(result.get("chars") or 0),
            "error": result.get("error") or "",
        })
    ctx.json(200, {
        "ok": True,
        "total": len(pending),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    })


def fulltext_extract(ctx):
    services = ctx.services
    paper = require_paper(ctx)
    if not paper:
        return None
    if not services.fulltext.available():
        return ctx.fail(501, services.fulltext.install_hint())
    if not paper.get("pdfFile"):
        return ctx.fail(400, "这条文献还没有关联 PDF，无法提取全文")
    result = services.extract_paper_fulltext(paper) or {}
    if not result.get("ok"):
        return ctx.fail(500, result.get("error") or "提取失败")
    ctx.json(200, {
        "ok": True,
        "pages": result.get("pages"),
        "chars": int(result.get("chars") or 0),
        "preview": result.get("preview") or "",
    })


def fulltext_get(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    text = ctx.services.fulltext.read(paper.get("id"))
    if text is None:
        return ctx.fail(404, "还没有提取过全文")
    ctx.json(200, {"ok": True, "chars": len(text), "text": text[:FULLTEXT_READ_LIMIT]})


def fulltext_save_text(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    try:
        body = ctx.read_json(FULLTEXT_SAVE_LIMIT)
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    text = str((body or {}).get("text") or "").strip() if isinstance(body, dict) else ""
    if not text:
        return ctx.fail(400, "正文是空的")
    saved = ctx.services.fulltext.save(paper.get("id"), text)
    ctx.json(200, {"ok": True, "chars": saved.get("chars", 0)})


# --------------------------------------------------------------------------- 附件


def attachments_list(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    fulltext = ctx.services.fulltext
    paper_id = paper.get("id")
    items = []
    for record in _paper_attachments(paper):
        row = dict(record)
        file = record.get("file")
        row["hasTextFile"] = bool(fulltext.has_attachment_text(paper_id, file))
        if row["hasTextFile"] and not row.get("hasText"):
            # 已入库的 SI 可能是在总结或问答时补抽的，文本已存但旧记录还没有字数。
            text = fulltext.read_attachment(paper_id, file) or ""
            row.update(hasText=bool(text.strip()), textChars=len(text))
        items.append(row)
    ctx.json(200, {"ok": True, "attachments": items})


def attachments_upload(ctx):
    services = ctx.services
    store = services.db
    fulltext = services.fulltext
    paper = require_paper(ctx)
    if not paper:
        return None
    original_name = unquote(str(_header(ctx, "X-Filename") or "attachment.bin"))
    try:
        buf = ctx.read_body(ATTACHMENT_LIMIT)
    except ValueError:
        return ctx.fail(413, "附件超过 200MB 上限")
    if not buf:
        return ctx.fail(400, "上传内容为空")
    ext = (os.path.splitext(original_name)[1] or "").lower() or ".bin"

    # 附件摊平放在 article/attachments/ 一层，文件名统一 <文献id>-<标题> SI<序号><ext>：
    # 前缀标明归属，也避免不同文献的同名 SI 互相覆盖（命名规则唯一来源是 domain/naming）。
    att_dir = Path(store.attachments_dir)
    ensure_dir(att_dir)
    try:
        taken = {entry.name for entry in att_dir.iterdir()}
    except OSError:
        taken = set()
    index = len(_paper_attachments(paper))
    while True:
        file_name = naming.next_attachment_name(paper, {"originalName": original_name}, index, taken)
        abs_path = safe_path_join(att_dir, file_name)
        if not abs_path:
            return ctx.fail(400, "文件名不合法")
        try:
            output = open(abs_path, "xb")
            break
        except FileExistsError:
            taken.add(file_name)

    paper_id = paper.get("id")
    updated = None
    try:
        with output:
            output.write(buf)
        extraction = services.extract_attachment_text(abs_path, ext) or {}
        if extraction.get("ok"):
            fulltext.save_attachment_text(paper_id, file_name, extraction.get("text") or "")
        record = {
            "file": file_name,
            "originalName": original_name,
            "size": len(buf),
            "kind": extraction.get("kind") or "other",
            "hasText": bool(extraction.get("ok")),
            "textChars": len(str(extraction.get("text") or "")) if extraction.get("ok") else 0,
            "addedAt": now_iso(),
        }
        updated = services.library.update(
            paper_id, lambda current: {"attachments": _paper_attachments(current) + [record]})
    finally:
        if updated is None:
            fulltext.remove_attachment_text(paper_id, file_name)
            try:
                os.unlink(abs_path)
            except OSError:
                pass
    if updated is None:
        return ctx.fail(404, "找不到这条文献")
    extract = ({"ok": True, "chars": record["textChars"]} if extraction.get("ok")
               else {"ok": False, "error": extraction.get("error") or "未抽取"})
    ctx.json(201, {
        "ok": True,
        "attachment": record,
        "extract": extract,
        "attachments": updated.get("attachments") or [],
    })


def attachment_download(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    wanted = str((ctx.params or {}).get("file") or "")
    record = next((a for a in _paper_attachments(paper) if a.get("file") == wanted), None)
    if record is None:
        return ctx.fail(404, "找不到这个附件")
    abs_path = safe_path_join(ctx.services.db.attachments_dir, wanted)
    if not abs_path:
        return ctx.fail(400, "文件名不合法")
    inline = record.get("originalName") or wanted
    ctx.send_file(abs_path, inline=inline, cache="no-cache")


def attachment_remove(ctx):
    services = ctx.services
    paper = require_paper(ctx)
    if not paper:
        return None
    wanted = str((ctx.params or {}).get("file") or "")

    def remove_attachment(current):
        attachments = _paper_attachments(current)
        remaining = [a for a in attachments if a.get("file") != wanted]
        return {"attachments": remaining} if len(remaining) != len(attachments) else None

    with services.db.paper_writes():
        updated = services.library.update(paper.get("id"), remove_attachment)
        if updated is None:
            return ctx.fail(404, "找不到这个附件")
        abs_path = safe_path_join(services.db.attachments_dir, wanted)
        if abs_path and os.path.isfile(abs_path):
            try:
                os.unlink(abs_path)
            except OSError:
                pass
        services.fulltext.remove_attachment_text(paper.get("id"), wanted)
        if wanted.lower().endswith(".pdf"):
            services.db.repo.delete_analysis(paper.get("id"), attachment_annotation_kind(wanted))
    ctx.json(200, {"ok": True, "attachments": updated.get("attachments") or []})


# --------------------------------------------------------------------------- PDF / 引用


def pdf(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    if not paper.get("pdfFile"):
        return ctx.fail(404, "这条文献还没有关联 PDF")
    abs_path = ctx.services.db.path_for_paper_file(paper.get("pdfFile"))
    if not abs_path:
        return ctx.fail(400, "文件路径不合法")
    ctx.send_file(abs_path, content_type="application/pdf",
                  inline=os.path.basename(str(paper.get("pdfFile"))), cache="no-cache")


def cite(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    kind = ctx.query("format", "gb") or "gb"
    ctx.json(200, {
        "ok": True,
        "format": kind,
        "text": citation.format(paper, kind),
        "available": citation.available(),
    })
