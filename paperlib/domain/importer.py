"""领域层 · 导入导出与文件整理（移植自 Sci-lib server/domain/importer.js，361 行）。

职责：JSON / BibTeX / Markdown 导出、JSON 导入、PDF 目录扫描（按内容判重与孤儿）、
卡片与文件的关联、原文与补充材料的重命名（tidy）。

写卡片一律经 repository（因此索引与记录始终一致）；本模块只负责文件与格式。

契约名：export(json|bibtex|markdown) + scanPdfContents + associatePdf + tidyPdfNames
        （shared/contracts/api.md §3.3 / §3.8；响应顶层键见 api-snapshot 的 papers-scan-pdfs 等行）
"""
from __future__ import annotations

import datetime
import os
from collections import defaultdict
from pathlib import Path
from threading import Lock

from paperlib.assets import asset_path, parent_path, sha256_file, unique_path, walk_assets
from paperlib.domain import pdf_import
from paperlib.domain.folders import Folders
from paperlib.domain.naming import (
    display_file_name,
    find_attachment_on_disk,
    next_attachment_name,
)
from paperlib.domain.repository import normalize_paper
from paperlib.util import now_iso, sanitize_file_name, to_string_array
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


def _file_key(path):
    return os.path.normcase(str(path or "").replace("\\", "/"))


