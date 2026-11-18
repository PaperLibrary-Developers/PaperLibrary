"""P2-16：journal 状态机、install.lock 过期/清理、helper recover 启动恢复。

覆盖：
- journal schema/阶段字段，成功路径 journal=success；
- install.lock 活跃时阻止、过期锁（PID 不存在 / 超过 30 分钟）可安全清理；
- recover 无 journal/success journal 的 no-op；
- 在 prepare/backup/replace/verify/launch 各阶段强杀 helper 后 recover 恢复旧版本并可用；
- 程序备份保留最近一个（app-*）。
"""
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

from update_helper_fixture import (  # noqa: E402
    CREATE_NO_WINDOW, POWERSHELL, kill_pid, free_port, tree_hashes, build_helper, build_fake_app,
    write_fake_config,
)

PHASES = ("prepare", "backup", "replace", "verify", "launch")


@unittest.skipUnless(POWERSHELL, "需要 PowerShell 才能构建 helper 与 fake app")
class RecoverTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.exe = build_helper(cls.dir)
        cls.fake_exe = build_fake_app(cls.dir)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.dir))
        self.program = self.work / "program"
        self.data = self.program / "data"
        self.updates = self.data / "updates"
        self.port = free_port()
        self.log = self.work / "install.log"
        self.pids = []
        self.make_program("1.6.0")
        self.make_staging("1.7.1")

    def tearDown(self):
        try:
            state_path = self.updates / "state.json"
            if state_path.is_file():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.pids.extend([state.get("launchedPid"), state.get("oldLaunchedPid")])
        except Exception:
            pass
        for pid in list(self.pids):
            kill_pid(pid)
        shutil.rmtree(self.work, ignore_errors=True)

    # ---------- fixtures ----------

    def make_program(self, version: str):
        self.program.mkdir(parents=True)
        (self.program / "PaperLibrary.exe").write_bytes(self.fake_exe.read_bytes())
        write_fake_config(self.program, self.port, version)
        (self.program / "version.json").write_text(
            json.dumps({"product": "PaperLibrary", "version": version}), encoding="utf-8")
        (self.program / "paperlib").mkdir()
        (self.program / "paperlib" / "app.py").write_text("old app", encoding="utf-8")
        (self.program / "public").mkdir()
        (self.program / "public" / "index.html").write_text("<html>old</html>", encoding="utf-8")
        self.data.mkdir()
        (self.data / "library.db").write_text("user database", encoding="utf-8")
        (self.data / "prefs.json").write_text('{"theme":"dark"}', encoding="utf-8")
        self.updates.mkdir(parents=True)
        (self.updates / "apply.json").write_text('{"schema":1,"version":"1.7.1"}', encoding="utf-8")

    def make_staging(self, version: str):
        self.staging = self.updates / version / "staging"
        self.staging.mkdir(parents=True)
        (self.staging / "PaperLibrary.exe").write_bytes(self.fake_exe.read_bytes())
        write_fake_config(self.staging, self.port, version)
        (self.staging / "version.json").write_text(
            json.dumps({"product": "PaperLibrary", "version": version}), encoding="utf-8")
        (self.staging / "paperlib").mkdir()
        (self.staging / "paperlib" / "app.py").write_text("new app", encoding="utf-8")
        (self.staging / "vendor").mkdir()
        (self.staging / "vendor" / "lib.js").write_text("new vendor", encoding="utf-8")

    # ---------- helpers ----------

    def install_args(self, phase_delay_ms=0, exit_after=""):
        args = [
            str(self.exe), "install",
            "--program-root", str(self.program),
            "--staging", str(self.staging),
            "--port", str(self.port),
            "--launcher-pid", "0",
            "--timeout-pid", "5",
            "--timeout-port", "5",
            "--timeout-lock", "5",
            "--timeout-total", "120",
            "--health-timeout", "10",
            "--health-interval", "200",
            "--health-request-timeout", "1000",
            "--health-successes", "2",
            "--phase-delay-ms", str(phase_delay_ms),
            "--log", str(self.log),
        ]
        if exit_after:
            args.extend(["--exit-after-phase", exit_after])
        return args

    def install(self, phase_delay_ms=0, exit_after=""):
        out = self.work / "install.out"
        err = self.work / "install.err"
        with open(out, "w", encoding="utf-8") as fout, open(err, "w", encoding="utf-8") as ferr:
            proc = subprocess.run(self.install_args(phase_delay_ms, exit_after),
                                  stdout=fout, stderr=ferr, timeout=180)
        stdout = out.read_text(encoding="utf-8", errors="replace")
        lines = [line for line in stdout.splitlines() if line.strip()]
        return proc.returncode, json.loads(lines[-1]) if lines else {}

    def recover(self):
        out = self.work / "recover.out"
        err = self.work / "recover.err"
        with open(out, "w", encoding="utf-8") as fout, open(err, "w", encoding="utf-8") as ferr:
            proc = subprocess.run([str(self.exe), "recover", "--program-root", str(self.program),
                                   "--log", str(self.log)],
                                  stdout=fout, stderr=ferr, timeout=120)
        stdout = out.read_text(encoding="utf-8", errors="replace")
        lines = [line for line in stdout.splitlines() if line.strip()]
        return proc.returncode, json.loads(lines[-1]) if lines else {}

    def journal(self):
        path = self.updates / "journal.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def state(self):
        path = self.updates / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def wait_phase(self, phase, timeout=20):
        path = self.updates / "journal.json"
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("phase") == phase:
                    return True
            except Exception:
                pass
            time.sleep(0.02)
        return False

    def kill_at_phase(self, phase):
        # 测试专用故障注入：helper 写完该阶段 journal 后 Environment.Exit(77)，
        # 等价于在该阶段被强杀（子进程/新应用由 recover 处理）。
        code, _ = self.install(exit_after=phase)
        self.assertEqual(code, 77, "阶段 " + phase + " 未按预期强制退出")
        self.assertEqual(self.journal().get("phase"), phase)

    # ---------- journal / lock ----------

    def test_journal_and_state_schema_on_success(self):
        code, data = self.install()
        self.assertEqual(code, 0, data)
        journal = self.journal()
        for key in ("schema", "product", "phase", "version", "fromVersion", "staging",
                    "dataBackupDir", "backupDir", "launcherPid", "startedAt",
                    "launchedPid", "updatedAt", "code", "error", "log"):
            self.assertIn(key, journal, key)
        self.assertEqual(journal["phase"], "success")
        self.assertEqual(journal["version"], "1.7.1")
        self.assertEqual(journal["fromVersion"], "1.6.0")
        self.assertGreater(journal["launchedPid"], 0)
        self.assertEqual(journal["code"], "")
        state = self.state()
        for key in ("schema", "product", "phase", "version", "fromVersion", "staging",
                    "dataBackupDir", "backupDir", "launchedPid", "oldLaunchedPid",
                    "restored", "recovered", "healthSeconds", "healthChecks",
                    "startedAt", "finishedAt", "code", "error", "log"):
            self.assertIn(key, state, key)
        self.assertEqual(state["phase"], "success")
        self.assertFalse(state["recovered"])
        self.assertGreaterEqual(state["healthChecks"], 2)
        self.pids.append(state["launchedPid"])

    def test_active_install_lock_blocks(self):
        self.updates.mkdir(parents=True, exist_ok=True)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (self.updates / "install.lock").write_text(json.dumps({
            "schema": 1, "product": "PaperLibrary", "pid": os.getpid(),
            "launcherPid": 0, "version": "1.7.1", "fromVersion": "1.6.0", "createdAt": now,
        }), encoding="utf-8")
        before = tree_hashes(self.program)
        code, data = self.install()
        self.assertEqual(code, 3)
        self.assertEqual(data["code"], "install-locked")
        self.assertEqual(tree_hashes(self.program), before)
        self.assertTrue((self.updates / "install.lock").exists())

    def test_stale_lock_dead_pid_allows_install(self):
        self.updates.mkdir(parents=True, exist_ok=True)
        (self.updates / "install.lock").write_text(json.dumps({
            "schema": 1, "product": "PaperLibrary", "pid": 999999,
            "launcherPid": 0, "version": "1.7.1", "fromVersion": "1.6.0",
            "createdAt": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        code, data = self.install()
        self.assertEqual(code, 0, data)
        self.assertFalse((self.updates / "install.lock").exists())
        state = self.state()
        self.pids.append(state["launchedPid"])

    def test_recover_no_journal_and_success_journal(self):
        code, data = self.recover()
        self.assertEqual(code, 0)
        self.assertFalse(data["recovered"])
        self.assertEqual(data["reason"], "no-journal")

        (self.updates / "journal.json").write_text(json.dumps({
            "schema": 1, "product": "PaperLibrary", "phase": "success",
            "version": "1.7.1", "fromVersion": "1.6.0",
        }), encoding="utf-8")
        code, data = self.recover()
        self.assertEqual(code, 0)
        self.assertFalse(data["recovered"])
        self.assertEqual(data["reason"], "journal-success")

    # ---------- strong kill / recovery ----------

    def test_strong_kill_each_phase_recovers_old_version(self):
        for phase in PHASES:
            with self.subTest(phase=phase):
                self.tearDown()
                self.setUp()
                self.kill_at_phase(phase)
                before_data = (self.data / "library.db").read_text(encoding="utf-8")
                code, data = self.recover()
                self.assertEqual(code, 0, data)
                self.assertTrue(data["recovered"], data)
                self.assertEqual(data["phase"], phase)
                state = self.state()
                self.assertEqual(state["phase"], "failed")
                self.assertTrue(state["recovered"])
                self.assertEqual(json.loads((self.program / "version.json").read_text(encoding="utf-8"))["version"],
                                 "1.6.0")
                self.assertEqual((self.data / "library.db").read_text(encoding="utf-8"), before_data)
                self.assertTrue((self.updates / "apply.json").exists(), "失败恢复后保留 apply 供重试")
                self.assertGreater(state["oldLaunchedPid"], 0, "恢复后应启动旧版本")
                self.pids.append(state["oldLaunchedPid"])
                self.assertEqual(self.journal()["phase"], "failed")
                self.assertEqual(self.journal()["code"], "recovered")

    def test_program_backup_retention_keeps_latest(self):
        backups = self.updates / "backups"
        old = backups / "app-1.5.0"
        old.mkdir(parents=True)
        (old / "PaperLibrary.exe").write_bytes(b"old backup")
        (old / "version.json").write_text('{"version":"1.5.0"}', encoding="utf-8")
        code, data = self.install()
        self.assertEqual(code, 0, data)
        names = sorted(path.name for path in backups.iterdir() if path.is_dir())
        self.assertEqual(names, ["app-1.6.0"])
        self.pids.append(self.state()["launchedPid"])

    def test_launch_recovery_refuses_bad_snapshot_and_can_retry(self):
        self.kill_at_phase("launch")
        db = self.data / "library.db"
        backup = self.data / "backups" / "update-1.7.1" / "library.db"
        original = backup.read_bytes()
        db.write_bytes(b"new version changed the database")
        backup.write_bytes(b"damaged backup")

        code, data = self.recover()
        self.assertEqual((code, data["code"]), (3, "recovery-failed"))
        self.assertEqual(self.journal()["phase"], "launch")
        self.assertEqual(db.read_bytes(), b"new version changed the database")
        self.assertEqual(self.state()["oldLaunchedPid"], 0)

        backup.write_bytes(original)
        code, data = self.recover()
        self.assertEqual(code, 0, data)
        self.assertEqual(db.read_bytes(), original)
        self.assertEqual(self.journal()["phase"], "failed")

    def test_recover_interrupted_program_backup_keeps_unmoved_files(self):
        self.kill_at_phase("backup")
        backup = self.updates / "backups" / "app-1.6.0"
        backup.mkdir(parents=True)
        (self.program / "PaperLibrary.exe").rename(backup / "PaperLibrary.exe")
        before = (self.program / "public" / "index.html").read_bytes()

        code, data = self.recover()
        self.assertEqual(code, 0, data)
        self.assertTrue((self.program / "PaperLibrary.exe").is_file())
        self.assertEqual((self.program / "public" / "index.html").read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
