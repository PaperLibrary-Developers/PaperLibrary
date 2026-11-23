"""导入端点（移植自 server/api/import.js，210 行；契约名：import.pdfPreflight / import.pdfConfirm / import.jsonImport）。

  POST   /api/import/pdf?batch=<id>&name=<原始文件名>   原始文件流（预检，不建记录）
  DELETE /api/import/pdf?batch=<id>                     放弃这次导入
  POST   /api/import/pdf/confirm                        确认落库（转正临时文件 → 建记录 → 提取全文）
  POST   /api/import/json                               导入 JSON

临时文件放在 data/tmp-import/<批次>/ 下，确认后才转正到 data/article/papers/。
这样中途放弃不会留下半个文献，也不会污染 papers/ 目录。

状态码：批次号不合法 / 非 PDF / 超批次数 → 400；请求体过大 → 413（与 Node 的 try/catch 一致）；
预检内部异常 → 500「预检失败：…」。确认接口的 JSON 解析失败按 400 处理（Node 会兜底成 500，
这里按 §1.1「400 参数校验失败」给，属已知轻微差异）。
"""
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from urllib.parse import unquote

from paperlib.domain import pdf_import
from paperlib.util import ensure_dir, now_iso, safe_path_join, sanitize_file_name

# 批次 id：只允许 [a-z0-9]，防止路径穿越（同时也是临时目录名）
BATCH_ID_RE = re.compile(r"^[a-z0-9]{4,40}$")
PDF_LIMIT = 300 * 1024 * 1024
CONFIRM_LIMIT = 8 * 1024 * 1024
JSON_IMPORT_LIMIT = 64 * 1024 * 1024
# 预检只做识别：题录源网络慢时最多等这么久（默认双来源预算 13 秒），超时按已有信息降级。
# 这样批量拉入多篇时，单篇扫描不会被慢网络拖到十几秒。
PREFLIGHT_LOOKUP_BUDGET_MS = 6000


def tmp_dir_of(services):
    return Path(services.runtime.data_dir) / "tmp-import"


def batch_dir(services, batch_id):
    if not BATCH_ID_RE.match(str(batch_id or "")):
        return None
    return safe_path_join(tmp_dir_of(services), str(batch_id))


def remove_batch(services, batch_id):
    directory = batch_dir(services, batch_id)
    if not directory:
        return False
    try:
        # 与 Node 的 fs.rmSync(dir, {recursive:true, force:true}) 一致：目录不存在也算成功
        shutil.rmtree(directory, ignore_errors=True)
        return True
    except OSError:
        return False


def cleanup_import_tmp(services, max_age_ms=None):
    """启动时清理旧批次（默认 6 小时），保留刚刚的（用户可能停在确认页）。"""
    return pdf_import.cleanup_import_tmp(services, max_age_ms)


# --------------------------------------------------------------------------- 预检


def pdf_preflight(ctx):
    services = ctx.services
    batch_id = str(ctx.query("batch", "") or "").strip().lower()
    directory = batch_dir(services, batch_id)
    if not directory:
        return ctx.fail(400, "批次号不合法（只允许小写字母与数字）")
    original_name = unquote(str(ctx.query("name", "document.pdf") or "document.pdf"))
    if not re.search(r"\.pdf$", original_name, re.I):
        return ctx.fail(400, "目前只支持上传 PDF 文件")

    try:
        buf = ctx.read_body(PDF_LIMIT)
    except ValueError as exc:
        return ctx.fail(413, str(exc))
    if not buf:
        return ctx.fail(400, "没有收到文件内容")
    if bytes(buf[:5]) != b"%PDF-":
        return ctx.fail(400, "这个文件看起来不是 PDF（缺少 %PDF- 文件头）")

    try:
        ensure_dir(directory)
        existing = [f for f in os.listdir(directory) if re.search(r"\.pdf$", f, re.I)]
        max_files = services.prefs.import_max_files()
        if len(existing) >= max_files:
            # 上限固定 200（设置页那个旋钮已去掉），所以这里只教用户「分批」，
            # 不再指路到一个已经不存在的设置项
            return ctx.fail(400, "一个批次最多 %d 个文件，请分批导入" % max_files)
        # 用「当前最大编号 + 1」而不是「文件个数 + 1」：部分导入后目录里会留下
        # 没导入的 2.pdf，如果按个数算会再次分配 2，把还没导入的临时文件覆盖掉。
        numbers = []
        for file_name in existing:
            match = re.match(r"^(\d+)\.pdf$", file_name, re.I)
            if match:
                numbers.append(int(match.group(1)))
        temp_id = str(max(numbers) + 1) if numbers else "1"
        temp_file = os.path.join(directory, temp_id + ".pdf")
        with open(temp_file, "wb") as fh:
            fh.write(buf)
        result = pdf_import.pdf_preflight(
            services, temp_file, original_name, temp_id,
            {"lookup_options": {"budgetMs": PREFLIGHT_LOOKUP_BUDGET_MS}})
        ctx.json(201, {
            "ok": True,
            "batch": batch_id,
            "tempId": temp_id,
            "name": original_name,
            "size": len(buf),
            "parsed": result.get("parsed") or {},
            "draft": result.get("draft") or {},
        })
    except Exception as exc:  # noqa: BLE001 - 预检失败也要给出 500 而不是裸栈
        return ctx.fail(500, "预检失败：" + str(exc))


