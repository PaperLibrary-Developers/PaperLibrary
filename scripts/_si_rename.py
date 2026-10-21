# -*- coding: utf-8 -*-
"""给 SI（补充材料）打标记，让它在**文献库里**和原文能区分开。

做两件事（同一批文件）：g
  1. 文件名加全角前缀 （SI）  —— 在文件夹里能区分
  2. papers.db 的 title 也加 （SI）  —— 在网页列表里能区分（关键！界面显示的是标题，
     只改文件名的话列表里两篇同名论文还是分不出来）

识别依据（文件名）：
  · 已带 （SI） / (SI) 前缀
  · _si_001 / -si1 / _SI / -SI 之类出版社导出后缀
  · 裸的 s3.pdf / s1.pdf / si.pdf（如 Chem 的 Supplemental information 常导成 s3.pdf）
  · supporting information / supplementary material 字样
  反向排除：含 "supporting electrolyte" 的是正刊（讲支持电解质的综述），一律不碰。

【范围】只处理**已经在 papers.db 里登记的文献**。库里没登记的一律不碰。

用法：
  python scripts/_si_rename.py            # 预演，只打印
  python scripts/_si_rename.py --go       # 真正执行（先备份文件与数据库）
  python scripts/_si_rename.py --titles   # 只补标题、不改文件名

设计原则：
  · 绝不移动文件、绝不改文件夹（用户要求原位改名）
  · 目标已存在时跳过并报告，不覆盖
  · 执行前把 PDF 与 papers.db 各备一份到 .si-rename-backup/
"""
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime

ROOT = r"y:\私人\PAPER"
APP = r"Y:\私人\PAPER\paper-library-app"
DB = os.path.join(APP, "data", "papers.db")
BACKUP_DIR = os.path.join(APP, ".si-rename-backup")
SKIP_DIRS = {"paper-library-app", "_scilib_ref", "data", "__pycache__", ".si-rename-backup"}

SI_PREFIX = "（SI）"
GO = "--go" in sys.argv
TITLES_ONLY = "--titles" in sys.argv

# ── 已带前缀（半角或全角）──
PREFIX_RX = re.compile(r"^\s*[（(]\s*SI\s*[）)]\s*", re.I)
# ── 出版社导出后缀：xxx_si_001 / xxx-si1 / xxx_si ──
SUFFIX_RX = re.compile(r"[_\-\s]si[_\-\s]?\d{0,3}$", re.I)
# ── Wiley 的 sup-0001-misc_information / -sup-0001 / _esm 之类 ──
SUP_RX = re.compile(r"[-_\s](sup|esm)[-_\s]?\d*[-_\s]*(misc[_\-]?information)?$"
                    r"|[-_\s]misc[_\-]?information$", re.I)
# ── 结尾 -SI / _SI ──
DASH_SI_RX = re.compile(r"[-_\s]SI$", re.I)
# ── 裸短名 s3 / s1 / si / si2 ──
BARE_RX = re.compile(r"^s(i)?\d{0,2}$", re.I)
# ── 明确的 SI 字样（必须带 information/material，避免误伤 supporting electrolyte）──
TEXT_SI_RX = re.compile(
    r"supporting\s+information|supplementary\s+(information|material|data)|"
    r"electronic\s+supplementary|补充材料|支持信息|附件材料", re.I)
FALSE_POS_RX = re.compile(r"supporting\s+electrolyte", re.I)


def library_rows():
    """文献库内的论文记录：[(abs_path, title, filename)]。"""
    if not os.path.isfile(DB):
        return None
    conn = sqlite3.connect(DB)
    rows = conn.execute(
        "SELECT abs_path, title, filename FROM papers WHERE abs_path != ''").fetchall()
    conn.close()
    return rows


def is_si_name(filename: str) -> bool:
    """按文件名判断是不是 SI。"""
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    if FALSE_POS_RX.search(stem):
        return False
    return bool(PREFIX_RX.match(stem) or SUFFIX_RX.search(stem)
                or DASH_SI_RX.search(stem) or BARE_RX.match(stem)
                or SUP_RX.search(stem) or TEXT_SI_RX.search(stem))


def si_target_name(filename: str):
    """算出加了 SI 标记后的新文件名；不需要改就返回 None。"""
    stem = filename[:-4]
    # 先剥掉已有前缀
    stem = PREFIX_RX.sub("", stem).strip()
    # 再剥掉各种后缀 / 裸短名（剥完为空则保留原样，避免把名字清空）
    stripped = SUP_RX.sub("", stem).strip(" _-")
    stripped = SUFFIX_RX.sub("", stripped).strip(" _-")
    stripped = DASH_SI_RX.sub("", stripped).strip(" _-")
    if not stripped or BARE_RX.match(stripped):
        stripped = stem          # 裸 s3 之类：只加前缀，不改主体
    if not stripped:
        return None
    new_name = SI_PREFIX + stripped + ".pdf"
    return new_name if new_name != filename else None


