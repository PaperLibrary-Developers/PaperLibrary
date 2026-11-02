# -*- coding: utf-8 -*-
# 集成测试：模拟 app.py 的「移动文献」全流程（搬文件 + 同步数据库）。
# 临时文献库放在 app 目录内（有写权限）；数据库用真实 papers.db 的副本，保证测的是真表结构。
import os
import shutil
import sqlite3
import sys
import tempfile

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
os.chdir(APP)

import library_fs
import database

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


work = tempfile.mkdtemp(prefix="lvmove_", dir=APP)          # 临时文献库根目录
db_copy = os.path.join(work, "test.db")
shutil.copy2(os.path.join(APP, "data", "papers.db"), db_copy)
database.DB_PATH = db_copy                                   # 让 database 用副本

real_root = r"y:\私人\PAPER"
src_pdf = None
for dp, dn, fn in os.walk(real_root):
    dn[:] = [d for d in dn if d not in ("paper-library-app", "_scilib_ref")]
    for f in fn:
        if f.lower().endswith(".pdf"):
            src_pdf = os.path.join(dp, f)
            break
    if src_pdf:
        break
print("取一篇真实 PDF 做样本:", os.path.basename(src_pdf))

try:
    # 布置：临时库里有一个分类，样本 PDF 放在里面；数据库插一条指向它的记录
    os.makedirs(os.path.join(work, "羰基还原"))
    staged = os.path.join(work, "羰基还原", "sample.pdf")
    shutil.copy2(src_pdf, staged)

    conn = sqlite3.connect(db_copy)
    conn.execute("""INSERT INTO papers (id, filename, title, authors, doi, year, folder,
                    abs_path, full_text, summary, parsed_at)
                    VALUES ('testid0001','sample.pdf','Test Paper','[]','',2024,
                            '羰基还原', ?, '', '', '')""", (staged,))
    conn.commit()
    conn.close()

    # 待移动到的两个分类
    library_fs.create_folder(work, "电催化")
    library_fs.create_folder(work, "手性材料")

    def folder_in_db():
        c = sqlite3.connect(db_copy)
        r = c.execute("SELECT abs_path, folder, filename FROM papers WHERE id='testid0001'").fetchone()
        c.close()
        return r

    print("\n=== 移动前 ===")
    a, f, n = folder_in_db()
    check("库里 folder=羰基还原", f == "羰基还原", f"实际 {f}")
    check("库里的文件存在", os.path.isfile(a))

    # ── 模拟 app.py 的移动流程：先搬文件，再同步数据库 ──
    print("\n=== 执行移动到「电催化」 ===")
    old = a
    new = library_fs.move_pdf(work, old, "电催化")
    # sync_paper_path 的逻辑（app.py 里用 get_scan_root，这里用临时库根目录）
    conn = sqlite3.connect(db_copy)
    conn.execute("UPDATE papers SET abs_path=?, filename=?, folder=? WHERE abs_path=?",
                 (new, os.path.basename(new),
                  library_fs.folder_of(work, new), old))
    conn.commit()
    conn.close()

    a2, f2, n2 = folder_in_db()
    check("文件已搬到 电催化", os.path.isfile(os.path.join(work, "电催化", "sample.pdf")))
    check("原位置已空", not os.path.exists(old))
    check("库内 abs_path 已更新", a2 == new, a2)
    check("库内 folder 已更新", f2 == "电催化", f"实际 {f2}")
    check("库内 filename 保持", n2 == "sample.pdf")

    # ── 再移动到嵌套分类，验证 folder 用 "/" 分隔 ──
    print("\n=== 移动到嵌套分类（电催化/亚类）===")
    os.makedirs(os.path.join(work, "电催化", "亚类"))
    old2 = a2
    new2 = library_fs.move_pdf(work, old2, "电催化/亚类")
    check("嵌套目录移动成功", os.path.isfile(new2))
    check("folder_of 处理嵌套", library_fs.folder_of(work, new2) == "电催化/亚类",
          library_fs.folder_of(work, new2))

    # ── 移回根目录 ──
    print("\n=== 移回根目录 ===")
    new3 = library_fs.move_pdf(work, new2, "")
    check("移回根目录", os.path.dirname(new3) == os.path.abspath(work))
    check("folder_of=根目录", library_fs.folder_of(work, new3) == "根目录")

    # ── 删除分类：空的可删，非空拒绝 ──
    print("\n=== 删除分类 ===")
    library_fs.delete_folder(work, "手性材料")
    check("空分类已删除", not os.path.exists(os.path.join(work, "手性材料")))

    # 放一份文件进「羰基还原」，再验证非空时拒绝删除
    os.makedirs(os.path.join(work, "羰基还原"), exist_ok=True)
    with open(os.path.join(work, "羰基还原", "keep.pdf"), "wb") as f:
        f.write(b"%PDF-1.4")
    check("非空分类计数正确",
          library_fs.count_files(os.path.join(work, "羰基还原")) == 1)
    try:
        library_fs.delete_folder(work, "羰基还原")
        check("非空分类被拒", False, "本该报错")
    except library_fs.FolderError as e:
        check("非空分类被拒", True)
        print(f"          -> {e}")
    check("被拒后目录仍在", os.path.isdir(os.path.join(work, "羰基还原")))

finally:
    shutil.rmtree(work, ignore_errors=True)
    print("\n临时目录已清理（真实文献库未被改动）")

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