# --------------------------------------------------------------------------- 放弃 / 确认


def pdf_abandon(ctx):
    services = ctx.services
    batch_id = str(ctx.query("batch", "") or "").strip().lower()
    if not batch_dir(services, batch_id):
        return ctx.fail(400, "批次号不合法")
    ctx.json(200, {"ok": True, "removed": remove_batch(services, batch_id)})


def pdf_confirm(ctx):
    services = ctx.services
    try:
        body = ctx.read_json(CONFIRM_LIMIT)
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    batch_id = str((body or {}).get("batch") or "").strip().lower() if isinstance(body, dict) else ""
    directory = batch_dir(services, batch_id)
    if not directory:
        return ctx.fail(400, "批次号不合法")
    entries = body.get("papers") if isinstance(body, dict) else None
    entries = entries if isinstance(entries, list) else []
    if not entries:
        return ctx.fail(400, "没有要导入的条目")

    imported = []
    failed = []
    consumed = []  # 本轮成功转正的临时文件编号（attach_file 是复制，需要显式清掉）
    for entry in entries:
        entry = entry if isinstance(entry, dict) else {}
        temp_id = re.sub(r"[^0-9]", "", str(entry.get("tempId") or ""))
        src = safe_path_join(directory, temp_id + ".pdf") if temp_id else None
        if not src or not os.path.isfile(src):
            failed.append({"tempId": temp_id, "title": entry.get("title") or "",
                           "error": "临时文件不在了（可能已过期或已取消）"})
            continue
        payload = entry.get("paper") if isinstance(entry.get("paper"), dict) else {}
        try:
            # 标题先立起来，文件名才能跟着标题走
            title = str(payload.get("title") or "").strip() or pdf_import.title_from_file_name(
                str(entry.get("name") or "document.pdf"))
            with services.db.paper_writes():
                folder = services.folders.ensure(payload.get("folder"))
                created = services.library.create(dict(payload, title=title, folder=folder))
                display = sanitize_file_name(created.get("title"), 70)
                file_name = str(created.get("id")) + "-" + display + ".pdf"
                if folder:
                    file_name = folder + "/" + file_name
                try:
                    services.db.attach_file(created.get("id"), src, {"fileName": file_name})
                except Exception:
                    services.library.remove(created.get("id"))
                    raise
            try:
                extract = services.extract_paper_fulltext(services.library.get(created.get("id"))) or {}
            except Exception as exc:  # noqa: BLE001 - 原文已入库，正文抽取可以单独重试
                extract = {"ok": False, "error": str(exc)}
            imported.append({
                "id": created.get("id"),
                "title": created.get("title"),
                "pdfFile": file_name,
                "extract": ({"ok": True, "chars": int(extract.get("chars") or 0),
                             "pages": extract.get("pages")}
                            if extract.get("ok")
                            else {"ok": False, "error": extract.get("error") or "未提取"}),
            })
            consumed.append(temp_id)
        except Exception as exc:  # noqa: BLE001 - 单篇失败不影响其它条目
            failed.append({"tempId": temp_id, "title": entry.get("title") or "", "error": str(exc)})

    # 只清掉本轮已经成功转正的临时文件。以前这里无条件 remove_batch(整批)，
    # 而前端「全部导入」是逐篇调用 confirm 的：第 1 篇成功就把整批临时文件删掉，
    # 第 2 篇起全部报「临时文件不在了（可能已过期或已取消）」。
    # 失败 / 还没提交的条目必须保留，用户才能重试。
    for temp_id in consumed:
        try:
            os.remove(os.path.join(directory, temp_id + ".pdf"))
        except OSError:
            pass
    try:
        if not os.listdir(directory):
            remove_batch(services, batch_id)
    except OSError:
        pass
    if imported:
        print("[import] 批次 %s：导入 %d 篇，失败 %d 篇" % (batch_id, len(imported), len(failed)))
    ctx.json(200, {"ok": True, "imported": imported, "failed": failed})


# --------------------------------------------------------------------------- JSON 导入


def json_import(ctx):
    services = ctx.services
    try:
        body = ctx.read_json(JSON_IMPORT_LIMIT)
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    overwrite = bool((body or {}).get("overwrite")) if isinstance(body, dict) else False
    try:
        result = services.library.import_json(body, {"overwrite": overwrite})
        ctx.json(200, {"ok": True, "result": result})
    except Exception as exc:  # noqa: BLE001 - Node 版同样把导入失败转成 400
        return ctx.fail(400, str(exc))
