"""P2-11R：启动器→helper 启动参数契约与真实子进程接收。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
if not CSC.is_file():
    CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe"

FAKE_HELPER = r'''
using System;
using System.IO;
class FakeHelper {
    static int Main(string[] args) {
        File.WriteAllLines(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "args.txt"), args);
        return 0;
    }
}
'''


@unittest.skipUnless(CSC.is_file(), "需要 .NET Framework csc.exe")
class LauncherInstallArgsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.probe = cls.dir / "LauncherCoreProbe.exe"
        core = cls.dir / "LauncherCore.cs"
        probe = cls.dir / "LauncherCoreProbe.cs"
        core.write_text((ROOT / "launcher" / "LauncherCore.cs").read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        probe.write_text((ROOT / "launcher" / "LauncherCoreProbe.cs").read_text(encoding="utf-8-sig"), encoding="utf-8-sig")
        proc = subprocess.run([str(CSC), "/nologo", "/target:exe", "/out:" + str(cls.probe),
                               "/r:System.dll", "/r:System.Core.dll", "/r:System.Web.Extensions.dll",
                               str(core), str(probe)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0:
            raise AssertionError("probe 构建失败：" + (proc.stdout or "") + (proc.stderr or ""))
        helper_dir = cls.dir / "fake-helper"
        helper_dir.mkdir()
        source = helper_dir / "FakeHelper.cs"
        source.write_text(FAKE_HELPER, encoding="utf-8-sig")
        cls.helper = helper_dir / "FakeHelper.exe"
        proc = subprocess.run([str(CSC), "/nologo", "/target:exe", "/out:" + str(cls.helper), str(source)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0:
            raise AssertionError("fake helper 构建失败：" + (proc.stdout or "") + (proc.stderr or ""))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def build_args(self, staging, backup, log):
        proc = subprocess.run([str(self.probe), "build-args", "1.7.1", "1.6.0",
                               str(staging), str(backup), str(log), str(self.dir / "program")],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return proc.stdout.strip()

    def test_args_include_launcher_pid_and_paths(self):
        staging = self.dir / "data" / "updates" / "1.7.1" / "staging"
        backup = self.dir / "data" / "updates" / "backups" / "app-1.6.0"
        log = self.dir / "data" / "updates" / "install-1.7.1.log"
        line = self.build_args(staging, backup, log)
        self.assertIn("install", line)
        self.assertIn("--program-root", line)
        self.assertIn("--launcher-pid 1234", line)
        self.assertIn("--old-version 1.6.0", line)
        self.assertIn("--new-version 1.7.1", line)
        self.assertIn(str(staging), line)
        self.assertIn(str(backup), line)
        self.assertIn(str(log), line)
        self.assertIn("--port 4321", line)

    def test_real_helper_process_receives_args(self):
        staging = self.dir / "data" / "updates" / "1.7.1" / "staging"
        backup = self.dir / "data" / "updates" / "backups" / "app-1.6.0"
        log = self.dir / "data" / "updates" / "install-1.7.1.log"
        line = self.build_args(staging, backup, log)
        args = line.split()
        proc = subprocess.run([str(self.helper)] + args, cwd=str(self.helper.parent),
                              capture_output=True, timeout=60)
        self.assertEqual(proc.returncode, 0)
        received = (self.helper.parent / "args.txt").read_text(encoding="utf-8").splitlines()
        self.assertEqual(received, args)
        self.assertIn("--launcher-pid", received)
        self.assertIn("1234", received)


if __name__ == "__main__":
    unittest.main()
