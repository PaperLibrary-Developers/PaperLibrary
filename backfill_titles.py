# -*- coding: utf-8 -*-
"""backfill_titles.py — 把数据库里的论文标题换成文章真实标题

原来标题 = 上传的 PDF 文件名，现改为从 PDF 元数据/首页字号提取的真实标题。
只更新 title 字段，不动全文、图片、AI 总结。
FTS 触发器会自动同步 papers_fts 索引。
"""
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from title_extract import extract_title

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(APP_DIR, "data", "papers.db")

conn = sqlite3.connect(DB)
rows = conn.execute("SELECT id, filename, title, abs_path FROM papers").fetchall()

updated = skipped = missing = 0
log = []
for pid, fn, old_title, ap in rows:
    if not ap or not os.path.exists(ap):
        missing += 1
        continue
    new_title, src = extract_title(ap, fn)
    if not new_title or new_title == old_title:
        skipped += 1
        continue
    conn.execute("UPDATE papers SET title=? WHERE id=?", (new_title, pid))
    updated += 1
    log.append(f"[{src:8}] {fn[:45]}")
    log.append(f"          旧: {old_title[:80]}")
    log.append(f"          新: {new_title[:80]}")

conn.commit()

# 校验
left = conn.execute(
    "SELECT COUNT(*) FROM papers WHERE title = '' OR title IS NULL").fetchone()[0]
total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
conn.close()

print(f"updated={updated} skipped={skipped} missing={missing} empty={left} total={total}")
with open(os.path.join(os.environ["TEMP"], "backfill_log.txt"), "w",
          encoding="utf-8") as f:
    f.write("\n".join(log))
