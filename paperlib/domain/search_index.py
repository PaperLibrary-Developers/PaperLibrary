"""检索索引的持有者（对应 Node 的 domain/search-index.js）。

索引只在**这里**被创建、重建、增删：repository 写卡片时同步 set/remove，
query 用它粗筛，启动时全量 rebuild 一次。
"""
from __future__ import annotations

from paperlib.domain.search import SearchIndex


class SearchIndexHolder:
    def __init__(self):
        self.current = SearchIndex()

    def rebuild(self, papers):
        self.current = SearchIndex()
        for paper in papers:
            self.current.set(paper)
        return self.current

    def set(self, paper):
        self.current.set(paper)

    def remove(self, paper_id):
        self.current.remove(paper_id)

    def search(self, query):
        return self.current.search(query)


def create_search_index():
    return SearchIndexHolder()
