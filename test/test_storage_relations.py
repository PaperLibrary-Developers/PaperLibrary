"""Regression tests for batched paper reads and metadata-only writes."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from paperlib.data.store import Store


class StorageRelationsTest(unittest.TestCase):
    def test_loads_legacy_relations_in_four_queries_and_preserves_order(self):
        with TemporaryDirectory() as temp:
            store = Store(Path(temp))
            try:
                repo = store.repo
                repo.save_paper({"id": "old", "title": "Old card", "attachments": [
                    {"file": "z.pdf", "originalName": "First PDF", "hasText": True},
                    {"file": "a.txt", "originalName": "Second attachment"},
                ], "chat": [
                    {"role": "user", "content": "First", "sources": ["source"]},
                    {"role": "assistant", "content": "Second", "usage": {"tokens": 3}},
                ]})
                repo.save_paper({"id": "new", "title": "New card", "tags": ["Z", "A"]})
                with repo.conn:
                    repo.conn.execute("INSERT INTO paper_tags(paper_id,tag) VALUES ('old','legacy')")
                queries = []
                repo.conn.set_trace_callback(queries.append)
                records = repo.list_papers()
                repo.conn.set_trace_callback(None)
                self.assertEqual(sum(q.startswith("SELECT") for q in queries), 4)
                self.assertEqual([p["id"] for p in records], ["new", "old"])
                new, old = records
                self.assertEqual(new["tags"], ["Z", "A"])
                self.assertEqual(old["tags"], ["legacy"])
                self.assertEqual([a["file"] for a in old["attachments"]], ["z.pdf", "a.txt"])
                self.assertEqual(old["chat"][1]["usage"], {"tokens": 3})
                self.assertEqual(repo.get_paper("old"), old)

                store.load()
                queries.clear()
                repo.conn.set_trace_callback(queries.append)
                store.patch_paper("old", {"rating": 5})
                repo.conn.set_trace_callback(None)
                self.assertFalse(any(q.startswith("DELETE") for q in queries))
                self.assertEqual(repo.get_paper("old"), dict(old, rating=5))
            finally:
                store.repo.close()
