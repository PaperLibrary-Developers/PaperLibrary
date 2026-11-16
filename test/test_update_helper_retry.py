"""P2-18：安装期共享/锁冲突重试（300ms/21s 默认，测试用覆盖值）。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))

from update_helper_fixture import POWERSHELL, CREATE_NO_WINDOW, build_helper  # noqa: E402


@unittest.skipUnless(POWERSHELL, "需要 PowerShell")
class FileRetryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.exe = build_helper(cls.dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.dir))
        self.program = self.work / "program"
        self.data = self.program / "data"
        self.staging = self.data / "updates" / "1.7.1" / "staging"
        self.program.mkdir(parents=True)
        for name in ("PaperLibrary.exe", "version.json"):
            (self.program / name).write_bytes(b"old " + name.encode())
        (self.program / "paperlib").mkdir()
        (self.program / "paperlib" / "app.py").write_text("old", encoding="utf-8")
        self.data.mkdir()
        (self.data / "library.db").write_text("database", encoding="utf-8")
        (self.staging / "vendor").mkdir(parents=True)
        (self.staging / "PaperLibrary.exe").write_bytes(b"new exe")
        (self.staging / "version.json").write_text('{"version":"1.7.1"}', encoding="utf-8")
        (self.staging / "vendor" / "lib.js").write_text("new lib", encoding="utf-8")
        self.log = self.work / "retry.log"

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def start_locker(self, path):
        marker = self.work / ("locked-" + path.name + ".txt")
        script = ("$f=[IO.File]::Open('" + str(path).replace("'", "''") + "', 'Open', 'Read', 'None'); "
                  "Set-Content -LiteralPath '" + str(marker).replace("'", "''") + "' -Value 'locked'; "
                  "Start-Sleep -Seconds 20")
        proc = subprocess.Popen([str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
                                 "-Command", script], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=CREATE_NO_WINDOW)
        deadline = time.time() + 10
        while time.time() < deadline:
            if marker.is_file(): return proc
            time.sleep(0.05)
        raise AssertionError("文件锁未就绪")

    def run_helper(self, *args):
        out = self.work / "out.txt"
        with open(out, "w", encoding="utf-8") as handle:
            proc = subprocess.run([str(self.exe)] + [str(a) for a in args], stdout=handle,
                                  stderr=subprocess.PIPE, timeout=120)
        lines = [line for line in out.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip()]
        return proc.returncode, json.loads(lines[-1]) if lines else {}

    def test_replace_retry_timeout_returns_file_lock_timeout(self):
        root = self.work / "replace-root"
        (root / "data").mkdir(parents=True)
        locker = self.start_locker(self.staging / "vendor" / "lib.js")
        try:
            code, data = self.run_helper("replace-program", "--program-root", root,
                                         "--staging", self.staging, "--new-version", "1.7.1",
                                         "--file-retry-max-ms", "300", "--file-retry-delay-ms", "50",
                                         "--log", self.log)
        finally:
            locker.kill(); locker.wait(timeout=10)
        self.assertEqual(code, 3, data)
        self.assertEqual(data["code"], "file-lock-timeout")
        self.assertIn("retry replace", self.log.read_text(encoding="utf-8", errors="replace"))

    def test_replace_retry_recovers_after_lock_released(self):
        root = self.work / "replace-root-ok"
        (root / "data").mkdir(parents=True)
        locker = self.start_locker(self.staging / "vendor" / "lib.js")
        proc = subprocess.Popen([str(self.exe), "replace-program", "--program-root", str(root),
                                 "--staging", str(self.staging), "--new-version", "1.7.1",
                                 "--file-retry-max-ms", "5000", "--file-retry-delay-ms", "100",
                                 "--log", str(self.log)],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                creationflags=CREATE_NO_WINDOW)
        time.sleep(0.7)
        locker.kill(); locker.wait(timeout=10)
        stdout, stderr = proc.communicate(timeout=30)
        self.assertEqual(proc.returncode, 0, stdout.decode("utf-8", "replace") + stderr.decode("utf-8", "replace"))
        self.assertTrue((root / "vendor" / "lib.js").is_file())

    def test_backup_data_retry_timeout(self):
        locker = self.start_locker(self.data / "library.db")
        try:
            code, data = self.run_helper("backup-data", "--program-root", self.program,
                                         "--new-version", "1.7.1", "--file-retry-max-ms", "300",
                                         "--file-retry-delay-ms", "50", "--log", self.log)
        finally:
            locker.kill(); locker.wait(timeout=10)
        self.assertEqual(code, 3, data)
        self.assertEqual(data["code"], "file-lock-timeout")
        self.assertFalse((self.data / "backups" / "update-1.7.1").exists())

    def test_backup_program_retry_timeout_rolls_back(self):
        backup = self.data / "updates" / "backups" / "app-1.7.1"
        locker = self.start_locker(self.program / "PaperLibrary.exe")
        try:
            code, data = self.run_helper("backup-program", "--program-root", self.program,
                                         "--backup", backup, "--old-version", "1.7.1",
                                         "--file-retry-max-ms", "300", "--file-retry-delay-ms", "50",
                                         "--log", self.log)
        finally:
            locker.kill(); locker.wait(timeout=10)
        self.assertEqual(code, 3, data)
        self.assertEqual(data["code"], "file-lock-timeout")
        self.assertTrue((self.program / "PaperLibrary.exe").is_file(), "失败后必须回滚已移动文件")


if __name__ == "__main__":
    unittest.main()
