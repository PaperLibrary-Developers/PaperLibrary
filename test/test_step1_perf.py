"""Step 1 性能优化回归测试：FullText 反向词集、Store O(1) 索引、stats 单次扫盘与无损全文布尔求值。"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["PAPERLIB_SKIP_MIGRATE"] = "1"
os.environ["PYTHONUTF8"] = "1"

from paperlib.adapters.pdf import FullText  # noqa: E402
from paperlib.data.store import Store  # noqa: E402
from paperlib.domain.library import Library  # noqa: E402


def _make_paper(pid, **overrides):
    base = {
        "id": pid,
        "title": f"Paper {pid}",
        "authors": ["Alice"],
        "year": 2025,
        "venue": "Chem",
        "type": "journal",
        "status": "unread",
        "rating": 0,
        "starred": False,
        "folder": "",
        "tags": [],
        "pdfFile": "",
        "summary": "",
        "addedAt": "2026-01-01T00:00:00.000Z",
        "updatedAt": "2026-01-01T00:00:00.000Z",
    }
    base.update(overrides)
    return base


class Step1OptimizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pl-step1-test-"))
        self.store = Store(self.tmp)
        self.store.load(migrate_legacy_dirs=False)
        self.ft = FullText(self.store)
        self.lib = Library(self.store, fulltext=self.ft)

    def tearDown(self):
        self.store.repo.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_fulltext_doc_tokens_cleans_postings_on_update_and_delete(self):
        self.store.replace_all([_make_paper("p1"), _make_paper("p2"), _make_paper("p3")])
        self.ft.save("p1", "alphaold commonword")
        self.ft.save("p2", "betaold commonword")
        self.ft.save("p3", "gammakeep commonword")
        self.assertEqual([h["id"] for h in self.ft.search("alphaold", None)], ["p1"])
        self.assertIn("alphaold", self.ft._doc_tokens["p1"])

        # 更新 p1：旧词应从 _postings 彻底移除，新词可见
        self.ft.save("p1", "alphanew commonword")
        self.assertEqual(self.ft.search("alphaold", None), [])
        self.assertNotIn("alphaold", self.ft._postings)
        self.assertEqual([h["id"] for h in self.ft.search("alphanew", None)], ["p1"])

        # 删除 p2：p2 独有词与 _doc_tokens 记录均被清除，p3 保留
        self.ft.remove("p2")
        self.assertEqual(self.ft.search("betaold", None), [])
        self.assertNotIn("betaold", self.ft._postings)
        self.assertNotIn("p2", self.ft._doc_tokens)
        self.assertEqual({h["id"] for h in self.ft.search("commonword", None)}, {"p1", "p3"})

    def test_store_by_id_and_pos_stay_in_sync_across_mutations(self):
        p1, p2, p3 = _make_paper("p1"), _make_paper("p2"), _make_paper("p3")
        self.store.insert_paper(p1)
        self.store.insert_paper(p2, {"append": True})
        self.store.insert_paper(p3)  # insert at 0 -> [p3, p1, p2]
        self.assertEqual([p["id"] for p in self.store.list_papers()], ["p3", "p1", "p2"])
        for expected_idx, pid in enumerate(("p3", "p1", "p2")):
            self.assertEqual(self.store.index_of(pid), expected_idx)
            self.assertEqual(self.store.find_paper(pid)["id"], pid)
        self.assertIsNone(self.store.find_paper("missing"))
        self.assertEqual(self.store.index_of("missing"), -1)

        self.store.patch_paper("p1", {"rating": 4})
        self.assertEqual(self.store.find_paper("p1")["rating"], 4)

        self.store.patch_papers({"p2": {"folder": "Sub"}, "p3": {"folder": "Top"}})
        self.assertEqual(self.store.find_paper("p2")["folder"], "Sub")
        self.assertEqual(self.store.find_paper("p3")["folder"], "Top")

        replaced = _make_paper("p1", title="Replaced P1")
        self.store.replace_paper("p1", replaced)
        self.assertEqual(self.store.find_paper("p1")["title"], "Replaced P1")

        self.store.remove_paper("p3")
        self.assertIsNone(self.store.find_paper("p3"))
        self.assertEqual(self.store.index_of("p3"), -1)
        self.assertEqual(self.store.index_of("p1"), 0)
        self.assertEqual(self.store.index_of("p2"), 1)

    def test_stats_counts_missing_pdfs_via_single_walk_without_per_paper_stat(self):
        (self.store.papers_dir / "Sub").mkdir(parents=True, exist_ok=True)
        (self.store.papers_dir / "ok.pdf").write_bytes(b"%PDF-1.4\n")
        (self.store.papers_dir / "Sub" / "nested.pdf").write_bytes(b"%PDF-1.4\n")
        self.store.replace_all([
            _make_paper("p1", pdfFile="ok.pdf"),
            _make_paper("p2", pdfFile="Sub/nested.pdf"),
            _make_paper("p3", pdfFile="missing.pdf"),
            _make_paper("p4", pdfFile="../escape.pdf"),
            _make_paper("p5", pdfFile=""),
        ])
        with patch.object(self.lib.query_engine, "pdf_exists", side_effect=AssertionError("per-paper stat")):
            stats = self.lib.stats()
        self.assertEqual(stats["withPdf"], 4)
        self.assertEqual(stats["pdfMissing"], 2)

    def test_lossless_fulltext_boolean_short_circuits_and_batches_reads(self):
        self.store.replace_all([
            _make_paper("p1", title="Target catalyst", folder="CatA", tags=["OER"]),
            _make_paper("p2", title="Target no-body", folder="CatA", tags=["OER"]),
            _make_paper("p3", title="Other folder", folder="CatB", tags=["other"]),
            _make_paper("p4", title="Long thesis", folder="CatA", tags=["OER"]),
            _make_paper("p5", title="Title hit for OR", folder="CatB", tags=["other"]),
        ])
        self.ft.save("p1", "Detailed study of electrocatalysis mechanisms.")
        # p2 故意不保存正文（测试无正文短路 + NOT fulltext）
        self.ft.save("p3", "Detailed study of electrocatalysis in another folder.")
        # p4 正文超过 200,000 字符，关键词位于末尾（测试超长文无损召回）
        self.ft.save("p4", ("filler " * 32000) + " tail_catalysis_marker")
        self.ft.save("p5", "Unrelated body text.")

        read_calls = []
        orig_batch = self.store.repo.read_body_batch

        def spy_batch(keys, char_limit=None):
            read_calls.append(list(keys))
            return orig_batch(keys, char_limit)

        with patch.object(self.store.repo, "read_body_batch", side_effect=spy_batch):
            # 1) folder=CatA + 英文子串 fulltext:cataly -> 命中 p1（子串）与 p4（>200k 末尾），绝不读 p2(无正文) / p3 / p5
            res = self.lib.query({"folder": "CatA", "q": "fulltext:cataly"})
            self.assertEqual({item["id"] for item in res["items"]}, {"p1", "p4"})
            self.assertEqual(read_calls, [["p1", "p4"]])
            self.assertEqual(self.lib.facets({"folder": "CatA", "q": "fulltext:cataly"})["total"], 2)

            # 2) title:Target AND fulltext:cataly -> 三值逻辑直接排除 p3/p4/p5，p2 无正文跳过，只读 p1
            read_calls.clear()
            res2 = self.lib.query({"q": "title:Target AND fulltext:cataly"})
            self.assertEqual([item["id"] for item in res2["items"]], ["p1"])
            self.assertEqual(read_calls, [["p1"]])

            # 3) title:hit OR fulltext:tail_catalysis_marker -> p5 靠 title 直接 True，不读 p5 正文
            read_calls.clear()
            res3 = self.lib.query({"q": "title:hit OR fulltext:tail_catalysis_marker"})
            self.assertEqual({item["id"] for item in res3["items"]}, {"p4", "p5"})
            self.assertNotIn("p5", [pid for batch in read_calls for pid in batch])
            self.assertNotIn("p2", [pid for batch in read_calls for pid in batch])

            # 4) NOT fulltext:electrocatalysis -> p2 无正文也应命中，且不触发 p2 读库
            read_calls.clear()
            res4 = self.lib.query({"folder": "CatA", "q": "NOT fulltext:electrocatalysis"})
            self.assertEqual({item["id"] for item in res4["items"]}, {"p2", "p4"})
            self.assertNotIn("p2", [pid for batch in read_calls for pid in batch])


if __name__ == "__main__":
    unittest.main()
