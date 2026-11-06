"""领域层 · 导入导出/文件扫描/整理（待实现：移植 server/domain/importer.js，361 行）。

对外接口：
  Importer(db, repository)
    .scan_pdfs() / .scan_pdf_contents()
    .associate_pdf(paper_id, file)
    .tidy_pdf_names() / .tidy_attachment_names(on_renamed=None)
    .export_json() / .export_bibtex(ids) / .export_markdown(ids)
    .import_json(payload, options) -> { added, updated, skipped, ... }
"""
from __future__ import annotations


class Importer:
    def __init__(self, db, repository):
        self.db = db
        self.repository = repository

    def scan_pdfs(self):
        raise NotImplementedError("待实现：Importer.scanPdfs")

    def scan_pdf_contents(self):
        raise NotImplementedError("待实现：Importer.scanPdfContents")

    def associate_pdf(self, paper_id, file):
        raise NotImplementedError("待实现：Importer.associatePdf")

    def tidy_pdf_names(self):
        raise NotImplementedError("待实现：Importer.tidyPdfNames")

    def tidy_attachment_names(self, on_renamed=None):
        raise NotImplementedError("待实现：Importer.tidyAttachmentNames")

    def export_json(self):
        raise NotImplementedError("待实现：Importer.exportJson")

    def export_bibtex(self, ids=None):
        raise NotImplementedError("待实现：Importer.exportBibtex")

    def export_markdown(self, ids=None):
        raise NotImplementedError("待实现：Importer.exportMarkdown")

    def import_json(self, payload, options=None):
        raise NotImplementedError("待实现：Importer.importJson")
