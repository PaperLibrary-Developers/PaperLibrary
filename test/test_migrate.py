"""旧库迁移测试（paperlib/data/migrate.py）。

覆盖：Sci-lib/PaperLibrary 字段映射、DOI/标题去重合并（用户字段以 Sci-lib 为准）、
幂等（二次运行零写入）、dry_run 不写盘、文件名安全、图片/PDF 复制、图表合并、
备份，以及真实旧库的干跑计数（有真实库时才跑）。

自测：python -m unittest test.test_migrate -v
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.config import Config
from paperlib.data.migrate import discover_sources, migrate_from_legacy
from paperlib.data.store import Store

SCILIB_REAL = Path(r"C:\Users\wzxle\Desktop\WB比赛\Sci-lib\data")
PAPERLIBRARY_REAL = Path(r"C:\Users\wzxle\Desktop\WB比赛\PaperLibrary\app\data")

PL_SCHEMA = """
CREATE TABLE papers (id TEXT PRIMARY KEY, filename TEXT NOT NULL, title TEXT DEFAULT '',
  authors TEXT DEFAULT '[]', doi TEXT DEFAULT '', year INTEGER, journal TEXT DEFAULT '',
  folder TEXT DEFAULT '', abs_path TEXT NOT NULL, full_text TEXT DEFAULT '',
  summary TEXT DEFAULT '', parsed_at TEXT DEFAULT '', arxiv_id TEXT DEFAULT '',
  abstract TEXT DEFAULT '', deep_analysis TEXT DEFAULT '', deep_scores TEXT DEFAULT '',
  deep_at TEXT DEFAULT '');
CREATE TABLE figures (id INTEGER PRIMARY KEY AUTOINCREMENT, paper_id TEXT NOT NULL,
  fig_index INTEGER NOT NULL, image_path TEXT DEFAULT '', caption TEXT DEFAULT '',
  context_text TEXT DEFAULT '', ai_description TEXT DEFAULT '', figure_type TEXT DEFAULT '',
  ai_conclusion TEXT DEFAULT '');
