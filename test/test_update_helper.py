"""P2-12：包外 Updater Helper 骨架、构建、部署与自举测试。

覆盖：
- 包内 helper 必须在 tools/updater/（不在包根），版本/大小/SHA-256 与 helper.json 一致；
- 部署到 %LOCALAPPDATA%/PaperLibrary/updater/<version>/（测试用 --home 覆盖）；
- 幂等、升级（新版本目录 + previousVersion + 旧版本保留）、按策略清理；
- 真实编译出的 helper：--version / --self-check / --hold（程序目录被替换期间仍运行）/
  --wait-pid；包内副本自检会明确报告“尚未部署”。
"""
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


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_version(root: Path, version="1.7.1"):
    tag = version_mod.ui_label(version)
    (root / "version.json").write_text(
        json.dumps({"product": "PaperLibrary", "version": version, "tag": tag}),
        encoding="utf-8")
    return tag


def make_package(root, version="1.7.1", exe_bytes=b"MZ fake helper", meta=None,
                 helper_at_root=False):
    root = Path(root)
    helper_dir = root / "tools" / "updater"
    helper_dir.mkdir(parents=True, exist_ok=True)
    (helper_dir / helper.HELPER_EXE).write_bytes(exe_bytes)
    payload = {
        "schema": 1,
        "product": "PaperLibrary",
        "version": version,
        "tag": version_mod.ui_label(version),
        "exe": helper.HELPER_EXE,
        "bytes": len(exe_bytes),
        "sha256": sha256(exe_bytes),
        "builtAt": "2026-11-16T00:00:00Z",
        "source": "tools/updater/PaperLibraryUpdater.cs",
    }
    if meta:
        payload.update(meta)
    (helper_dir / helper.HELPER_META).write_text(json.dumps(payload), encoding="utf-8")
    if helper_at_root:
        (root / helper.HELPER_EXE).write_bytes(exe_bytes)
    return root


class UpdaterHomeTest(unittest.TestCase):
    def test_env_override(self):
        self.assertEqual(helper.updater_home({"PAPERLIB_UPDATER_HOME": r"C:\tmp\h"}),
                         Path(r"C:\tmp\h"))

    def test_local_appdata_layout(self):
        home = helper.updater_home({"LOCALAPPDATA": r"C:\Users\x\AppData\Local"})
        self.assertEqual(str(home), str(Path(r"C:\Users\x\AppData\Local") / "PaperLibrary" / "updater"))

    def test_missing_local_appdata(self):
        with self.assertRaises(helper.HelperError) as ctx:
            helper.updater_home({})
        self.assertEqual(ctx.exception.code, "no-local-appdata")


class DeployTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.home = self.dir / "home"
        self.pkg = make_package(self.dir / "pkg")

    def tearDown(self):
        self.tmp.cleanup()

    def test_package_helper_location(self):
        info = helper.read_package_helper(self.pkg)
        self.assertEqual(info["version"], "1.7.1")
        self.assertEqual(info["tag"], "v1.7.1")
        self.assertEqual(Path(info["path"]), self.pkg / "tools" / "updater" / helper.HELPER_EXE)
        self.assertFalse((self.pkg / helper.HELPER_EXE).exists(), "helper 不得位于包根")

    def test_helper_at_package_root_rejected(self):
        pkg = make_package(self.dir / "bad-root", helper_at_root=True)
        with self.assertRaises(helper.HelperError) as ctx:
            helper.read_package_helper(pkg)
        self.assertEqual(ctx.exception.code, "helper-in-package-root")

    def test_deploy_copies_outside_package(self):
        result = helper.deploy(self.pkg, home=self.home)
        self.assertTrue(result["deployed"])
        self.assertEqual(result["version"], "1.7.1")
        deployed = Path(result["helperPath"])
        self.assertTrue(deployed.is_file())
        self.assertTrue(str(deployed).lower().startswith(str(self.home.resolve()).lower()))
        self.assertFalse(str(deployed).lower().startswith(str(self.pkg.resolve()).lower()),
                         "helper 必须运行在包外")
        current = json.loads((self.home / "current.json").read_text(encoding="utf-8"))
        self.assertEqual(current["version"], "1.7.1")
        self.assertEqual(current["exe"], "1.7.1/" + helper.HELPER_EXE)
        self.assertEqual(current["sha256"], sha256((self.pkg / "tools" / "updater"
                                                   / helper.HELPER_EXE).read_bytes()))
        self.assertEqual(helper.deployed_helper(self.home)["helperPath"], str(deployed))

    def test_deploy_idempotent(self):
        first = helper.deploy(self.pkg, home=self.home)
        before = (self.home / "current.json").read_text(encoding="utf-8")
        second = helper.deploy(self.pkg, home=self.home)
        self.assertFalse(second["deployed"])
        self.assertEqual(second["reason"], "current")
        self.assertEqual(first["sha256"], second["sha256"])
        self.assertEqual(before, (self.home / "current.json").read_text(encoding="utf-8"))

    def test_upgrade_switches_current_and_keeps_previous(self):
        helper.deploy(self.pkg, home=self.home)
        old_exe = self.home / "1.7.1" / helper.HELPER_EXE
        old_bytes = old_exe.read_bytes()
        pkg2 = make_package(self.dir / "pkg2", version="1.7.2", exe_bytes=b"MZ newer helper")
        result = helper.deploy(pkg2, home=self.home)
        self.assertTrue(result["deployed"])
        self.assertEqual(result["version"], "1.7.2")
        self.assertEqual(result["previousVersion"], "1.7.1")
        current = helper.deployed_helper(self.home)
        self.assertEqual(current["version"], "1.7.2")
        self.assertTrue((self.home / "1.7.1" / helper.HELPER_EXE).is_file())
        self.assertEqual(old_exe.read_bytes(), old_bytes, "升级不得破坏上一个版本")
        self.assertIn("1.7.2", (self.home / "current.json").read_text(encoding="utf-8"))

    def test_prune_keeps_two_recent_versions(self):
        for version in ("1.7.1", "1.7.2", "1.7.3"):
            pkg = make_package(self.dir / ("pkg-" + version), version=version,
                               exe_bytes=("MZ " + version).encode())
            helper.deploy(pkg, home=self.home)
        names = sorted(child.name for child in self.home.iterdir() if child.is_dir())
        self.assertEqual(names, ["1.7.2", "1.7.3"])

    def test_sha_mismatch_rejected(self):
        pkg = make_package(self.dir / "bad-sha", meta={"sha256": "0" * 64})
        with self.assertRaises(helper.HelperError) as ctx:
            helper.deploy(pkg, home=self.home)
        self.assertEqual(ctx.exception.code, "helper-sha256")
        self.assertFalse((self.home / "current.json").exists())

    def test_size_mismatch_rejected(self):
        pkg = make_package(self.dir / "bad-size", meta={"bytes": 1})
        with self.assertRaises(helper.HelperError) as ctx:
            helper.deploy(pkg, home=self.home)
        self.assertEqual(ctx.exception.code, "helper-size")

    def test_missing_meta_and_exe(self):
        pkg = make_package(self.dir / "no-meta")
        (pkg / "tools" / "updater" / helper.HELPER_META).unlink()
        with self.assertRaises(helper.HelperError) as ctx:
            helper.read_package_helper(pkg)
        self.assertEqual(ctx.exception.code, "helper-meta-missing")

        pkg2 = make_package(self.dir / "no-exe")
        (pkg2 / "tools" / "updater" / helper.HELPER_EXE).unlink()
        with self.assertRaises(helper.HelperError) as ctx2:
            helper.read_package_helper(pkg2)
        self.assertEqual(ctx2.exception.code, "helper-missing")

    def test_bad_tag_rejected(self):
        pkg = make_package(self.dir / "bad-tag", meta={"tag": "v9.9"})
        with self.assertRaises(helper.HelperError) as ctx:
            helper.read_package_helper(pkg)
        self.assertEqual(ctx.exception.code, "helper-meta-invalid")

    def test_tampered_deployed_helper_not_current(self):
        result = helper.deploy(self.pkg, home=self.home)
        with open(result["helperPath"], "ab") as handle:
            handle.write(b"x")
        self.assertEqual(helper.deployed_helper(self.home), {})

    def test_corrupt_current_is_ignored(self):
        helper.deploy(self.pkg, home=self.home)
        (self.home / "current.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(helper.read_current(self.home), {})
        result = helper.deploy(self.pkg, home=self.home)
        self.assertTrue(result["deployed"], "current.json 损坏时应重新部署")


class DeployCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.home = self.dir / "home"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools" / "deploy_updater.py"), *args],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", cwd=str(ROOT), timeout=120)

    def test_deploy_cli(self):
        pkg = make_package(self.dir / "pkg")
        proc = self.run_cli("--package-root", str(pkg), "--home", str(self.home), "--json")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        record = json.loads(proc.stdout)
        self.assertTrue(record["deployed"])
        self.assertEqual(record["version"], "1.7.1")

    def test_deploy_cli_missing_helper(self):
        pkg = self.dir / "empty"
        pkg.mkdir()
        proc = self.run_cli("--package-root", str(pkg), "--home", str(self.home))
        self.assertEqual(proc.returncode, 1)
        self.assertIn("helper-missing", proc.stderr)


