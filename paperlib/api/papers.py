"""文献端点：检索 / 建卡 / 整理 / 导出（待实现：对应 Node api/papers.js，287 行）。"""
from __future__ import annotations

from paperlib.todo import todo

list_papers = todo("papers.list")
create = todo("papers.create")
bulk = todo("papers.bulk")
scan_pdfs = todo("papers.scanPdfs")
associate_pdf = todo("papers.associatePdf")
cleanup_duplicates = todo("papers.cleanupDuplicates")
tidy_pdf_names = todo("papers.tidyPdfNames")
facets = todo("papers.facets")
export_by_kind = todo("papers.exportByKind")
get_one = todo("papers.getOne")
update = todo("papers.update")
remove = todo("papers.remove")
