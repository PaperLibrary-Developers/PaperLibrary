"""PDF 全文抽取与缓存（对应 Node server/adapters/pdf.js，248 行）。

合并后权威实现是 PyMuPDF（不是 pdfjs）：图注 bbox 匹配、矢量图渲染、排版还原都依赖它。
"""
from __future__ import annotations

from pathlib import Path


class FullText:
    """全文缓存：正文落 paper_text 表（store），附件文本用 att-<文件名> 键。"""

    def __init__(self, store):
        self.store = store

    def load_all(self, papers):
        return None

    def available(self):
        return True  # PyMuPDF 随包内置

    def install_hint(self):
        return ""

    def has(self, paper_id):
        return self.store.has_text(str(paper_id))

    def read(self, paper_id, limit=None):
        text = self.store.get_text(str(paper_id))
        if limit and len(text) > limit:
            return text[:limit]
        return text

    def save(self, paper_id, text):
        self.store.set_text(str(paper_id), text)
        return {"chars": len(str(text or ""))}

    def extract_from_file(self, abs_path):
        raise NotImplementedError("待实现：FullText.extract_from_file（PyMuPDF 逐页取文本）")

    def extract_with_meta(self, abs_path):
        raise NotImplementedError("待实现：FullText.extract_with_meta（PyMuPDF 分页 + 字体信息）")