@unittest.skipUnless(POWERSHELL and CSC, "需要 PowerShell 与 .NET Framework csc 才能编译 helper")
class BuiltHelperTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)
        cls.pkg = cls.dir / "pkg"
        (cls.pkg / "tools" / "updater").mkdir(parents=True)
        write_version(cls.pkg)
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

    def run_helper(self, *args, timeout=60):
        return subprocess.run([str(self.exe), *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)

    def test_build_outputs_helper_under_tools_updater(self):
        exe = self.pkg / "tools" / "updater" / helper.HELPER_EXE
        meta = json.loads((self.pkg / "tools" / "updater" / helper.HELPER_META)
                          .read_text(encoding="utf-8"))
        self.assertTrue(exe.is_file())
        self.assertFalse((self.pkg / helper.HELPER_EXE).exists())
        self.assertEqual(meta["version"], "1.7.1")
        self.assertEqual(meta["tag"], "v1.7.1")
        self.assertEqual(meta["sha256"], sha256(exe.read_bytes()))
        self.assertEqual(meta["bytes"], exe.stat().st_size)
        self.assertGreater(exe.stat().st_size, 4096)

    def test_version_json_and_exe_location(self):
        proc = self.run_helper("--version", "--updater-home", str(self.home))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        self.assertEqual(data["helperVersion"], "1.7.1")
        self.assertEqual(data["helperTag"], "v1.7.1")
        self.assertEqual(Path(data["exe"]), self.exe.resolve())
        self.assertEqual(Path(data["updaterHome"]), self.home.resolve())
        self.assertEqual(data["exeSha256"], sha256(self.exe.read_bytes()))

    def test_self_check_passes_when_deployed(self):
        proc = self.run_helper("--self-check", "--program-root", str(self.pkg),
                               "--updater-home", str(self.home))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["ok"])
        checks = {item["name"]: item["ok"] for item in data["checks"]}
        self.assertTrue(checks["helper-not-at-package-root"])
        self.assertTrue(checks["helper-outside-program-root"])
        self.assertTrue(checks["helper-inside-updater-home"])
        self.assertTrue(checks["updater-home-writable"])
        self.assertTrue(checks["log-writable"])

    def test_package_copy_self_check_reports_not_deployed(self):
        package_exe = self.pkg / "tools" / "updater" / helper.HELPER_EXE
        proc = subprocess.run([str(package_exe), "--self-check",
                               "--program-root", str(self.pkg),
                               "--updater-home", str(self.dir / "empty-home")],
                              capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        data = json.loads(proc.stdout)
        self.assertFalse(data["ok"])
        checks = {item["name"]: item["ok"] for item in data["checks"]}
        self.assertTrue(checks["helper-not-at-package-root"], "包内副本在 tools/updater，不在包根")
        self.assertFalse(checks["helper-outside-program-root"])
        self.assertFalse(checks["helper-inside-updater-home"])

    def test_hold_survives_program_directory_replacement(self):
        # 独立的“程序目录”副本，替换它不能影响共享的构建产物（其余用例还要用）。
        program = self.dir / "program"
        (program / "tools" / "updater").mkdir(parents=True)
        shutil.copy2(self.pkg / "tools" / "updater" / helper.HELPER_EXE,
                     program / "tools" / "updater" / helper.HELPER_EXE)
        (program / "version.json").write_text("{}", encoding="utf-8")
        log = self.home / "logs" / "hold.log"
        proc = subprocess.Popen([str(self.exe), "--hold", "5", "--log", str(log),
                                 "--updater-home", str(self.home)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 15
            while time.time() < deadline:
                if log.is_file() and "hold start" in log.read_text(encoding="utf-8", errors="replace"):
                    break
                time.sleep(0.2)
            else:
                self.fail("helper 未写出 hold start 日志")
            shutil.rmtree(program)
            program.mkdir()
            (program / "new-package.txt").write_text("replaced", encoding="utf-8")
            self.assertIsNone(proc.poll(), "程序目录被替换期间 helper 必须仍在运行")
            self.assertEqual(proc.wait(timeout=30), 0)
            text = log.read_text(encoding="utf-8", errors="replace")
            self.assertIn("hold end", text)
            self.assertTrue(self.exe.is_file(), "helper 必须仍在包外目录")
            self.assertFalse((program / "tools").exists())
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

    def test_wait_pid_timeout_and_exit(self):
        child = subprocess.Popen(
            [str(POWERSHELL), "-NoProfile", "-Command", "Start-Sleep -Seconds 30"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            proc = self.run_helper("wait-pid", str(child.pid), "--timeout", "2",
                                   "--updater-home", str(self.home), timeout=30)
            self.assertEqual(proc.returncode, 4, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertFalse(data["ok"])
            self.assertEqual(data["error"], "timeout")
        finally:
            child.kill()
            child.wait(timeout=10)

        short = subprocess.Popen(["cmd.exe", "/c", "exit 0"], stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL)
        short.wait(timeout=30)
        proc2 = self.run_helper("wait-pid", str(short.pid), "--timeout", "5",
                                "--updater-home", str(self.home), timeout=30)
        self.assertEqual(proc2.returncode, 0, proc2.stdout + proc2.stderr)
        self.assertTrue(json.loads(proc2.stdout)["exited"])

    def test_wait_pid_invalid_arguments(self):
        proc = self.run_helper("wait-pid", "not-a-pid", "--updater-home", str(self.home))
        self.assertEqual(proc.returncode, 2)
        proc2 = self.run_helper("hold", "0", "--updater-home", str(self.home))
        self.assertEqual(proc2.returncode, 2)


if __name__ == "__main__":
    unittest.main()
