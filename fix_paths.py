"""修复数据库中 PDF 路径（一次性维护脚本）
早期扫描时路径编码损坏，导致 abs_path 全部失效。
通过文件内容 SHA256 重新匹配，只更新路径，不重新解析全文/图片。
"""
import os, sys, sqlite3, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parser import find_pdfs, sha256_file

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = r"y:\私人\PAPER"
DB = os.path.join(APP_DIR, "data", "papers.db")
CFG = os.path.join(APP_DIR, "data", ".app_config.json")

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row

pdfs = find_pdfs(ROOT)
print(f"[1] Found {len(pdfs)} PDFs under {ROOT}")

by_hash = {}
for pi in pdfs:
    h = sha256_file(pi["full_path"])
    by_hash[h] = pi

rows = conn.execute("SELECT id, filename, folder, abs_path FROM papers").fetchall()
fixed = 0
unmatched = []
for r in rows:
    pi = by_hash.get(r["id"])
    if pi:
        if r["abs_path"] != pi["full_path"] or r["folder"] != pi["folder"]:
            conn.execute("UPDATE papers SET abs_path=?, folder=? WHERE id=?",
                         (pi["full_path"], pi["folder"], r["id"]))
            fixed += 1
    else:
        unmatched.append(r["filename"])

conn.commit()
print(f"[2] Fixed {fixed} paths, {len(unmatched)} papers have no matching file")
for u in unmatched[:10]:
    print("     MISSING:", u)

bad = conn.execute("SELECT COUNT(*) FROM papers WHERE abs_path='' OR abs_path IS NULL").fetchone()[0]
print(f"[3] Remaining empty abs_path: {bad}")
conn.close()

# Update config: set scan root so future browser scans find PDFs
cfg = {}
if os.path.exists(CFG):
    with open(CFG, encoding="utf-8") as f:
        cfg = json.load(f)
cfg["pdf_scan_root"] = ROOT
with open(CFG, "w", encoding="utf-8") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print(f"[4] Config pdf_scan_root -> {ROOT}")
print("DONE")
