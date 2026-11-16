"""P2-17：健康判定口径 B、超时/重试、失败分类与回滚。

真实 helper + 可配置假 PaperLibrary.exe：
- 失败分类：health-not-ok / health-app / health-version / health-json / health-http / health-timeout；
- 单次 2 秒、间隔 1 秒、总 60 秒（测试用覆盖值缩短）、连续成功 2 次；
- 失败后回滚旧版；成功写 state.json 且 healthChecks >= 2；
- 不修改后端 health 字段（仅客户端判定）。
"""
from __future__ import annotations

import json
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
    POWERSHELL, build_helper, build_fake_app, free_port, kill_pid, write_fake_config,
)

CASES = [
    ("health-not-ok", {"failHealth": True}, 3),
    ("health-app", {"app": "other-app"}, 3),
    ("health-version", {"version": "1.7.0"}, 3),
    ("health-version", {"product": "OtherProduct"}, 3),
    ("health-json", {"badJson": True}, 3),
    ("health-http", {"httpStatus": 500}, 3),
    ("health-timeout", {"healthDelayMs": 3000}, 2),
]


@unittest.skipUnless(POWERSHELL, "需要 PowerShell 才能构建 helper 与 fake app")
class HealthCheckTest(unittest.TestCase):
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
            path = self.updates / "state.json"
            if path.is_file():
                state = json.loads(path.read_text(encoding="utf-8"))
                self.pids.extend([state.get("launchedPid"), state.get("oldLaunchedPid")])
        except Exception:
            pass
        for pid in list(self.pids):
            kill_pid(pid)
        shutil.rmtree(self.work, ignore_errors=True)

    def make_program(self, version):
        self.program.mkdir(parents=True)
        (self.program / "PaperLibrary.exe").write_bytes(self.fake_exe.read_bytes())
        write_fake_config(self.program, self.port, version)
        (self.program / "version.json").write_text(
            json.dumps({"product": "PaperLibrary", "version": version}), encoding="utf-8")
        (self.program / "paperlib").mkdir()
        (self.program / "paperlib" / "app.py").write_text("old", encoding="utf-8")
        self.data.mkdir()
        (self.data / "library.db").write_text("user database", encoding="utf-8")
        self.updates.mkdir()
        (self.updates / "apply.json").write_text('{"schema":1}', encoding="utf-8")

    def make_staging(self, version):
        self.staging = self.updates / version / "staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        (self.staging / "PaperLibrary.exe").write_bytes(self.fake_exe.read_bytes())
        write_fake_config(self.staging, self.port, version)
        (self.staging / "version.json").write_text(
            json.dumps({"product": "PaperLibrary", "version": version}), encoding="utf-8")
        (self.staging / "paperlib").mkdir(exist_ok=True)
        (self.staging / "paperlib" / "app.py").write_text("new", encoding="utf-8")

    def install(self, health_timeout=3.0, request_ms=1000):
        out = self.work / "install.out"
        err = self.work / "install.err"
        args = [
            str(self.exe), "install",
            "--program-root", str(self.program),
            "--staging", str(self.staging),
            "--port", str(self.port),
            "--launcher-pid", "0",
            "--timeout-pid", "5", "--timeout-port", "5", "--timeout-lock", "5",
            "--timeout-total", "120",
            "--health-timeout", str(int(health_timeout)),
            "--health-interval", "200",
            "--health-request-timeout", str(request_ms),
            "--health-successes", "2",
            "--log", str(self.log),
        ]
        started = time.time()
        with open(out, "w", encoding="utf-8") as fout, open(err, "w", encoding="utf-8") as ferr:
            proc = subprocess.run(args, stdout=fout, stderr=ferr, timeout=180)
        elapsed = time.time() - started
        stdout = out.read_text(encoding="utf-8", errors="replace")
        lines = [line for line in stdout.splitlines() if line.strip()]
        return proc.returncode, json.loads(lines[-1]) if lines else {}, elapsed

    def state(self):
        path = self.updates / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def test_health_failure_codes_and_rollback(self):
        for code, overrides, timeout in CASES:
            with self.subTest(code=code, overrides=overrides):
                self.tearDown()
                self.setUp()
                overrides = dict(overrides)
                reported_version = overrides.pop("version", "1.7.1")
                write_fake_config(self.staging, self.port, reported_version, **overrides)
                rc, data, elapsed = self.install(health_timeout=timeout, request_ms=400)
                self.assertEqual(rc, 3, data)
                self.assertEqual(data["code"], code)
                state = self.state()
                self.assertEqual(state["phase"], "failed")
                self.assertEqual(state["code"], code)
                self.assertTrue(state["restored"], state)
                self.assertEqual(json.loads((self.program / "version.json").read_text(encoding="utf-8"))["version"],
                                 "1.6.0")
                self.assertGreater(state["oldLaunchedPid"], 0)
                self.pids.append(state["oldLaunchedPid"])
                if code == "health-timeout":
                    self.assertGreaterEqual(elapsed, 1.5, "总超时未生效")

    def test_two_consecutive_successes_required(self):
        rc, data, elapsed = self.install(health_timeout=3.0)
        self.assertEqual(rc, 0, data)
        state = self.state()
        self.assertEqual(state["phase"], "success")
        self.assertGreaterEqual(state["healthChecks"], 2, "连续成功 2 次才应成功")
        self.pids.append(state["launchedPid"])

    def test_single_success_is_not_enough(self):
        write_fake_config(self.staging, self.port, "1.7.1", okFirstOnly=True)
        rc, data, elapsed = self.install(health_timeout=2, request_ms=1000)
        self.assertEqual(rc, 3, data)
        self.assertEqual(data["code"], "health-not-ok", "一次成功不构成连续 2 次")
        self.assertEqual(self.state()["phase"], "failed")
        self.assertTrue(self.state()["restored"])
        self.assertGreaterEqual(elapsed, 1.5)
        self.pids.append(self.state()["oldLaunchedPid"])


if __name__ == "__main__":
    unittest.main()
