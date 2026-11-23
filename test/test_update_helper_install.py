"""P2-14：程序文件替换范围与旧程序备份（保留 data/）。

用真实编译出的 helper 在临时目录里执行 plan-replace / backup-program / replace-program：
- 只移动/替换程序文件，包根 data/ 原地保留；
- 旧程序文件进入 data/updates/backups/app-<oldVersion>/，含旧 exe 与 version.json；
- 替换后程序文件与 staging 逐文件一致（相对路径 + SHA-256）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
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


def write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def tree_hashes(root: Path, skip_top=("data",)) -> dict:
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        if current == root:
            dirnames[:] = [name for name in dirnames if name not in skip_top]
        for name in filenames:
            path = current / name
            result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


@unittest.skipUnless(POWERSHELL and CSC, "需要 PowerShell 与 .NET Framework csc 才能编译 helper")
class ReplaceSequenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.pkg = cls.dir / "pkg"
        (cls.pkg / "tools" / "updater").mkdir(parents=True)
        write(cls.pkg / "version.json",
              json.dumps({"product": "PaperLibrary", "version": "1.7.1", "tag": "v1.7.1"}))
        shutil.copy2(ROOT / "tools" / "make_updater.ps1", cls.pkg / "tools" / "make_updater.ps1")
        for src in (ROOT / "tools" / "updater").glob("PaperLibraryUpdater*.cs"):
            shutil.copy2(src, cls.pkg / "tools" / "updater" / src.name)
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
        self.staging = self.work / "staging"
        self.make_program()
        self.make_staging()

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def make_program(self, version="1.6.0"):
        write(self.program / "PaperLibrary.exe", "OLD EXE " + version)
        write(self.program / "version.json", json.dumps({"product": "PaperLibrary", "version": version}))
        write(self.program / "paperlib" / "app.py", "old app")
        write(self.program / "public" / "index.html", "<html>old</html>")
        write(self.program / "data" / "library.db", "user database")
        write(self.program / "data" / "prefs.json", '{"theme":"light"}')

    def make_staging(self, version="1.7.1"):
        write(self.staging / "PaperLibrary.exe", "NEW EXE " + version)
        write(self.staging / "version.json", json.dumps({"product": "PaperLibrary", "version": version}))
        write(self.staging / "paperlib" / "app.py", "new app")
        write(self.staging / "vendor" / "lib.js", "new vendor")

    def run_helper(self, *args, timeout=90):
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def backup_dir(self, version="1.6.0"):
        return self.program / "data" / "updates" / "backups" / ("app-" + version)

    def backup(self):
        proc = self.run_helper("backup-program", "--program-root", str(self.program),
                               "--backup", str(self.backup_dir()), "--old-version", "1.6.0")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    def replace(self, new_version="1.7.1"):
        proc = self.run_helper("replace-program", "--program-root", str(self.program),
                               "--staging", str(self.staging), "--new-version", new_version)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return json.loads(proc.stdout)

    # ---------- plan ----------

    def test_plan_reports_scope(self):
        proc = self.run_helper("plan-replace", "--program-root", str(self.program),
                               "--staging", str(self.staging),
                               "--new-version", "1.7.1", "--old-version", "1.6.0")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], "1.7.1")
        self.assertEqual(data["files"], 4)
        self.assertIn("PaperLibrary.exe", data["topLevel"])
        self.assertIn("vendor", data["topLevel"])
        self.assertTrue(data["dataExcluded"])
        self.assertFalse(data["stagingHasData"])
        self.assertTrue(data["backupDir"].lower().endswith("data\\updates\\backups\\app-1.6.0"))

    def test_plan_rejects_staging_data(self):
        write(self.staging / "data" / "library.db", "leaked user data")
        proc = self.run_helper("plan-replace", "--program-root", str(self.program),
                               "--staging", str(self.staging))
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["code"], "data-directory")

    def test_plan_rejects_missing_exe(self):
        (self.staging / "PaperLibrary.exe").unlink()
        proc = self.run_helper("plan-replace", "--program-root", str(self.program),
                               "--staging", str(self.staging))
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "missing-exe")

    def test_plan_rejects_version_mismatch(self):
        proc = self.run_helper("plan-replace", "--program-root", str(self.program),
                               "--staging", str(self.staging), "--new-version", "1.7.2")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "version-mismatch")

    def test_plan_rejects_staging_inside_program(self):
        inside = self.program / "staging"
        write(inside / "PaperLibrary.exe", "x")
        write(inside / "version.json", '{"version":"1.7.1"}')
        proc = self.run_helper("plan-replace", "--program-root", str(self.program),
                               "--staging", str(inside))
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "staging-inside-program")

    # ---------- backup ----------

    def test_backup_moves_program_files_and_keeps_data(self):
        data = self.backup()
        self.assertTrue(data["dataPreserved"])
        self.assertTrue(data["oldExeBackedUp"])
        self.assertTrue(data["oldVersionBackedUp"])
        self.assertEqual(data["files"], 4)  # exe + version.json + 2 program files
        names = sorted(path.name for path in self.program.iterdir())
        self.assertEqual(names, ["data"], "程序根只应留下 data/")
        backup = self.backup_dir()
        self.assertEqual((backup / "PaperLibrary.exe").read_text(encoding="utf-8"), "OLD EXE 1.6.0")
        self.assertEqual(json.loads((backup / "version.json").read_text(encoding="utf-8"))["version"], "1.6.0")
        self.assertEqual((self.program / "data" / "library.db").read_text(encoding="utf-8"), "user database")
        record = json.loads((backup / "backup.json").read_text(encoding="utf-8"))
        self.assertEqual(record["oldVersion"], "1.6.0")
        self.assertTrue(record["dataExcluded"])
        self.assertTrue(record["oldExeBackedUp"])
        self.assertTrue(record["oldVersionBackedUp"])

    def test_backup_rejects_outside_data_updates(self):
        outside = self.work / "outside-backup"
        proc = self.run_helper("backup-program", "--program-root", str(self.program),
                               "--backup", str(outside), "--old-version", "1.6.0")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "backup-path-invalid")
        self.assertTrue((self.program / "PaperLibrary.exe").is_file())

    def test_backup_rejects_nonempty_existing_dir(self):
        backup = self.backup_dir()
        write(backup / "stale.txt", "stale")
        proc = self.run_helper("backup-program", "--program-root", str(self.program),
                               "--backup", str(backup), "--old-version", "1.6.0")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "backup-exists")
        self.assertTrue((self.program / "PaperLibrary.exe").is_file())

    # ---------- replace ----------

    def test_replace_requires_backup_first(self):
        proc = self.run_helper("replace-program", "--program-root", str(self.program),
                               "--staging", str(self.staging))
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "program-root-not-empty")
        self.assertTrue((self.program / "PaperLibrary.exe").is_file())

    def test_replace_after_backup_matches_staging(self):
        self.backup()
        data = self.replace()
        self.assertTrue(data["dataPreserved"])
        self.assertEqual(data["version"], "1.7.1")
        self.assertEqual(data["files"], 4)
        self.assertEqual(tree_hashes(self.program), tree_hashes(self.staging),
                         "替换后程序文件必须与 staging 逐文件一致")
        self.assertEqual((self.program / "data" / "library.db").read_text(encoding="utf-8"), "user database")
        self.assertEqual((self.program / "data" / "prefs.json").read_text(encoding="utf-8"),
                         '{"theme":"light"}')
        self.assertEqual((self.backup_dir() / "PaperLibrary.exe").read_text(encoding="utf-8"),
                         "OLD EXE 1.6.0")

    def test_replace_rejects_staging_data(self):
        write(self.staging / "data" / "library.db", "leaked")
        proc = self.run_helper("replace-program", "--program-root", str(self.program),
                               "--staging", str(self.staging))
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "data-directory")
        self.assertTrue((self.program / "PaperLibrary.exe").is_file(), "拒绝时不得破坏程序目录")

    def test_replace_verifies_copied_version(self):
        write(self.staging / "version.json", json.dumps({"version": "1.7.2"}))
        proc = self.run_helper("replace-program", "--program-root", str(self.program),
                               "--staging", str(self.staging), "--new-version", "1.7.1")
        self.assertEqual(proc.returncode, 3)
        self.assertEqual(json.loads(proc.stdout)["code"], "version-mismatch")


if __name__ == "__main__":
    unittest.main()
