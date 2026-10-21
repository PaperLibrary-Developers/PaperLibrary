# -*- coding: utf-8 -*-
"""修复 papers.db 里失效的文件路径（记录在库、但文件已不在原位置）。

典型场景：你在资源管理器里把某个文件夹改了名或挪了位置，
数据库里存的还是旧路径 —— 于是文献在界面上显示「PDF 未找到」，
但文件其实好好地在硬盘上，只是换了个目录。

做法：
  1. 找出所有 abs_path 指向的文件已经不存在的记录
  2. 在扫描根目录下按**文件名**去找现在的位置
  3. 只认「唯一匹配」；有多个同名候选时，选路径结构最接近原路径的那个
  4. 找到就更新 abs_path / filename / folder（folder 是相对扫描根、用 / 分隔）
  5. 找不到或有歧义的，如实列出来，让你自己决定

【绝不移动或修改任何文件】，只改数据库。执行前先备份 papers.db。

用法：
  python scripts/_relink_paths.py          # 预演
  python scripts/_relink_paths.py --go     # 执行
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

import json

APP = r"Y:\私人\PAPER\paper-library-app"
DB = os.path.join(APP, "data", "papers.db")
CFG = os.path.join(APP, ".app_config.json")
BACKUP_DIR = os.path.join(APP, ".si-rename-backup")
SKIP_DIRS = {"paper-library-app", "_scilib_ref", "__pycache__", ".si-rename-backup",
             "thumb_cache"}
GO = "--go" in sys.argv


def scan_root():
    """从 app 配置里读扫描根；读不到就退回 y:\\私人\\PAPER。"""
    try:
        with open(CFG, encoding="utf-8") as f:
            cfg = json.load(f)
        root = (cfg.get("pdf_scan_root") or "").strip()
        if root:
            # 配置里可能有 GBK 乱码，只要目录真实存在就采用
            if os.path.isdir(root):
                return root
            # 尝试常见编码还原
            for enc in ("gbk", "utf-8", "latin-1"):
                try:
                    fixed = root.encode("latin-1").decode(enc)
                    if os.path.isdir(fixed):
                        return fixed
                except Exception:
                    pass
    except Exception:
        pass
    return r"y:\私人\PAPER"


def make_folder(abs_path, root):
    """算出 folder 字段：相对扫描根、/ 分隔、根目录下写作「根目录」。"""
    ap = os.path.abspath(abs_path)
    rp = os.path.abspath(root)
    try:
        rel = os.path.relpath(ap, rp)
    except ValueError:
        return ""
    d = os.path.dirname(rel)
    if d in ("", "."):
        return "根目录"
    return d.replace(os.sep, "/")


def common_tail(a, b):
    """两个路径从末尾起有多少段是相同的（用来判断哪个候选更可能是原文件）。"""
    pa = [p for p in os.path.normcase(os.path.abspath(a)).split(os.sep) if p]
    pb = [p for p in os.path.normcase(os.path.abspath(b)).split(os.sep) if p]
    n = 0
    for x, y in zip(reversed(pa), reversed(pb)):
        if x != y:
            break
        n += 1
    return n


def main():
    if not os.path.isfile(DB):
        print("！找不到 papers.db")
        return 2
    root = scan_root()
    print(f"扫描根目录: {root}")
    if not os.path.isdir(root):
        print("！扫描根目录不存在，中止")
        return 2

    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT abs_path, filename, folder FROM papers WHERE abs_path != ''").fetchall()
    broken = [(a, fn, fo) for a, fn, fo in rows if not os.path.isfile(a)]
    print(f"库里共 {len(rows)} 条记录，其中 {len(broken)} 条路径失效")

    if not broken:
        print("没有失效路径，无需修复。")
        conn.close()
        return 0

    # 建磁盘索引：小写文件名 -> [真实路径]
    disk = {}
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.lower().endswith(".pdf"):
                disk.setdefault(f.lower(), []).append(os.path.join(dp, f))

    fixes, ambiguous, missing = [], [], []
    for old, fn, fo in broken:
        name = os.path.basename(old).lower()
        cands = disk.get(name, [])
        if not cands:
            missing.append((old, fn, fo))
        elif len(cands) == 1:
            fixes.append((old, cands[0], fn, make_folder(cands[0], root), fo))
        else:
            best = max(cands, key=lambda c: common_tail(old, c))
            ambiguous.append((old, best, cands, fn, fo))

    print("=" * 78)
    print("模式:", "执行（会改数据库）" if GO else "预演（不改动任何东西）")
    print(f"能唯一确定新位置: {len(fixes)}")
    print(f"有多个同名候选（需你确认，本次不动）: {len(ambiguous)}")
    print(f"磁盘上也找不到: {len(missing)}")
    print("=" * 78)

    if fixes:
        print("\n【将修复】")
        for old, new, fn, nfo, ofo in fixes[:60]:
            print(f"   {os.path.basename(old)[:66]}")
            print(f"      {os.path.dirname(old)}")
            print(f"   -> {os.path.dirname(new)}")
            if nfo != ofo:
                print(f"      folder: {ofo!r} -> {nfo!r}")
        if len(fixes) > 60:
            print(f"   … 还有 {len(fixes) - 60} 条")

    if ambiguous:
        print("\n【同名多处，需人工确认】")
        for old, best, cands, fn, fo in ambiguous[:20]:
            print(f"   {os.path.basename(old)[:60]}")
            for c in cands:
                mark = "  ← 结构最接近" if c == best else ""
                print(f"      {c}{mark}")

    if missing:
        print("\n【磁盘上找不到，保留原样】")
        for old, fn, fo in missing[:20]:
            print(f"   {os.path.basename(old)[:70]}")

    if not GO:
        print("\n预演结束。确认无误后执行：")
        print("   python scripts/_relink_paths.py --go")
        conn.close()
        return 0

    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    shutil.copy2(DB, os.path.join(BACKUP_DIR, f"papers.db.{stamp}.bak"))
    print(f"\n已备份数据库 → {os.path.join(BACKUP_DIR, f'papers.db.{stamp}.bak')}")

    n = 0
    for old, new, fn, nfo, ofo in fixes:
        cur = conn.execute(
            "UPDATE papers SET abs_path=?, filename=?, folder=? WHERE abs_path=?",
            (os.path.abspath(new), os.path.basename(new), nfo, old))
        n += cur.rowcount
    conn.commit()

    left = [a for a, in conn.execute(
        "SELECT abs_path FROM papers WHERE abs_path != ''").fetchall()
        if not os.path.isfile(a)]
    conn.close()
    print(f"已修复 {n} 条记录")
    print(f"剩余失效路径: {len(left)} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
