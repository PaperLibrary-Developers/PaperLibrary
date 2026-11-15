"""P2-01 更新目录与状态文件结构测试。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib.update.store import (PHASES, UpdateStore, UpdateStoreError,  # noqa: E402
                                   default_state, is_within, now_iso)


class LayoutTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        # 数据目录在仓库外（模拟 data/ 移到外部目录），结构必须一致
        self.data = Path(self.tmp.name) / "external-data"
        self.store = UpdateStore(self.data)

    def tearDown(self):
        self.tmp.cleanup()

    def test_paths_layout(self):
        paths = self.store.paths("1.7.1")
        self.assertEqual(paths.root, self.data / "updates" / "1.7.1")
        self.assertEqual(paths.part, paths.root / "package.zip.part")
        self.assertEqual(paths.package, paths.root / "package.zip")
        self.assertEqual(paths.manifest, paths.root / "manifest.json")
        self.assertEqual(paths.staging, paths.root / "staging")
        self.assertEqual(paths.state, paths.root / "download-state.json")
        self.assertEqual(paths.ready, paths.root / "ready.json")
        self.assertFalse(self.data.exists(), "构造 store 不应创建目录")

    def test_invalid_versions_rejected(self):
        for bad in ("../evil", "1.7", "1.7.1/../2.0.0", "", "v1.7.1", "1.7.1\\..\\x"):
            with self.assertRaises(UpdateStoreError):
                self.store.paths(bad)

    def test_prepare_creates_layout_and_state(self):
        paths = self.store.prepare("1.7.1")
        self.assertTrue(paths.root.is_dir())
        state = self.store.read_state("1.7.1")
        self.assertEqual(state["schema"], 1)
        self.assertEqual(state["version"], "1.7.1")
        self.assertEqual(state["phase"], "downloading")
        self.assertTrue(paths.state.is_file())
        raw = json.loads(paths.state.read_text(encoding="utf-8"))
        self.assertEqual(raw["phase"], "downloading")
        self.assertEqual(list(Path(paths.root).glob("*.tmp-*")), [], "不应残留原子写的临时文件")

    def test_reset_clears_current_but_keeps_other_versions(self):
        paths = self.store.prepare("1.7.1")
        # prepare() 之后才创建“其它版本”，避免被 prepare 的清理策略删掉
        other = self.store.ensure("1.6.0")
        other.package.write_bytes(b"old")
        paths.part.write_bytes(b"partial")
        paths.package.write_bytes(b"zip")
        paths.manifest.write_bytes(b"manifest")
        paths.staging.mkdir()
        (paths.staging / "x.txt").write_text("x", encoding="utf-8")
        self.store.write_ready("1.7.1", {"files": 1})

        self.store.reset("1.7.1")
        for path in (paths.part, paths.package, paths.manifest, paths.state, paths.ready):
            self.assertFalse(path.exists(), path)
        self.assertFalse(paths.staging.exists())
        self.assertTrue(other.package.is_file(), "其它版本的包不应被 reset 影响")

    def test_prepare_prunes_other_versions(self):
        self.store.ensure("1.6.0")
        self.store.ensure("1.7.0")
        paths = self.store.prepare("1.7.1")
        versions = sorted(child.name for child in self.store.updates_dir.iterdir())
        self.assertEqual(versions, ["1.7.1"], "阶段 2A 策略：只保留当前目标版本")
        self.assertTrue(paths.root.is_dir())

    def test_old_part_is_not_reused_after_prepare(self):
        paths = self.store.prepare("1.7.1")
        paths.part.write_bytes(b"stale bytes")
        self.store.prepare("1.7.1")
        self.assertFalse(paths.part.exists(), "同版本重新开始必须清掉旧 .part，避免串包")

    def test_manifest_and_ready_roundtrip(self):
        self.store.prepare("1.7.1")
        self.store.write_manifest("1.7.1", b'{"schema":1}')
        self.assertEqual(self.store.read_manifest("1.7.1"), b'{"schema":1}')
        ready = self.store.write_ready("1.7.1", {"files": 712, "bytes": 99349229})
        self.assertEqual(ready["version"], "1.7.1")
        self.assertTrue(ready["createdAt"])
        self.assertEqual(self.store.read_ready("1.7.1")["files"], 712)
        self.store.clear_ready("1.7.1")
        self.assertEqual(self.store.read_ready("1.7.1"), {})

    def test_state_phase_validation(self):
        self.store.prepare("1.7.1")
        with self.assertRaises(UpdateStoreError):
            self.store.write_state("1.7.1", {"phase": "installing"})
        for phase in PHASES:
            self.store.write_state("1.7.1", {"phase": phase})
            self.assertEqual(self.store.read_state("1.7.1")["phase"], phase)

    def test_update_state_merges_and_keeps_version(self):
        self.store.prepare("1.7.1")
        state = self.store.update_state("1.7.1", downloadedBytes=123, totalBytes=456)
        self.assertEqual(state["downloadedBytes"], 123)
        self.assertEqual(state["totalBytes"], 456)
        self.assertEqual(state["version"], "1.7.1")
        self.assertEqual(state["phase"], "downloading")
        self.assertTrue(state["updatedAt"])

    def test_corrupt_state_falls_back_to_default(self):
        paths = self.store.prepare("1.7.1")
        paths.state.write_text("{bad", encoding="utf-8")
        self.assertEqual(self.store.read_state("1.7.1"), default_state("1.7.1"))

    def test_is_within(self):
        base = Path(self.tmp.name) / "data" / "updates"
        self.assertTrue(is_within(base, base / "1.7.1"))
        self.assertTrue(is_within(base, base))
        self.assertFalse(is_within(base, Path(self.tmp.name) / "data" / "other"))


if __name__ == "__main__":
    unittest.main()
