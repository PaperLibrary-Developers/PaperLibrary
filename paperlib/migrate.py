"""旧库迁移（union 合库）：Sci-lib library.json + PaperLibrary papers.db → SQLite 唯一真源。

移植自 Sci-lib server/store/accessors.js（写入口）与 PaperLibrary app/database.py（读入口）。
契约名：repository(Paper / Attachment / Tag)；字段映射依据 shared/contracts/data-model.md §8。

映射（工程计划 §4.2 B8）：
  · Sci-lib：逐篇 normalize_paper，保留 id / addedAt；legacy_ids 记 source="scilib"；
    顺带复制 article/papers 原文、article/attachments 的 SI、fulltext/*.txt 正文。
  · PaperLibrary：papers 表 → 卡片（journal→venue、summary 空则用 abstract、parsed_at→addedAt）；
    abs_path → article/papers/<paperId>-<sanitize(标题)>.pdf；full_text → paper_text；
    deep_analysis/deep_scores → analysis(kind="deep")；figures → figures 表 + images/<paperId>/
    （image_path 存相对 data 目录的路径）。legacy_ids 记 source="paperlibrary"。
  · 去重：同 DOI（去前缀小写）或同标题（trim/lower）合并；用户编辑字段以 Sci-lib 侧为准。
  · 幂等：legacy_ids 已映射的条目不重复插入；文件按「目标已存在」判断是否复制。
  · dry_run=True 不写任何表 / 文件 / 备份，只返回统计。
只读旧库 SQL 在本文件内（旧库是外部 SQLite，没有别的入口）；HELIX 自己的库一律走 store
访问器，本文件不写一行 HELIX SQL。
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil
import sqlite3
from pathlib import Path
from urllib.parse import quote

from paperlib.domain.repository import normalize_paper
from paperlib.util import (
    ensure_dir, now_iso, normalize_doi, read_json, sanitize_file_name, to_string_array, uid,
)

SOURCE_SCILIB = "scilib"
SOURCE_PAPERLIBRARY = "paperlibrary"
# 用户编辑字段（合并时以 Sci-lib 侧为准）与题录字段（只补空）
USER_FIELDS = ("tags", "notes", "status", "rating", "starred", "starredAt", "chat", "attachments")
BIB_FIELDS = ("title", "authors", "year", "venue", "volume", "issue", "pages", "publisher",
              "doi", "url", "keywords", "summary", "folder", "arxivId", "pdfFile", "bibtexKey")
MAX_WARNINGS = 40


def _pick(candidates, resolve):
    for candidate in candidates:
        found = resolve(candidate)
        if found:
            return found
    return None


def _scilib_data_dir(path):
    """Sci-lib 路径可能是仓库根，也可能是 data 目录；返回含 library.json 的目录。"""
    p = Path(path) if path else None
    return next((c for c in ((p, p / "data") if p else ()) if (c / "library.json").is_file()), None)


def _paperlibrary_app_dir(path):
    """PaperLibrary 路径可能是 app 目录，也可能是 data 目录；返回 (app_dir, data_dir)。"""
    p = Path(path) if path else None
    data = next((c for c in ((p / "data", p) if p else ()) if (c / "papers.db").is_file()), None)
    return ((data.parent if data.name == "data" else data), data) if data else (None, None)


def _explicit_path(config, legacy, keys):
    extra = getattr(config, "extra", None) or {}
    for key in keys:
        value = (legacy or {}).get(key) or (extra.get(key) if isinstance(extra, dict) else None)
        if value:
            return str(value)
    return ""


def discover_sources(config):
    """定位两处旧库；找不到的源为 None（不报错，迁移只做找得到的）。

    优先级：config.extra["legacy"]{scilibDir,paperlibraryDir} → 同名 extra 键 →
    环境变量 PAPERLIB_SCILIB_DIR / PAPERLIB_PAPERLIBRARY_DIR →
    与 HELIX 同级的老工程目录（<root>/../Sci-lib、<root>/../PaperLibrary/app）。
    """
    extra = getattr(config, "extra", None) or {}
    legacy = extra.get("legacy") if isinstance(extra.get("legacy"), dict) else {}
    base = Path(getattr(config, "root", None) or ".").resolve().parent
    raw = (_explicit_path(config, legacy, ("scilibDir", "scilib", "legacySciLib"))
           or os.environ.get("PAPERLIB_SCILIB_DIR", ""))
    data_dir = _pick(([raw] if raw else []) + [base / "Sci-lib", base / "Sci-lib" / "data"],
                     _scilib_data_dir)
    raw = (_explicit_path(config, legacy, ("paperlibraryDir", "paperlibrary", "legacyPaperLibrary"))
           or os.environ.get("PAPERLIB_PAPERLIBRARY_DIR", ""))
    pl_app, pl_data = _pick(([raw] if raw else []) + [base / "PaperLibrary" / "app",
                                                      base / "PaperLibrary" / "app" / "data"],
                            _paperlibrary_app_dir) or (None, None)
    return {
        "scilib": ({"data_dir": data_dir, "library": data_dir / "library.json"}
                   if data_dir else None),
        "paperlibrary": ({"app_dir": pl_app, "data_dir": pl_data, "db": pl_data / "papers.db",
                          "images": pl_data / "images"} if pl_app else None),
    }


def _blank(value):
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple)):
        return len(value) == 0
    return value is None


def _signature(paper):
    """比较卡片内容用的指纹（忽略每次写都会刷新的 updatedAt）。"""
    return json.dumps({k: v for k, v in (paper or {}).items() if k != "updatedAt"},
                      ensure_ascii=False, sort_keys=True)


def _norm_title(value):
    return " ".join(str(value or "").split()).lower()


def _to_iso(value):
    """旧库时间（"2026-11-04 00:26:23" / 已是 ISO）→ ISO Z；失败返回空串。"""
    text = str(value or "").strip()
    if not text or (text.endswith("Z") and "T" in text):
        return text
    try:
        stamp = datetime.datetime.fromisoformat(text)
    except ValueError:
        return ""
    stamp = stamp if stamp.tzinfo else stamp.replace(tzinfo=datetime.timezone.utc)
    return stamp.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _jsonish(value):
    """旧库里常存 JSON 字符串：能解析就给对象，否则原样返回字符串。"""
    if isinstance(value, (dict, list)):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return text  # 不是 JSON 的老数据原样保留


def _safe_name(name):
    base = os.path.basename(str(name or "").replace("\\", "/"))
    return re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", base).strip(" .") or "figure.jpg"


def _unique_name(used, name, old_id):
    """同一篇文献下图片不重名；重名时用旧 paperId 前缀，保证两次运行结果一致。"""
    candidate = _safe_name(name)
    if candidate not in used:
        used.add(candidate)
        return candidate
    stem, ext = os.path.splitext("%s-%s" % (str(old_id or "x")[:8], candidate))
    n = 2
    while "%s-%s%s" % (stem, n, ext) in used:
        n += 1
    candidate = "%s-%s%s" % (stem, n, ext)
    used.add(candidate)
    return candidate


def _pdf_file_name(paper_id, title):
    """与 Sci-lib 上传一致：<id 的 [a-f0-9]>-<sanitize(标题,70)>.pdf。"""
    id_safe = re.sub(r"[^a-f0-9]", "", str(paper_id or ""), flags=re.I) or "unlinked"
    return id_safe + "-" + sanitize_file_name(title, 70) + ".pdf"


def _copy_file(plan, source, target, bucket, dry_run):
    """复制旧库文件并记进 bucket（copied/missing）；复制而非移动，旧库仍可回退对照。"""
    if not target:
        return "failed:目标路径不合法"
    if not source or not os.path.isfile(source):
        bucket["missing"] += 1
        return "missing"
    if os.path.isfile(target) and os.path.getsize(target) > 0:
        return "exists"
    if not dry_run:
        try:
            ensure_dir(os.path.dirname(str(target)))
            shutil.copyfile(str(source), str(target))
        except OSError as exc:
            return "failed:" + str(exc)
    plan.copied_files += 1
    bucket["copied"] += 1
    return "would" if dry_run else "copied"


def _resolve_media(source, raw_path):
    """旧库 image_path（相对 app 目录）→ 绝对路径；找不到就返回最可能的候选。"""
    raw = str(raw_path or "").strip()
    if not raw:
        return ""
    rel = raw.replace("\\", "/")
    candidates = ([raw] if os.path.isabs(raw) else []) + [
        str(Path(source["app_dir"]) / rel), str(Path(source["data_dir"]) / rel)]
    return next((c for c in candidates if os.path.isfile(c)), candidates[0])


def _read_scilib(library_path):
    payload = read_json(library_path, {}) or {}
    papers = payload.get("papers") if isinstance(payload, dict) else None
    return [p for p in (papers or []) if isinstance(p, dict)]


def _table_rows(conn, table, order_by):
    cols = [row[1] for row in conn.execute("PRAGMA table_info(%s)" % table)]
    if not cols:
        return []
    sql = "SELECT %s FROM %s ORDER BY %s" % (",".join(cols), table, order_by)
    return [dict(zip(cols, row)) for row in conn.execute(sql)]


def _read_paperlibrary(db_path):
    """只读打开旧库（缺表/损坏由调用方兜底，迁移继续做 Sci-lib 那部分）。"""
    conn = sqlite3.connect("file:%s?mode=ro" % quote(Path(str(db_path)).as_posix(), safe="/:"),
                           uri=True)
    try:
        return (_table_rows(conn, "papers", "parsed_at, id"),
                _table_rows(conn, "figures", "paper_id, fig_index, id"))
    finally:
        conn.close()


def _parse_authors(value):
    try:
        data = json.loads(value) if isinstance(value, str) else value
    except ValueError:
        data = value
    if isinstance(data, (list, tuple)):
        return to_string_array([a for a in data if isinstance(a, (str, int, float))])
    return to_string_array(data)


def _scilib_card(raw):
    paper = normalize_paper(raw, None)
    paper["id"] = str(raw.get("id") or paper["id"])
    for key in ("addedAt", "updatedAt"):
        if str(raw.get(key) or "").strip():
            paper[key] = str(raw[key])
    return paper


def _paperlibrary_card(row, paper_id):
    title = str(row.get("title") or "").strip()
    if not title:
        title = os.path.splitext(os.path.basename(str(row.get("filename") or "")))[0]
    summary = str(row.get("summary") or "").strip() or str(row.get("abstract") or "").strip()
    paper = normalize_paper({
        "id": paper_id, "title": title or "（未命名文献）",
        "authors": _parse_authors(row.get("authors")), "year": row.get("year"),
        "venue": row.get("journal") or "", "doi": row.get("doi") or "",
        "folder": row.get("folder") or "", "arxivId": row.get("arxiv_id") or "",
        "summary": summary, "addedAt": _to_iso(row.get("parsed_at"))}, None)
    return paper


def _merge_card(existing, incoming, prefer_user):
    """合并同一篇的两条记录：用户编辑字段以 Sci-lib 侧为准，题录字段只补空。"""
    merged = dict(existing)
    if prefer_user:
        for key in USER_FIELDS:
            if not _blank(incoming.get(key)):
                merged[key] = incoming.get(key)
    for key in BIB_FIELDS:
        if _blank(merged.get(key)) and not _blank(incoming.get(key)):
            merged[key] = incoming.get(key)
    stamps = sorted(s for s in (str(merged.get("addedAt") or ""),
                                str(incoming.get("addedAt") or "")) if s)
    if stamps:
        merged["addedAt"] = stamps[0]
    merged["updatedAt"] = now_iso()
    return merged


class _Index:
    """DOI / 标题索引：本次迁移里新旧卡片都能查到（去重的唯一依据）。"""

    def __init__(self, papers):
        self.by_pid, self.by_doi, self.by_title = {}, {}, {}
        for paper in papers or []:
            self.put(paper)

    def put(self, paper):
        pid = str(paper.get("id") or "")
        if not pid:
            return
        self.by_pid[pid] = paper
        for table, value in ((self.by_doi, normalize_doi(paper.get("doi")).lower()),
                             (self.by_title, _norm_title(paper.get("title")))):
            if value:
                table.setdefault(value, pid)

    def find(self, paper):
        doi, title = normalize_doi(paper.get("doi")).lower(), _norm_title(paper.get("title"))
        if doi and doi in self.by_doi:
            return self.by_doi[doi]
        return self.by_title.get(title, "") if title else ""


def _empty_stats():
    copies = {"copied": 0, "missing": 0}
    return {
        "dryRun": False,
        "libraryJson": {"found": False, "path": "", "papers": 0, "added": 0, "merged": 0,
                        "skipped": 0, "pdfs": dict(copies), "attachments": dict(copies),
                        "texts": {"added": 0, "skipped": 0, "chars": 0}},
        "paperLibrary": {"found": False, "path": "", "papers": 0, "figures": 0, "added": 0,
                         "merged": 0, "skipped": 0, "pdfs": dict(copies), "images": dict(copies),
                         "texts": {"added": 0, "skipped": 0, "chars": 0},
                         "analysis": {"added": 0, "skipped": 0}},
        # papers / texts / ids / warnings 由入口按计划重算，这里只留 figures 累加器
        "figures": {"added": 0, "skipped": 0},
    }


class _Plan:
    """迁移计划：dry_run 只走到这一层（除文件复制判断外不写任何东西）。"""

    def __init__(self, dry_run):
        self.dry_run = bool(dry_run)
        self.cards = {}          # pid → 新建或合并后的卡片（只放有变化/新增的）
        self.texts = {}          # pid → 正文（每个 pid 只留第一份）
        self.analyses = {}       # (pid, kind) → payload
        self.figure_plans = {}   # pid → [本次新增的 figure]
        self.legacy = []         # (legacy_id, pid, source)
        self.warnings = []
        self.figure_names = {}   # pid → 已用图片文件名
        self.copied_files = 0
        self.stats = _empty_stats()
        self.stats["dryRun"] = self.dry_run

    def warn(self, message):
        if len(self.warnings) < MAX_WARNINGS:
            self.warnings.append(str(message))

    def database_work(self):
        return bool(self.cards or self.texts or self.analyses or self.figure_plans or self.legacy)

    def has_work(self):
        return self.database_work() or self.copied_files > 0


def _settle(plan, index, target, incoming, prefer_user):
    """把 incoming 落进计划：新卡片 / 合并后的卡片 / 无变化。返回 (pid, 计数键)。"""
    if not target:
        pid = str(incoming.get("id") or "")
        plan.cards[pid] = incoming
        index.put(incoming)
        return pid, "added"
    existing = index.by_pid.get(target) or incoming
    merged = _merge_card(existing, incoming, prefer_user)
    if _signature(merged) == _signature(existing):
        return target, "skipped"
    plan.cards[target] = merged
    index.put(merged)
    return target, "merged"


def _plan_text(plan, store, pid, text, bucket):
    """正文进 paper_text（键=paperId）；已有正文不覆盖（同一 pid 只留第一份）。"""
    text = str(text or "")
    if not text.strip():
        return
    if pid in plan.texts or store.has_text(pid):
        bucket["skipped"] += 1
        return
    plan.texts[pid] = text
    bucket["added"] += 1
    bucket["chars"] += len(text)


def _plan_analysis(plan, store, pid, row, bucket):
    deep = str(row.get("deep_analysis") or "").strip()
    scores = str(row.get("deep_scores") or "").strip()
    if not deep and not scores:
        return
    if (pid, "deep") in plan.analyses or store.repo.get_analysis(pid, "deep") is not None:
        bucket["skipped"] += 1
        return
    plan.analyses[(pid, "deep")] = {"analysis": _jsonish(deep), "scores": _jsonish(scores),
                                    "at": str(row.get("deep_at") or "")}
    bucket["added"] += 1


def _plan_scilib_files(plan, store, source, raw, pid, first_time):
    stats = plan.stats["libraryJson"]
    pdf = str(raw.get("pdfFile") or "").strip()
    if pdf:
        name = os.path.basename(pdf.replace("\\", "/"))
        status = _copy_file(plan, str(source["data_dir"] / "article" / "papers" / name),
                            store.path_for_paper_file(name) or "", stats["pdfs"], plan.dry_run)
        if first_time and status not in ("copied", "would", "exists"):
            plan.warn("Sci-lib 原文未复制：%s（%s）" % (name, status))
    for att in raw.get("attachments") or []:
        file = str((att or {}).get("file") or "").strip() if isinstance(att, dict) else ""
        if file:
            status = _copy_file(plan, str(source["data_dir"] / "article" / "attachments" / file),
                                store.path_for_attachment(file) or "", stats["attachments"],
                                plan.dry_run)
            if first_time and status == "missing":
                plan.warn("Sci-lib 附件不存在，未复制：%s" % file)
    text_file = source["data_dir"] / "fulltext" / (str(raw.get("id") or "") + ".txt")
    if text_file.is_file():
        try:
            _plan_text(plan, store, pid, text_file.read_text(encoding="utf-8", errors="replace"),
                       stats["texts"])
        except OSError:
            plan.warn("Sci-lib 正文读取失败：%s" % text_file.name)


def _plan_scilib(plan, store, source, index):
    stats = plan.stats["libraryJson"]
    papers = _read_scilib(source["library"])
    stats.update({"found": True, "path": str(source["library"]), "papers": len(papers)})
    for raw in papers:
        legacy_id = str(raw.get("id") or "").strip()
        if not legacy_id:
            plan.warn("Sci-lib 有一条记录没有 id，已跳过")
            continue
        incoming = _scilib_card(raw)
        mapped = store.repo.get_legacy_id(legacy_id) or ""
        target = mapped if mapped in index.by_pid else index.find(incoming)
        pid, status = _settle(plan, index, target, incoming, prefer_user=True)
        stats[status] += 1
        if mapped != pid:
            plan.legacy.append((legacy_id, pid, SOURCE_SCILIB))
        _plan_scilib_files(plan, store, source, raw, pid, first_time=not mapped)


_FIGURE_COLUMNS = (("imagePath", "image_path"), ("caption", "caption"),
                   ("contextText", "context_text"), ("aiDescription", "ai_description"),
                   ("figureType", "figure_type"), ("aiConclusion", "ai_conclusion"), ("createdAt", "created_at"))


def _figure_from_row(row):
    fig = {key: str(row.get(column) or "") for key, column in _FIGURE_COLUMNS}
    fig["figIndex"] = int(row.get("fig_index") or 0)
    return fig


def _figure_key(fig):
    return (int(fig.get("figIndex") or 0), str(fig.get("imagePath") or ""),
            " ".join(str(fig.get("caption") or "").split())[:120])


def _attach_pdf(plan, store, index, pid, title, abs_path, first_time):
    stats = plan.stats["paperLibrary"]
    name = _pdf_file_name(pid, title)
    status = _copy_file(plan, str(abs_path or "").strip(),
                        store.path_for_paper_file(name) or "", stats["pdfs"], plan.dry_run)
    if first_time and status not in ("copied", "would", "exists"):
        plan.warn("原文未复制：%s（%s）" % (abs_path or "（abs_path 为空）", status))
    card = plan.cards.get(pid)
    if card is None:
        card = index.by_pid.get(pid)
        if card is None:
            return
        card = dict(card)                      # 先拷贝：确认要改 pdfFile 才进计划
    current = str(card.get("pdfFile") or "")
    path = store.path_for_paper_file(current) or ""
    attach = status in ("copied", "would", "exists")
    if _blank(current) or (attach and not (os.path.isfile(path) and os.path.getsize(path) > 0)):
        card["pdfFile"] = name
        if pid not in plan.cards:
            plan.cards[pid] = card
            index.put(card)


def _plan_figures(plan, store, source, pid, old_id, rows, first_time, bucket):
    if not rows:
        return
    used = plan.figure_names.setdefault(pid, set())
    known = {_figure_key(_figure_from_row(row)) for row in store.repo.list_figures(pid)}
    planned = []
    for row in rows:
        fig = _figure_from_row(row)
        raw_path = str(row.get("image_path") or "").strip()
        if raw_path:
            src_abs = _resolve_media(source, raw_path)
            name = _unique_name(used, os.path.basename(src_abs or raw_path), old_id)
            status = _copy_file(plan, src_abs, str(Path(store.data_dir) / "images" / pid / name),
                                bucket, plan.dry_run)
            if first_time and status == "missing":
                plan.warn("图表图片不存在：%s" % raw_path)
            fig["imagePath"] = "images/%s/%s" % (pid, name)
        key = _figure_key(fig)
        if key in known:
            plan.stats["figures"]["skipped"] += 1
            continue
        known.add(key)
        planned.append(fig)
        plan.stats["figures"]["added"] += 1
    if planned:
        plan.figure_plans.setdefault(pid, []).extend(planned)


def _plan_paperlibrary(plan, store, source, index):
    stats = plan.stats["paperLibrary"]
    try:
        rows, figures = _read_paperlibrary(source["db"])
    except (sqlite3.Error, OSError) as exc:
        plan.warn("PaperLibrary 旧库打不开，跳过：%s" % exc)
        return
    stats.update({"found": True, "path": str(source["db"]),
                  "papers": len(rows), "figures": len(figures)})
    figures_by_paper = {}
    for row in figures:
        figures_by_paper.setdefault(str(row.get("paper_id") or ""), []).append(row)
    for row in rows:
        legacy_id = str(row.get("id") or "").strip()
        if not legacy_id:
            plan.warn("PaperLibrary 有一条记录没有 id，已跳过")
            continue
        incoming = _paperlibrary_card(row, uid())
        mapped = store.repo.get_legacy_id(legacy_id) or ""
        target = mapped if mapped in index.by_pid else index.find(incoming)
        pid, status = _settle(plan, index, target, incoming, prefer_user=False)
        stats[status] += 1
        if mapped != pid:
            plan.legacy.append((legacy_id, pid, SOURCE_PAPERLIBRARY))
        _attach_pdf(plan, store, index, pid, incoming.get("title"), row.get("abs_path"), not mapped)
        _plan_text(plan, store, pid, row.get("full_text"), stats["texts"])
        _plan_analysis(plan, store, pid, row, stats["analysis"])
        _plan_figures(plan, store, source, pid, legacy_id,
                      figures_by_paper.get(legacy_id, []), not mapped, stats["images"])


def _guard(plan, label, pid, func, *args):
    try:
        func(*args)
    except (ValueError, OSError, sqlite3.Error) as exc:
        plan.warn("%s写入失败：%s（%s）" % (label, pid, exc))


def _apply(plan, store):
    """落库：先备份，再卡片 → 正文 → 分析 → 图表 → legacy_ids。"""
    store.backup_now()
    for pid, paper in list(plan.cards.items()):
        if store.find_paper(pid) is None:
            _guard(plan, "卡片", pid, store.insert_paper, paper,
                   {"append": True, "touch": False})
        else:
            _guard(plan, "卡片", pid, store.replace_paper, pid, paper, {"touch": False})
    for pid, text in plan.texts.items():
        _guard(plan, "正文", pid, store.set_text, pid, text)
    for (pid, kind), payload in plan.analyses.items():
        _guard(plan, "分析", pid, store.repo.save_analysis, pid, kind, payload)
    for pid, figures in plan.figure_plans.items():
        combined = [_figure_from_row(row) for row in store.repo.list_figures(pid)]
        known = {_figure_key(fig) for fig in combined}
        for fig in figures:
            key = _figure_key(fig)
            if key not in known:
                known.add(key)
                combined.append(fig)
        if combined:
            store.repo.insert_figures(pid, combined, replace=True)
    for legacy_id, pid, source in plan.legacy:
        store.repo.set_legacy_id(legacy_id, pid, source)
    store.save_now()


def migrate_from_legacy(store, config, dry_run=False):
    """把 Sci-lib library.json 与 PaperLibrary papers.db 合并进 SQLite（幂等）。

    返回统计 {libraryJson, paperLibrary, papers:{added,merged,skipped},
    figures:{added,skipped}, texts:{added,skipped,chars}, ids, warnings, dryRun}。
    无事可做（旧库不存在或已迁移完）时返回 {} —— 调用方据此静默，不刷启动日志。
    """
    plan = _Plan(dry_run)
    sources = discover_sources(config)
    index = _Index(store.list_papers())
    if sources["scilib"]:
        _plan_scilib(plan, store, sources["scilib"], index)
    if sources["paperlibrary"]:
        _plan_paperlibrary(plan, store, sources["paperlibrary"], index)
    stats = plan.stats
    stats["papers"] = {key: stats["libraryJson"][key] + stats["paperLibrary"][key]
                       for key in ("added", "merged", "skipped")}
    stats["texts"] = {
        "added": len(plan.texts),
        "chars": stats["libraryJson"]["texts"]["chars"] + stats["paperLibrary"]["texts"]["chars"],
        "skipped": (stats["libraryJson"]["texts"]["skipped"]
                    + stats["paperLibrary"]["texts"]["skipped"])}
    stats["ids"] = {SOURCE_SCILIB: sum(1 for i in plan.legacy if i[2] == SOURCE_SCILIB),
                    SOURCE_PAPERLIBRARY: sum(1 for i in plan.legacy if i[2] == SOURCE_PAPERLIBRARY)}
    stats["warnings"] = list(plan.warnings)
    if dry_run:
        return stats
    if not plan.has_work():
        return {}
    _apply(plan, store)
    stats["warnings"] = list(plan.warnings)
    return stats


__all__ = ["migrate_from_legacy", "discover_sources"]
