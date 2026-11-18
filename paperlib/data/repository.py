"""领域数据访问（SQL 只在这里出现）。

对上层暴露「论文全量记录」：papers.data 是完整卡片 JSON，attachments/chat/tags 从
各自的表装配回来（它们在库里是规范化的，在 API 形状里是内嵌数组）。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from paperlib.data import schema
from paperlib.util import ensure_dir, now_iso


def _loads(text, fallback):
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return fallback


class Repository:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir)
        ensure_dir(self.data_dir)
        self.db_path = self.data_dir / "library.db"
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        schema.init_schema(self.conn)

    # ------------------------------------------------------------------ 基础
    def close(self):
        with self._lock:
            self.conn.close()

    def meta_get(self, key, default=None):
        with self._lock:
            row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        if row is None:
            return default
        return row["value"]

    def meta_set(self, key, value):
        with self._lock, self.conn:
            self._set_meta(key, value)

    def _set_meta(self, key, value):
        self.conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, "" if value is None else str(value)),
        )

    # ------------------------------------------------------------------ 论文
    def _assemble(self, row):
        paper = _loads(row["data"], {})
        paper["id"] = row["id"]
        paper["title"] = row["title"]
        paper["year"] = row["year"]
        paper["venue"] = row["venue"] or paper.get("venue", "")
        paper["type"] = row["type"]
        paper["status"] = row["status"]
        paper["rating"] = row["rating"]
        paper["starred"] = bool(row["starred"])
        if row["folder"]:
            paper["folder"] = row["folder"]
        else:
            # folder 列是唯一真源：列空了就必须把 JSON 里的旧值删掉，
            # 否则「移动到根目录」会被 data JSON 里的旧路径盖回去。
            paper.pop("folder", None)
        if row["arxiv_id"]:
            paper["arxivId"] = row["arxiv_id"]
        paper["doi"] = row["doi"]
        paper["pdfFile"] = row["pdf_file"]
        paper["addedAt"] = row["added_at"]
        paper["updatedAt"] = row["updated_at"]
        stored_tags = paper.get("tags")
        if isinstance(stored_tags, list) and stored_tags:
            paper["tags"] = [str(t) for t in stored_tags]
        else:
            paper["tags"] = [r["tag"] for r in self.conn.execute(
                "SELECT tag FROM paper_tags WHERE paper_id = ?", (row["id"],))]
        paper["attachments"] = [dict(r) for r in self.conn.execute(
            "SELECT file, original_name, size, kind, has_text, text_chars, added_at "
            "FROM attachments WHERE paper_id = ? ORDER BY rowid", (row["id"],))]
        for att in paper["attachments"]:
            att["originalName"] = att.pop("original_name")
            att["hasText"] = bool(att.pop("has_text"))
            att["textChars"] = att.pop("text_chars")
            att["addedAt"] = att.pop("added_at")
        paper["chat"] = self._load_chat(row["id"])
        return paper

    def _load_chat(self, paper_id):
        out = []
        for r in self.conn.execute(
                "SELECT role, content, at, model, sources, images, usage "
                "FROM chat_messages WHERE paper_id = ? ORDER BY id", (paper_id,)):
            msg = {
                "role": r["role"], "content": r["content"], "at": r["at"],
                "model": r["model"], "sources": _loads(r["sources"], []),
                "images": int(r["images"] or 0),
            }
            usage = _loads(r["usage"], None) if r["usage"] else None
            msg["usage"] = usage
            out.append(msg)
        return out

    def list_papers(self):
        with self._lock:
            rows = self.conn.execute("SELECT * FROM papers ORDER BY added_at DESC, rowid DESC").fetchall()
            return [self._assemble(r) for r in rows]

    def get_paper(self, paper_id):
        with self._lock:
            row = self.conn.execute("SELECT * FROM papers WHERE id = ?", (paper_id,)).fetchone()
            return self._assemble(row) if row else None

    def save_paper(self, paper):
        self.write_papers((paper,))

    def write_papers(self, papers=(), removed=(), updated_at=None):
        """Commit card records, related rows and library metadata together."""
        with self._lock, self.conn:
            for paper in papers:
                self._save_paper(paper)
            for paper_id in removed:
                self._delete_paper(paper_id)
            if updated_at is not None:
                self._set_meta("updatedAt", updated_at)

    def _save_paper(self, paper):
        data = dict(paper)
        data.pop("attachments", None)
        data.pop("chat", None)
        self.conn.execute(
            "INSERT INTO papers(id,title,year,venue,journal,type,status,rating,starred,folder,"
            "arxiv_id,doi,pdf_file,added_at,updated_at,data) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET title=excluded.title, year=excluded.year, "
            "venue=excluded.venue, journal=excluded.journal, type=excluded.type, "
            "status=excluded.status, rating=excluded.rating, starred=excluded.starred, "
            "folder=excluded.folder, arxiv_id=excluded.arxiv_id, doi=excluded.doi, "
            "pdf_file=excluded.pdf_file, added_at=excluded.added_at, "
            "updated_at=excluded.updated_at, data=excluded.data",
            (
                paper.get("id"), paper.get("title") or "", paper.get("year"),
                paper.get("venue") or "", paper.get("journal") or "",
                paper.get("type") or "journal", paper.get("status") or "unread",
                int(paper.get("rating") or 0), 1 if paper.get("starred") else 0,
                paper.get("folder") or "", paper.get("arxivId") or "",
                paper.get("doi") or "", paper.get("pdfFile") or "",
                paper.get("addedAt") or "", paper.get("updatedAt") or "",
                json.dumps(data, ensure_ascii=False),
            ),
        )
        self._save_tags(paper.get("id"), paper.get("tags") or [])
        self._save_attachments(paper.get("id"), paper.get("attachments") or [])
        if "chat" in paper:
            self._save_chat(paper.get("id"), paper.get("chat") or [])

    def _save_tags(self, paper_id, tags):
        self.conn.execute("DELETE FROM paper_tags WHERE paper_id = ?", (paper_id,))
        seen = set()
        for tag in tags:
            name = str(tag or "").strip()
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())
            self.conn.execute("INSERT OR IGNORE INTO tags(name) VALUES(?)", (name,))
            self.conn.execute("INSERT OR IGNORE INTO paper_tags(paper_id, tag) VALUES(?,?)", (paper_id, name))

    def _save_attachments(self, paper_id, attachments):
        self.conn.execute("DELETE FROM attachments WHERE paper_id = ?", (paper_id,))
        for att in attachments:
            self.conn.execute(
                "INSERT OR REPLACE INTO attachments(paper_id,file,original_name,size,kind,has_text,"
                "text_chars,added_at) VALUES(?,?,?,?,?,?,?,?)",
                (paper_id, att.get("file") or "", att.get("originalName") or "",
                 int(att.get("size") or 0), att.get("kind") or "other",
                 1 if att.get("hasText") else 0, int(att.get("textChars") or 0),
                 att.get("addedAt") or ""),
            )

    def save_chat(self, paper_id, messages):
        with self._lock, self.conn:
            self._save_chat(paper_id, messages)

    def _save_chat(self, paper_id, messages):
        self.conn.execute("DELETE FROM chat_messages WHERE paper_id = ?", (paper_id,))
        for msg in messages:
            self.conn.execute(
                "INSERT INTO chat_messages(paper_id,role,content,at,model,sources,images,usage) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (paper_id, msg.get("role") or "user", msg.get("content") or "",
                 msg.get("at") or "", msg.get("model") or "",
                 json.dumps(msg.get("sources") or [], ensure_ascii=False),
                 int(msg.get("images") or 0),
                 json.dumps(msg.get("usage"), ensure_ascii=False) if msg.get("usage") else ""),
            )

    def delete_paper(self, paper_id):
        self.write_papers(removed=(paper_id,))

    def _delete_paper(self, paper_id):
        self.conn.execute("DELETE FROM papers WHERE id = ?", (paper_id,))
        self.conn.execute("DELETE FROM paper_tags WHERE paper_id = ?", (paper_id,))
        self.conn.execute("DELETE FROM attachments WHERE paper_id = ?", (paper_id,))
        self.conn.execute("DELETE FROM chat_messages WHERE paper_id = ?", (paper_id,))
        self.conn.execute("DELETE FROM figures WHERE paper_id = ?", (paper_id,))
        self.conn.execute("DELETE FROM analysis WHERE paper_id = ?", (paper_id,))
        self.conn.execute("DELETE FROM paper_text WHERE paper_id = ?", (paper_id,))

    def count_papers(self):
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0])

    def updated_at(self):
        with self._lock:
            row = self.conn.execute("SELECT MAX(updated_at) AS t FROM papers").fetchone()
        return (row["t"] if row and row["t"] else "") or now_iso()

    # ------------------------------------------------------------------ 文件夹（库内分类）
    # 说明：这是「文献库内部的分类」，与磁盘目录无关。path 用 '/' 拼层级，
    # 根目录用空串；folder 列是唯一真（papers.data JSON 里的 folder 会被 _assemble 覆盖）。
    def list_folders(self):
        with self._lock:
            rows = self.conn.execute(
                "SELECT path, name, parent, sort, created_at FROM folders "
                "ORDER BY parent, sort, name").fetchall()
        return [{"path": r["path"], "name": r["name"], "parent": r["parent"],
                 "sort": r["sort"], "createdAt": r["created_at"]} for r in rows]

    def get_folder(self, path):
        with self._lock:
            row = self.conn.execute("SELECT * FROM folders WHERE path = ?", (path,)).fetchone()
        return dict(row) if row else None

    def folder_counts(self):
        """每个路径下**直属**文献数（不含子文件夹）。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT folder, COUNT(*) AS n FROM papers GROUP BY folder").fetchall()
        return {r["folder"] or "": int(r["n"]) for r in rows}

    def save_folder(self, path, name, parent, sort=0):
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO folders(path, name, parent, sort, created_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(path) DO UPDATE SET name = excluded.name, parent = excluded.parent, "
                "sort = excluded.sort",
                (path, name, parent, int(sort), now_iso()))

    def delete_folder_rows(self, prefix):
        """删掉 path == prefix 及其所有子孙（只删文件夹行，不动文献）。"""
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM folders WHERE path = ? OR path LIKE ?",
                              (prefix, prefix + "/%"))

    def rename_folder_prefix(self, old, new):
        """把 old 及其子树整体改名成 new：文件夹行与 papers.folder 同步迁移。

        返回受影响的文献条数。调用方需先校验 new 未被占用。
        """
        with self._lock, self.conn:
            rows = self.conn.execute(
                "SELECT path, name, parent FROM folders WHERE path = ? OR path LIKE ?",
                (old, old + "/%")).fetchall()
            # 被改名那个节点自己的 name 要换掉；子孙节点保留原来的名字
            renamed_name = new.rsplit("/", 1)[-1]
            for row in rows:
                path = row["path"]
                parent = row["parent"]
                moved = new + path[len(old):]
                if parent == old:
                    parent = new
                elif parent.startswith(old + "/"):
                    parent = new + parent[len(old):]
                name = renamed_name if path == old else row["name"]
                self.conn.execute(
                    "UPDATE folders SET path = ?, name = ?, parent = ? WHERE path = ?",
                    (moved, name, parent, path))
            cur = self.conn.execute(
                "UPDATE papers SET folder = ? || substr(folder, ?) "
                "WHERE folder = ? OR folder LIKE ?",
                (new, len(old) + 1, old, old + "/%"))
            affected = cur.rowcount or 0
        return affected

    def move_folder_papers(self, prefix, dest):
        """把 prefix 子树里所有文献挪到 dest，返回条数。"""
        with self._lock, self.conn:
            cur = self.conn.execute(
                "UPDATE papers SET folder = ? WHERE folder = ? OR folder LIKE ?",
                (dest or "", prefix, prefix + "/%"))
            count = cur.rowcount or 0
        return count

    def set_paper_folders(self, ids, path):
        """批量把指定文献放到某个文件夹（path 为空串 = 根目录）。"""
        ids = [str(i) for i in (ids or []) if str(i)]
        if not ids:
            return 0
        with self._lock, self.conn:
            marks = ",".join("?" * len(ids))
            cur = self.conn.execute("UPDATE papers SET folder = ? WHERE id IN (%s)" % marks,
                                    tuple([path or ""] + ids))
            count = cur.rowcount or 0
        return count

    def papers_in_folder(self, prefix):
        with self._lock:
            rows = self.conn.execute(
                "SELECT id FROM papers WHERE folder = ? OR folder LIKE ?",
                (prefix, prefix + "/%")).fetchall()
        return [r["id"] for r in rows]

    # ------------------------------------------------------------------ 全文
    def get_text(self, key):
        with self._lock:
            row = self.conn.execute("SELECT text FROM paper_text WHERE paper_id = ?", (key,)).fetchone()
        return row["text"] if row else ""

    def set_text(self, key, text, updated_at=None):
        text = str(text or "")
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO paper_text(paper_id,text,chars,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(paper_id) DO UPDATE SET text=excluded.text, chars=excluded.chars, "
                "updated_at=excluded.updated_at",
                (key, text, len(text), updated_at or now_iso()),
            )

    def delete_text(self, key):
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM paper_text WHERE paper_id = ?", (key,))

    def text_chars(self, key):
        with self._lock:
            row = self.conn.execute("SELECT chars FROM paper_text WHERE paper_id = ?", (key,)).fetchone()
        return int(row["chars"]) if row else 0

    def list_text_keys(self):
        with self._lock:
            return [r["paper_id"] for r in self.conn.execute("SELECT paper_id FROM paper_text")]

    # ------------------------------------------------------------------ 图表
    def list_figures(self, paper_id=None):
        with self._lock:
            if paper_id:
                rows = self.conn.execute(
                    "SELECT * FROM figures WHERE paper_id = ? ORDER BY fig_index, id", (paper_id,)).fetchall()
            else:
                rows = self.conn.execute("SELECT * FROM figures ORDER BY paper_id, fig_index, id").fetchall()
        return [dict(r) for r in rows]

    def count_figures(self):
        with self._lock:
            return int(self.conn.execute("SELECT COUNT(*) FROM figures").fetchone()[0])

    def insert_figures(self, paper_id, figures, replace=True):
        with self._lock, self.conn:
            if replace:
                self.conn.execute("DELETE FROM figures WHERE paper_id = ?", (paper_id,))
            for fig in figures:
                self.conn.execute(
                    "INSERT INTO figures(paper_id,fig_index,image_path,caption,context_text,"
                    "ai_description,figure_type,ai_conclusion,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (paper_id, int(fig.get("figIndex") or 0), fig.get("imagePath") or "",
                     fig.get("caption") or "", fig.get("contextText") or "",
                     fig.get("aiDescription") or "", fig.get("figureType") or "",
                     fig.get("aiConclusion") or "", fig.get("createdAt") or now_iso()),
                )

    def delete_figure(self, figure_id):
        """删一条图表记录，返回被删的行（找不到返回 None）。"""
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT * FROM figures WHERE id = ?", (figure_id,)).fetchone()
            if row is None:
                return None
            deleted = dict(row)
            self.conn.execute("DELETE FROM figures WHERE id = ?", (figure_id,))
        return deleted

    def figures_for_export(self):
        return self.list_figures()

    # ------------------------------------------------------------------ 分析
    def get_analysis(self, paper_id, kind):
        with self._lock:
            row = self.conn.execute(
                "SELECT payload FROM analysis WHERE paper_id = ? AND kind = ?", (paper_id, kind)).fetchone()
        return _loads(row["payload"], None) if row else None

    def save_analysis(self, paper_id, kind, payload):
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO analysis(paper_id,kind,payload,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(paper_id,kind) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (paper_id, kind, json.dumps(payload, ensure_ascii=False), now_iso()),
            )

    def list_analysis(self, kind=None, limit=100):
        with self._lock:
            if kind:
                rows = self.conn.execute(
                    "SELECT paper_id, kind, payload, updated_at FROM analysis WHERE kind = ? "
                    "ORDER BY updated_at DESC LIMIT ?", (kind, limit)).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT paper_id, kind, payload, updated_at FROM analysis "
                    "ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        return [{"paperId": r["paper_id"], "kind": r["kind"],
                 "payload": _loads(r["payload"], None), "updatedAt": r["updated_at"]} for r in rows]

    # ------------------------------------------------------------------ 旧 id / 任务
    def set_legacy_id(self, legacy_id, paper_id, source="paperlibrary"):
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO legacy_ids(legacy_id,paper_id,source) VALUES(?,?,?)",
                (str(legacy_id), paper_id, source),
            )

    def get_legacy_id(self, legacy_id):
        with self._lock:
            row = self.conn.execute(
                "SELECT paper_id FROM legacy_ids WHERE legacy_id = ?", (str(legacy_id),)).fetchone()
        return row["paper_id"] if row else None

    def save_job(self, job_id, kind, status, payload=None):
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO jobs(id,kind,status,payload,created_at,updated_at) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (job_id, kind, status, json.dumps(payload or {}, ensure_ascii=False),
                 now_iso(), now_iso()),
            )

    def get_job(self, job_id):
        with self._lock:
            row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            return None
        data = dict(row)
        data["payload"] = _loads(data.get("payload"), {})
        return data

    def list_jobs(self, limit=50):
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM jobs ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = _loads(d.get("payload"), {})
            out.append(d)
        return out
