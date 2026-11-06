"""外部检索（对应 Node server/adapters/external.js，332 行；契约名 search.external）。

5 源检索 + 跨源折叠 + 统一字段（OpenAlex / Crossref / Europe PMC / arXiv / Semantic Scholar）。
"""
from __future__ import annotations

_sources = None


def configure(sources):
    global _sources
    _sources = sources


def sources_list():
    raise NotImplementedError("待实现：external.sources_list")


def search(query, filters=None):
    raise NotImplementedError("待实现：external.search")
