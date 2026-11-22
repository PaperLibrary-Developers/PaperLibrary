"""存储层（对应 Node 的 server/store.js + store/accessors.js）。

SQLite 是唯一真源：启动时把 papers 全量装配进内存（检索/排序走内存），
卡片修改先提交到数据库，再更新内存缓存。

约定与 Node 版一致：
  · 访问器是唯一写入口，不做业务规则（规整字段/生成 id 在 domain/repository.py）；
  · 写方法内部已落库，调用方不要再手动 touch()；
  · find_paper 返回库里那一份记录（引用），只读使用时最省事。
"""
from __future__ import annotations

import hashlib
import json
import shutil
import threading
from pathlib import Path

from paperlib.assets import asset_path, parent_path, sha256_file
from paperlib.data.repository import Repository
from paperlib.util import ensure_dir, now_iso, read_json, safe_path_join, write_json_atomic

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
        self._by_id: dict[str, dict] = {}
        self._pos: dict[str, int] = {}
        self._pos_dirty = False
        self.meta = {"createdAt": now_iso(), "updatedAt": now_iso()}
        self._hashes: dict[str, str] = {}
        self._loaded = False

    def _rebuild_index(self):
        self._by_id = {p["id"]: p for p in self.papers if p.get("id")}
        self._pos = {p["id"]: i for i, p in enumerate(self.papers) if p.get("id")}
        self._pos_dirty = False

    # ------------------------------------------------------------------ 读/装配
    def load(self, *, migrate_legacy_dirs=True):
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
            self._rebuild_index()
            self._hashes = {p["id"]: _hash_paper(p) for p in self.papers}
            if migrate_legacy_dirs:
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
            if len(self._by_id) != len(self.papers):
                self._rebuild_index()
            return self._by_id.get(target)

    def index_of(self, paper_id):
        target = str(paper_id)
        with self._lock:
            if self._pos_dirty or len(self._pos) != len(self.papers):
                self._rebuild_index()
            idx = self._pos.get(target, -1)
            if 0 <= idx < len(self.papers) and self.papers[idx].get("id") == target:
                return idx
            self._rebuild_index()
            return self._pos.get(target, -1)

    def data_file(self):
        return str(self.repo.db_path)

    # ------------------------------------------------------------------ 写
    def paper_writes(self):
        """序列化卡片的读取、业务合并、提交以及索引更新。"""
        return self._lock

    def _persist(self, paper, previous=None):
        paper_id = paper["id"]
        digest = _hash_paper(paper)
        updated = now_iso()
        self.repo.write_papers((paper,), updated_at=updated, previous=previous)
        self._hashes[paper_id] = digest
        self.meta["updatedAt"] = updated

    def insert_paper(self, paper, options=None):
        if not paper or not paper.get("id"):
            raise ValueError("insert_paper：卡片必须有 id")
        with self._lock:
            self._persist(paper)
            pid = paper["id"]
            if options and options.get("append") is True:
                self.papers.append(paper)
                self._by_id[pid] = paper
                if not self._pos_dirty:
                    self._pos[pid] = len(self.papers) - 1
            else:
                self.papers.insert(0, paper)
                self._by_id[pid] = paper
                self._pos_dirty = True
            return paper

    def replace_paper(self, paper_id, record, options=None):
        with self._lock:
            idx = self.index_of(paper_id)
            if idx < 0:
                return None
            old_id = self.papers[idx].get("id")
            self._persist(record, self.papers[idx])
            self.papers[idx] = record
            new_id = record.get("id")
            if old_id and old_id != new_id:
                self._by_id.pop(old_id, None)
                self._pos.pop(old_id, None)
            if new_id:
                self._by_id[new_id] = record
                if not self._pos_dirty:
                    self._pos[new_id] = idx
            return record

    def patch_paper(self, paper_id, patch, options=None):
        with self._lock:
            idx = self.index_of(paper_id)
            if idx < 0:
                return None
            merged = dict(self.papers[idx])
            merged.update(patch or {})
            self._persist(merged, self.papers[idx])
            self.papers[idx] = merged
            self._by_id[merged["id"]] = merged
            return merged

    def patch_papers(self, patches):
        """同时提交多篇文献的文件位置，并在提交后更新内存。"""
        with self._lock:
            previous = {}
            records = {}
            for index, paper in enumerate(self.papers):
                patch = patches.get(paper["id"])
                if patch and any((paper.get(key) or "") != value for key, value in patch.items()):
                    previous[paper["id"]] = paper
                    records[index] = dict(paper, **patch)
            if not records:
                return 0
            updated = now_iso()
            self.repo.write_papers(records.values(), updated_at=updated, previous_by_id=previous)
            for index, record in records.items():
                self.papers[index] = record
                self._by_id[record["id"]] = record
                self._hashes[record["id"]] = _hash_paper(record)
            self.meta["updatedAt"] = updated
            return len(records)

    def remove_paper(self, paper_id, options=None):
        with self._lock:
            idx = self.index_of(paper_id)
            if idx < 0:
                return None
            paper = self.papers[idx]
            updated = now_iso()
            self.repo.write_papers(removed=(paper["id"],), updated_at=updated)
            self.papers.pop(idx)
            self._by_id.pop(paper["id"], None)
            self._pos_dirty = True
            self._hashes.pop(paper["id"], None)
            self.meta["updatedAt"] = updated
            return paper

    def replace_all(self, papers):
        with self._lock:
            records = list(papers or [])
            hashes = {p["id"]: _hash_paper(p) for p in records}
            updated = now_iso()
            self.repo.write_papers(records, self._hashes.keys() - hashes.keys(), updated)
            self.papers = records
            self._rebuild_index()
            self._hashes = hashes
            self.meta["updatedAt"] = updated

    def touch(self):
        """把内存里改过的卡片落库（内容哈希比对，只写变了的）。"""
        with self._lock:
            changed = []
            hashes = {}
            for paper in self.papers:
                pid = paper.get("id")
                if not pid:
                    continue
                digest = _hash_paper(paper)
                if digest != self._hashes.get(pid):
                    changed.append(paper)
                    hashes[pid] = digest
            if changed:
                updated = now_iso()
                self.repo.write_papers(changed, updated_at=updated)
                self._hashes.update(hashes)
                self.meta["updatedAt"] = updated
            return len(changed)

    def save_now(self):
        self.touch()
        try:
            self.repo.meta_set("updatedAt", self.meta.get("updatedAt") or now_iso())
        except Exception:
            pass

    # ------------------------------------------------------------------ 文件
    def attach_file(self, paper_id, source, options=None):
        """把原文 PDF 放进 article/papers/ 并记到卡片上（fileName 由调用方定）。"""
        name = str((options or {}).get("fileName") or "").replace("\\", "/")
        if not name:
            raise ValueError("attach_file：需要 options.fileName")
        with self._lock:
            abs_path = self.path_for_paper_file(name)
            if not abs_path:
                raise ValueError("文件名不合法")
            ensure_dir(Path(abs_path).parent)
            if isinstance(source, (bytes, bytearray)):
                with open(abs_path, "wb") as fh:
                    fh.write(bytes(source))
            else:
                shutil.copyfile(str(source), abs_path)
            paper = self.patch_paper(paper_id, {"pdfFile": name, "folder": parent_path(name),
                                              "pdfSha256": sha256_file(abs_path), "updatedAt": now_iso()}) if paper_id else None
        return {"file": name, "abs": abs_path, "paper": paper}

    def path_for_paper_file(self, file_name):
        path = asset_path(self.papers_dir, file_name)
        return str(path) if path else None

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
