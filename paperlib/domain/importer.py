"""领域层 · 导入导出与文件整理（移植自 Sci-lib server/domain/importer.js，361 行）。

职责：JSON / BibTeX / Markdown 导出、JSON 导入、PDF 目录扫描（按内容判重与孤儿）、
卡片与文件的关联、原文与补充材料的重命名（tidy）。

写卡片一律经 repository（因此索引与记录始终一致）；本模块只负责文件与格式。

契约名：export(json|bibtex|markdown) + scanPdfContents + associatePdf + tidyPdfNames
        （shared/contracts/api.md §3.3 / §3.8；响应顶层键见 api-snapshot 的 papers-scan-pdfs 等行）
"""
from __future__ import annotations

import datetime
import hashlib
import os
from pathlib import Path

from paperlib.domain.naming import (
    display_file_name,
    find_attachment_on_disk,
    next_attachment_name,
)
from paperlib.domain.repository import normalize_paper
from paperlib.util import now_iso, safe_path_join, sanitize_file_name, to_string_array
from shared.pure.citation import bibtex_key, format as format_citation


def _mtime_iso(stat):
    """与 Node 的 stat.mtime.toISOString() 同格式（UTC、毫秒、Z 结尾）。"""
    return datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _listdir(directory):
    try:
        return sorted(os.listdir(str(directory)))
    except OSError:
        return []


def _file_size(path):
    try:
        return os.stat(path).st_size
    except OSError:
        return 0


def _sha256(path):
    try:
        with open(path, "rb") as fh:
            digest = hashlib.sha256()
            while True:
                chunk = fh.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


