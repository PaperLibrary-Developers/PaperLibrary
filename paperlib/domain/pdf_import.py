"""领域层 · PDF 建档预检（待实现：移植 server/domain/pdf-import.js，213 行）。

对外接口：
  pdf_preflight(services, file_path, name) -> { parsed, draft, tempId }
  cleanup_import_tmp(services) -> { removed }
"""
from __future__ import annotations


def pdf_preflight(services, file_path, name):
    raise NotImplementedError("待实现：pdf_import.pdf_preflight")


def cleanup_import_tmp(services):
    raise NotImplementedError("待实现：pdf_import.cleanup_import_tmp")
