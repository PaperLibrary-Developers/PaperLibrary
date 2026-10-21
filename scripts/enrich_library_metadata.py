# -*- coding: utf-8 -*-
"""enrich_library_metadata.py
为本地文献库批量丰富作者、期刊、发表年份与规范 DOI。
"""
import os
import sys
import time
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import metadata_enricher

DB_PATH = os.path.join(ROOT, "data", "papers.db")

def batch_enrich(limit: int = 30, only_missing_authors: bool = True):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    query = "SELECT id, title, filename, doi, authors, journal, year FROM papers"
    if only_missing_authors:
        query += " WHERE authors IS NULL OR authors = '' OR authors = '[]'"
    query += " ORDER BY CASE WHEN doi != '' THEN 0 ELSE 1 END, year DESC"
    if limit:
        query += f" LIMIT {limit}"

    rows = conn.execute(query).fetchall()
    conn.close()

    total = len(rows)
    print(f"开始批量丰富元数据，共计划处理 {total} 篇文献...")

    success_cnt = 0
    for idx, r in enumerate(rows, 1):
        pid = r["id"]
        t_label = (r["title"] or r["filename"])[:45]
        print(f"[{idx}/{total}] 正在解析: {t_label} (DOI: {r['doi'] or '无'})...")
        try:
            ok, msg = metadata_enricher.enrich_and_update_db(DB_PATH, pid)
            if ok:
                success_cnt += 1
                print(f"   -> [成功] {msg}")
            else:
                print(f"   -> [跳过] {msg}")
        except Exception as e:
            print(f"   -> [异常] {e}")

        time.sleep(0.3)

    print(f"\n批量处理完成！成功为 {success_cnt}/{total} 篇文献补全了权威学术元数据！")

if __name__ == "__main__":
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    batch_enrich(limit=count)
