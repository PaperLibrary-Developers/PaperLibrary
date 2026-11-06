"""题录解析（对应 Node server/adapters/metadata.js，410 行；契约名 metadata.resolve）。

多源合并题录（Crossref / OpenAlex / Europe PMC / arXiv / Semantic Scholar），返回 provenance。
"""
from __future__ import annotations

_sources = None


def configure(sources):
    global _sources
    _sources = sources


def resolve(doi=None, title=None):
    raise NotImplementedError("待实现：metadata.resolve（多源合并题录）")


def resolve_from_pdf(abs_path, fallback_title=None):
    raise NotImplementedError("待实现：metadata.resolve_from_pdf（PyMuPDF 首屏 + 字体级标题）")
