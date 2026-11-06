"""SQLite schema（10 张表）与迁移。

唯一真源：papers / figures / tags / paper_tags / attachments / chat_messages /
analysis / paper_text / legacy_ids / jobs（+ meta 记录版本）。

设计：
  · papers 行存「列级可查询字段 + data JSON（完整卡片形状）」——对外 API 形状不变，
    同时给排序/筛选提供索引列；
  · tags/paper_tags 规范化多对多（合并 union 字段）；
  · attachments / chat_messages 从卡片里拆出来（data-model.md §8 union 要求）；
  · 不用 FTS5（便携 Python 的 sqlite 可能没编 FTS5）：检索用 bigram 倒排内存索引。
"""
from __future__ import annotations

SCHEMA_VERSION = 3

DDL = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS papers (
  id        TEXT PRIMARY KEY,
  title     TEXT NOT NULL DEFAULT '',
  year      INTEGER,
  venue     TEXT NOT NULL DEFAULT '',
  journal   TEXT NOT NULL DEFAULT '',
  type      TEXT NOT NULL DEFAULT 'journal',
  status    TEXT NOT NULL DEFAULT 'unread',
  rating    INTEGER NOT NULL DEFAULT 0,
  starred   INTEGER NOT NULL DEFAULT 0,
  folder    TEXT NOT NULL DEFAULT '',
  arxiv_id  TEXT NOT NULL DEFAULT '',
  doi       TEXT NOT NULL DEFAULT '',
  pdf_file  TEXT NOT NULL DEFAULT '',
  added_at  TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  data      TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS papers_updated ON papers(updated_at);
CREATE INDEX IF NOT EXISTS papers_year ON papers(year);
CREATE INDEX IF NOT EXISTS papers_doi ON papers(doi);

CREATE TABLE IF NOT EXISTS attachments (
  paper_id      TEXT NOT NULL,
  file          TEXT NOT NULL,
  original_name TEXT NOT NULL DEFAULT '',
  size          INTEGER NOT NULL DEFAULT 0,
  kind          TEXT NOT NULL DEFAULT 'other',
  has_text      INTEGER NOT NULL DEFAULT 0,
  text_chars    INTEGER NOT NULL DEFAULT 0,
  added_at      TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (paper_id, file)
);

CREATE TABLE IF NOT EXISTS tags (
  name TEXT PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS paper_tags (
  paper_id TEXT NOT NULL,
  tag      TEXT NOT NULL,
  PRIMARY KEY (paper_id, tag)
);
CREATE INDEX IF NOT EXISTS paper_tags_tag ON paper_tags(tag);

CREATE TABLE IF NOT EXISTS paper_text (
  paper_id   TEXT PRIMARY KEY,
  text       TEXT NOT NULL DEFAULT '',
  chars      INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS chat_messages (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id TEXT NOT NULL,
  role     TEXT NOT NULL DEFAULT 'user',
  content  TEXT NOT NULL DEFAULT '',
  at       TEXT NOT NULL DEFAULT '',
  model    TEXT NOT NULL DEFAULT '',
  sources  TEXT NOT NULL DEFAULT '[]',
  images   INTEGER NOT NULL DEFAULT 0,
  usage    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS chat_messages_paper ON chat_messages(paper_id, id);

CREATE TABLE IF NOT EXISTS figures (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  paper_id       TEXT NOT NULL,
  fig_index      INTEGER NOT NULL DEFAULT 0,
  image_path     TEXT NOT NULL DEFAULT '',
  caption        TEXT NOT NULL DEFAULT '',
  context_text   TEXT NOT NULL DEFAULT '',
  ai_description TEXT NOT NULL DEFAULT '',
  figure_type    TEXT NOT NULL DEFAULT '',
  ai_conclusion  TEXT NOT NULL DEFAULT '',
  created_at     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS figures_paper ON figures(paper_id);

CREATE TABLE IF NOT EXISTS analysis (
  paper_id   TEXT NOT NULL,
  kind       TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT '',
  PRIMARY KEY (paper_id, kind)
);

CREATE TABLE IF NOT EXISTS legacy_ids (
  legacy_id TEXT PRIMARY KEY,
  paper_id  TEXT NOT NULL,
  source    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS legacy_ids_paper ON legacy_ids(paper_id);

CREATE TABLE IF NOT EXISTS jobs (
  id         TEXT PRIMARY KEY,
  kind       TEXT NOT NULL DEFAULT '',
  status     TEXT NOT NULL DEFAULT '',
  payload    TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
"""


def init_schema(conn) -> None:
    conn.executescript(DDL)
    row = conn.execute("PRAGMA user_version").fetchone()
    current = int(row[0]) if row else 0
    if current < SCHEMA_VERSION:
        conn.execute("PRAGMA user_version = %d" % SCHEMA_VERSION)
    conn.commit()