def with_prefix(title: str) -> str:
    """标题加 （SI） 前缀（已有就不重复加）。"""
    t = (title or "").strip()
    if not t:
        return t
    if PREFIX_RX.match(t):
        return PREFIX_RX.sub(SI_PREFIX, t)
    return SI_PREFIX + t


def main():
    rows = library_rows()
    if rows is None:
        print("！找不到 papers.db，无法确定文献库范围，已中止（不会动任何文件）")
        return 2
    lib = {os.path.abspath(a) for a, _, _ in rows}

    # 全盘统计，让「只动文献库」这件事可见
    total_pdf = outside = 0
    for dp, dn, fn in os.walk(ROOT):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        for f in fn:
            if f.lower().endswith(".pdf"):
                total_pdf += 1
                if os.path.abspath(os.path.join(dp, f)) not in lib:
                    outside += 1

    # ── 收集：文件改名计划 + 标题补前缀计划 ──
    rename_plan = []
    title_plan = []
    for a, t, _fn in rows:
        if not os.path.isfile(a):
            continue                                  # 记录已失效，跳过
        fn = os.path.basename(a)
        if not is_si_name(fn):
            continue
        new_fn = si_target_name(fn)
        if new_fn and not TITLES_ONLY:
            rename_plan.append((a, os.path.join(os.path.dirname(a), new_fn), fn))
        new_t = with_prefix(t)
        if new_t and new_t != (t or "").strip():
            title_plan.append((a, new_t, t, fn))

    print("=" * 78)
    print("模式:", "只补标题（不改文件名）" if TITLES_ONLY
          else ("执行（会真正改名并补标题）" if GO else "预演（不改动任何东西）"))
    print(f"文献库范围: papers.db 登记 {len(lib)} 个文件")
    print(f"磁盘 PDF 共 {total_pdf} 个，其中 {outside} 个不在文献库内 → 一律不碰")
    print(f"文件名待改: {len(rename_plan)} 个；标题待补: {len(title_plan)} 个")
    print("=" * 78)

    if rename_plan:
        print("\n【文件名】")
        for src, dst, _fn in rename_plan:
            print("   ", os.path.relpath(src, ROOT))
            print("      ->", os.path.basename(dst))
    if title_plan:
        print("\n【标题（界面列表里显示的就是这个）】")
        for _a, nt, ot, fn in title_plan:
            print(f"    {str(ot)[:60]}")
            print(f"      -> {nt[:60]}")

    if not GO and not TITLES_ONLY:
        print("\n预演结束。确认无误后执行：")
        print("   python scripts/_si_rename.py --go")
        return 0

    # ── 备份 ──
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    if os.path.isfile(DB):
        shutil.copy2(DB, os.path.join(BACKUP_DIR, f"papers.db.{stamp}.bak"))
    for src, _dst, _fn in rename_plan:
        rel = os.path.relpath(src, ROOT).replace(os.sep, "__")
        try:
            shutil.copy2(src, os.path.join(BACKUP_DIR, rel))
        except Exception as e:
            print("   备份失败（将跳过该文件）:", rel, e)

    # ── 执行改名 ──
    done, skipped, failed = [], [], []
    for src, dst, fn in rename_plan:
        if os.path.exists(dst):
            skipped.append((fn, "目标已存在，跳过"))
            continue
        try:
            os.rename(src, dst)
            done.append((src, dst))
        except Exception as e:
            failed.append((fn, str(e)))

    print()
    print(f"已改名: {len(done)}   跳过: {len(skipped)}   失败: {len(failed)}")
    for fn, r in skipped:
        print("   跳过:", fn, "->", r)
    for fn, e in failed:
        print("   失败:", fn, "->", e)

    # ── 同步数据库：路径 + 标题 ──
    # 注意顺序：先改名再写标题时，标题必须按**新路径**匹配 ——
    # 否则被改名的那些文件会因为 WHERE abs_path=旧路径 匹配不到而漏掉。
    moved_to = {os.path.abspath(s): os.path.abspath(d) for s, d in done}
    conn = sqlite3.connect(DB)
    moved = 0
    for src, dst in done:
        cur = conn.execute(
            "UPDATE papers SET abs_path=?, filename=? WHERE abs_path=?",
            (os.path.abspath(dst), os.path.basename(dst), os.path.abspath(src)))
        moved += cur.rowcount
    titled = 0
    for a, nt, _ot, _fn in title_plan:
        cur = conn.execute("UPDATE papers SET title=? WHERE abs_path=?",
                           (nt, moved_to.get(os.path.abspath(a), os.path.abspath(a))))
        titled += cur.rowcount
    conn.commit()
    rows2 = conn.execute("SELECT abs_path FROM papers").fetchall()
    broken = [r[0] for r in rows2 if r[0] and not os.path.isfile(r[0])]
    conn.close()

    print(f"数据库: 路径更新 {moved} 条，标题更新 {titled} 条")
    print(f"仍失效的路径: {len(broken)} 条" + ("（这些文件本来就不在了）" if broken else ""))
    for b in broken[:6]:
        print("   失效:", os.path.basename(b))
    print("   备份目录:", BACKUP_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
