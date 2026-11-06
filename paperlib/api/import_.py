"""导入端点：PDF 建档（预检/确认/放弃）与 JSON 导入（对应 Node api/import.js，210 行）。"""
from __future__ import annotations

from paperlib.todo import todo

pdf_preflight = todo("import.pdfPreflight")
pdf_abandon = todo("import.pdfAbandon")
pdf_confirm = todo("import.pdfConfirm")
json_import = todo("import.jsonImport")


def cleanup_import_tmp(services):
    return {"removed": 0}
