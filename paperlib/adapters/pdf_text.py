"""PDF 文本层（PyMuPDF；对应 Node server/adapters/pdf-text.js，188 行）。

输出与 Node 版一致：{ pages: int, text: str }，中文走 PyMuPDF 内置 CJK 支持。
"""
from __future__ import annotations


def extract_text(abs_path, options=None):
    """逐页抽取文本，按页拼接；返回 {pages, text, perPage}。"""
    raise NotImplementedError("待实现：pdf_text.extract_text（PyMuPDF）")


def extract_page(abs_path, page_number):
    raise NotImplementedError("待实现：pdf_text.extract_page")
