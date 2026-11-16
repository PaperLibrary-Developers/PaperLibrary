"""P2-13：helper install 完整时序（等待、备份、替换、启动、健康、回滚）。

真实编译 helper 与一个假 PaperLibrary.exe（本地 HTTP 服务）后，在临时程序目录上执行 install：
- 正常路径：备份 → 替换 → 启动新版 → 健康检查（health + /api/version，2 次）→ state success；
- 启动器未退出 / 端口占用 / 文件锁占用：超时中止，文件未动；
- 健康失败 / 版本不符 / 替换失败：回滚旧版并重新启动；
- 数据备份失败、install.lock：中止且不改程序文件；
- staging 允许位于 data/updates/<version>/staging（正式布局）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from paperlib import version as version_mod  # noqa: E402
from paperlib.update import helper  # noqa: E402

POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))
CREATE_NO_WINDOW = 0x08000000


def find_csc():
    for framework in ("Framework64", "Framework"):
        candidate = WINDIR / "Microsoft.NET" / framework / "v4.0.30319" / "csc.exe"
        if candidate.is_file():
            return candidate
    return None


CSC = find_csc()

FAKE_APP_SOURCE = r'''using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;

class FakeApp
{
    static int Port;
    static string Version = "";
    static bool FailHealth;
    static string BaseDir = "";

    static void Main(string[] args)
    {
        BaseDir = AppDomain.CurrentDomain.BaseDirectory;
        string config = File.ReadAllText(Path.Combine(BaseDir, "fake-app.json"), Encoding.UTF8);
        Port = int.Parse(Regex.Match(config, "\"port\"\\s*:\\s*(\\d+)").Groups[1].Value);
        Version = Regex.Match(config, "\"version\"\\s*:\\s*\"([^\"]*)\"").Groups[1].Value;
        FailHealth = Regex.IsMatch(config, "\"failHealth\"\\s*:\\s*true");
        Append("start pid=" + Process.GetCurrentProcess().Id + " version=" + Version + " port=" + Port);
        TcpListener listener = new TcpListener(IPAddress.Loopback, Port);
        listener.Start();
        while (true)
        {
            TcpClient client;
            try { client = listener.AcceptTcpClient(); }
            catch (Exception) { break; }
            ThreadPool.QueueUserWorkItem(Handle, client);
        }
    }

    static void Handle(object state)
    {
        TcpClient client = (TcpClient)state;
        try
        {
            using (client)
            {
                NetworkStream stream = client.GetStream();
                StreamReader reader = new StreamReader(stream, Encoding.ASCII);
                string line = reader.ReadLine();
                if (line == null) return;
                string[] parts = line.Split(' ');
                string path = parts.Length > 1 ? parts[1] : "/";
                while (true)
                {
                    string header = reader.ReadLine();
                    if (header == null || header.Length == 0) break;
                }
                if (path.StartsWith("/api/shutdown"))
                {
                    Write(stream, 200, "{\"ok\":true,\"message\":\"shutting down\"}");
                    Append("shutdown requested");
                    Environment.Exit(0);
                    return;
                }
                if (path.StartsWith("/api/health"))
                {
                    string body = "{\"ok\":" + (FailHealth ? "false" : "true")
                        + ",\"app\":\"literature-vault\",\"version\":1,\"pid\":"
                        + Process.GetCurrentProcess().Id + ",\"port\":" + Port + "}";
                    Write(stream, 200, body);
                    return;
                }
                if (path.StartsWith("/api/version"))
                {
                    string body = "{\"ok\":true,\"product\":\"PaperLibrary\",\"version\":\""
                        + Version + "\",\"tag\":\"v" + Version + "\"}";
                    Write(stream, 200, body);
                    return;
                }
                Write(stream, 404, "{\"ok\":false}");
            }
        }
        catch (Exception) { }
    }

    static void Write(NetworkStream stream, int status, string body)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(body);
        string header = "HTTP/1.1 " + status + " OK\r\n"
            + "Content-Type: application/json; charset=utf-8\r\n"
            + "Content-Length: " + bytes.Length + "\r\nConnection: close\r\n\r\n";
        byte[] head = Encoding.ASCII.GetBytes(header);
        stream.Write(head, 0, head.Length);
        stream.Write(bytes, 0, bytes.Length);
        stream.Flush();
    }

    static void Append(string line)
    {
        try { File.AppendAllText(Path.Combine(BaseDir, "instance.log"), line + "\n"); }
        catch (Exception) { }
    }
}
'''


def free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict:
    """只比较程序文件（跳过顶层 data/，因为安装会写 state/journal/lock）。"""
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if Path(dirpath) == root:
            dirnames[:] = [name for name in dirnames if name != "data"]
        for name in filenames:
            path = Path(dirpath) / name
            result[path.relative_to(root).as_posix()] = sha256(path)
    return result


def kill_pid(pid):
    if not pid:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                       capture_output=True, timeout=20,
                       creationflags=CREATE_NO_WINDOW)
    except Exception:
        pass


@unittest.skipUnless(POWERSHELL and CSC, "需要 PowerShell 与 .NET Framework csc 才能编译 helper/fake app")
class InstallSequenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

        # helper
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

        # fake app（本地 HTTP 服务，充当 PaperLibrary.exe）
        fake_dir = cls.dir / "fake"
        fake_dir.mkdir()
        (fake_dir / "FakeApp.cs").write_text(FAKE_APP_SOURCE, encoding="utf-8")
        fake_exe = fake_dir / "FakeApp.exe"
        compile_proc = subprocess.run(
            [str(CSC), "/nologo", "/target:exe", "/out:" + str(fake_exe),
             "/r:System.dll", str(fake_dir / "FakeApp.cs")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if compile_proc.returncode != 0:
            raise AssertionError("fake app 构建失败：" + (compile_proc.stdout or "") + (compile_proc.stderr or ""))
        cls.fake_exe = fake_exe

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(dir=self.dir))
        self.program = self.work / "program"
        self.data = self.program / "data"
        self.port = free_port()
        self.log = self.work / "install.log"
        self.pids = []
        self.helpers = []
        self.make_program("1.6.0")
        self.make_staging("1.7.1")

    def tearDown(self):
        try:
            state_path = self.data / "updates" / "state.json"
            if state_path.is_file():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                self.pids.extend([state.get("launchedPid"), state.get("oldLaunchedPid")])
        except Exception:
            pass
        for pid in list(self.pids):
            kill_pid(pid)
        for proc in list(self.helpers):
            try:
                if hasattr(proc, "kill"):
                    proc.kill()
                    proc.wait(timeout=10)
                elif hasattr(proc, "close"):
                    proc.close()
            except Exception:
                pass
        shutil.rmtree(self.work, ignore_errors=True)

    # ---------- fixtures ----------

    def write_fake_config(self, root: Path, version: str, fail_health=False):
        (root / "fake-app.json").write_text(json.dumps({
            "port": self.port, "version": version, "failHealth": bool(fail_health),
        }), encoding="utf-8")

    def make_program(self, version: str):
        self.program.mkdir(parents=True)
        (self.program / "PaperLibrary.exe").write_bytes(self.fake_exe.read_bytes())
        self.write_fake_config(self.program, version)
        (self.program / "version.json").write_text(
            json.dumps({"product": "PaperLibrary", "version": version}), encoding="utf-8")
        (self.program / "paperlib").mkdir()
        (self.program / "paperlib" / "app.py").write_text("old app", encoding="utf-8")
        (self.program / "public").mkdir()
        (self.program / "public" / "index.html").write_text("<html>old</html>", encoding="utf-8")
        self.data.mkdir()
        (self.data / "library.db").write_text("user database", encoding="utf-8")
        (self.data / "prefs.json").write_text('{"theme":"dark"}', encoding="utf-8")
        updates = self.data / "updates"
        updates.mkdir()
        (updates / "apply.json").write_text(json.dumps({
            "schema": 1, "version": "1.7.1", "stagingRel": "updates/1.7.1/staging",
        }), encoding="utf-8")

    def make_staging(self, version: str, fail_health=False, reported_version=None):
        self.staging = self.data / "updates" / version / "staging"
        self.staging.mkdir(parents=True, exist_ok=True)
        (self.staging / "PaperLibrary.exe").write_bytes(self.fake_exe.read_bytes())
        self.write_fake_config(self.staging, reported_version or version, fail_health=fail_health)
        (self.staging / "version.json").write_text(
            json.dumps({"product": "PaperLibrary", "version": version}), encoding="utf-8")
        (self.staging / "paperlib").mkdir(exist_ok=True)
        (self.staging / "paperlib" / "app.py").write_text("new app", encoding="utf-8")
        (self.staging / "vendor").mkdir(exist_ok=True)
        (self.staging / "vendor" / "lib.js").write_text("new vendor", encoding="utf-8")

    # ---------- helpers ----------

    def install(self, launcher_pid=0, timeout_pid=10, timeout_port=5, timeout_lock=5,
                timeout_total=120, health_timeout=10, health_interval=200,
                health_successes=2):
        args = [
            str(self.exe), "install",
            "--program-root", str(self.program),
            "--staging", str(self.staging),
            "--port", str(self.port),
            "--launcher-pid", str(launcher_pid),
            "--timeout-pid", str(timeout_pid),
            "--timeout-port", str(timeout_port),
            "--timeout-lock", str(timeout_lock),
            "--timeout-total", str(timeout_total),
            "--health-timeout", str(health_timeout),
            "--health-interval", str(health_interval),
            "--health-successes", str(health_successes),
            "--health-request-timeout", "1000",
            "--log", str(self.log),
        ]
        # 用文件而不是管道接收输出：helper 会启动新应用，管道句柄可能被孙进程继承，
        # 用 capture_output 会让测试一直等不到 EOF。
        out_path = self.work / "install.out"
        err_path = self.work / "install.err"
        with open(out_path, "w", encoding="utf-8") as out, open(err_path, "w", encoding="utf-8") as err:
            proc = subprocess.run(args, stdout=out, stderr=err, timeout=180)
        stdout = out_path.read_text(encoding="utf-8", errors="replace")
        stderr = err_path.read_text(encoding="utf-8", errors="replace")
        lines = [line for line in stdout.splitlines() if line.strip()]
        data = json.loads(lines[-1]) if lines else {}
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr), data

    def state(self):
        path = self.data / "updates" / "state.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def journal(self):
        path = self.data / "updates" / "journal.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def wait_marker(self, path: Path, timeout=10):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if path.is_file():
                return True
            time.sleep(0.05)
        return False

    def start_locker(self, path: Path):
        marker = self.work / ("locked-" + path.name + ".txt")
        script = ("$f=[IO.File]::Open('" + str(path).replace("'", "''") + "', 'Open', 'Read', 'None'); "
                  "Set-Content -LiteralPath '" + str(marker).replace("'", "''") + "' -Value 'locked'; "
                  "Start-Sleep -Seconds 30")
        proc = subprocess.Popen([str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
                                 "-Command", script],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
        self.helpers.append(proc)
        self.assertTrue(self.wait_marker(marker, 10), "文件锁未就绪")
        return proc

    def start_sleep_process(self, seconds):
        proc = subprocess.Popen([str(POWERSHELL), "-NoProfile", "-Command",
                                 "Start-Sleep -Seconds " + str(seconds)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=CREATE_NO_WINDOW)
        self.helpers.append(proc)
        return proc

    def start_port_blocker(self):
        blocker = socket.socket()
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", self.port))
        blocker.listen(5)
        self.helpers.append(blocker)
        return blocker

    def http_json(self, path):
        with urllib.request.urlopen("http://127.0.0.1:%d%s" % (self.port, path), timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))

    # ---------- tests ----------

    def test_install_success_from_data_updates_staging(self):
        proc, data = self.install()
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertTrue(data["ok"])
        self.assertEqual(data["version"], "1.7.1")
        self.assertGreater(data["launchedPid"], 0)
        state = self.state()
        self.assertEqual(state["phase"], "success")
        self.assertEqual(state["version"], "1.7.1")
        self.assertEqual(state["fromVersion"], "1.6.0")
        self.assertTrue(state["launchedPid"] > 0)
        self.assertEqual(self.journal()["phase"], "success")
        self.assertEqual(json.loads((self.program / "version.json").read_text(encoding="utf-8"))["version"],
                         "1.7.1")
        self.assertEqual((self.program / "paperlib" / "app.py").read_text(encoding="utf-8"), "new app")
        self.assertEqual((self.data / "library.db").read_text(encoding="utf-8"), "user database")
        self.assertTrue((self.data / "updates" / "backups" / "app-1.6.0" / "PaperLibrary.exe").is_file())
        self.assertFalse((self.data / "updates" / "apply.json").exists(), "成功后必须清理 apply.json")
        self.assertFalse((self.data / "updates" / "install.lock").exists(), "成功后必须释放安装锁")
        self.assertTrue((self.data / "updates" / "backups" / "app-1.6.0" / "backup.json").is_file())
        self.assertEqual(self.http_json("/api/version")["version"], "1.7.1")
        self.pids.append(state["launchedPid"])

    def test_install_waits_for_launcher_pid(self):
        sleeper = self.start_sleep_process(2)
        started = time.time()
        proc, data = self.install(launcher_pid=sleeper.pid, timeout_pid=10)
        elapsed = time.time() - started
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertGreaterEqual(elapsed, 1.0, "install 没有等待启动器 PID 退出")
        state = self.state()
        self.assertEqual(state["phase"], "success")
        self.pids.append(state["launchedPid"])

    def test_launcher_timeout_aborts_untouched(self):
        before = tree_hashes(self.program)
        sleeper = self.start_sleep_process(20)
        proc, data = self.install(launcher_pid=sleeper.pid, timeout_pid=1)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "launcher-timeout")
        self.assertEqual(tree_hashes(self.program), before, "等待超时不得改动文件")
        self.assertFalse((self.data / "updates" / "backups" / "app-1.6.0").exists())
        self.assertFalse((self.data / "updates" / "install.lock").exists())
        self.assertEqual(self.journal()["phase"], "failed")
        self.assertEqual(self.state()["phase"], "failed")

    def test_port_timeout_aborts_untouched(self):
        before = tree_hashes(self.program)
        self.start_port_blocker()
        proc, data = self.install(timeout_port=1)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "port-timeout")
        self.assertEqual(tree_hashes(self.program), before, "端口占用超时不得改动文件")
        self.assertFalse((self.data / "updates" / "backups" / "app-1.6.0").exists())
        self.assertEqual(self.state()["phase"], "failed")

    def test_file_lock_timeout_aborts_untouched(self):
        before = tree_hashes(self.program)
        locker = self.start_locker(self.program / "PaperLibrary.exe")
        proc, data = self.install(timeout_lock=1)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "file-lock-timeout")
        locker.kill()
        locker.wait(timeout=10)
        self.assertEqual(tree_hashes(self.program), before, "文件锁超时不得改动文件")
        self.assertFalse((self.data / "updates" / "backups" / "app-1.6.0").exists())
        self.assertEqual(self.state()["phase"], "failed")

    def test_data_backup_failure_aborts_before_program(self):
        before = tree_hashes(self.program)
        (self.data / "library.db").unlink()
        proc, data = self.install()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "data-backup-failed")
        self.assertEqual(tree_hashes(self.program), before, "数据备份失败不得改动程序文件")
        self.assertTrue((self.data / "updates" / "apply.json").exists(), "失败保留 apply.json 供重试")
        self.assertEqual(self.state()["phase"], "failed")

    def test_health_failure_rolls_back_and_relaunches_old(self):
        self.make_staging("1.7.1", fail_health=True)
        proc, data = self.install(health_timeout=3, health_interval=200)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "health-failed")
        self.assertTrue(data["restored"], data)
        state = self.state()
        self.assertEqual(state["phase"], "failed")
        self.assertTrue(state["restored"])
        self.assertGreater(state["oldLaunchedPid"], 0, "回滚后应重新启动旧版本")
        self.assertEqual(json.loads((self.program / "version.json").read_text(encoding="utf-8"))["version"],
                         "1.6.0")
        self.assertEqual((self.data / "library.db").read_text(encoding="utf-8"), "user database")
        self.assertTrue((self.data / "updates" / "apply.json").exists(), "失败保留 apply.json 供重试")
        self.assertTrue((self.data / "updates" / "backups" / "app-1.6.0" / "backup.json").is_file(),
                        "回滚后备份目录保留 backup.json 元数据")
        self.pids.append(state["oldLaunchedPid"])
        deadline = time.time() + 10
        log = self.program / "instance.log"
        while time.time() < deadline:
            if log.is_file() and "version=1.6.0" in log.read_text(encoding="utf-8", errors="replace"):
                break
            time.sleep(0.1)
        self.assertIn("version=1.6.0", log.read_text(encoding="utf-8", errors="replace"))

    def test_health_version_mismatch_rolls_back(self):
        self.make_staging("1.7.1", reported_version="1.7.0")
        proc, data = self.install(health_timeout=3, health_interval=200)
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "health-failed")
        self.assertTrue(data["restored"])
        self.assertEqual(json.loads((self.program / "version.json").read_text(encoding="utf-8"))["version"],
                         "1.6.0")
        self.assertGreater(self.state()["oldLaunchedPid"], 0)
        self.pids.append(self.state()["oldLaunchedPid"])

    def test_replace_failure_rolls_back(self):
        self.start_locker(self.staging / "vendor" / "lib.js")
        proc, data = self.install()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "replace-failed")
        self.assertTrue(data["restored"], data)
        self.assertEqual(json.loads((self.program / "version.json").read_text(encoding="utf-8"))["version"],
                         "1.6.0")
        self.assertGreater(self.state()["oldLaunchedPid"], 0)
        self.pids.append(self.state()["oldLaunchedPid"])

    def test_install_lock_blocks(self):
        before = tree_hashes(self.program)
        (self.data / "updates" / "install.lock").write_text("{}", encoding="utf-8")
        proc, data = self.install()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "install-locked")
        self.assertEqual(tree_hashes(self.program), before)
        self.assertTrue((self.data / "updates" / "install.lock").exists(), "不能删除别人的锁")

    def test_staging_directly_in_program_rejected(self):
        direct = self.program / "staging"
        direct.mkdir()
        shutil.copy2(self.staging / "PaperLibrary.exe", direct / "PaperLibrary.exe")
        shutil.copy2(self.staging / "version.json", direct / "version.json")
        self.staging = direct
        before = tree_hashes(self.program)
        proc, data = self.install()
        self.assertEqual(proc.returncode, 3, proc.stdout + proc.stderr)
        self.assertEqual(data["code"], "staging-inside-program")
        self.assertEqual(tree_hashes(self.program), before)


if __name__ == "__main__":
    unittest.main()
