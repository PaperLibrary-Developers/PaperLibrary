"""P2-15：更新前数据备份（library.db + 设置）与保留策略。

用真实编译出的 helper 执行 backup-data：
- 备份到 data/backups/update-<version>/，含 library.db 与存在设置文件；
- 缺失 library.db / data 目录 / 数据项不是文件 → 中止安装并清理临时目录；
- 幂等、版本冲突拒绝、保留最近 N 份；
- 备份可恢复（逐文件哈希一致，SQLite 可打开查询）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib import version as version_mod  # noqa: E402
from paperlib.update import helper  # noqa: E402

POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))


def find_csc():
    for framework in ("Framework64", "Framework"):
        candidate = WINDIR / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe"
        if candidate.is_file():
            return candidate
    return None


CSC = find_csc()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(POWERSHELL and CSC, "需要 PowerShell 与 .NET Framework csc 才能编译 helper")
class DataBackupTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.pkg = cls.dir / "pkg"
        (cls.pkg / "tools" / "updater").mkdir(parents=True)
        (cls.pkg / "version.json").write_text(
            json.dumps({"product": "PaperLibrary", "version": "1.7.1", "tag": "v1.7.1"}),
            encoding="utf-8")
        shutil.copy2(ROOT / "tools" / "make_updater.ps1", cls.pkg / "tools" / "make_updater.ps1")
        shutil.copy2(ROOT / "tools" / "updater" / "PaperLibraryUpdater.cs",
                     cls.pkg / "tools" / "updater" / "PaperLibraryUpdater.cs")
        proc = subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-File", str(cls.pkg / "tools" / "make_updater.ps1"), "-OutDir", str(cls.pkg)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(cls.pkg), timeout=180)
        if proc.returncode != 0:
            raise AssertionError("helper 构建失败：" + (proc.stdout or "") + (proc.stderr or ""))
        cls.home = cls.dir / "home"
        cls.deploy = helper.deploy(cls.pkg, home=cls.home)
        cls.exe = Path(cls.deploy["helperPath"])

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.dir))
        self.program = self.work / "program"
        self.data = self.program / "data"
        self.data.mkdir(parents=True)
        connection = sqlite3.connect(str(self.data / "library.db"))
        connection.execute("create table papers(id text, title text)")
        connection.execute("insert into papers values ('p1', 'A paper')")
        connection.commit()
        connection.close()
        (self.data / "prefs.json").write_text('{"theme":"dark"}', encoding="utf-8")
        (self.data / "settings.json").write_text('{"autoCheck":true}', encoding="utf-8")
        (self.data / "sources.json").write_text('[{"name":"arxiv"}]', encoding="utf-8")
        (self.data / "fulltext.txt").write_text("not part of the backup", encoding="utf-8")

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def run_helper(self, *args, timeout=90):
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def backup(self, version="1.7.1", *extra):
        proc = self.run_helper("backup-data", "--program-root", str(self.program),
                               "--new-version", version, *extra)
        return proc

    def backup_dir(self, version="1.7.1"):
        return self.data / "backups" / ("update-" + version)

    def test_backup_copies_database_and_settings(self):
        proc = self.backup()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        self.assertFalse(data["idempotent"])
        self.assertTrue(data["databaseIncluded"])
        self.assertEqual(data["files"], 4)
        self.assertEqual(sorted(item["name"] for item in data["items"]),
                         ["library.db", "prefs.json", "settings.json", "sources.json"])
        backup = self.backup_dir()
        for name in ("library.db", "prefs.json", "settings.json", "sources.json"):
            self.assertTrue((backup / name).is_file(), name)
            self.assertEqual(sha256(backup / name), sha256(self.data / name), name)
        self.assertFalse((backup / "fulltext.txt").exists(), "未列入备份的数据不应复制")
        record = json.loads((backup / "data-backup.json").read_text(encoding="utf-8"))
        self.assertEqual(record["newVersion"], "1.7.1")
        self.assertTrue(record["databaseIncluded"])
        self.assertEqual(record["files"], 4)
        self.assertIn("library.db-wal", record["missing"])

    def test_missing_optional_settings_recorded(self):
        (self.data / "prefs.json").unlink()
        proc = self.backup()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["files"], 3)
        self.assertIn("prefs.json", data["missing"])
        self.assertFalse((self.backup_dir() / "prefs.json").exists())

    def test_missing_library_db_aborts(self):
        (self.data / "library.db").unlink()
        proc = self.backup()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["code"], "library-db-missing")
        self.assertFalse(self.backup_dir().exists())
        backups = self.data / "backups"
        self.assertFalse(backups.exists(), "中止时不应留下备份目录")

    def test_missing_data_dir_aborts(self):
        shutil.rmtree(self.data)
        proc = self.backup()
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "data-missing")

    def test_non_file_data_item_fails_and_cleans_temp(self):
        (self.data / "prefs.json").unlink()
        (self.data / "prefs.json").mkdir()
        proc = self.backup()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["code"], "data-backup-failed")
        self.assertFalse(self.backup_dir().exists())
        leftovers = [path.name for path in (self.data / "backups").iterdir()
                     if ".tmp-" in path.name]
        self.assertEqual(leftovers, [], "失败后不得留下临时备份目录")

    def test_idempotent_backup(self):
        first = self.backup()
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        before = (self.backup_dir() / "data-backup.json").read_text(encoding="utf-8")
        second = self.backup()
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        data = json.loads(second.stdout)
        self.assertTrue(data["idempotent"])
        self.assertEqual(before, (self.backup_dir() / "data-backup.json").read_text(encoding="utf-8"))

    def test_conflicting_existing_backup_rejected(self):
        self.assertEqual(self.backup().returncode, 0)
        (self.data / "prefs.json").write_text('{"theme":"light"}', encoding="utf-8")
        proc = self.backup()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["code"], "data-backup-exists")

    def test_wal_and_shm_copied_when_present(self):
        (self.data / "library.db-wal").write_bytes(b"wal data")
        (self.data / "library.db-shm").write_bytes(b"shm data")
        proc = self.backup()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["files"], 6)
        self.assertEqual(sha256(self.backup_dir() / "library.db-wal"),
                         sha256(self.data / "library.db-wal"))

    def test_retention_keeps_last_n(self):
        for version in ("1.7.1", "1.7.2", "1.7.3", "1.7.4"):
            proc = self.backup(version, "--keep", "2")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        names = sorted(path.name for path in (self.data / "backups").iterdir() if path.is_dir())
        self.assertEqual(names, ["update-1.7.3", "update-1.7.4"])

    def test_backup_is_restorable(self):
        proc = self.backup()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        restore = self.work / "restored-data"
        restore.mkdir()
        backup = self.backup_dir()
        for name in ("library.db", "prefs.json", "settings.json", "sources.json"):
            shutil.copy2(backup / name, restore / name)
        for name in ("library.db", "prefs.json", "settings.json", "sources.json"):
            self.assertEqual(sha256(restore / name), sha256(self.data / name), name)
        connection = sqlite3.connect(str(restore / "library.db"))
        try:
            rows = connection.execute("select id, title from papers").fetchall()
        finally:
            connection.close()
        self.assertEqual(rows, [("p1", "A paper")], "恢复后的数据库必须可打开并可查询")

    def test_invalid_version_rejected(self):
        proc = self.run_helper("backup-data", "--program-root", str(self.program),
                               "--new-version", "../1.7.1")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "invalid-version")
        self.assertFalse((self.data / "backups").exists())


if __name__ == "__main__":
    unittest.main()
