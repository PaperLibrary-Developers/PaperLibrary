"""T3 单测：原文 / 全文 / 附件 / 引用 / PDF（api/files.py）与导入（api/import_.py）。

对应契约：pdf.extract_text(path) → Text
（Node 参考：server/api/files.js 272 行 + server/api/import.js 210 行）。
HTTP 层用真实 HttpServer（端口 0 随机）验证 Range / ETag / 304 与中文文件名的 Content-Disposition；
其余端点用 FakeCtx 直调 handler，避免依赖前端与外部网络。
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote
from zipfile import ZipFile

import pymupdf
from openpyxl import Workbook

from paperlib.api import files, import_, papers as papers_api
from paperlib.config import Config
from paperlib.data.store import Store
from paperlib.domain import fullread
from paperlib.http.router import Router
from paperlib.http.server import HttpServer
from paperlib.http.static import StaticHandler
from paperlib.routes import ROUTES
from paperlib.services import create_services
from shared.pure import citation

ROOT = Path(__file__).resolve().parent.parent
CJK_FONT = r"C:\Windows\Fonts\msyh.ttc"
TITLE = "金属有机笼的气体分离 测试论文"
SAMPLE_DOI = "10.1021/jacs.3b12345"
WORD97_FIXTURE = Path(__file__).with_name("fixtures") / "si-word97.doc"


def pdf_bytes():
    """一页 PDF（英文正文 + 中文标题行），PyMuPDF 生成，随用随建。"""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 96), "Porous Metal-Organic Cages for Gas Separation", fontsize=15)
    page.insert_text((72, 124), "Khaleel I. Assaf and Werner M. Nau", fontsize=10)
    page.insert_text((72, 190),
                     "Abstract: Porous metal-organic cages were synthesized and characterized.",
                     fontsize=9)
    page.insert_text((72, 260), "doi: " + SAMPLE_DOI, fontsize=9)
    if os.path.isfile(CJK_FONT):
        page.insert_text((72, 320), "金属有机笼：设计、合成与应用", fontsize=13,
                         fontname="cjk", fontfile=CJK_FONT)
    doc.subset_fonts()  # 不子集化会把整个 msyh.ttc（~19MB）嵌进去，fixture 会变成 19MB
    data = doc.write()
    doc.close()
    return data


class Headers(dict):
    def get(self, key, default=None):
        lowered = str(key).lower()
        for name, value in self.items():
            if str(name).lower() == lowered:
                return value
        return default


class FakeCtx:
    """只实现 handler 用到的那几个 Ctx 方法（json/fail/read_body/read_json/send_file）。"""

    def __init__(self, services, method="POST", params=None, headers=None, query=None,
                 body=b"", json_body=None):
        self.services = services
        self.method = method
        self.params = params or {}
        self.headers = Headers(headers or {})
        self._query = query or {}
        self._body = body
        self._json_body = json_body
        self.status = None
        self.payload = None
        self.file_args = []

    def query(self, name, default=""):
        return self._query.get(name, default)

    def read_body(self, limit=40 * 1024 * 1024):
        if len(self._body) > limit:
            raise ValueError("请求体过大（上限 %dMB）" % round(limit / 1024 / 1024))
        return self._body

    def read_json(self, limit=None):
        return self._json_body if self._json_body is not None else {}

    def json(self, status, data):
        self.status = status
        self.payload = data

    def fail(self, status, message, extra=None):
        body = {"ok": False, "error": message}
        if extra:
            body.update(extra)
        self.status = status
        self.payload = body
        return None

    def send_file(self, abs_path, inline=None, download=None, cache=None, content_type=None):
        self.file_args.append({"path": str(abs_path), "inline": inline, "download": download,
                               "cache": cache, "content_type": content_type})
        self.status = 200
        self.payload = None


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.PDF = pdf_bytes()

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="t3-files-")
        self.config = Config(root=ROOT, data_dir=Path(self.tmp), port=4392, host="127.0.0.1")
        self.config.started_at = time.time()
        self.store = Store(self.config.data_dir)
        self.store.load()
        self.services = create_services(self.config, self.store)
        self.paper = self.services.library.create({"title": TITLE, "authors": ["张三", "Li Si"]})

    def tearDown(self):
        try:
            self.store.repo.close()  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass

    # 小工具
    def upload(self, ctx_body=None, filename="原始 论文.pdf", paper_id=None):
        ctx = FakeCtx(
            self.services,
            headers={"X-Filename": quote(filename), "X-Paper-Id": paper_id or self.paper["id"]},
            body=ctx_body if ctx_body is not None else self.PDF,
        )
        files.upload(ctx)
        return ctx


class UploadTest(Base):
    def test_upload_saves_file_and_extracts_fulltext(self):
        ctx = self.upload()
        self.assertEqual(ctx.status, 201)
        payload = ctx.payload
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["pdfFile"].startswith(self.paper["id"] + "-"))
        self.assertTrue(payload["pdfFile"].endswith(".pdf"))
        self.assertEqual(payload["size"], len(self.PDF))
        self.assertTrue(payload["extract"]["ok"])
        self.assertEqual(payload["extract"]["pages"], 1)
        self.assertGreater(payload["extract"]["chars"], 100)

        saved = self.store.find_paper(self.paper["id"])
        self.assertEqual(saved["pdfFile"], payload["pdfFile"])
        abs_path = self.store.path_for_paper_file(payload["pdfFile"])
        self.assertTrue(os.path.isfile(abs_path))
        # 全文入库：检索（domain/query）从 store 读正文
        self.assertTrue(self.store.has_text(self.paper["id"]))
        self.assertIn("Cages", self.store.get_text(self.paper["id"]))

    def test_upload_rejects_non_pdf_name(self):
        ctx = self.upload(filename="说明.txt")
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "目前只支持上传 PDF 文件")

    def test_upload_rejects_bytes_without_pdf_header(self):
        ctx = self.upload(ctx_body=b"definitely not a pdf")
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "这个文件看起来不是 PDF")

    def test_upload_without_paper_id_only_stores_file(self):
        ctx = FakeCtx(self.services, headers={"X-Filename": quote("孤立.pdf")}, body=self.PDF)
        files.upload(ctx)
        self.assertEqual(ctx.status, 201)
        self.assertTrue(ctx.payload["pdfFile"].startswith("unlinked-"))
        self.assertIsNone(ctx.payload["extract"])
        self.assertTrue(os.path.isfile(self.store.path_for_paper_file(ctx.payload["pdfFile"])))

    def test_upload_oversize_is_413(self):
        with patch.object(files, "PDF_LIMIT", 10):
            ctx = self.upload()
        self.assertEqual(ctx.status, 413)
        self.assertEqual(ctx.payload["error"], "PDF 超过 300MB 上限")


class FulltextTest(Base):
    def test_fulltext_get_after_upload(self):
        self.upload()
        ctx = FakeCtx(self.services, method="GET", params={"id": self.paper["id"]})
        files.fulltext_get(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["chars"], len(self.store.get_text(self.paper["id"])))
        self.assertIn("Metal-Organic", ctx.payload["text"])

    def test_fulltext_get_missing_returns_404(self):
        ctx = FakeCtx(self.services, method="GET", params={"id": self.paper["id"]})
        files.fulltext_get(ctx)
        self.assertEqual(ctx.status, 404)
        self.assertEqual(ctx.payload["error"], "还没有提取过全文")

    def test_fulltext_save_text(self):
        ctx = FakeCtx(self.services, params={"id": self.paper["id"]}, json_body={"text": "   "})
        files.fulltext_save_text(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "正文是空的")

        ctx = FakeCtx(self.services, params={"id": self.paper["id"]}, json_body={"text": "识别出来的正文"})
        files.fulltext_save_text(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["chars"], len("识别出来的正文"))
        self.assertEqual(self.store.get_text(self.paper["id"]), "识别出来的正文")

    def test_fulltext_extract_requires_pdf(self):
        ctx = FakeCtx(self.services, params={"id": self.paper["id"]})
        files.fulltext_extract(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "这条文献还没有关联 PDF，无法提取全文")

    def test_fulltext_extract_unknown_paper_404(self):
        ctx = FakeCtx(self.services, params={"id": "0" * 16})
        files.fulltext_extract(ctx)
        self.assertEqual(ctx.status, 404)

    def test_stats_available_is_null_until_probed(self):
        # Node 语义：availabilityKnown() 在首次探测前是 null（不是 true/false）
        from paperlib.adapters import pdf_text

        pdf_text._probed = False
        pdf_text._available = None
        try:
            self.assertIsNone(self.services.fulltext.availability_known())
            self.assertIsNone(self.services.fulltext.stats()["available"])
        finally:
            self.assertTrue(self.services.fulltext.available())
        self.assertTrue(self.services.fulltext.availability_known())
        self.assertTrue(self.services.fulltext.stats()["available"])

    def test_fulltext_batch_extracts_pending(self):
        self.upload()
        second = self.services.library.create({"title": "第二篇"})
        self.store.attach_file(second["id"], self.PDF, {"fileName": second["id"] + "-second.pdf"})
        ctx = FakeCtx(self.services)
        files.fulltext_batch(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["total"], 1)
        self.assertEqual(ctx.payload["succeeded"], 1)
        self.assertEqual(ctx.payload["failed"], 0)
        self.assertTrue(self.store.has_text(second["id"]))


class AttachmentsTest(Base):
    def _upload_attachment(self, content=b"supplementary info", name="SI 说明.txt"):
        ctx = FakeCtx(self.services, params={"id": self.paper["id"]},
                      headers={"X-Filename": quote(name)}, body=content)
        files.attachments_upload(ctx)
        return ctx

    def test_upload_lists_extracts_and_removes(self):
        ctx = self._upload_attachment()
        self.assertEqual(ctx.status, 201)
        record = ctx.payload["attachment"]
        self.assertTrue(record["file"].startswith(self.paper["id"] + "-"))
        self.assertTrue(record["file"].endswith(".txt"))
        self.assertEqual(record["originalName"], "SI 说明.txt")
        self.assertEqual(record["size"], len(b"supplementary info"))
        self.assertTrue(record["hasText"])
        self.assertGreater(record["textChars"], 0)
        self.assertEqual(len(ctx.payload["attachments"]), 1)
        self.assertTrue(ctx.payload["extract"]["ok"])

        # 附件文本落 store（键 = att-<附件文件名>）
        self.assertTrue(self.services.fulltext.has_attachment_text(self.paper["id"], record["file"]))
        self.assertIn("supplementary", self.store.get_text("att-" + record["file"]))

        list_ctx = FakeCtx(self.services, method="GET", params={"id": self.paper["id"]})
        files.attachments_list(list_ctx)
        self.assertEqual(list_ctx.status, 200)
        self.assertTrue(list_ctx.payload["attachments"][0]["hasTextFile"])

        download = FakeCtx(self.services, method="GET", params={"id": self.paper["id"], "file": record["file"]})
        files.attachment_download(download)
        self.assertEqual(download.status, 200)
        self.assertEqual(download.file_args[0]["path"], self.store.path_for_attachment(record["file"]))
        self.assertEqual(download.file_args[0]["inline"], "SI 说明.txt")

        remove = FakeCtx(self.services, method="DELETE", params={"id": self.paper["id"], "file": record["file"]})
        files.attachment_remove(remove)
        self.assertEqual(remove.status, 200)
        self.assertEqual(remove.payload["attachments"], [])
        self.assertFalse(os.path.exists(self.store.path_for_attachment(record["file"])))
        self.assertFalse(self.services.fulltext.has_attachment_text(self.paper["id"], record["file"]))

        again = FakeCtx(self.services, params={"id": self.paper["id"], "file": record["file"]})
        files.attachment_remove(again)
        self.assertEqual(again.status, 404)
        self.assertEqual(again.payload["error"], "找不到这个附件")

    def test_attachment_names_do_not_collide(self):
        first = self._upload_attachment(b"one")
        second = FakeCtx(self.services, params={"id": self.paper["id"]},
                         headers={"X-Filename": quote("SI 说明.txt")}, body=b"two")
        files.attachments_upload(second)
        self.assertNotEqual(first.payload["attachment"]["file"], second.payload["attachment"]["file"])
        self.assertEqual(len(second.payload["attachments"]), 2)

    def test_uploads_word_files_and_spreadsheet_into_ai_reference(self):
        docx = BytesIO()
        w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
        with ZipFile(docx, "w") as archive:
            archive.writestr("word/document.xml", f'<w:document {w}><w:body>'
                '<w:p><w:r><w:t>补充实验</w:t><w:tab/><w:t>结论</w:t></w:r></w:p>'
                '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>项目</w:t></w:r></w:p></w:tc>'
                '<w:tc><w:p><w:r><w:t>测定值</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
                '</w:body></w:document>')
            archive.writestr("word/footnotes.xml", f'<w:footnotes {w}>'
                '<w:footnote w:id="1"><w:p><w:r><w:t>补充脚注</w:t></w:r></w:p></w:footnote>'
                '</w:footnotes>')
        xlsx = BytesIO()
        workbook = Workbook()
        workbook.active.title = "原始数据"
        workbook.active.append(["样品编号", None, "读数"])
        workbook.active.append(["S17", None, 0])
        workbook.create_sheet("结果").append(["有效", False])
        workbook.save(xlsx)
        workbook.close()

        cases = [
            ("方法.DOCX", docx.getvalue(), "docx", "补充实验\t结论\n项目\t测定值\n\n补充脚注"),
            ("旧版.DOC", WORD97_FIXTURE.read_bytes(), "doc", "补充材料：样品编号 S17。\nSample\tValue\nS17\t7.25"),
            ("表格.xlsx", xlsx.getvalue(), "xlsx", "工作表：原始数据\n样品编号\t\t读数\nS17\t\t0\n工作表：结果\n有效\tFalse"),
            ("读数.rtf", rb"{\rtf1\ansi SI result\par Reading = 7.25}", "rtf", "SI result\nReading = 7.25"),
            ("附件.pdf", self.PDF, "pdf", "Porous Metal-Organic Cages"),
        ]
        for name, data, kind, expected in cases:
            with self.subTest(name=name):
                ctx = self._upload_attachment(data, name)
                self.assertEqual(ctx.status, 201)
                record = ctx.payload["attachment"]
                self.assertEqual(record["kind"], kind)
                self.assertTrue(record["hasText"])
                text = self.services.fulltext.read_attachment(self.paper["id"], record["file"])
                self.assertIn(expected, text)
                self.assertEqual(ctx.payload["extract"], {"ok": True, "chars": len(text)})

        paper = self.services.library.get(self.paper["id"])
        prepared = fullread.prepare(self.services, paper)
        self.assertEqual(prepared["coverage"]["missing"], [])
        reference = fullread.paper_reference(prepared)["content"]
        for name, _, _, expected in cases:
            self.assertIn(f"补充材料：{name}", reference)
            self.assertIn(expected, reference)

    def test_uploads_short_and_multiencoding_text(self):
        cases = [
            ("短文.TXT", "需重测".encode("utf-8-sig"), "需重测"),
            ("说明.txt", "数据\r\n7.25".encode("utf-16"), "数据\n7.25"),
            ("表格.tsv", "峰值\t7.25".encode("gb18030"), "峰值\t7.25"),
        ]
        for name, data, expected in cases:
            with self.subTest(name=name):
                ctx = self._upload_attachment(data, name)
                self.assertEqual(ctx.status, 201)
                self.assertEqual(ctx.payload["extract"], {"ok": True, "chars": len(expected)})
                record = ctx.payload["attachment"]
                self.assertEqual(self.services.fulltext.read_attachment(self.paper["id"], record["file"]), expected)

    def test_corrupt_or_unsupported_si_is_saved_without_text(self):
        for name, data in [("损坏.docx", b"invalid ZIP"), ("损坏.doc", b"invalid OLE"),
                           ("图片.png", b"\x89PNG"), ("空白.txt", b" \r\n"), ("二进制.txt", b"\x00\x01\x02")]:
            with self.subTest(name=name):
                ctx = self._upload_attachment(data, name)
                self.assertEqual(ctx.status, 201)
                self.assertFalse(ctx.payload["extract"]["ok"])
                record = ctx.payload["attachment"]
                self.assertFalse(record["hasText"])
                self.assertEqual(Path(self.store.path_for_attachment(record["file"])).read_bytes(), data)
                self.assertFalse(self.services.fulltext.has_attachment_text(self.paper["id"], record["file"]))

    def test_existing_word_attachment_is_extracted_when_first_used(self):
        file = self.paper["id"] + "-old.doc"
        directory = Path(self.store.attachments_dir)
        directory.mkdir(parents=True, exist_ok=True)
        (directory / file).write_bytes(WORD97_FIXTURE.read_bytes())
        paper = self.services.library.update(self.paper["id"], {"attachments": [
            {"file": file, "originalName": "原有附件.doc", "kind": "other"},
        ]})
        self.assertFalse(self.services.fulltext.has_attachment_text(paper["id"], file))
        prepared = fullread.prepare(self.services, paper)
        self.assertIn("补充材料：样品编号 S17。", fullread.paper_reference(prepared)["content"])
        cached = self.services.fulltext.read_attachment(paper["id"], file)
        self.assertEqual(prepared["coverage"]["siChars"], len(cached))

        ctx = FakeCtx(self.services, method="GET", params={"id": paper["id"]})
        files.attachments_list(ctx)
        record = ctx.payload["attachments"][0]
        self.assertTrue(record["hasText"])
        self.assertTrue(record["hasTextFile"])
        self.assertEqual(record["textChars"], len(cached))

    def test_empty_attachment_is_400(self):
        ctx = FakeCtx(self.services, params={"id": self.paper["id"]},
                      headers={"X-Filename": quote("empty.txt")}, body=b"")
        files.attachments_upload(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "上传内容为空")

    def test_unknown_attachment_404(self):
        ctx = FakeCtx(self.services, method="GET", params={"id": self.paper["id"], "file": "nope.bin"})
        files.attachment_download(ctx)
        self.assertEqual(ctx.status, 404)


class CiteTest(Base):
    def test_all_formats(self):
        for kind in ("gb", "apa", "mla", "bibtex", "markdown"):
            ctx = FakeCtx(self.services, method="GET", params={"id": self.paper["id"]},
                          query={"format": kind})
            files.cite(ctx)
            self.assertEqual(ctx.status, 200)
            self.assertEqual(ctx.payload["format"], kind)
            self.assertTrue(ctx.payload["text"].strip(), kind)
            self.assertEqual(ctx.payload["available"], ["gb", "apa", "mla", "bibtex", "markdown"])
        self.assertEqual(ctx.payload["text"], citation.format(self.paper, "markdown"))

    def test_default_and_unknown_format_fall_back_to_gb(self):
        for query in ({}, {"format": ""}, {"format": "zzz"}):
            ctx = FakeCtx(self.services, method="GET", params={"id": self.paper["id"]}, query=query)
            files.cite(ctx)
            self.assertEqual(ctx.status, 200)
            self.assertEqual(ctx.payload["text"], citation.format(self.paper, "gb"))

    def test_missing_paper_404(self):
        ctx = FakeCtx(self.services, method="GET", params={"id": "0" * 16})
        files.cite(ctx)
        self.assertEqual(ctx.status, 404)


class HttpPdfTest(Base):
    """真实 HTTP：Range / ETag / 304 / 中文文件名头（PDF 阅读器的验收项）。"""

    def setUp(self):
        super().setUp()
        self.upload()
        self.pdf_name = self.store.find_paper(self.paper["id"])["pdfFile"]
        router = Router(ROUTES, self.services)
        static = StaticHandler(ROOT, ROOT / "public", ROOT / "shared", ROOT / "vendor")
        self.server = HttpServer(router, static, "127.0.0.1", 0, verbose=False)
        self.port = self.server.httpd.server_address[1]
        self.server.start_background()

    def tearDown(self):
        try:
            self.server.shutdown()
            self.server.close()
        finally:
            super().tearDown()

    def _url(self, path):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    def test_range_request_returns_206_slice(self):
        req = urllib.request.Request(self._url("/api/papers/%s/pdf" % self.paper["id"]),
                                     headers={"Range": "bytes=0-99"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read()
            self.assertEqual(resp.status, 206)
            self.assertEqual(len(body), 100)
            self.assertEqual(body, self.PDF[:100])
            self.assertEqual(resp.headers["Content-Range"], "bytes 0-99/%d" % len(self.PDF))
            self.assertEqual(resp.headers["Accept-Ranges"], "bytes")
            self.assertEqual(resp.headers["Content-Type"], "application/pdf")
            self.assertIn("inline", resp.headers["Content-Disposition"])

    def test_etag_304_and_full_get(self):
        with urllib.request.urlopen(self._url("/api/papers/%s/pdf" % self.paper["id"]), timeout=5) as resp:
            etag = resp.headers["ETag"]
            self.assertEqual(resp.read(), self.PDF)
        req = urllib.request.Request(self._url("/api/papers/%s/pdf" % self.paper["id"]),
                                     headers={"If-None-Match": etag})
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 304)
        caught.exception.close()

    def test_head_and_404(self):
        req = urllib.request.Request(self._url("/api/papers/%s/pdf" % self.paper["id"]), method="HEAD")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(int(resp.headers["Content-Length"]), len(self.PDF))

        blank = self.services.library.create({"title": "没有原文"})
        req = urllib.request.Request(self._url("/api/papers/%s/pdf" % blank["id"]))
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=5)
        self.assertEqual(caught.exception.code, 404)
        caught.exception.close()

    def test_attachment_download_over_http(self):
        ctx = FakeCtx(self.services, params={"id": self.paper["id"]},
                      headers={"X-Filename": quote("SI 说明.txt")}, body=b"supplementary info")
        files.attachments_upload(ctx)
        name = ctx.payload["attachment"]["file"]
        url = self._url("/api/papers/%s/attachments/%s" % (self.paper["id"], quote(name)))
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertEqual(resp.read(), b"supplementary info")
            self.assertIn("inline", resp.headers["Content-Disposition"])


class DirectoryScanTest(Base):
    def _scan(self):
        ctx = FakeCtx(self.services)
        papers_api.sync_pdfs(ctx)
        self.assertEqual(ctx.status, 200)
        return ctx.payload

    def test_scan_imports_in_place_then_relinks_an_externally_moved_pdf(self):
        root = self.store.papers_dir
        for folder in ("A/子类", "B", "C"):
            (root / folder).mkdir(parents=True)
        for relative, title in (("A/子类/原文.pdf", "Classified article"), ("根目录.pdf", "Unclassified article")):
            with pymupdf.open() as doc:
                doc.set_metadata({"title": title})
                doc.new_page().insert_text((72, 72), title + " supplementary verification content")
                doc.save(root / relative)
        (root / "B/副本.pdf").write_bytes((root / "A/子类/原文.pdf").read_bytes())
        (root / "C/损坏.pdf").write_bytes(b"not a PDF")

        passive = FakeCtx(self.services, method="GET")
        papers_api.scan_pdfs(passive)
        self.assertEqual(passive.payload["scan"]["total"], 4)
        self.assertEqual(self.store.count(), 1)  # 只有 Base 创建的无原文卡片

        first = self._scan()
        cards = {row["file"]: row["id"] for row in first["imported"]}
        self.assertEqual(set(cards), {"A/子类/原文.pdf", "根目录.pdf"})
        self.assertEqual([entry["file"] for entry in first["failed"]], ["C/损坏.pdf"])
        paper_id = cards["A/子类/原文.pdf"]
        self.assertEqual(self.services.library.get(paper_id)["folder"], "A/子类")
        folders = {row["path"]: row for row in self.services.folders.list()["folders"]}
        self.assertEqual(folders["A/子类"]["parent"], "A")
        self.assertEqual(folders["A/子类"]["count"], 1)
        self.assertEqual(folders["A"]["countDeep"], 1)
        self.assertFalse(self.services.library.get(cards["根目录.pdf"]).get("folder"))
        self.assertTrue(self.services.fulltext.has(paper_id))
        self.assertEqual(self._scan()["imported"], [])

        self.services.library.update(paper_id, {"notes": "retain existing card"})
        (root / "B/副本.pdf").unlink()
        (root / "A/子类/原文.pdf").rename(root / "B/重命名.pdf")
        repeat = self._scan()
        self.assertEqual(repeat["relinked"], 1)
        self.assertEqual(repeat["imported"], [])
        paper = self.services.library.get(paper_id)
        self.assertEqual(paper["pdfFile"], "B/重命名.pdf")
        self.assertEqual(paper["folder"], "B")
        self.assertEqual(paper["notes"], "retain existing card")
        self.assertFalse((root / "未分类").exists())

    def test_upload_and_edit_place_the_pdf_in_the_selected_directory(self):
        first = self.services.folders.ensure("A")
        self.services.library.update(self.paper["id"], {"folder": first})
        ctx = self.upload()
        self.assertEqual(ctx.status, 201)
        original = self.store.papers_dir / ctx.payload["pdfFile"]
        self.assertEqual(original.parent.name, "A")
        ctx = FakeCtx(self.services, method="PATCH", params={"id": self.paper["id"]}, json_body={"folder": "B"})
        papers_api.update(ctx)
        self.assertEqual(ctx.status, 200)
        current = self.store.papers_dir / ctx.payload["paper"]["pdfFile"]
        self.assertEqual(current.parent.name, "B")
        self.assertTrue(current.is_file())
        self.assertFalse(original.exists())


class ImportEndpointsTest(Base):
    def _preflight(self, batch="abc123", name="原始 论文.pdf", body=None):
        ctx = FakeCtx(self.services, query={"batch": batch, "name": quote(name)},
                      body=self.PDF if body is None else body)
        import_.pdf_preflight(ctx)
        return ctx

    def test_preflight_returns_draft_and_hides_record(self):
        ctx = self._preflight()
        self.assertEqual(ctx.status, 201)
        payload = ctx.payload
        self.assertEqual(set(payload), {"ok", "batch", "tempId", "name", "size", "parsed", "draft"})
        self.assertEqual(payload["batch"], "abc123")
        self.assertEqual(payload["tempId"], "1")
        self.assertEqual(payload["size"], len(self.PDF))
        self.assertEqual(payload["parsed"]["doi"], SAMPLE_DOI)
        self.assertIn("duplicate", payload["draft"])
        self.assertEqual(self.store.count(), 1)  # 预检不建记录
        temp = Path(self.tmp) / "tmp-import" / "abc123" / "1.pdf"
        self.assertTrue(temp.is_file())

        again = self._preflight()
        self.assertEqual(again.payload["tempId"], "2")

    def test_preflight_validation_errors(self):
        self.assertEqual(self._preflight(batch="AB").payload["error"], "批次号不合法（只允许小写字母与数字）")
        self.assertEqual(self._preflight(name="x.txt").payload["error"], "目前只支持上传 PDF 文件")
        self.assertEqual(self._preflight(body=b"").payload["error"], "没有收到文件内容")
        self.assertIn("%PDF-", self._preflight(body=b"not pdf at all").payload["error"])

    def test_preflight_oversize_413(self):
        with patch.object(import_, "PDF_LIMIT", 10):
            ctx = self._preflight()
        self.assertEqual(ctx.status, 413)

    def test_abandon_removes_batch(self):
        self._preflight()
        ctx = FakeCtx(self.services, method="DELETE", query={"batch": "abc123"})
        import_.pdf_abandon(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertTrue(ctx.payload["removed"])
        self.assertFalse((Path(self.tmp) / "tmp-import" / "abc123").exists())
        again = FakeCtx(self.services, method="DELETE", query={"batch": "abc123"})
        import_.pdf_abandon(again)
        self.assertEqual(again.status, 200)
        bad = FakeCtx(self.services, method="DELETE", query={"batch": "AB"})
        import_.pdf_abandon(bad)
        self.assertEqual(bad.status, 400)

    def test_confirm_creates_record_moves_file_and_extracts(self):
        self._preflight()
        ctx = FakeCtx(self.services, json_body={
            "batch": "abc123",
            "papers": [{"tempId": "1", "name": "原始 论文.pdf", "paper": {"title": "导入测试论文"}}],
        })
        import_.pdf_confirm(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(len(ctx.payload["imported"]), 1)
        self.assertEqual(ctx.payload["failed"], [])
        item = ctx.payload["imported"][0]
        self.assertEqual(item["title"], "导入测试论文")
        self.assertTrue(item["extract"]["ok"])
        self.assertTrue(os.path.isfile(self.store.path_for_paper_file(item["pdfFile"])))
        self.assertTrue(self.store.has_text(item["id"]))
        self.assertFalse((Path(self.tmp) / "tmp-import" / "abc123").exists())

    def test_confirm_reports_missing_temp_file(self):
        self._preflight()
        ctx = FakeCtx(self.services, json_body={"batch": "abc123",
                                                "papers": [{"tempId": "9", "title": "丢了"}]})
        import_.pdf_confirm(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload["imported"], [])
        self.assertEqual(len(ctx.payload["failed"]), 1)
        self.assertIn("临时文件不在了", ctx.payload["failed"][0]["error"])

    def test_confirm_validation(self):
        ctx = FakeCtx(self.services, json_body={"batch": "zz", "papers": [{"tempId": "1"}]})
        import_.pdf_confirm(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "批次号不合法")
        self._preflight()
        ctx = FakeCtx(self.services, json_body={"batch": "abc123", "papers": []})
        import_.pdf_confirm(ctx)
        self.assertEqual(ctx.payload["error"], "没有要导入的条目")

    def test_json_import_passes_payload_and_overwrite(self):
        seen = {}

        def fake_import(payload, options):
            seen["payload"] = payload
            seen["options"] = options
            return {"added": 1}

        services = SimpleNamespace(library=SimpleNamespace(import_json=fake_import))
        ctx = FakeCtx(services, json_body={"papers": [{"title": "x"}], "overwrite": True})
        import_.json_import(ctx)
        self.assertEqual(ctx.status, 200)
        self.assertEqual(ctx.payload, {"ok": True, "result": {"added": 1}})
        self.assertEqual(seen["options"], {"overwrite": True})
        self.assertEqual(seen["payload"]["papers"][0]["title"], "x")

    def test_confirm_one_by_one_imports_every_entry(self):
        """回归：前端「全部导入」是逐篇 confirm 的。

        以前 confirm 处理完一批就删整个批次目录，第 1 篇成功会让第 2 篇起
        报「临时文件不在了」；现在只删本轮已转正的文件。
        """
        self._preflight()
        self._preflight()
        for temp_id in ("1", "2"):
            ctx = FakeCtx(self.services, json_body={
                "batch": "abc123",
                "papers": [{"tempId": temp_id, "name": "分篇 论文.pdf",
                            "paper": {"title": "分篇导入 " + temp_id}}],
            })
            import_.pdf_confirm(ctx)
            self.assertEqual(ctx.status, 200)
            self.assertEqual(ctx.payload["failed"], [])
            self.assertEqual(len(ctx.payload["imported"]), 1)
        self.assertFalse((Path(self.tmp) / "tmp-import" / "abc123").exists())
        self.assertEqual(self.store.count(), 3)  # 播种 1 篇 + 本次导入 2 篇

    def test_confirm_missing_entry_keeps_other_temp_files(self):
        """失败条目不能连锁删掉同批次里其它还没导入的临时文件。"""
        self._preflight()
        self._preflight()
        ctx = FakeCtx(self.services, json_body={
            "batch": "abc123",
            "papers": [
                {"tempId": "9", "title": "丢了"},
                {"tempId": "1", "name": "论文.pdf", "paper": {"title": "保留检查"}},
            ],
        })
        import_.pdf_confirm(ctx)
        self.assertEqual(len(ctx.payload["imported"]), 1)
        self.assertEqual(len(ctx.payload["failed"]), 1)
        batch_dir = Path(self.tmp) / "tmp-import" / "abc123"
        self.assertTrue((batch_dir / "2.pdf").is_file())   # 未提交的保留
        self.assertFalse((batch_dir / "1.pdf").exists())   # 已导入的清掉

    def test_preflight_numbering_does_not_overwrite_after_partial_import(self):
        """部分导入后继续扫描：新文件不能覆盖还没导入的临时文件。"""
        self._preflight()   # 1.pdf
        self._preflight()   # 2.pdf
        ctx = FakeCtx(self.services, json_body={
            "batch": "abc123",
            "papers": [{"tempId": "1", "name": "论文.pdf", "paper": {"title": "先导入一篇"}}],
        })
        import_.pdf_confirm(ctx)
        self.assertEqual(len(ctx.payload["imported"]), 1)
        again = self._preflight()
        self.assertEqual(again.payload["tempId"], "3")
        batch_dir = Path(self.tmp) / "tmp-import" / "abc123"
        self.assertTrue((batch_dir / "2.pdf").is_file())
        self.assertTrue((batch_dir / "3.pdf").is_file())

    def test_preflight_passes_network_budget_to_lookup(self):
        """预检题录查询要有预算上限，网络慢时不能每篇卡满 13 秒。"""
        captured = {}
        real = import_.pdf_import.pdf_preflight

        def fake_preflight(services, path, name, temp_id="", deps=None):
            captured["deps"] = deps
            return {"parsed": {}, "draft": {}, "tempId": temp_id}

        import_.pdf_import.pdf_preflight = fake_preflight
        try:
            self._preflight()
        finally:
            import_.pdf_import.pdf_preflight = real
        self.assertEqual(captured["deps"]["lookup_options"]["budgetMs"],
                         import_.PREFLIGHT_LOOKUP_BUDGET_MS)
        self.assertLess(import_.PREFLIGHT_LOOKUP_BUDGET_MS, 13000)

    def test_json_import_failure_is_400(self):
        def boom(payload, options):
            raise ValueError("导入内容里没有 papers 数组")

        services = SimpleNamespace(library=SimpleNamespace(import_json=boom))
        ctx = FakeCtx(services, json_body={"papers": []})
        import_.json_import(ctx)
        self.assertEqual(ctx.status, 400)
        self.assertEqual(ctx.payload["error"], "导入内容里没有 papers 数组")


if __name__ == "__main__":
    unittest.main()
