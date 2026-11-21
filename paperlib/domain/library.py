"""业务层门面（对应 Node 的 server/library.js）：只做转调。

  · 写卡片 / 规整字段 / 标签改名  → domain/repository.py
  · 查询 / 排序 / facets / 统计   → domain/query.py
  · 导入导出 / 文件扫描 / tidy    → domain/importer.py
  · 文件命名规则                  → domain/naming.py
  · 内存倒排索引                  → domain/search_index.py
"""
from __future__ import annotations

from paperlib.domain import naming
from paperlib.domain.importer import Importer
from paperlib.domain.query import QueryEngine
from paperlib.domain.repository import (
    PAPER_TYPES,
    STATUSES,
    Repository,
    normalize_chat,
    normalize_attachments,
    normalize_paper,
    normalize_refs,
)
from paperlib.domain.search_index import create_search_index


class Library:
    def __init__(self, db):
        self.db = db
        self.index = create_search_index()
        self.repository = Repository(db, self.index)
        self.query_engine = QueryEngine(db, self.index)
        self.importer = Importer(db, self.repository)
        self.reindex()

    def reindex(self):
        with self.db.paper_writes():
            self.index.rebuild(self.db.list_papers())

    # 卡片读写
    def all(self):
        return self.db.list_papers()

    def get(self, paper_id):
        return self.db.find_paper(paper_id)

    def create(self, input_data):
        return self.repository.create(input_data)

    def update(self, paper_id, input_data):
        return self.repository.update(paper_id, input_data)

    def remove(self, paper_id):
        return self.repository.remove(paper_id)

    def bulk(self, ids, patch):
        return self.repository.bulk(ids, patch)

    def tags(self):
        return self.query_engine.tags()

    def rename_tag(self, source, target):
        return self.repository.rename_tag(source, target)

    def remove_tag(self, name):
        return self.repository.remove_tag(name)

    def authors(self, limit=200):
        return self.query_engine.authors(limit)

    def venues(self, limit=200):
        return self.query_engine.venues(limit)

    def stats(self):
        return self.query_engine.stats()

    # 查询
    def searchable_text(self, paper, scope="metadata"):
        return self.query_engine.searchable_text(paper, scope)

    def field_text(self, paper, field):
        return self.query_engine.field_text(paper, field)

    def query(self, params):
        return self.query_engine.query(params)

    def facets(self, params):
        return self.query_engine.facets(params)

    def to_list_item(self, paper, score=None):
        return self.query_engine.to_list_item(paper, score)

    def pdf_exists(self, pdf_file):
        return self.query_engine.pdf_exists(pdf_file)

    # 文件 / 导入导出
    def scan_pdfs(self):
        return self.importer.scan_pdfs()

    def scan_pdf_contents(self):
        return self.importer.scan_pdf_contents()

    def sync_pdf_directory(self, services):
        return self.importer.sync_pdf_directory(services)

    def associate_pdf(self, paper_id, file):
        return self.importer.associate_pdf(paper_id, file)

    def tidy_pdf_names(self):
        return self.importer.tidy_pdf_names()

    def tidy_attachment_names(self, on_renamed=None):
        return self.importer.tidy_attachment_names(on_renamed)

    def export_json(self):
        return self.importer.export_json()

    def export_bibtex(self, ids=None):
        return self.importer.export_bibtex(ids)

    def export_markdown(self, ids=None):
        return self.importer.export_markdown(ids)

    def import_json(self, payload, options=None):
        return self.importer.import_json(payload, options)

    @property
    def fulltext_text_for(self):
        return self.query_engine.fulltextTextFor

    @fulltext_text_for.setter
    def fulltext_text_for(self, fn):
        self.query_engine.fulltextTextFor = fn


__all__ = [
    "Library", "normalize_paper", "normalize_chat", "normalize_attachments",
    "normalize_refs", "PAPER_TYPES", "STATUSES", "naming",
]
