"""unit test：paperlib/domain/importer.py（移植自 Sci-lib server/domain/importer.js）。

覆盖：JSON / BibTeX / Markdown 导出、JSON 导入合并策略、按内容扫描（重复组/孤儿/引用）、
关联 PDF、原文与补充材料改名（含记录与文件对不上时的自愈）。

跑法：python -m unittest test.test_importer -v
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from paperlib.data.store import Store
from paperlib.domain.library import Library


def paper(**over):
    base = {
        "id": "p1", "title": "Cucurbituril Review", "authors": ["Werner M. Nau"],
        "year": 2015, "type": "journal", "venue": "Chemical Society Reviews",
        "volume": "44", "issue": "2", "pages": "394-418", "publisher": "RSC",
        "doi": "10.1039/c4cs00273c", "url": "https://doi.org/10.1039/c4cs00273c",
        "tags": ["CB"], "keywords": ["cucurbituril"], "status": "read", "rating": 5,
        "starred": True, "starredAt": "2026-11-01T00:00:00.000Z",
        "summary": "a review", "notes": "", "aiSummary": "", "aiSummaryAt": "",
        "attachments": [], "chat": [], "pdfFile": "",
        "bibtexKey": "", "addedAt": "2026-10-01T00:00:00.000Z",
        "updatedAt": "2026-10-01T00:00:00.000Z",
    }
    base.update(over)
    return base


class ImporterTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(self.tmp.name)
        self.store.load()
        self.library = Library(self.store)
        self.addCleanup(self.store.repo.close)      # Windows：先关库再删临时目录

    def add(self, **over):
        item = paper(**over)
        self.store.insert_paper(item)
        self.library.index.set(item)
        return item

    def write_pdf(self, name, content=b"%PDF-1.4 fake"):
        target = Path(self.store.papers_dir) / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target


class ExportTest(ImporterTestBase):
    def test_export_json_shape(self):
        self.add()
        payload = self.library.export_json()
        self.assertEqual(payload["version"], 1)
        self.assertEqual(payload["app"], "literature-vault")
        self.assertRegex(payload["exportedAt"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
        self.assertEqual(len(payload["papers"]), 1)
        self.assertEqual(payload["papers"][0]["id"], "p1")

    def test_export_bibtex_all_and_subset(self):
        self.add(id="p1", bibtexKey="Nau2015")
        self.add(id="p2", title="Other", bibtexKey="Other2020", year=2020)
        text = self.library.export_bibtex()
        self.assertTrue(text.endswith("\n"))
        self.assertIn("@article{Nau2015,", text)
        self.assertIn("@article{Other2020,", text)
        self.assertEqual(text.count("@article{"), 2)
        only = self.library.export_bibtex(["p2"])
        self.assertNotIn("Nau2015", only)
        self.assertIn("Other2020", only)

    def test_export_bibtex_dedupes_keys(self):
        self.add(id="p1", bibtexKey="same")
        self.add(id="p2", title="Second", bibtexKey="same")
        text = self.library.export_bibtex()
        self.assertIn("@article{same,", text)
        self.assertIn("@article{sameb,", text)       # 第二个依次加 b/c…

    def test_export_markdown_joins_with_separator(self):
        self.add(id="p1", bibtexKey="A2015")
        self.add(id="p2", title="Second", year=2020, bibtexKey="B2020")
        text = self.library.export_markdown()
        self.assertTrue(text.endswith("\n"))
        self.assertIn("\n\n---\n\n", text)
        self.assertEqual(text.count("# Cucurbituril Review"), 1)
        one = self.library.export_markdown(["p1"])
        self.assertNotIn("Second", one)


class ImportTest(ImporterTestBase):
    def test_import_array_adds_and_skips_duplicates(self):
        self.add(id="p1", doi="10.1/x", title="Existing")
        payload = [
            {"title": "Existing", "doi": "10.1/x"},                 # 同 DOI → 跳过
            {"title": "Brand new", "doi": "10.2/y"},                # 新增
        ]
        result = self.library.import_json(payload)
        self.assertEqual(result, {"added": 1, "updated": 0, "skipped": 1, "total": 2})
        self.assertEqual(self.store.count(), 2)

    def test_import_wrapper_payload_and_overwrite(self):
        existing = self.add(id="p1", doi="10.1/x", title="Old title")
        payload = {"papers": [{"title": "New title", "doi": "10.1/x", "year": 2021}]}
        skipped = self.library.import_json(payload)
        self.assertEqual(skipped["skipped"], 1)
        self.assertEqual(self.store.find_paper("p1")["title"], "Old title")
        updated = self.library.import_json(payload, {"overwrite": True})
        self.assertEqual(updated["updated"], 1)
        paper_now = self.store.find_paper("p1")
        self.assertEqual(paper_now["title"], "New title")
        self.assertEqual(paper_now["id"], "p1")                       # id 保留
        self.assertEqual(paper_now["addedAt"], existing["addedAt"])   # addedAt 保留
        self.assertEqual(paper_now["year"], 2021)

    def test_import_rejects_payload_without_papers(self):
        with self.assertRaises(ValueError) as ctx:
            self.library.import_json({"nope": 1})
        self.assertIn("导入内容里找不到 papers 数组", str(ctx.exception))

    def test_export_then_import_roundtrip(self):
        self.add(id="p1", bibtexKey="A2015")
        self.add(id="p2", title="Second", year=2020, bibtexKey="B2020", doi="10.2/second")
        exported = json.loads(json.dumps(self.library.export_json(), ensure_ascii=False))
        other_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(other_tmp.cleanup)
        store2 = Store(other_tmp.name)
        store2.load()
        library2 = Library(store2)
        self.addCleanup(store2.repo.close)
        result = library2.import_json(exported)
        self.assertEqual(result["added"], 2)
        self.assertEqual(store2.count(), 2)


class ScanTest(ImporterTestBase):
    def test_scan_finds_duplicates_orphans_and_linked(self):
        self.add(id="p1", pdfFile="a.pdf")
        self.write_pdf("a.pdf", b"AAA")
        self.write_pdf("b.pdf", b"AAA")       # 与 a 内容相同、无引用 → 可删
        self.write_pdf("c.pdf", b"BBBBBB")    # 无引用的孤儿
        scan = self.library.scan_pdf_contents()
        self.assertEqual(scan["total"], 3)
        self.assertEqual(scan["bytes"], 12)
        self.assertEqual(scan["duplicates"], 1)
        self.assertEqual(scan["wastedBytes"], 3)
        self.assertEqual(len(scan["duplicateGroups"]), 1)
        group = scan["duplicateGroups"][0]
        self.assertEqual(group["keep"], "a.pdf")
        self.assertEqual(group["keepReason"], "有记录引用它")
        self.assertEqual(group["removable"], ["b.pdf"])
        self.assertEqual(group["blocked"], [])
        self.assertEqual(len(group["sha256"]), 16)
        self.assertEqual([row["file"] for row in scan["orphans"]], ["c.pdf", "b.pdf"])
        self.assertEqual([row["file"] for row in scan["linked"]], ["a.pdf"])
        self.assertEqual(scan["linked"][0]["usedBy"], [{"id": "p1", "title": "Cucurbituril Review"}])

    def test_scan_blocks_removal_of_referenced_copies(self):
        self.add(id="p1", pdfFile="a.pdf")
        self.add(id="p2", title="Second", pdfFile="b.pdf")
        first = self.write_pdf("a.pdf", b"SAME")
        second = self.write_pdf("b.pdf", b"SAME")
        os.utime(first, (1000, 1000))
        os.utime(second, (2000, 2000))
        scan = self.library.scan_pdf_contents()
        group = scan["duplicateGroups"][0]
        self.assertEqual(group["keep"], "a.pdf")          # 更早的一份
        self.assertEqual(group["removable"], [])
        self.assertEqual(scan["duplicates"], 0)
        self.assertEqual([row["file"] for row in group["blocked"]], ["b.pdf"])
        self.assertEqual(scan["orphans"], [])
        self.assertEqual(len(scan["linked"]), 2)

    def test_scan_keep_reason_when_nothing_referenced(self):
        first = self.write_pdf("x.pdf", b"ZZZ")
        second = self.write_pdf("y.pdf", b"ZZZ")
        os.utime(first, (1000, 1000))
        os.utime(second, (2000, 2000))
        group = self.library.scan_pdf_contents()["duplicateGroups"][0]
        self.assertEqual(group["keep"], "x.pdf")
        self.assertEqual(group["keepReason"], "保留最早的一份")
        self.assertEqual(group["removable"], ["y.pdf"])

    def test_scan_empty_dir(self):
        self.assertEqual(self.library.scan_pdf_contents(),
                         {"total": 0, "bytes": 0, "duplicateGroups": [], "duplicates": 0,
                          "wastedBytes": 0, "orphans": [], "linked": []})

    def test_scan_pdfs_marks_used(self):
        self.add(id="p1", pdfFile="a.pdf")
        self.write_pdf("a.pdf")
        rows = self.library.scan_pdfs()
        self.assertEqual([row["file"] for row in rows], ["a.pdf"])
        self.assertTrue(rows[0]["used"])
        self.assertEqual(rows[0]["displayName"], "a.pdf")


class AssociateTest(ImporterTestBase):
    def test_associate_ok(self):
        self.add(id="p1")
        self.write_pdf("found.pdf")
        result = self.library.associate_pdf("p1", "/found.pdf")
        self.assertTrue(result["ok"])
        self.assertEqual(result["file"], "found.pdf")
        self.assertEqual(result["paper"]["pdfFile"], "found.pdf")
        self.assertEqual(self.store.find_paper("p1")["pdfFile"], "found.pdf")

    def test_associate_errors(self):
        self.add(id="p1")
        self.assertEqual(self.library.associate_pdf("nope", "a.pdf"),
                         {"ok": False, "error": "找不到这条文献"})
        self.assertEqual(self.library.associate_pdf("p1", "a.txt"),
                         {"ok": False, "error": "文件名不合法"})
        self.assertEqual(self.library.associate_pdf("p1", "a.pdf"),
                         {"ok": False, "error": "文件不存在：a.pdf"})
        self.assertEqual(self.library.associate_pdf("p1", "../a.pdf"),
                         {"ok": False, "error": "文件不存在：../a.pdf"})


class TidyTest(ImporterTestBase):
    def test_tidy_pdf_renames_to_title(self):
        self.add(id="p1", pdfFile="abcdef1234567890-old-name.pdf")
        self.write_pdf("abcdef1234567890-old-name.pdf")
        result = self.library.tidy_pdf_names()
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["old"], "abcdef1234567890-old-name.pdf")
        self.assertEqual(result["results"][0]["next"], "Cucurbituril Review.pdf")
        self.assertEqual(result["results"][0]["paperId"], "p1")
        self.assertTrue((Path(self.store.papers_dir) / "Cucurbituril Review.pdf").is_file())
        self.assertEqual(self.store.find_paper("p1")["pdfFile"], "Cucurbituril Review.pdf")

    def test_tidy_pdf_already_standard(self):
        self.add(id="p1", pdfFile="Cucurbituril Review.pdf")
        self.write_pdf("Cucurbituril Review.pdf")
        result = self.library.tidy_pdf_names()
        self.assertEqual(result["changed"], 0)
        self.assertEqual(result["results"][0]["reason"], "已经规范")

    def test_tidy_pdf_avoids_name_collision(self):
        self.add(id="p1", pdfFile="Cucurbituril Review.pdf")
        self.add(id="p2", title="Cucurbituril Review", pdfFile="ffffffff-old.pdf")
        self.write_pdf("Cucurbituril Review.pdf")
        self.write_pdf("ffffffff-old.pdf")
        result = self.library.tidy_pdf_names()
        renamed = [row for row in result["results"] if row["changed"]]
        self.assertEqual(len(renamed), 1)
        self.assertEqual(renamed[0]["next"], "Cucurbituril Review (2).pdf")

    def test_tidy_attachment_names_and_callback(self):
        att = {"file": "p1-乱码.pdf", "originalName": "乱码.pdf", "size": 3, "kind": "pdf"}
        self.add(id="p1", attachments=[att, {"file": "p1-b.pdf", "originalName": "b.pdf"}])
        (Path(self.store.attachments_dir) / "p1-乱码.pdf").write_bytes(b"abc")
        (Path(self.store.attachments_dir) / "p1-b.pdf").write_bytes(b"de")
        seen = []
        result = self.library.tidy_attachment_names(
            lambda pid, old, new: seen.append((pid, old, new)))
        names = [row["next"] for row in result["results"] if row["changed"]]
        self.assertEqual(names, ["p1-Cucurbituril Review SI.pdf",
                                 "p1-Cucurbituril Review SI (2).pdf"])
        self.assertEqual(len(seen), 2)
        self.assertTrue((Path(self.store.attachments_dir) / names[0]).is_file())
        stored = self.store.find_paper("p1")["attachments"]
        self.assertEqual([row["file"] for row in stored], names)

    def test_tidy_attachment_reclaims_file_by_id_prefix(self):
        self.add(id="p1", attachments=[{"file": "gone.pdf", "originalName": "gone.pdf"}])
        (Path(self.store.attachments_dir) / "p1-something.pdf").write_bytes(b"xyz")
        result = self.library.tidy_attachment_names()
        self.assertEqual(result["repaired"], 1)
        row = result["results"][0]
        self.assertTrue(row["changed"])
        self.assertTrue(row["repaired"])
        self.assertIn("按 id 前缀认领", row["reason"])
        self.assertEqual(row["next"], "p1-Cucurbituril Review SI.pdf")

    def test_tidy_attachment_missing_dir(self):
        result = self.library.tidy_attachment_names()
        self.assertEqual(result, {"changed": 0, "total": 0, "results": [], "repaired": 0})


if __name__ == "__main__":
    unittest.main()