class Importer:
    """文件与格式相关的领域逻辑（对应 Node 的 Importer）。"""

    def __init__(self, db, repository):
        self.db = db
        self.repository = repository

    # ------------------------------------------------------------------ 扫描
    def scan_pdfs(self):
        """列出 papers/ 目录下的 PDF，标记哪些已被卡片引用。

        Node 版把「全体已引用文件名」的 Set 塞进每一项（JSON 序列化成 {}）；
        这里按项给出 used 布尔值，形状见 papers-scan-pdfs 契约只在 scanPdfContents。
        """
        root = Path(str(self.db.papers_dir))
        papers = self.db.list_papers()
        out = []
        for current, _dirs, files in os.walk(str(root)):
            for name in sorted(files):
                if not name.lower().endswith(".pdf"):
                    continue
                abs_path = os.path.join(current, name)
                rel = os.path.relpath(abs_path, str(root)).replace(os.sep, "/")
                used_by = [{"id": p.get("id"), "title": p.get("title")}
                           for p in papers if p.get("pdfFile") == rel]
                out.append({
                    "file": rel,
                    "displayName": display_file_name(rel),
                    "size": _file_size(abs_path),
                    "used": bool(used_by),
                    "usedBy": used_by,
                })
        return sorted(out, key=lambda item: item["file"])

    def scan_pdf_contents(self):
        """按内容扫描原文目录：内容完全相同的重复文件 + 没有任何记录引用的孤儿。

        为什么不按文件名猜：文件名会因为「重整文件名」「导入时按标题命名」而变，
        靠名字猜归属会把别人的文件认到错误的文献上。内容哈希不会骗人。

        返回 {total, bytes, duplicateGroups, duplicates, wastedBytes, orphans, linked}。
        """
        root = Path(str(self.db.papers_dir))
        empty = {"total": 0, "bytes": 0, "duplicateGroups": [], "duplicates": 0,
                 "wastedBytes": 0, "orphans": [], "linked": []}
        if not root.is_dir():
            return empty
        files = []
        for name in _listdir(root):
            if not name.lower().endswith(".pdf"):
                continue
            abs_path = root / name
            if not abs_path.is_file():
                continue
            stat = abs_path.stat()
            files.append({"file": name, "size": stat.st_size, "mtime": _mtime_iso(stat)})

        # 内容哈希：先按大小分组——不同大小不可能同内容，省掉大部分读盘
        by_size = {}
        for item in files:
            by_size.setdefault(item["size"], []).append(item)
        for group in by_size.values():
            for item in group:
                item["sha256"] = _sha256(root / item["file"])

        by_hash = {}
        for item in files:
            if not item.get("sha256"):
                continue
            by_hash.setdefault(item["sha256"], []).append(item)

        papers = self.db.list_papers()

        def users_of(file_name):
            return [{"id": p.get("id"), "title": p.get("title")}
                    for p in papers if p.get("pdfFile") == file_name]

        duplicate_groups = []
        wasted_bytes = 0
        duplicates = 0
        for sha, group in by_hash.items():
            if len(group) < 2:
                continue
            items = []
            for item in group:
                entry = dict(item)
                entry["usedBy"] = users_of(item["file"])
                items.append(entry)
            items.sort(key=lambda x: (-len(x["usedBy"]), str(x["mtime"])))
            # 建议保留：优先「被记录引用的那份」，否则保留最早的那个
            keep = next((x for x in items if x["usedBy"]), items[0])
            removable = [x for x in items if x is not keep]
            # 被记录引用的文件绝不列入「可删」，即使它内容重复
            safe = [x for x in removable if not x["usedBy"]]
            duplicates += len(safe)
            wasted_bytes += sum(int(x["size"]) for x in safe)
            duplicate_groups.append({
                "sha256": sha[:16],
                "size": items[0]["size"],
                "keep": keep["file"],
                "keepReason": "有记录引用它" if keep["usedBy"] else "保留最早的一份",
                "items": items,
                "removable": [x["file"] for x in safe],
                "blocked": [{"file": x["file"], "usedBy": x["usedBy"]}
                            for x in removable if x["usedBy"]],
            })
        duplicate_groups.sort(key=lambda g: -int(g["size"]))

        orphans = []
        linked = []
        for item in files:
            used_by = users_of(item["file"])
            entry = dict(item)
            entry["usedBy"] = used_by
            (linked if used_by else orphans).append(entry)
        orphans.sort(key=lambda x: -int(x["size"]))
        linked.sort(key=lambda x: x["file"])

        return {
            "total": len(files),
            "bytes": sum(int(f["size"]) for f in files),
            "duplicateGroups": duplicate_groups,
            "duplicates": duplicates,
            "wastedBytes": wasted_bytes,
            "orphans": orphans,
            "linked": linked,
        }

    # ------------------------------------------------------------------ 关联
    def associate_pdf(self, paper_id, file):
        """把一个已存在的 PDF 关联给某篇文献（文件名不参与猜测，由调用方指定）。"""
        paper = self.repository.get(paper_id)
        if not paper:
            return {"ok": False, "error": "找不到这条文献"}
        rel = str(file or "").lstrip("/\\")
        if not rel or not rel.lower().endswith(".pdf"):
            return {"ok": False, "error": "文件名不合法"}
        abs_path = safe_path_join(self.db.papers_dir, rel)
        if not abs_path or not os.path.isfile(abs_path):
            return {"ok": False, "error": "文件不存在：" + rel}
        updated = self.repository.update(paper_id, {"pdfFile": rel})
        return {"ok": True, "paper": updated, "file": rel}

    # ------------------------------------------------------------------ 整理
    def tidy_pdf_names(self):
        """把 papers/ 里现存 PDF 的文件名统一重整（去 id 前缀、去非法字符、同名加序号）。"""
        root = Path(str(self.db.papers_dir))
        results = []
        files = [name for name in _listdir(root) if name.lower().endswith(".pdf")]
        if not files:
            return {"changed": 0, "total": 0, "results": results}
        taken = set(files)
        for old_name in files:
            paper = next((p for p in self.db.list_papers() if p.get("pdfFile") == old_name), None)
            # 目标名：优先用文献标题，其次用去掉前缀的原名
            if paper and paper.get("title"):
                base = sanitize_file_name(paper.get("title"), 70)
            else:
                stripped = old_name
                if old_name[:8].lower() and stripped[:8].lower() == old_name[:8].lower():
                    parts = stripped.split("-", 1)
                    if len(parts) == 2 and len(parts[0]) >= 8 and all(
                            ch in "0123456789abcdefABCDEF" for ch in parts[0]):
                        stripped = parts[1]
                base = sanitize_file_name(os.path.splitext(stripped)[0], 70)
            nxt = base + ".pdf"
            if nxt == old_name:
                results.append({"old": old_name, "next": nxt, "changed": False, "reason": "已经规范"})
                continue
            candidate = nxt
            n = 2
            while candidate in taken and candidate != old_name:
                candidate = base + " (" + str(n) + ").pdf"
                n += 1
            nxt = candidate
            try:
                os.rename(str(root / old_name), str(root / nxt))
            except OSError as exc:
                results.append({"old": old_name, "next": nxt, "changed": False,
                                "reason": "重命名失败：" + str(exc)})
                continue
            taken.discard(old_name)
            taken.add(nxt)
            if paper:
                self.repository.update(paper.get("id"), {"pdfFile": nxt})
            results.append({"old": old_name, "next": nxt, "changed": True,
                            "paperId": paper.get("id") if paper else None})
        return {"changed": len([r for r in results if r["changed"]]),
                "total": len(files), "results": results}

    def tidy_attachment_names(self, on_renamed=None):
        """重整补充材料（SI）的文件名：<文献id>-<乱名>.pdf → <文献id>-<标题> SI.pdf。

        附件摊平放在 data/article/attachments/ 一层里，<文献id>- 前缀既是归属标记，
        也避免不同文献的同名 SI 互相覆盖。on_renamed(paperId, old, new) 用于同步抽取文本。
        """
        root = Path(str(self.db.attachments_dir))
        results = []
        files = _listdir(root)
        if not root.is_dir():
            return {"changed": 0, "total": 0, "results": results, "repaired": 0}
        taken = set(files)
        # 记录里认得出来的文件先从 taken 摘掉（它们马上会被处理成自己的目标名）
        for paper in self.db.list_papers():
            for att in paper.get("attachments") or []:
                name = str((att or {}).get("file") or "")
                if name:
                    taken.discard(name)
        repaired = 0
        for paper in self.db.list_papers():
            attachments = paper.get("attachments") if isinstance(paper.get("attachments"), list) else []
            if not attachments:
                continue
            for index, att in enumerate(attachments):
                old_name = str(att.get("file") or "")
                repaired_note = ""
                # 自愈：记录里的文件名对不上盘上的文件时，按 <文献id>- 前缀认领回来
                if not old_name or not (root / old_name).is_file():
                    found = find_attachment_on_disk(str(root), paper)
                    if not found:
                        results.append({"old": old_name, "next": "", "changed": False,
                                        "paperId": paper.get("id"), "reason": "文件不在了"})
                        continue
                    repaired_note = "记录与文件对不上，已按 id 前缀认领：" + found
                    att["file"] = found
                    old_name = found
                    self.db.touch()
                    repaired += 1
                nxt = next_attachment_name(paper, att, index, taken)
                if nxt == old_name:
                    results.append({"old": old_name, "next": nxt, "changed": False,
                                    "paperId": paper.get("id"),
                                    "reason": repaired_note or "已经规范",
                                    "repaired": bool(repaired_note)})
                    continue
                try:
                    os.rename(str(root / old_name), str(root / nxt))
                except OSError as exc:
                    results.append({"old": old_name, "next": nxt, "changed": False,
                                    "paperId": paper.get("id"), "reason": "重命名失败：" + str(exc)})
                    continue
                taken.discard(old_name)
                taken.add(nxt)
                att["file"] = nxt
                self.db.touch()
                if callable(on_renamed):
                    try:
                        on_renamed(paper.get("id"), old_name, nxt)
                    except Exception as exc:  # noqa: BLE001
                        print("[attachments] 回调失败：" + str(exc))
                # 一条附件只推一条结果（自愈 + 改名合并成一条），避免 total 虚高
                results.append({"old": old_name, "next": nxt, "changed": True,
                                "paperId": paper.get("id"),
                                "repaired": bool(repaired_note), "reason": repaired_note})
        return {"changed": len([r for r in results if r["changed"]]),
                "total": len(results), "results": results, "repaired": repaired}

    # ------------------------------------------------------------------ 导出
    def export_json(self):
        return {
            "version": 1,
            "exportedAt": now_iso(),
            "app": "literature-vault",
            "papers": self.db.list_papers(),
        }

    def export_bibtex(self, ids=None):
        wanted = set(to_string_array(ids)) if to_string_array(ids) else None
        papers = self.db.list_papers()
        picked = [p for p in papers if p.get("id") in wanted] if wanted else list(papers)
        used = set()
        chunks = []
        for paper in picked:
            key = str(paper.get("bibtexKey") or bibtex_key(paper))
            unique = key
            n = 1
            while unique in used:
                n += 1
                unique = key + chr(96 + n)
            used.add(unique)
            merged = dict(paper)
            merged["bibtexKey"] = unique
            chunks.append(format_citation(merged, "bibtex"))
        return "\n\n".join(chunks) + "\n"

    def export_markdown(self, ids=None):
        wanted = set(to_string_array(ids)) if to_string_array(ids) else None
        papers = self.db.list_papers()
        picked = [p for p in papers if p.get("id") in wanted] if wanted else list(papers)
        return "\n\n---\n\n".join(format_citation(p, "markdown") for p in picked) + "\n"

    # ------------------------------------------------------------------ 导入
    def import_json(self, payload, options=None):
        """导入：合并策略——同 DOI 或同标题视为重复，overwrite 决定是否覆盖。"""
        opts = options or {}
        if isinstance(payload, list):
            incoming = payload
        elif isinstance(payload, dict) and isinstance(payload.get("papers"), list):
            incoming = payload.get("papers")
        else:
            raise ValueError("导入内容里找不到 papers 数组")
        added = 0
        updated = 0
        skipped = 0
        for raw in incoming:
            norm = normalize_paper(raw, None)
            dup = self.repository.find_duplicate(norm)
            if dup:
                if opts.get("overwrite"):
                    merged = dict(norm)
                    merged["id"] = dup.get("id")
                    merged["addedAt"] = dup.get("addedAt")
                    self.repository.update(dup.get("id"), merged)
                    updated += 1
                else:
                    skipped += 1
            else:
                self.repository.insert_normalized(norm, {"touch": False})
                added += 1
        if added or updated:
            self.db.touch()
        return {"added": added, "updated": updated, "skipped": skipped, "total": len(incoming)}
