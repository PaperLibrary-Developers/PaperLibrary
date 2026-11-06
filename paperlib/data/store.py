"""存储层（对应 Node 的 server/store.js + store/accessors.js）。

SQLite 是唯一真源：启动时把 papers 全量装配进内存（检索/排序走内存），
所有写操作经这里落库；嵌套字段（attachments/chat）被外部改动后调 touch()，
touch() 用内容哈希找出确实变了的卡片再落库（省掉无意义的写）。

约定与 Node 版一致：
  · 访问器是唯一写入口，不做业务规则（规整字段/生成 id 在 domain/repository.py）；
  · 写方法内部已落库，调用方不要再手动 touch()；批量写可用 {touch: False} 后统一 touch()；
  · find_paper 返回库里那一份记录（引用），只读使用时最省事。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
from pathlib import Path

from paperlib.data.repository import Repository
from paperlib.util import dump_json, ensure_dir, now_iso, read_json, safe_path_join, write_json_atomic

DATA_VERSION = 1


def _hash_paper(paper) -> str:
    payload = json.dumps(paper, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


class Store:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        self.article_dir = self.data_dir / "article"
        self.papers_dir = self.article_dir / "papers"
        self.attachments_dir = self.article_dir / "attachments"
        self.fulltext_dir = self.data_dir / "fulltext"
        self.backup_dir = self.data_dir / "backups"
        self.tmp_import_dir = self.data_dir / "tmp-import"
        self.library_file = self.data_dir / "library.json"
        self.repo = Repository(self.data_dir)
        self._lock = threading.RLock()
        self.papers: list[dict] = []
        self.meta = {"createdAt": now_iso(), "updatedAt": now_iso()}
        self._hashes: dict[str, str] = {}
        self._loaded = False

    # ------------------------------------------------------------------ 读/装配
    def load(self):
        with self._lock:
            ensure_dir(self.data_dir)
            ensure_dir(self.papers_dir)
            ensure_dir(self.attachments_dir)
            ensure_dir(self.backup_dir)
            ensure_dir(self.fulltext_dir)
            ensure_dir(self.tmp_import_dir)
            created = self.repo.meta_get("createdAt") or now_iso()
            self.meta = {"createdAt": created, "updatedAt": self.repo.meta_get("updatedAt") or now_iso()}
            self.papers = self.repo.list_papers()
            self._hashes = {p["id"]: _hash_paper(p) for p in self.papers}
            self.migrate_legacy_dirs()
            self._loaded = True
            return self.papers

    @property
    def state(self):
        return {
            "version": DATA_VERSION,
            "papers": self.papers,
            "meta": self.meta,
        }

    def count(self):
        return len(self.papers)

    def list_papers(self):
        with self._lock:
            return list(self.papers)

    def find_paper(self, paper_id):
        target = str(paper_id)
        with self._lock:
            for paper in self.papers:
                if paper.get("id") == target:
                    return paper
        return None

    def index_of(self, paper_id):
        target = str(paper_id)
        for i, paper in enumerate(self.papers):
            if paper.get("id") == target:
                return i
        return -1

    def data_file(self):
        return str(self.repo.db_path)

    # ------------------------------------------------------------------ 写
    def _persist(self, paper):
        self.repo.save_paper(paper)
        self._hashes[paper["id"]] = _hash_paper(paper)

    def _commit(self, options=None, touched_ids=None):
        if options and options.get("touch") is False:
            self.meta["updatedAt"] = now_iso()
            return
        self.touch()

    def insert_paper(self, paper, options=None):
        if not paper or not paper.get("id"):
            raise ValueError("insert_paper：卡片必须有 id")
        with self._lock:
            if options and options.get("append") is True:
                self.papers.append(paper)
            else:
                self.papers.insert(0, paper)
            self._persist(paper)
            self._commit(options)
            return paper

    def replace_paper(self, paper_id, record, options=None):
        with self._lock:
            idx = self.index_of(paper_id)
            if idx < 0:
                return None
            self.papers[idx] = record
            self._persist(record)
            self._commit(options)
            return record

    def patch_paper(self, paper_id, patch, options=None):
        with self._lock:
            idx = self.index_of(paper_id)
            if idx < 0:
                return None
            merged = dict(self.papers[idx])
            merged.update(patch or {})
            self.papers[idx] = merged
            self._persist(merged)
            self._commit(options)
            return merged

    def remove_paper(self, paper_id, options=None):
        with self._lock:
            idx = self.index_of(paper_id)
            if idx < 0:
                return None
            paper = self.papers.pop(idx)
            self.repo.delete_paper(paper["id"])
            self._hashes.pop(paper["id"], None)
            self._commit(options)
            return paper

    def replace_all(self, papers):
        with self._lock:
            self.papers = list(papers or [])
            for paper in self.papers:
                self._persist(paper)
            ids = {p.get("id") for p in self.papers}
            for stale in [pid for pid in self._hashes if pid not in ids]:
                self.repo.delete_paper(stale)
                self._hashes.pop(stale, None)
            self.meta["updatedAt"] = now_iso()
            self.repo.meta_set("updatedAt", self.meta["updatedAt"])

    def touch(self):
        """把内存里改过的卡片落库（内容哈希比对，只写变了的）。"""
        with self._lock:
            changed = 0
            for paper in self.papers:
                pid = paper.get("id")
                if not pid:
                    continue
                if _hash_paper(paper) == self._hashes.get(pid):
                    continue
                self.repo.save_paper(paper)
                self._hashes[pid] = _hash_paper(paper)
                changed += 1
            if changed:
                self.meta["updatedAt"] = now_iso()
                self.repo.meta_set("updatedAt", self.meta["updatedAt"])
            return changed

    def save_now(self):
        self.touch()
        try:
            self.repo.meta_set("updatedAt", self.meta.get("updatedAt") or now_iso())
        except Exception:
            pass

    # ------------------------------------------------------------------ 文件
    def attach_file(self, paper_id, source, options=None):
        """把原文 PDF 放进 article/papers/ 并记到卡片上（fileName 由调用方定）。"""
        name = str((options or {}).get("fileName") or "").strip()
        if not name:
            raise ValueError("attach_file：需要 options.fileName")
        abs_path = safe_path_join(self.papers_dir, name)
        if not abs_path:
            raise ValueError("文件名不合法")
        ensure_dir(Path(abs_path).parent)
        if isinstance(source, (bytes, bytearray)):
            with open(abs_path, "wb") as fh:
                fh.write(bytes(source))
        else:
            shutil.copyfile(str(source), abs_path)
        paper = self.patch_paper(paper_id, {"pdfFile": name, "updatedAt": now_iso()}) if paper_id else None
        return {"file": name, "abs": abs_path, "paper": paper}

    def path_for_paper_file(self, file_name):
        return safe_path_join(self.papers_dir, file_name)

    def path_for_attachment(self, file_name):
        return safe_path_join(self.attachments_dir, file_name)

    # ------------------------------------------------------------------ 全文
    def get_text(self, key):
        return self.repo.get_text(str(key))

    def set_text(self, key, text):
        self.repo.set_text(str(key), text)
        try:
            ensure_dir(self.fulltext_dir)
            safe_key = str(key).replace(":", "-").replace("/", "-").replace("\\", "-")
            with open(self.fulltext_dir / (safe_key + ".txt"), "w", encoding="utf-8") as fh:
                fh.write(str(text or ""))
        except OSError:
            pass

    def has_text(self, key):
        return self.repo.text_chars(str(key)) > 0

    def text_chars(self, key):
        return self.repo.text_chars(str(key))

    def list_text_keys(self):
        return self.repo.list_text_keys()

    # ------------------------------------------------------------------ 旧目录迁移
    def migrate_legacy_dirs(self):
        """把老布局搬进 data/article/（幂等）：papers/*、attachments/<id>/*。"""
        report = {"papers": 0, "attachments": 0}
        old_papers = self.data_dir / "papers"
        old_attachments = self.data_dir / "attachments"
        if old_papers.is_dir():
            ensure_dir(self.papers_dir)
            for entry in old_papers.iterdir():
                if not entry.is_file():
                    continue
                target = self.papers_dir / entry.name
                try:
                    if not target.exists():
                        shutil.move(str(entry), str(target))
                        report["papers"] += 1
                except OSError as exc:
                    print("[migrate] 原文搬移失败：%s → %s" % (entry.name, exc))
            try:
                old_papers.rmdir()
            except OSError:
                pass
        if old_attachments.is_dir():
            for sub in list(old_attachments.iterdir()):
                if sub.is_dir():
                    for file in list(sub.iterdir()):
                        if not file.is_file():
                            continue
                        flat = sub.name + "-" + file.name
                        target = self.attachments_dir / flat
                        try:
                            ensure_dir(self.attachments_dir)
                            if target.exists():
                                continue
                            shutil.move(str(file), str(target))
                            report["attachments"] += 1
                        except OSError as exc:
                            print("[migrate] 附件搬移失败：%s → %s" % (file.name, exc))
                    try:
                        sub.rmdir()
                    except OSError:
                        pass
                elif sub.is_file():
                    try:
                        ensure_dir(self.attachments_dir)
                        target = self.attachments_dir / sub.name
                        if not target.exists():
                            shutil.move(str(sub), str(target))
                            report["attachments"] += 1
                    except OSError:
                        pass
            try:
                old_attachments.rmdir()
            except OSError:
                pass
        if report["papers"] or report["attachments"]:
            print("[migrate] 已把老目录搬进 data/article/：原文 %d 个，补充材料 %d 个"
                  % (report["papers"], report["attachments"]))
        return report

    # ------------------------------------------------------------------ 配置/备份
    def read_prefs(self):
        return read_json(self.data_dir / "prefs.json", {}) or {}

    def write_prefs(self, patch):
        merged = dict(self.read_prefs())
        merged.update(patch or {})
        write_json_atomic(self.data_dir / "prefs.json", merged)
        return merged

    def backup_now(self):
        """把当前库导出成一份 library.json 备份（沿用老格式，便于回退）。"""
        try:
            ensure_dir(self.backup_dir)
            stamp = now_iso().replace(":", "-").replace(".", "-")
            target = self.backup_dir / ("library-" + stamp + ".json")
            write_json_atomic(target, {
                "version": DATA_VERSION,
                "papers": self.papers,
                "meta": self.meta,
            })
            files = sorted(self.backup_dir.glob("library-*.json"))
            for victim in files[:-10]:
                try:
                    victim.unlink()
                except OSError:
                    pass
            return str(target)
        except OSError as exc:
            print("[store] 备份失败：%s" % exc)
            return None
