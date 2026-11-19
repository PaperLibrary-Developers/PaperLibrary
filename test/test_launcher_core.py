"""P2-10R：启动器核心的 WebView2 消息协议、apply/helper 预检与启动判定。"""
from __future__ import annotations

import hashlib
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
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
if not CSC.is_file():
    CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "csc.exe"


def write_utf8_bom(path: Path, text: str):
    path.write_text(text, encoding="utf-8-sig")


@unittest.skipUnless(CSC.is_file(), "需要 .NET Framework csc.exe")
class LauncherCoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.probe = cls.dir / "LauncherCoreProbe.exe"
        core = ROOT / "launcher" / "LauncherCore.cs"
        probe = ROOT / "launcher" / "LauncherCoreProbe.cs"
        core_tmp = cls.dir / "LauncherCore.cs"
        probe_tmp = cls.dir / "LauncherCoreProbe.cs"
        write_utf8_bom(core_tmp, core.read_text(encoding="utf-8-sig"))
        write_utf8_bom(probe_tmp, probe.read_text(encoding="utf-8-sig"))
        proc = subprocess.run([str(CSC), "/nologo", "/target:exe", "/out:" + str(cls.probe),
                               "/r:System.dll", "/r:System.Core.dll", str(core_tmp), str(probe_tmp)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=120)
        if proc.returncode != 0:
            raise AssertionError("LauncherCoreProbe 构建失败：" + (proc.stdout or "") + (proc.stderr or ""))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.dir))
        self.base = self.work / "program"
        self.data = self.base / "data"
        self.updates = self.data / "updates"
        self.base.mkdir(parents=True)
        self.data.mkdir()
        self.updates.mkdir()
        self.home = self.work / "updater-home"
        self.helper_dir = self.home / "1.7.1"
        self.helper_dir.mkdir(parents=True)
        self.helper = self.helper_dir / "PaperLibraryUpdater.exe"
        self.helper.write_bytes(b"fake helper exe")
        self.helper_sha = hashlib.sha256(self.helper.read_bytes()).hexdigest()
        (self.home / "current.json").write_text(json.dumps({
            "schema": 1, "product": "PaperLibrary", "version": "1.7.1",
            "exe": "1.7.1/PaperLibraryUpdater.exe", "sha256": self.helper_sha,
        }), encoding="utf-8")
        self.staging = self.updates / "1.7.1" / "staging"
        self.staging.mkdir(parents=True)
        (self.staging / "PaperLibrary.exe").write_bytes(b"new launcher")
        (self.staging / "version.json").write_text(json.dumps({"product": "PaperLibrary", "version": "1.7.1"}), encoding="utf-8")
        self.apply_path = self.updates / "apply.json"
        self.write_apply()

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def write_apply(self, **overrides):
        payload = {
            "schema": 1, "version": "1.7.1", "fromVersion": "1.6.0",
            "stagingPath": str(self.staging), "backupPath": str(self.updates / "backups" / "app-1.6.0"),
        }
        payload.update(overrides)
        self.apply_path.write_text(json.dumps(payload), encoding="utf-8")

    def probe_json(self, *args):
        proc = subprocess.run([str(self.probe)] + [str(arg) for arg in args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=60)
        lines = [line for line in (proc.stdout or "").splitlines() if line.strip()]
        return proc.returncode, json.loads(lines[-1]) if lines else {}

    def precheck(self):
        return self.probe_json("precheck", self.base, self.home)

    def startup(self):
        return self.probe_json("startup", self.base, self.home)

    def test_allowed_source_and_request_protocol(self):
        code, data = self.probe_json("source", "http://127.0.0.1:4321/settings")
        self.assertEqual((code, data), (0, {"allowed": True}))
        code, data = self.probe_json("source", "https://127.0.0.1:4321/")
        self.assertEqual((code, data), (0, {"allowed": False}))
        code, data = self.probe_json("source", "http://evil.example:4321/")
        self.assertEqual((code, data), (0, {"allowed": False}))

        request = json.dumps({"type": "paperlibrary:install.request", "requestId": "req-1", "version": "1.7.1"})
        code, data = self.probe_json("request", request)
        self.assertEqual(code, 0)
        self.assertEqual(data["ok"], True)
        self.assertEqual(data["requestId"], "req-1")
        self.assertEqual(data["version"], "1.7.1")

        code, data = self.probe_json("request", json.dumps({"type": "other", "requestId": "x"}))
        self.assertEqual(code, 3)
        self.assertEqual(data["code"], "not-install-request")
        code, data = self.probe_json("request", json.dumps({"type": "paperlibrary:install.request", "requestId": "../bad"}))
        self.assertEqual(data["code"], "invalid-request-id")

        code, data = self.probe_json("result", "req-1", "false", "helper-missing", "缺少助手")
        self.assertEqual(code, 0)
        self.assertEqual(data["type"], "paperlibrary:install.result")
        self.assertEqual(data["requestId"], "req-1")
        self.assertEqual(data["ok"], False)
        self.assertEqual(data["code"], "helper-missing")

    def test_precheck_valid_apply_and_helper(self):
        code, data = self.precheck()
        self.assertEqual(code, 0, data)
        self.assertEqual(data["ok"], True, data)
        self.assertEqual(data["code"], "ok")
        self.assertEqual(data["version"], "1.7.1")
        self.assertEqual(data["fromVersion"], "1.6.0")
        self.assertEqual(Path(data["stagingPath"]), self.staging.resolve())
        self.assertEqual(Path(data["helperPath"]), self.helper.resolve())
        self.assertTrue(data["logPath"].endswith("install-1.7.1.log"))

    def test_precheck_requires_300mb_margin(self):
        code, data = self.probe_json("precheck", self.base, self.home, "1")
        self.assertEqual(code, 3, data)
        self.assertEqual(data["code"], "disk-low")
        self.assertIn("300 MB", data["message"])

        code, data = self.probe_json("precheck", self.base, self.home, str(10 * 1024 * 1024 * 1024))
        self.assertEqual(code, 0, data)
        self.assertEqual(data["code"], "ok")

    def test_precheck_rejects_program_files(self):
        code, data = self.probe_json("precheck", self.base, self.home,
                                     str(10 * 1024 * 1024 * 1024), "C:\\")
        self.assertEqual(code, 3, data)
        self.assertEqual(data["code"], "program-files")
        self.assertIn("Program Files", data["message"])

    def test_precheck_rejects_invalid_apply(self):
        self.apply_path.unlink()
        code, data = self.precheck()
        self.assertEqual(code, 3)
        self.assertEqual(data["code"], "apply-missing")

        self.write_apply(version="1.7.2")
        code, data = self.precheck()
        self.assertEqual(data["code"], "version-mismatch")

        outside = self.work / "outside-staging"
        outside.mkdir()
        (outside / "PaperLibrary.exe").write_bytes(b"x")
        (outside / "version.json").write_text('{"version":"1.7.1"}', encoding="utf-8")
        self.write_apply(stagingPath=str(outside))
        code, data = self.precheck()
        self.assertEqual(data["code"], "staging-outside-data")

        self.write_apply(stagingPath=str(self.updates / "missing" / "staging"))
        code, data = self.precheck()
        self.assertEqual(data["code"], "staging-missing")

        (self.staging / "PaperLibrary.exe").unlink()
        self.write_apply()
        code, data = self.precheck()
        self.assertEqual(data["code"], "staging-incomplete")

    def test_precheck_rejects_helper_missing_or_bad_hash(self):
        self.helper.unlink()
        code, data = self.precheck()
        self.assertEqual(code, 3)
        self.assertEqual(data["code"], "helper-path")

        self.helper.write_bytes(b"changed helper")
        code, data = self.precheck()
        self.assertEqual(data["code"], "helper-sha256")

    def test_startup_decisions(self):
        code, data = self.startup()
        self.assertEqual(data["action"], "prompt-apply", "有 apply 时必须只提示，不能静默安装")

        self.apply_path.unlink()
        code, data = self.startup()
        self.assertEqual(data["action"], "start")

        (self.updates / "journal.json").write_text(json.dumps({
            "schema": 1, "product": "PaperLibrary", "phase": "replace", "version": "1.7.1",
        }), encoding="utf-8")
        code, data = self.startup()
        self.assertEqual(data["action"], "recover")
        self.assertEqual(data["code"], "journal-replace")

        (self.updates / "journal.json").write_text(json.dumps({
            "schema": 1, "product": "PaperLibrary", "phase": "failed", "version": "1.7.1",
        }), encoding="utf-8")
        (self.updates / "state.json").write_text(json.dumps({
            "schema": 1, "phase": "failed", "recovered": True, "code": "recovered",
            "version": "1.7.1", "log": "install.log",
        }), encoding="utf-8")
        code, data = self.startup()
        self.assertEqual(data["action"], "notice-failed")
        self.assertIn("install.log", data["message"])

    def test_install_lock_active_and_stale(self):
        lock = self.updates / "install.lock"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        lock.write_text(json.dumps({
            "schema": 1, "pid": os.getpid(), "createdAt": now,
        }), encoding="utf-8")
        code, data = self.probe_json("lock", lock)
        self.assertEqual(data["active"], True)
        code, data = self.startup()
        self.assertEqual(data["action"], "install-in-progress")

        lock.write_text(json.dumps({
            "schema": 1, "pid": 999999, "createdAt": "2026-01-01T00:00:00Z",
        }), encoding="utf-8")
        code, data = self.probe_json("lock", lock)
        self.assertEqual(data["active"], False)
        code, data = self.startup()
        self.assertEqual(data["action"], "clean-stale-lock")

    def test_state_notices(self):
        self.apply_path.unlink()
        (self.updates / "state.json").write_text(json.dumps({
            "schema": 1, "phase": "success", "version": "1.7.1", "code": "",
        }), encoding="utf-8")
        code, data = self.startup()
        self.assertEqual(data["action"], "notice-success")
        self.assertIn("1.7.1", data["message"])

        (self.updates / "state.json").write_text(json.dumps({
            "schema": 1, "phase": "failed", "version": "1.7.1", "code": "health-version",
            "log": "data/logs/install.log",
        }), encoding="utf-8")
        code, data = self.startup()
        self.assertEqual(data["action"], "notice-failed")
        self.assertIn("health-version", data["message"])
        self.assertIn("data/logs/install.log", data["message"])

    def test_only_recorded_candidate_can_probe_before_commit(self):
        pid = os.getpid()
        (self.updates / "install.lock").write_text(json.dumps({
            "pid": pid, "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }), encoding="utf-8")
        journal = self.updates / "journal.json"
        journal.write_text(json.dumps({"phase": "launch", "launchedPid": 0}), encoding="utf-8")
        self.assertEqual(self.probe_json("update-probe", self.base, pid)[1]["state"], "starting")

        journal.write_text(json.dumps({"phase": "launch", "launchedPid": pid}), encoding="utf-8")
        self.assertEqual(self.probe_json("update-probe", self.base, pid)[1]["state"], "validating")
        self.assertEqual(self.probe_json("update-probe", self.base, pid + 1)[1]["state"], "")
        self.assertEqual(self.startup()[1]["action"], "install-in-progress")

        journal.write_text(json.dumps({"phase": "success", "launchedPid": pid}), encoding="utf-8")
        self.assertEqual(self.probe_json("update-probe", self.base, pid)[1]["state"], "committed")


@unittest.skipUnless(CSC.is_file(), "需要 .NET Framework csc.exe")
class LauncherClosingTest(unittest.TestCase):
    def test_unavailable_helper_does_not_block_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            webview = ROOT / "tools" / "webview2"
            sources = []
            for name in ("LauncherCore.cs", "PaperLibraryLauncher.cs"):
                source = root / name
                write_utf8_bom(source, (ROOT / "launcher" / name).read_text(encoding="utf-8-sig"))
                sources.append(str(source))
            references = ["System.dll", "System.Core.dll", "System.Drawing.dll", "System.Windows.Forms.dll"]
            for name in ("Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll"):
                dll = root / name
                shutil.copyfile(webview / name, dll)
                references.append(str(dll))
            probe_source = root / "ClosingProbe.cs"
            write_utf8_bom(probe_source, r'''
using System;
using System.Windows.Forms;

class ClosingProbe
{
    [STAThread]
    static int Main(string[] args)
    {
        int nextAnswer = 1;
        var closing = new FormClosingEventArgs((CloseReason)Enum.Parse(typeof(CloseReason), args[0]), false);
        Launcher.HandleClosing(closing, (message, buttons, icon, defaultButton) =>
        {
            Console.WriteLine(buttons + " " + defaultButton);
            return (DialogResult)Enum.Parse(typeof(DialogResult), args[nextAnswer++]);
        });
        Console.WriteLine(closing.Cancel);
        return nextAnswer == args.Length ? 0 : 1;
    }
}
''')
            probe = root / "ClosingProbe.exe"
            build = subprocess.run(
                [str(CSC), "/nologo", "/target:exe", "/main:ClosingProbe", "/out:" + str(probe),
                 *("/r:" + ref for ref in references), *sources, str(probe_source)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            self.assertEqual(build.returncode, 0, build.stdout + build.stderr)

            updates = root / "data" / "updates"
            staging = updates / "1.8.3" / "staging"
            staging.mkdir(parents=True)
            executable = staging / "PaperLibrary.exe"
            executable.write_bytes(b"staged launcher")
            (staging / "version.json").write_text('{"version":"1.8.3"}', encoding="utf-8")
            apply_path = updates / "apply.json"
            apply_contents = json.dumps({"schema": 1, "version": "1.8.3", "stagingPath": str(staging)})
            apply_path.write_text(apply_contents, encoding="utf-8")
            log = root / "data" / "logs" / "launcher.log"

            initial = "YesNoCancel Button3"
            failed = "YesNo Button2"
            scenarios = (
                ("UserClosing", ("Cancel",), [initial, "True"]),
                ("UserClosing", ("No",), [initial, "False"]),
                ("UserClosing", ("Yes", "No"), [initial, failed, "True"]),
                ("UserClosing", ("Yes", "Yes"), [initial, failed, "False"]),
                ("WindowsShutDown", (), ["False"]),
            )
            for reason, answers, expected in scenarios:
                with self.subTest(reason=reason, answers=answers):
                    log.unlink(missing_ok=True)
                    result = subprocess.run([str(probe), reason, *answers], capture_output=True,
                                            text=True, encoding="utf-8", errors="replace", timeout=10)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertEqual(result.stdout.splitlines(), expected)
                    logged = log.read_text(encoding="utf-8")
                    self.assertEqual("window closing" in logged, expected[-1] == "False")
                    self.assertEqual("manual close install precheck: helper-missing" in logged, failed in expected)
                    self.assertEqual(apply_path.read_text(encoding="utf-8"), apply_contents)
                    self.assertEqual(executable.read_bytes(), b"staged launcher")


if __name__ == "__main__":
    unittest.main()
