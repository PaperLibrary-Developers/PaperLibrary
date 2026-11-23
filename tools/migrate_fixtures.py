"""迁移工具的脚手架：对真实旧库干跑 / 正式迁移 / 迁移后自检 / 缩样造 fixture。

用法（在 HELIX 仓库根执行）：
  python tools/migrate_fixtures.py --dry-run                     # 默认：真实旧库干跑，不写盘
  python tools/migrate_fixtures.py --apply --data-dir data-migrate
  python tools/migrate_fixtures.py --source <PaperLibrary app|data> \
                                  --scilib <Sci-lib data> --dry-run
  python tools/migrate_fixtures.py --verify --data-dir data-migrate
  python tools/migrate_fixtures.py --make-fixture test/fixtures/migrate --papers 4

--dry-run 只读旧库；--apply 才写 SQLite 与 data/ 下的文件（迁移前会自动备份到 data/backups）。
--verify 会用 PyMuPDF 打开抽样 PDF、用 Pillow 打开抽样图片，并检查 DOI/标题重复。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.config import Config  # noqa: E402
from paperlib.migrate import discover_sources, migrate_from_legacy  # noqa: E402
from paperlib.data.store import Store  # noqa: E402
from paperlib.util import dump_json, ensure_dir  # noqa: E402

def _real_path(env_name):
    """真实旧库路径只从环境变量读：仓库与发布包不得写死任何本机路径。"""
    raw = os.environ.get(env_name, "").strip()
    return Path(raw) if raw else None


REAL_SCILIB = _real_path("PAPERLIB_SCILIB_REAL")
REAL_PAPERLIBRARY = _real_path("PAPERLIB_PAPERLIBRARY_REAL")


def _config(args):
    legacy = {}
    if args.scilib:
        legacy["scilibDir"] = str(Path(args.scilib).resolve())
    if args.source:
        legacy["paperlibraryDir"] = str(Path(args.source).resolve())
    return Config(root=ROOT, data_dir=Path(args.data_dir).resolve(),
                  extra={"legacy": legacy} if legacy else {})


def _open_store(args):
    store = Store(Path(args.data_dir).resolve())
    store.load()
    return store


def _verify(store):
    """迁移后自检：计数 + PDF/图片能打开 + 无重复。"""
    papers = store.list_papers()
    figures = store.repo.list_figures()
    report = {"papers": len(papers), "figures": len(figures),
              "texts": len([k for k in store.list_text_keys() if not str(k).startswith("att-")]),
              "errors": []}
    try:
        import pymupdf
    except ImportError:  # 便携运行时里叫 fitz
        import fitz as pymupdf
    pdf_checked = pdf_ok = 0
    for paper in papers:
        name = str(paper.get("pdfFile") or "")
        if not name or pdf_checked >= 20:
            continue
        path = store.path_for_paper_file(name)
        pdf_checked += 1
        try:
            doc = pymupdf.open(path)
            doc.load_page(0)
            doc.close()
            pdf_ok += 1
        except Exception as exc:  # noqa: BLE001
            report["errors"].append("PDF 打不开：%s（%s）" % (name, exc))
    from PIL import Image

    img_checked = img_ok = 0
    for fig in figures:
        rel = str(fig.get("image_path") or "")
        if not rel or img_checked >= 20:
            continue
        path = Path(store.data_dir) / rel
        img_checked += 1
        try:
            with Image.open(path) as image:
                image.verify()
            img_ok += 1
        except Exception as exc:  # noqa: BLE001
            report["errors"].append("图片打不开：%s（%s）" % (rel, exc))
    seen_doi, seen_title, duplicates = {}, {}, []
    for paper in papers:
        doi = str(paper.get("doi") or "").strip().lower()
        title = " ".join(str(paper.get("title") or "").split()).lower()
        if doi and doi in seen_doi:
            duplicates.append("DOI 重复：%s" % doi)
        if title and title in seen_title:
            duplicates.append("标题重复：%s" % title[:60])
        seen_doi.setdefault(doi, paper["id"])
        seen_title.setdefault(title, paper["id"])
    report.update({"pdfChecked": pdf_checked, "pdfOpenable": pdf_ok,
                   "imagesChecked": img_checked, "imagesOpenable": img_ok,
                   "duplicates": duplicates})
    report["errors"] += duplicates
    return report


def _make_fixture(args):
    """从真实 PaperLibrary 库缩样：papers 只留前 N 篇，附带的图表与对应 PDF 一起复制。"""
    target = Path(args.make_fixture).resolve()
    if target.exists():
        shutil.rmtree(target)
    data_dir = ensure_dir(target / "data")
    source = Path(args.source).resolve() if args.source else REAL_PAPERLIBRARY
    if not source:
        raise SystemExit("缺少真实 PaperLibrary 路径：用 --source 指定，"
                         "或设置环境变量 PAPERLIB_PAPERLIBRARY_REAL")
    src_db = (source / "data" / "papers.db") if (source / "data" / "papers.db").is_file() \
        else (source / "papers.db")
    db_target = data_dir / "papers.db"
    shutil.copyfile(src_db, db_target)   # 先复制，再只改副本（真实旧库永远只读）
    conn = sqlite3.connect(str(db_target))
    conn.row_factory = sqlite3.Row
    try:
        keep = [dict(row) for row in conn.execute(
            "SELECT * FROM papers ORDER BY parsed_at, id LIMIT ?", (int(args.papers),))]
        kept_ids = [row["id"] for row in keep]
        marks = ",".join("?" * len(kept_ids))
        conn.execute("DELETE FROM papers WHERE id NOT IN (%s)" % marks, kept_ids)
        conn.execute("DELETE FROM figures WHERE paper_id NOT IN (%s)" % marks, kept_ids)
        conn.commit()
        pdfs = ensure_dir(target / "pdf")
        for row in keep:
            source_pdf = Path(str(row.get("abs_path") or ""))
            if not source_pdf.is_file():
                continue
            copied = pdfs / source_pdf.name
            shutil.copyfile(source_pdf, copied)
            conn.execute("UPDATE papers SET abs_path=? WHERE id=?", (str(copied), row["id"]))
        images_root = source / "data" / "images"
        for figure in conn.execute("SELECT * FROM figures").fetchall():
            old = Path(str(figure["image_path"]).replace("\\", "/"))
            source_img = old if old.is_absolute() else source / old
            if not source_img.is_file():
                continue
            rel = Path("data") / "images" / figure["paper_id"] / source_img.name
            ensure_dir(target / rel.parent)
            shutil.copyfile(source_img, target / rel)
            conn.execute("UPDATE figures SET image_path=? WHERE id=?",
                         (str(rel).replace("\\", "/"), figure["id"]))
        conn.commit()
    finally:
        conn.close()
    with open(target / "fixture.json", "w", encoding="utf-8") as handle:
        json.dump({"source": str(source), "papers": len(kept_ids),
                   "note": "缩样 fixture：源库 db 的子集 + 对应 PDF/图片"},
                  handle, ensure_ascii=False, indent=2)
    return {"fixture": str(target), "papers": len(kept_ids)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="旧库迁移工具（干跑 / 正式 / 自检 / 造 fixture）")
    parser.add_argument("--source", help="PaperLibrary app 或 data 目录")
    parser.add_argument("--scilib", help="Sci-lib 仓库根或 data 目录")
    parser.add_argument("--data-dir", default="data-migrate", help="HELIX 数据目录（默认 data-migrate）")
    parser.add_argument("--dry-run", action="store_true", help="只干跑，不写库（默认行为）")
    parser.add_argument("--apply", action="store_true", help="真的写库")
    parser.add_argument("--verify", action="store_true", help="迁移后自检（计数/打开抽样/查重）")
    parser.add_argument("--make-fixture", help="把真实旧库缩样到指定目录")
    parser.add_argument("--papers", type=int, default=4, help="--make-fixture 保留篇数")
    args = parser.parse_args(argv)
    if args.make_fixture:
        print(dump_json(_make_fixture(args)))
        return 0
    config = _config(args)
    sources = discover_sources(config)
    print("[sources] " + dump_json({k: (str(v["data_dir"]) if v else None)
                                    for k, v in sources.items()}))
    store = _open_store(args)
    try:
        if args.verify and not args.apply:          # 只自检已有数据目录（不跑迁移）
            check = _verify(store)
            print(json.dumps(check, ensure_ascii=False, indent=2))
            return 0 if not check["errors"] else 1
        report = migrate_from_legacy(store, config, dry_run=not args.apply)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if args.verify and args.apply:
            check = _verify(store)
            print(json.dumps(check, ensure_ascii=False, indent=2))
            return 0 if not check["errors"] else 1
        return 0
    finally:
        store.repo.close()


if __name__ == "__main__":
    sys.exit(main())
