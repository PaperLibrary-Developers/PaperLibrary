# -*- coding: utf-8 -*-
"""clean_dirty_years.py
修复数据库中已有的脏年份（如 2074, 2054, 1929, 1900 等）以及回填缺失的年份
"""
import os
import sys
import sqlite3

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from parser import _extract_year_robust
import fitz

DB_PATH = os.path.join(ROOT, "data", "papers.db")

def clean_years():
    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute("SELECT id, filename, abs_path, year, full_text FROM papers").fetchall()
    updated = 0
    cleaned_abnormal = 0

    for pid, fname, abs_path, old_year, full_text in rows:
        needs_fix = False
        if old_year is None:
            needs_fix = True
        elif old_year > 2026 or old_year < 1970:
            needs_fix = True
            cleaned_abnormal += 1

        if needs_fix:
            meta = {}
            if abs_path and os.path.isfile(abs_path):
                try:
                    with fitz.open(abs_path) as doc:
                        meta = doc.metadata or {}
                except Exception:
                    pass

            new_year = _extract_year_robust(full_text or '', fname, meta)
            if new_year != old_year:
                conn.execute("UPDATE papers SET year = ? WHERE id = ?", (new_year, pid))
                updated += 1
                print(f"  Fixed: [{old_year} -> {new_year}] {fname[:50]}")

    conn.commit()
    conn.close()
    print(f"\nDone! Cleaned {cleaned_abnormal} abnormal years. Total updated: {updated}")

if __name__ == "__main__":
    clean_years()
