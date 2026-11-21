"""原文目录分类：路径归一化、磁盘与库内联动、目录筛选。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from paperlib.data.store import Store
from paperlib.domain.folders import FolderError, Folders, clean_name, normalize_path
from paperlib.domain.library import Library


def make_paper(pid, title, folder=""):
    payload = {
        "id": pid, "title": title, "authors": ["张三"], "year": 2024,
        "type": "journal", "status": "unread", "rating": 0, "starred": False,
        "tags": [], "addedAt": "2024-01-01T00:00:00Z", "updatedAt": "2024-01-01T00:00:00Z",
    }
    if folder:
        payload["folder"] = folder
    return payload


class PathNormalizeTest(unittest.TestCase):
    def test_clean_name(self):
        self.assertEqual(clean_name("  电催化  "), "电催化")
        self.assertEqual(clean_name("a/b"), "a b")      # 斜杠被换成空格，不能当层级用
        self.assertEqual(clean_name("a\\b"), "a b")
        self.assertEqual(clean_name("  "), "")
        self.assertEqual(clean_name(".."), "")

    def test_normalize_path(self):
        self.assertEqual(normalize_path("/电催化//钙钛矿/"), "电催化/钙钛矿")
        self.assertEqual(normalize_path("   "), "")
        self.assertEqual(normalize_path(None), "")
        # 超过 MAX_DEPTH 的层级被截断
        deep = "/".join("L%d" % i for i in range(12))
        self.assertEqual(len(normalize_path(deep).split("/")), 8)


class FolderCRUDTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nexus-folders-")
        self.store = Store(Path(self.tmp))
        self.store.load()
        self.folders = Folders(self.store)
        self.library = Library(self.store)

    def tearDown(self):
        self.store.repo.close()

    def _paper_in(self, pid, folder=""):
        """直接写库：论文的 folder 由 _ensure 与 normalize 一起决定。"""
        paper = make_paper(pid, "论文 " + pid, folder)
        self.library.create(paper)
        return paper

    def test_create_creates_parents_and_is_idempotent(self):
        first = self.folders.create(path="电催化/钙钛矿")
        self.assertTrue(first["created"])
        self.assertEqual(first["path"], "电催化/钙钛矿")
        again = self.folders.create(path="电催化/钙钛矿")
        self.assertFalse(again["created"])
        paths = [row["path"] for row in self.folders.list()["folders"]]
        self.assertIn("电催化", paths)
        self.assertIn("电催化/钙钛矿", paths)

    def test_create_rejects_blank_name(self):
        with self.assertRaises(FolderError):
            self.folders.create(path=" ///  ")

    def test_counts_direct_and_deep(self):
        self.folders.ensure("电催化/钙钛矿")
        self.folders.ensure("电催化/单原子")
        self._paper_in("p1", "电催化")
        self._paper_in("p2", "电催化/钙钛矿")
        self._paper_in("p3", "电催化/钙钛矿")
        self._paper_in("p4", "")          # 根目录
        rows = {row["path"]: row for row in self.folders.list()["folders"]}
        self.assertEqual(rows["电催化"]["count"], 1)
        self.assertEqual(rows["电催化"]["countDeep"], 3)
        self.assertEqual(rows["电催化/钙钛矿"]["countDeep"], 2)
        self.assertEqual(self.folders.list()["rootCount"], 1)

    def test_rename_moves_subtree_and_papers(self):
        self.folders.ensure("电催化/钙钛矿")
        self._paper_in("p1", "电催化/钙钛矿")
        result = self.folders.rename("电催化", "电催化-2024")
        self.assertTrue(result["renamed"])
        self.assertEqual(result["moved"], 1)
        paths = [row["path"] for row in self.folders.list()["folders"]]
        self.assertIn("电催化-2024", paths)
        self.assertIn("电催化-2024/钙钛矿", paths)
        self.assertNotIn("电催化", paths)
        self.assertEqual(self.library.get("p1")["folder"], "电催化-2024/钙钛矿")
        # 被改名那一行的显示名也要换（只改 path 不改 name 的话，侧栏会出现「名字没变」）
        rows = {row["path"]: row for row in self.folders.list()["folders"]}
        self.assertEqual(rows["电催化-2024"]["name"], "电催化-2024")
        self.assertEqual(rows["电催化-2024/钙钛矿"]["name"], "钙钛矿")

    def test_rename_conflict(self):
        self.folders.ensure("电催化")
        self.folders.ensure("催化")
        with self.assertRaises(FolderError) as ctx:
            self.folders.rename("电催化", "催化")
        self.assertEqual(ctx.exception.status, 409)

    def test_delete_moves_papers_to_parent(self):
        self.folders.ensure("电催化/钙钛矿")
        self._paper_in("p1", "电催化/钙钛矿")
        self._paper_in("p2", "电催化")
        result = self.folders.delete("电催化/钙钛矿")
        self.assertEqual(result["moved"], 1)
        self.assertEqual(result["to"], "电催化")
        self.assertEqual(self.library.get("p1")["folder"], "电催化")
        # 父目录还在，只有被删的那个消失
        paths = [row["path"] for row in self.folders.list()["folders"]]
        self.assertIn("电催化", paths)
        self.assertNotIn("电催化/钙钛矿", paths)

    def test_delete_top_folder_moves_papers_to_root(self):
        self.folders.ensure("电催化")
        self._paper_in("p1", "电催化")
        result = self.folders.delete("电催化")
        self.assertEqual(result["to"], "")
        # folder 列清空后，卡片上不能残留旧路径（_assemble 会 pop 掉 JSON 里的旧值）
        self.assertFalse(self.library.get("p1").get("folder"))

    def test_root_cannot_be_deleted_or_renamed(self):
        with self.assertRaises(FolderError):
            self.folders.delete("")
        with self.assertRaises(FolderError):
            self.folders.rename("", "x")

    def test_move_papers_creates_target(self):
        self._paper_in("p1")
        result = self.folders.move_papers(["p1"], "新目录/子目录")
        self.assertEqual(result["moved"], 1)
        self.assertEqual(self.library.get("p1")["folder"], "新目录/子目录")
        paths = [row["path"] for row in self.folders.list()["folders"]]
        self.assertIn("新目录", paths)

    def test_rename_move_and_delete_update_the_same_physical_pdf(self):
        self._paper_in("p1")
        root = self.store.papers_dir
        self.store.attach_file("p1", b"original PDF", {"fileName": "A/子类/原文.pdf"})
        self.folders.rename("A", "B")
        self.assertEqual(self.library.get("p1")["pdfFile"], "B/子类/原文.pdf")
        self.assertFalse((root / "A").exists())
        (root / "原文.pdf").write_bytes(b"another PDF")
        self.folders.move_papers(["p1"], "")
        self.assertEqual((root / "原文 (2).pdf").read_bytes(), b"original PDF")
        self.assertEqual((root / "原文.pdf").read_bytes(), b"another PDF")
        self.assertFalse((root / "未分类").exists())
        self.folders.move_papers(["p1"], "B/子类")
        self.assertEqual(self.folders.delete("B")["moved"], 1)
        self.assertFalse((root / "B").exists())
        self.assertEqual(self.library.get("p1")["pdfFile"], "原文 (2).pdf")
        self.assertEqual(self.library.query({"folder": ""})["total"], 1)

    def test_existing_disk_folders_supply_the_tree_and_classification(self):
        self._paper_in("p1", "旧的虚拟分类")
        root = self.store.papers_dir
        (root / "A" / "两个  空格").mkdir(parents=True)
        (root / "C").mkdir()
        self.store.attach_file("p1", b"PDF", {"fileName": "A/两个  空格/原文.pdf"})
        self.store.patch_paper("p1", {"folder": "旧的虚拟分类"})
        self.folders.sync_locations()
        paths = {row["path"]: row for row in self.folders.list()["folders"]}
        self.assertEqual(set(paths), {"A", "A/两个  空格", "C"})
        self.assertEqual(paths["A"]["countDeep"], 1)
        self.assertEqual(paths["C"]["count"], 0)
        self.assertEqual(self.library.get("p1")["folder"], "A/两个  空格")
        self.assertEqual(self.folders.create(parent="A/两个  空格", name="子类")["path"], "A/两个  空格/子类")
        self.assertTrue((root / "A/两个  空格/子类").is_dir())

    def test_query_filter_by_folder(self):
        self.folders.ensure("电催化/钙钛矿")
        self._paper_in("p1", "电催化")
        self._paper_in("p2", "电催化/钙钛矿")
        self._paper_in("p3", "")
        deep = self.library.query({"folder": "电催化"})
        self.assertEqual(sorted(item["id"] for item in deep["items"]), ["p1", "p2"])
        shallow = self.library.query({"folder": "电催化", "includeSubfolders": "false"})
        self.assertEqual([item["id"] for item in shallow["items"]], ["p1"])
        root = self.library.query({"folder": ""})
        self.assertEqual([item["id"] for item in root["items"]], ["p3"])
        everything = self.library.query({})
        self.assertEqual(everything["total"], 3)
        # 列表项要带上 folder，前端卡片直接显示
        self.assertEqual(deep["items"][0]["folder"].split("/")[0], "电催化")


if __name__ == "__main__":
    unittest.main()