"""


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def make_pdf(path, payload=b"%PDF-1.4\n% migrate fixture\n"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def make_image(path, color=(200, 30, 30)):
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (6, 6), color).save(path)


def scilib_fixture(root, papers, pdfs=(), attachments=(), texts=()):
    """root = Sci-lib data 目录形状（library.json + article/ + fulltext/）。"""
    write_json(root / "library.json", {"version": 1, "papers": papers, "meta": {}})
    for name in pdfs:
        make_pdf(root / "article" / "papers" / name)
    for name in attachments:
        make_pdf(root / "article" / "attachments" / name)
    for paper_id, text in texts:
        (root / "fulltext").mkdir(parents=True, exist_ok=True)
        (root / "fulltext" / (paper_id + ".txt")).write_text(text, encoding="utf-8")
    return root


def paperlibrary_fixture(root, papers, figures=(), images=()):
    """root = PaperLibrary app 目录形状（data/papers.db + data/images/<pid>/*.jpeg）。"""
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(data / "papers.db"))
    try:
        conn.executescript(PL_SCHEMA)
        for row in list(papers) + list(figures):
            cols = list(row)
            conn.execute("INSERT INTO %s(%s) VALUES(%s)"
                         % ("figures" if "fig_index" in row else "papers", ",".join(cols),
                            ",".join("?" * len(cols))), [row[key] for key in cols])
        conn.commit()
    finally:
        conn.close()
    for rel in images:
        make_image(root / rel)
    return root


def make_config(tmp, scilib=None, pl=None, name="data"):
    legacy = {}
    if scilib:
        legacy["scilibDir"] = str(scilib)
    if pl:
        legacy["paperlibraryDir"] = str(pl)
    return Config(root=tmp, data_dir=tmp / name, extra={"legacy": legacy} if legacy else {})


class MigrateTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def open_store(self, config):
        store = Store(config.data_dir)
        store.load()
        return store

    def close(self, store):
        store.repo.close()


class ScilibMappingTest(MigrateTestCase):
    def test_scilib_card_files_and_legacy_id(self):
        paper_id = "a1b2c3d4e5f6a7b8"
        pdf = paper_id + "-Alpha Paper.pdf"
        si = paper_id + "-Alpha Paper SI1.pdf"
        scilib = scilib_fixture(
            self.tmp / "scilib",
            [{"id": paper_id, "title": "Alpha Paper", "authors": ["Ann", "Bob"], "year": 2020,
              "venue": "J. Tests", "doi": "10.1/ALPHA", "tags": ["t1"], "notes": "手写笔记",
              "status": "read", "rating": 4, "starred": True, "addedAt": "2026-11-01T01:02:03.004Z",
              "updatedAt": "2026-11-02T01:02:03.004Z", "pdfFile": pdf,
              "attachments": [{"file": si, "originalName": "SI.pdf", "size": 12}]}],
            pdfs=[pdf], attachments=[si], texts=[(paper_id, "Alpha 正文" * 10)])
        config = make_config(self.tmp, scilib=scilib)
        store = self.open_store(config)
        try:
            stats = migrate_from_legacy(store, config)
            self.assertFalse(stats["dryRun"])
            self.assertEqual(stats["libraryJson"]["added"], 1)
            self.assertEqual(store.count(), 1)
            paper = store.find_paper(paper_id)
            self.assertIsNotNone(paper)
            self.assertEqual(paper["title"], "Alpha Paper")
            self.assertEqual(paper["tags"], ["t1"])
            self.assertEqual(paper["notes"], "手写笔记")
            self.assertEqual((paper["status"], paper["rating"], paper["starred"]),
                             ("read", 4, True))
            self.assertEqual(paper["addedAt"], "2026-11-01T01:02:03.004Z")
            self.assertTrue(Path(store.path_for_paper_file(paper["pdfFile"])).is_file())
            self.assertTrue(Path(store.path_for_attachment(si)).is_file())
            self.assertEqual(store.get_text(paper_id), "Alpha 正文" * 10)
            self.assertEqual(store.repo.get_legacy_id(paper_id), paper_id)
            self.assertEqual(stats["papers"]["added"], 1)
            self.assertEqual(stats["texts"]["added"], 1)
            self.assertEqual(stats["ids"]["scilib"], 1)
        finally:
            self.close(store)

    def test_repo_root_path_also_accepted(self):
        paper_id = "0011223344556677"
        scilib = scilib_fixture(self.tmp / "Sci-lib" / "data",
                                [{"id": paper_id, "title": "Root Path Paper"}])
        config = make_config(self.tmp, scilib=self.tmp / "Sci-lib")  # 仓库根而不是 data 目录
        sources = discover_sources(config)
        self.assertEqual(sources["scilib"]["data_dir"], scilib)
        store = self.open_store(config)
        try:
            migrate_from_legacy(store, config)
            self.assertEqual(store.count(), 1)
        finally:
            self.close(store)


class PaperLibraryMappingTest(MigrateTestCase):
    def test_full_field_mapping(self):
        old_id = "f" * 64
        title = 'Beta: A/B test *star*? "quoted" <angle>|pipe'
        abs_pdf = self.tmp / "source" / "Beta Paper.pdf"
        make_pdf(abs_pdf, b"%PDF-1.4\n% beta\n")
        full_text = "Beta 正文内容。" * 50
        figures = [
            {"paper_id": old_id, "fig_index": 1,
             "image_path": "data\\images\\%s\\fig_001.jpeg" % old_id,
             "caption": "图1 说明", "context_text": "上下文一", "ai_description": "AI 描述",
             "figure_type": "data_plot", "ai_conclusion": "结论一"},
            {"paper_id": old_id, "fig_index": 2,
             "image_path": "data\\images\\%s\\fig_002.jpeg" % old_id,
             "caption": "", "context_text": "", "ai_description": "",
             "figure_type": "", "ai_conclusion": ""},
        ]
        pl = paperlibrary_fixture(
            self.tmp / "pl",
            [{"id": old_id, "filename": "Beta Paper.pdf", "title": title,
              "authors": json.dumps(["Carl", "Dora"], ensure_ascii=False), "doi": "10.2/beta",
              "year": 2021, "journal": "Journal of Tests", "folder": "分类A",
              "abs_path": str(abs_pdf), "full_text": full_text, "summary": "摘要内容",
              "parsed_at": "2026-11-04 00:26:23", "arxiv_id": "2401.00001",
              "abstract": "旧摘要", "deep_analysis": '{"summary": "深度"}',
              "deep_scores": '{"novelty": 8}', "deep_at": "2026-11-05 10:00:00"}],
            figures=figures,
            images=["data/images/%s/fig_001.jpeg" % old_id,
                    "data/images/%s/fig_002.jpeg" % old_id])
        config = make_config(self.tmp, pl=pl)
        store = self.open_store(config)
        try:
            stats = migrate_from_legacy(store, config)
            self.assertEqual(stats["paperLibrary"]["papers"], 1)
            self.assertEqual(stats["paperLibrary"]["figures"], 2)
            self.assertEqual(stats["figures"]["added"], 2)
            self.assertEqual(store.count(), 1)
            paper = store.list_papers()[0]
            pid = paper["id"]
            self.assertNotEqual(pid, old_id)          # 新卡片用新的 16 位 id
            self.assertEqual(paper["authors"], ["Carl", "Dora"])
            self.assertEqual(paper["venue"], "Journal of Tests")
            self.assertEqual(paper["folder"], "分类A")
            self.assertEqual(paper["arxivId"], "2401.00001")
            self.assertEqual(paper["summary"], "摘要内容")
            self.assertEqual(paper["addedAt"], "2026-11-04T00:26:23.000Z")
            self.assertEqual(store.get_text(pid), full_text)
            deep = store.repo.get_analysis(pid, "deep")
            self.assertEqual(deep["analysis"], {"summary": "深度"})
            self.assertEqual(deep["scores"], {"novelty": 8})
            self.assertEqual(deep["at"], "2026-11-05 10:00:00")
            self.assertEqual(store.repo.get_legacy_id(old_id), pid)
            # PDF：<newId>-<sanitize(标题)>.pdf，内容一致，标题里的非法字符被清掉
            pdf_file = paper["pdfFile"]
            self.assertRegex(pdf_file, r"^[a-f0-9]{16}-")
            self.assertNotIn("/", pdf_file)
            self.assertNotIn("\\", pdf_file)
            for bad in '*?"<>|:':
                self.assertNotIn(bad, pdf_file)
            self.assertTrue(Path(store.path_for_paper_file(pdf_file)).is_file())
            self.assertEqual(Path(store.path_for_paper_file(pdf_file)).read_bytes(),
                             abs_pdf.read_bytes())
            # 图片：复制到 data/images/<paperId>/，image_path 是相对 data 目录的路径
            rows = store.repo.list_figures(pid)
            self.assertEqual([r["fig_index"] for r in rows], [1, 2])
            self.assertEqual(rows[0]["caption"], "图1 说明")
            self.assertEqual(rows[0]["figure_type"], "data_plot")
            self.assertEqual(rows[0]["ai_conclusion"], "结论一")
            self.assertTrue(rows[0]["image_path"].startswith("images/%s/" % pid))
            self.assertTrue((Path(store.data_dir) / rows[0]["image_path"]).is_file())
            self.assertTrue((Path(store.data_dir) / rows[1]["image_path"]).is_file())
            self.assertEqual(stats["paperLibrary"]["pdfs"]["copied"], 1)
            self.assertEqual(stats["paperLibrary"]["images"]["copied"], 2)
        finally:
            self.close(store)

    def test_abstract_used_when_summary_empty(self):
        old_id = "ab" * 32
        pl = paperlibrary_fixture(self.tmp / "pl", [
            {"id": old_id, "filename": "x.pdf", "title": "Abstract Only",
             "authors": "[]", "abs_path": "", "full_text": "正文", "summary": "",
             "abstract": "只有摘要", "parsed_at": "2026-11-04 00:00:00"}])
        config = make_config(self.tmp, pl=pl)
        store = self.open_store(config)
        try:
            stats = migrate_from_legacy(store, config)
            self.assertTrue(stats["paperLibrary"]["pdfs"]["missing"] >= 1)  # 源缺失记 warning
            self.assertEqual(store.list_papers()[0]["summary"], "只有摘要")
            self.assertTrue(any("原文未复制" in w for w in stats["warnings"]))
        finally:
            self.close(store)


class DedupeTest(MigrateTestCase):
    def test_same_title_merges_and_scilib_user_fields_win(self):
        paper_id = "dd" * 8
        old_id = "ee" * 32
        abs_pdf = self.tmp / "src" / "Same Title.pdf"
        make_pdf(abs_pdf, b"%PDF-1.4\n% same\n")
        scilib = scilib_fixture(self.tmp / "scilib", [
            {"id": paper_id, "title": "Same  Title", "addedAt": "2026-11-03T00:00:00.000Z",
             "tags": ["mine"], "notes": "我的笔记", "status": "reading", "rating": 5,
             "starred": True}])
        pl = paperlibrary_fixture(self.tmp / "pl", [
            {"id": old_id, "filename": "Same Title.pdf", "title": " same title ",
             "authors": json.dumps(["Eve"]), "doi": "", "year": 2019,
             "abs_path": str(abs_pdf), "full_text": "PL 正文", "parsed_at": "2026-11-04 01:00:00"}])
        config = make_config(self.tmp, scilib=scilib, pl=pl)
        store = self.open_store(config)
        try:
            stats = migrate_from_legacy(store, config)
            self.assertEqual(store.count(), 1)
            paper = store.list_papers()[0]
            self.assertEqual(paper["id"], paper_id)          # 合并到 Sci-lib 那条（保留 id）
            self.assertEqual(paper["tags"], ["mine"])
            self.assertEqual(paper["notes"], "我的笔记")
            self.assertEqual((paper["status"], paper["rating"], paper["starred"]),
                             ("reading", 5, True))
            self.assertEqual(paper["year"], 2019)            # 题录字段补空
            self.assertEqual(paper["authors"], ["Eve"])
            self.assertEqual(store.get_text(paper_id), "PL 正文")
            self.assertEqual(store.repo.get_legacy_id(paper_id), paper_id)
            self.assertEqual(store.repo.get_legacy_id(old_id), paper_id)
            self.assertEqual(stats["papers"]["merged"], 1)
            self.assertEqual(stats["ids"]["scilib"], 1)
            self.assertEqual(stats["ids"]["paperlibrary"], 1)
            # Sci-lib 侧没有真 PDF，PL 侧有 → 复制后挂到卡片上且文件存在
            self.assertTrue(Path(store.path_for_paper_file(paper["pdfFile"])).is_file())
        finally:
            self.close(store)

    def test_same_doi_merges_after_normalizing_prefix_and_case(self):
        paper_id = "1122334455667788"
        old_id = "99" * 32
        scilib = scilib_fixture(self.tmp / "scilib", [
            {"id": paper_id, "title": "DOI Side A", "doi": "https://doi.org/10.1000/ABC",
             "addedAt": "2026-11-01T00:00:00.000Z"}])
        pl = paperlibrary_fixture(self.tmp / "pl", [
            {"id": old_id, "filename": "b.pdf", "title": "DOI Side B", "doi": "10.1000/abc",
             "authors": "[]", "abs_path": "", "full_text": "", "parsed_at": "2026-11-04 00:00:00"}])
        config = make_config(self.tmp, scilib=scilib, pl=pl)
        store = self.open_store(config)
        try:
            migrate_from_legacy(store, config)
            self.assertEqual(store.count(), 1)
            paper = store.list_papers()[0]
            self.assertEqual(paper["id"], paper_id)
            self.assertEqual(paper["title"], "DOI Side A")   # 标题不覆盖已有
            self.assertEqual(store.repo.get_legacy_id(old_id), paper_id)
        finally:
            self.close(store)


class IdempotencyTest(MigrateTestCase):
    def build(self):
        paper_id = "abcabcabcabcabca"
        old_id = "77" * 32
        abs_pdf = self.tmp / "src" / "Idem.pdf"
        make_pdf(abs_pdf, b"%PDF-1.4\n% idem\n")
        scilib = scilib_fixture(self.tmp / "scilib", [
            {"id": paper_id, "title": "Idem Paper", "tags": ["x"],
             "addedAt": "2026-11-01T00:00:00.000Z"}], texts=[(paper_id, "Sci 正文")])
        pl = paperlibrary_fixture(self.tmp / "pl", [
            {"id": old_id, "filename": "Idem.pdf", "title": "Another Paper",
             "authors": "[]", "abs_path": str(abs_pdf), "full_text": "PL 正文",
             "parsed_at": "2026-11-04 00:00:00"}],
            figures=[{"paper_id": old_id, "fig_index": 1,
                      "image_path": "data\\images\\%s\\fig_001.jpeg" % old_id,
                      "caption": "图"}],
            images=["data/images/%s/fig_001.jpeg" % old_id])
        return paper_id, old_id, make_config(self.tmp, scilib=scilib, pl=pl)

    def test_second_run_changes_nothing(self):
        paper_id, old_id, config = self.build()
        store = self.open_store(config)
        try:
            first = migrate_from_legacy(store, config)
            self.assertEqual(store.count(), 2)
            pdf_path = Path(store.path_for_paper_file(store.find_paper(paper_id)["pdfFile"]))
            stat_before = pdf_path.stat().st_mtime_ns
            backups_before = len(list(store.backup_dir.glob("library-*.json")))
            papers_before = sorted(p["id"] for p in store.list_papers())
            figures_before = len(store.repo.list_figures())
            texts_before = sorted(store.list_text_keys())
            second = migrate_from_legacy(store, config)
            self.assertFalse(second)                      # 无事可做：返回 {}（app.py 静默）
            self.assertEqual(sorted(p["id"] for p in store.list_papers()), papers_before)
            self.assertEqual(len(store.repo.list_figures()), figures_before)
            self.assertEqual(sorted(store.list_text_keys()), texts_before)
            self.assertEqual(pdf_path.stat().st_mtime_ns, stat_before)
            self.assertEqual(len(list(store.backup_dir.glob("library-*.json"))), backups_before)
            self.assertTrue(first["papers"]["added"] >= 2)
            # 第三次仍然零写入
            self.assertFalse(migrate_from_legacy(store, config))
            self.assertEqual(store.count(), 2)
        finally:
            self.close(store)

    def test_third_party_written_store_is_untouched(self):
        """库里已有用户自己的记录且同标题时：迁移只补空，不重复插入。"""
        paper_id, old_id, config = self.build()
        store = self.open_store(config)
        try:
            migrate_from_legacy(store, config)
            paper = store.find_paper(paper_id)
            store.patch_paper(paper_id, {"notes": "用户后来写的笔记"})
            self.assertFalse(migrate_from_legacy(store, config))
            self.assertEqual(store.find_paper(paper_id)["notes"], "用户后来写的笔记")
        finally:
            self.close(store)


class DryRunTest(MigrateTestCase):
    def test_dry_run_writes_nothing(self):
        paper_id = "drydrydrydrydryd"
        old_id = "66" * 32
        abs_pdf = self.tmp / "src" / "Dry.pdf"
        make_pdf(abs_pdf, b"%PDF-1.4\n% dry\n")
        scilib = scilib_fixture(self.tmp / "scilib", [
            {"id": paper_id, "title": "Dry Paper", "tags": ["d"]}], texts=[(paper_id, "干跑正文")])
        pl = paperlibrary_fixture(self.tmp / "pl", [
            {"id": old_id, "filename": "Dry.pdf", "title": "Dry PL", "authors": "[]",
             "abs_path": str(abs_pdf), "full_text": "PL 干跑", "parsed_at": "2026-11-04 00:00:00"}],
            figures=[{"paper_id": old_id, "fig_index": 1,
                      "image_path": "data\\images\\%s\\fig_001.jpeg" % old_id}],
            images=["data/images/%s/fig_001.jpeg" % old_id])
        config = make_config(self.tmp, scilib=scilib, pl=pl)
        store = self.open_store(config)
        try:
            stats = migrate_from_legacy(store, config, dry_run=True)
            self.assertTrue(stats["dryRun"])
            self.assertEqual(stats["libraryJson"]["papers"], 1)
            self.assertEqual(stats["paperLibrary"]["papers"], 1)
            self.assertEqual(stats["paperLibrary"]["figures"], 1)
            self.assertEqual(stats["papers"]["added"] + stats["papers"]["merged"], 2)
            self.assertEqual(stats["figures"]["added"], 1)
            self.assertEqual(stats["paperLibrary"]["pdfs"]["copied"], 1)
            self.assertEqual(stats["paperLibrary"]["images"]["copied"], 1)
            # 一张表、一个文件、一份备份都不该写
            self.assertEqual(store.count(), 0)
            self.assertEqual(store.repo.list_figures(), [])
            self.assertEqual(store.list_text_keys(), [])
            self.assertFalse(list(Path(store.papers_dir).glob("*.pdf")))
            self.assertFalse(list(store.backup_dir.glob("library-*.json")))
            self.assertFalse((Path(store.data_dir) / "images").exists())
            self.assertIsNone(store.repo.get_legacy_id(old_id))
            self.assertIsNone(store.repo.get_legacy_id(paper_id))
        finally:
            self.close(store)

    def test_apply_after_dry_run_matches_counts(self):
        old_id = "55" * 32
        pl = paperlibrary_fixture(self.tmp / "pl", [
            {"id": old_id, "filename": "a.pdf", "title": "Dry Then Apply", "authors": "[]",
             "abs_path": "", "full_text": "正文", "parsed_at": "2026-11-04 00:00:00"}])
        config = make_config(self.tmp, pl=pl)
        store = self.open_store(config)
        try:
            stats = migrate_from_legacy(store, config, dry_run=True)
            self.assertEqual(store.count(), 0)
            migrate_from_legacy(store, config)
            self.assertEqual(store.count(), 1)
            self.assertEqual(stats["papers"]["added"], 1)
        finally:
            self.close(store)


class MergeFilesTest(MigrateTestCase):
    def test_duplicate_title_figures_both_kept(self):
        first, second = "aa" * 32, "bb" * 32
        figures = [
            {"paper_id": first, "fig_index": 1,
             "image_path": "data\\images\\%s\\fig_001.jpeg" % first, "caption": "第一张"},
            {"paper_id": second, "fig_index": 1,
             "image_path": "data\\images\\%s\\fig_001.jpeg" % second, "caption": "第二张"},
        ]
        pl = paperlibrary_fixture(self.tmp / "pl", [
            {"id": first, "filename": "a.pdf", "title": "Dup Title", "authors": "[]",
             "abs_path": "", "full_text": "a", "parsed_at": "2026-11-04 00:00:00"},
            {"id": second, "filename": "b.pdf", "title": "Dup Title", "authors": "[]",
             "abs_path": "", "full_text": "b", "parsed_at": "2026-11-04 00:00:01"}],
            figures=figures,
            images=["data/images/%s/fig_001.jpeg" % first,
                    "data/images/%s/fig_001.jpeg" % second])
        config = make_config(self.tmp, pl=pl)
        store = self.open_store(config)
        try:
            migrate_from_legacy(store, config)
            self.assertEqual(store.count(), 1)
            pid = store.list_papers()[0]["id"]
            rows = store.repo.list_figures(pid)
            self.assertEqual(len(rows), 2)                 # 两篇同名的图都在，互不覆盖
            files = [Path(store.data_dir) / row["image_path"] for row in rows]
            self.assertEqual(len({str(path) for path in files}), 2)
            for path in files:
                self.assertTrue(path.is_file())
            self.assertEqual(store.repo.get_legacy_id(first), pid)
            self.assertEqual(store.repo.get_legacy_id(second), pid)
            # 二次运行不重复插图表
            self.assertFalse(migrate_from_legacy(store, config))
            self.assertEqual(len(store.repo.list_figures(pid)), 2)
        finally:
            self.close(store)


class BackupTest(MigrateTestCase):
    def test_backup_written_before_apply(self):
        scilib = scilib_fixture(self.tmp / "scilib", [{"id": "b" * 16, "title": "Backup Me"}])
        config = make_config(self.tmp, scilib=scilib)
        store = self.open_store(config)
        try:
            migrate_from_legacy(store, config)
            backups = list(store.backup_dir.glob("library-*.json"))
            self.assertEqual(len(backups), 1)
            payload = json.loads(backups[0].read_text(encoding="utf-8"))
            self.assertIn("papers", payload)
        finally:
            self.close(store)


class NoSourceTest(MigrateTestCase):
    def test_no_legacy_sources_returns_empty(self):
        config = make_config(self.tmp)  # 既没有 Sci-lib 也没有 PaperLibrary
        store = self.open_store(config)
        try:
            self.assertFalse(migrate_from_legacy(store, config))
            self.assertEqual(store.count(), 0)
            stats = migrate_from_legacy(store, config, dry_run=True)   # 干跑仍返回统计
            self.assertFalse(stats["libraryJson"]["found"])
            self.assertFalse(stats["paperLibrary"]["found"])
        finally:
            self.close(store)


@unittest.skipUnless(PAPERLIBRARY_REAL.joinpath("papers.db").is_file()
                     and SCILIB_REAL.joinpath("library.json").is_file(),
                     "本机没有真实旧库，跳过真实库干跑")
class RealSourcesTest(MigrateTestCase):
    def test_real_dry_run_counts_match_sources(self):
        config = Config(root=ROOT, data_dir=self.tmp / "data")  # root=仓库根 → 默认找同级旧库
        sources = discover_sources(config)
        self.assertEqual(sources["paperlibrary"]["data_dir"], PAPERLIBRARY_REAL)
        self.assertEqual(sources["scilib"]["data_dir"], SCILIB_REAL)
        store = self.open_store(config)
        try:
            stats = migrate_from_legacy(store, config, dry_run=True)
            self.assertEqual(stats["libraryJson"]["papers"], 1)
            self.assertEqual(stats["paperLibrary"]["papers"], 76)
            self.assertEqual(stats["paperLibrary"]["figures"], 1441)
            self.assertEqual(stats["paperLibrary"]["pdfs"]["copied"], 76)
            self.assertEqual(stats["paperLibrary"]["images"]["copied"], 1441)
            self.assertEqual(stats["ids"]["paperlibrary"], 76)
            self.assertEqual(stats["figures"]["added"], 1441)
            self.assertEqual(stats["papers"]["added"] + stats["papers"]["merged"]
                             + stats["papers"]["skipped"], 77)
            self.assertEqual(store.count(), 0)             # 干跑一行没写
        finally:
            self.close(store)


if __name__ == "__main__":
    unittest.main()
