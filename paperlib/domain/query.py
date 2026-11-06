"""领域层 · 查询（待实现：移植 server/domain/query.js，436 行）。

对外接口（api 层只依赖这些，实现时不要改签名/返回形状）：
  QueryEngine(db, index)
    .query(params) -> { items, total, page, pageSize, pageCount, scope, boolean, fulltext }
    .toListItem(paper, score) -> 列表项（不含 chat/notes 大字段）
    .facets(params) -> { authors, venues, tags, years, query, fulltext }
    .tags() -> [{name, count}]
    .authors(limit) / .venues(limit) -> 字符串数组
    .stats() -> { total, withPdf, starred, pdfMissing, avgRating, noteChars, byYear, byType, byStatus, tags, updatedAt }
    .searchableText(paper, scope) / .fieldText(paper, field)
    .pdfExists(pdfFile) -> bool
    .fulltextTextFor(paperId) -> str   （由 services 注入，带读取预算）
"""
from __future__ import annotations


class QueryEngine:
    def __init__(self, db, index):
        self.db = db
        self.index = index
        self.fulltextTextFor = lambda paper_id: ""

    def query(self, params):
        raise NotImplementedError("待实现：QueryEngine.query（移植 server/domain/query.js）")

    def to_list_item(self, paper, score=None):
        raise NotImplementedError("待实现：QueryEngine.toListItem")

    def facets(self, params):
        raise NotImplementedError("待实现：QueryEngine.facets")

    def tags(self):
        raise NotImplementedError("待实现：QueryEngine.tags")

    def authors(self, limit=200):
        raise NotImplementedError("待实现：QueryEngine.authors")

    def venues(self, limit=200):
        raise NotImplementedError("待实现：QueryEngine.venues")

    def stats(self):
        raise NotImplementedError("待实现：QueryEngine.stats")

    def searchable_text(self, paper, scope="metadata"):
        raise NotImplementedError("待实现：QueryEngine.searchableText")

    def field_text(self, paper, field):
        raise NotImplementedError("待实现：QueryEngine.fieldText")

    def pdf_exists(self, pdf_file):
        raise NotImplementedError("待实现：QueryEngine.pdfExists")