class Importer:
    """文件与格式相关的领域逻辑（对应 Node 的 Importer）。"""

    def __init__(self, db, repository):
        self.db = db
        self.repository = repository
        self._scan_lock = Lock()

    # ------------------------------------------------------------------ 扫描
    def scan_pdfs(self):
        """列出 papers/ 目录下的 PDF，标记哪些已被卡片引用。

        Node 版把「全体已引用文件名」的 Set 塞进每一项（JSON 序列化成 {}）；
        这里按项给出 used 布尔值，形状见 papers-scan-pdfs 契约只在 scanPdfContents。
        """
        root = Path(str(self.db.papers_dir))
        papers = self.db.list_papers()
        out = []
        for _, files in walk_assets(root):
            for rel in files:
                abs_path = root / rel
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

    def _pdf_files(self):
        root = self.db.papers_dir
        for _, paths in walk_assets(root):
            for relative in paths:
                path = asset_path(root, relative)
                try:
                    info = path.stat() if path is not None else None
                except OSError:
                    continue
                if info is not None:
                    yield {"file": relative, "size": info.st_size, "mtime": _mtime_iso(info),
                           "sha256": sha256_file(path)}

    def scan_pdf_contents(self, files=None):
        """按内容扫描原文目录：内容完全相同的重复文件 + 没有任何记录引用的孤儿。

        为什么不按文件名猜：文件名会因为「重整文件名」「导入时按标题命名」而变，
        靠名字猜归属会把别人的文件认到错误的文献上。内容哈希不会骗人。

        返回 {total, bytes, duplicateGroups, duplicates, wastedBytes, orphans, linked}。
        """
        files = list(self._pdf_files()) if files is None else files

        by_hash = {}
        for item in files:
            if not item.get("sha256"):
                continue
            by_hash.setdefault(item["sha256"], []).append(item)

        references = defaultdict(list)
        for paper in self.db.list_papers():
            references[_file_key(paper.get("pdfFile"))].append({"id": paper.get("id"), "title": paper.get("title")})

        def users_of(file_name):
            return references[_file_key(file_name)]

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

    def sync_pdf_directory(self, services):
        """同步已移动的原文并就地登记新 PDF，扫描不会复制或改名。"""
        with self._scan_lock:
            files = list(self._pdf_files())
            with self.db.paper_writes():
                references, digests, relinked = self._reconcile_pdf_locations(files)
                services.folders.sync_locations()
            imported, failed = [], []
            offline = {"lookup_doi": lambda _doi: None, "fetch_openalex_abstract": lambda _doi: ""}
            for item in files:
                relative, digest = item["file"], item["sha256"]
                if _file_key(relative) in references or (digest and digest in digests):
                    continue
                path = asset_path(self.db.papers_dir, relative)
                if not digest or path is None or not path.is_file():
                    failed.append({"file": relative, "error": "无法读取 PDF"})
                    continue
                try:
                    parsed = pdf_import.parse_pdf(services, path, offline)
                    if not parsed.get("ok"):
                        raise ValueError(parsed.get("warning") or "无法打开 PDF")
                    draft = pdf_import.draft_from_parsed(services, parsed, deps=offline)
                    with self.db.paper_writes():
                        current = self.db.list_papers()
                        if any(_file_key(p.get("pdfFile")) == _file_key(relative) for p in current):
                            continue
                        if any(p.get("pdfSha256") == digest for p in current):
                            raise ValueError("内容与已登记的原文相同，请在下方核对关联")
                        path = asset_path(self.db.papers_dir, relative)
                        info = path.stat() if path is not None else None
                        if info is None or info.st_size != item["size"] or _mtime_iso(info) != item["mtime"]:
                            raise ValueError("PDF 已变更，请重新扫描")
                        card = self.repository.create(dict(draft, folder=parent_path(relative),
                                                           pdfFile=relative, pdfSha256=digest))
                except (OSError, ValueError) as exc:
                    failed.append({"file": relative, "error": str(exc)})
                    continue
                imported.append({"id": card["id"], "file": relative})
                digests.add(digest)
                services.extract_imported_pdf(card)
            return {"scan": self.scan_pdf_contents(files), "imported": imported, "relinked": relinked, "failed": failed}

    def _reconcile_pdf_locations(self, files):
        inventory = {_file_key(item["file"]): item for item in files}
        papers = [paper for paper in self.db.list_papers() if paper.get("pdfFile")]
        referenced = {_file_key(paper["pdfFile"]) for paper in papers if _file_key(paper["pdfFile"]) in inventory}
        unclaimed = defaultdict(list)
        by_id_prefix = defaultdict(list)
        for key, item in inventory.items():
            if key not in referenced:
                if item["sha256"]:
                    unclaimed[item["sha256"]].append(item)
                name = Path(item["file"]).name
                by_id_prefix[name.partition("-")[0]].append(item)
        patches = {}
        recovered = {}
        relinked = 0
        digests = set()
        for paper in papers:
            previous = _file_key(paper["pdfFile"])
            item = inventory.get(previous)
            if item is None:
                candidates = unclaimed.get(paper.get("pdfSha256"), ())
                if not candidates and not paper.get("pdfSha256"):
                    candidates = by_id_prefix.get(paper["id"], ())
                item = recovered.get(previous)
                if item is None and len(candidates) == 1 and _file_key(candidates[0]["file"]) not in referenced:
                    item = recovered[previous] = candidates[0]
                if item is not None:
                    relinked += 1
            if item is None:
                continue
            relative, digest = item["file"], item["sha256"]
            referenced.add(_file_key(relative))
            if digest:
                digests.add(digest)
            patches[paper["id"]] = {"pdfFile": relative, "folder": parent_path(relative),
                                    "pdfSha256": digest or paper.get("pdfSha256") or ""}
        self.db.patch_papers(patches)
        return referenced, digests, relinked

    # ------------------------------------------------------------------ 关联
    def associate_pdf(self, paper_id, file):
        """把一个已存在的 PDF 关联给某篇文献（文件名不参与猜测，由调用方指定）。"""
        with self.db.paper_writes():
            return self._associate_pdf(paper_id, file)

    def _associate_pdf(self, paper_id, file):
        paper = self.repository.get(paper_id)
        if not paper:
            return {"ok": False, "error": "找不到这条文献"}
        rel = str(file or "").lstrip("/\\")
        if not rel or not rel.lower().endswith(".pdf"):
            return {"ok": False, "error": "文件名不合法"}
        abs_path = self.db.path_for_paper_file(rel)
        if not abs_path or not os.path.isfile(abs_path):
            return {"ok": False, "error": "文件不存在：" + rel}
        rel = rel.replace("\\", "/")
        updated = self.repository.update(paper_id, {"pdfFile": rel, "folder": parent_path(rel),
                                                    "pdfSha256": sha256_file(abs_path)})
        return {"ok": True, "paper": updated, "file": rel}

    # ------------------------------------------------------------------ 整理
    def tidy_pdf_names(self):
        """在原分类目录内重整每个 PDF 的文件名。"""
        with self.db.paper_writes():
            return self._tidy_pdf_names()

    def _tidy_pdf_names(self):
        root = self.db.papers_dir
        results = []
        references = defaultdict(list)
        for paper in self.db.list_papers():
            references[_file_key(paper.get("pdfFile"))].append(paper)
        files = [name for _, names in walk_assets(root) for name in names]
        for old_name in files:
            source = root / old_name
            papers = references[_file_key(old_name)]
            paper = papers[0] if papers else None
            # 目标名：优先用文献标题，其次用去掉前缀的原名
            if paper and paper.get("title"):
                base = sanitize_file_name(paper.get("title"), 70)
            else:
                stripped = source.name
                parts = stripped.split("-", 1)
                if len(parts) == 2 and len(parts[0]) >= 8 and all(
                        ch in "0123456789abcdefABCDEF" for ch in parts[0]):
                    stripped = parts[1]
                base = sanitize_file_name(os.path.splitext(stripped)[0], 70)
            target = source.with_name(base + ".pdf")
            if source == target:
                results.append({"old": old_name, "next": old_name, "changed": False, "reason": "已经规范"})
                continue
            target = unique_path(target)
            nxt = target.relative_to(root).as_posix()
            try:
                source.rename(target)
            except OSError as exc:
                results.append({"old": old_name, "next": nxt, "changed": False,
                                "reason": "重命名失败：" + str(exc)})
                continue
            try:
                self.db.patch_papers({p["id"]: {"pdfFile": nxt, "folder": parent_path(nxt)} for p in papers})
            except Exception:
                target.rename(source)
                raise
            results.append({"old": old_name, "next": nxt, "changed": True,
                            "paperId": paper.get("id") if paper else None})
        return {"changed": len([r for r in results if r["changed"]]),
                "total": len(files), "results": results}

    def tidy_attachment_names(self, on_renamed=None):
        """重整补充材料（SI）的文件名：<文献id>-<乱名>.pdf → <文献id>-<标题> SI.pdf。

        附件摊平放在 data/article/attachments/ 一层里，<文献id>- 前缀既是归属标记，
        也避免不同文献的同名 SI 互相覆盖。on_renamed(paperId, old, new) 用于同步抽取文本。
        """
        with self.db.paper_writes():
            return self._tidy_attachment_names(on_renamed)

    def _tidy_attachment_names(self, on_renamed):
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
            paper_id = paper["id"]
            attachments = [dict(att) for att in (paper.get("attachments") or [])]
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
                    self.db.patch_paper(paper_id, {"attachments": [dict(a) for a in attachments]})
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
                att["file"] = nxt
                try:
                    self.db.patch_paper(paper_id, {"attachments": [dict(a) for a in attachments]})
                except Exception:
                    os.rename(str(root / nxt), str(root / old_name))
                    raise
                taken.discard(old_name)
                taken.add(nxt)
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
                self.repository.insert_normalized(norm)
                added += 1
        Folders(self.db).sync_locations()
        return {"added": added, "updated": updated, "skipped": skipped, "total": len(incoming)}
