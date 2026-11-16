"""P2-19 GUI 验收：真实启动器 + WebView2 CDP 截图/桌面截图。

用法：
  python tools/gui_acceptance.py --package <dist 包> --out docs/update-p2-20261116/p2-19-shots

覆盖：
1. P2-09R 真实点击（确认框/失败后窗口保持）截图；
2. P2-11R helper 启动失败结果截图；
3. P2-18 disk-low / Program Files 结果截图；
4. P2-10R 启动时有 apply 的原生提示截图；
5. P2-16 启动器 recover 后原生提示截图。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
POWERSHELL = shutil.which("powershell.exe") or shutil.which("pwsh")
NODE = shutil.which("node")

FAKE_HELPER = r'''
using System;
using System.IO;
using System.Threading;
class FakeHelper {
  static int Main(string[] a) {
    string root = AppDomain.CurrentDomain.BaseDirectory;
    File.WriteAllLines(Path.Combine(root, "args.txt"), a);
    try {
      string program = "";
      for (int i = 0; i + 1 < a.Length; i++) if (a[i] == "--program-root") program = a[i + 1];
      if (a.Length > 0 && a[0] == "recover" && program.Length > 0) {
        string dir = Path.Combine(program, "data", "updates");
        Directory.CreateDirectory(dir);
        File.WriteAllText(Path.Combine(dir, "state.json"),
          "{\"schema\":1,\"product\":\"PaperLibrary\",\"phase\":\"failed\",\"version\":\"1.7.1\"," +
          "\"fromVersion\":\"1.6.0\",\"restored\":true,\"recovered\":true,\"code\":\"recovered\"," +
          "\"error\":\"recovered by acceptance helper\",\"log\":\"recover.log\"}");
        return 0;
      }
    } catch (Exception) { }
    if (Environment.GetEnvironmentVariable("FAKE_HELPER_FAIL") == "1") return 1;
    Thread.Sleep(2000);
    return 0;
  }
}
'''


def free_port(port):
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def wait_url(url, timeout=20):
    import urllib.request
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.2)
    return False


def screenshot_desktop(path: Path, pid=None, title="PaperLibrary v1.7.1 Nexus"):
    """只截取启动器的 MessageBox（#32770）或主窗口，避免把桌面其它内容写入证据。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    activate = ""
    if pid:
        activate = (
            "$p=Get-Process -Id " + str(pid) + " -ErrorAction SilentlyContinue; "
            "if($p){[W]::ShowWindow($p.MainWindowHandle,9)|Out-Null; [W]::SetForegroundWindow($p.MainWindowHandle)|Out-Null; Start-Sleep -Milliseconds 500}; "
        )
    script = (
        "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
        "Add-Type @'\nusing System;using System.Runtime.InteropServices;"
        "public class W{ "
        "[StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left,Top,Right,Bottom; } "
        "[DllImport(\"user32.dll\")] public static extern bool SetForegroundWindow(IntPtr h);"
        "[DllImport(\"user32.dll\")] public static extern bool ShowWindow(IntPtr h,int c);"
        "[DllImport(\"user32.dll\")] public static extern bool GetWindowRect(IntPtr h,out RECT r);"
        "[DllImport(\"user32.dll\",CharSet=CharSet.Unicode)] public static extern IntPtr FindWindow(string c,string t);"
        "public delegate bool EnumProc(IntPtr h,IntPtr l);"
        "[DllImport(\"user32.dll\")] public static extern bool EnumWindows(EnumProc cb,IntPtr l);"
        "[DllImport(\"user32.dll\")] public static extern uint GetWindowThreadProcessId(IntPtr h,out uint p);"
        "[DllImport(\"user32.dll\",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h,System.Text.StringBuilder s,int n);"
        "public static IntPtr FindDialog(int pid){IntPtr found=IntPtr.Zero;EnumWindows(delegate(IntPtr h,IntPtr l){uint p;GetWindowThreadProcessId(h,out p);if(p==(uint)pid){var sb=new System.Text.StringBuilder(64);GetClassName(h,sb,64);if(sb.ToString()==\"#32770\"){found=h;return false;}}return true;},IntPtr.Zero);return found;}}\n'@; "
        + activate +
        "$h=[IntPtr]::Zero; if(`$p){$h=[W]::FindDialog(`$p.Id)}; "
        "if($h -eq [IntPtr]::Zero){$h=[W]::FindWindow('#32770','" + title.replace("'", "''") + "')}; "
        "if($h -eq [IntPtr]::Zero -and $p){$h=$p.MainWindowHandle}; "
        "if($h -eq [IntPtr]::Zero){throw 'no window'}; "
        "$r=New-Object W+RECT; [W]::GetWindowRect($h,[ref]$r)|Out-Null; "
        "$w=$r.Right-$r.Left; $hgt=$r.Bottom-$r.Top; "
        "if($w -le 0 -or $hgt -le 0){throw 'bad rect'}; "
        "$b=New-Object System.Drawing.Bitmap($w,$hgt); "
        "$g=[System.Drawing.Graphics]::FromImage($b); "
        "$g.CopyFromScreen($r.Left,$r.Top,0,0,$b.Size); $b.Save('" + str(path).replace("'", "''") + "')"
    )
    subprocess.run([str(POWERSHELL), "-NoProfile", "-Command", script], capture_output=True, timeout=30)


class Acceptance:
    def __init__(self, package: Path, out: Path):
        self.tmp = Path(tempfile.mkdtemp(prefix="p219-gui-"))
        self.program = self.tmp / "program"
        self.home = self.tmp / "updater-home"
        self.out = out
        self.package = package
        self.port = 4321
        self.debug = 9231
        self.proc = None
        self.helper = None

    def setup(self):
        shutil.copytree(self.package, self.program)
        shutil.copytree(ROOT / "public", self.program / "public", dirs_exist_ok=True)
        build = subprocess.run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                                "-File", str(ROOT / "tools" / "make_launcher.ps1"),
                                "-OutDir", str(self.program)],
                               cwd=str(ROOT), capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=180)
        if build.returncode != 0:
            raise RuntimeError("launcher build failed: " + (build.stdout or "") + (build.stderr or ""))
        self.home.mkdir(parents=True)
        hd = self.home / "1.7.1"; hd.mkdir(parents=True)
        source = self.tmp / "FakeHelper.cs"
        source.write_text(FAKE_HELPER, encoding="utf-8-sig")
        self.helper = hd / "PaperLibraryUpdater.exe"
        build = subprocess.run([str(CSC), "/nologo", "/target:exe", "/out:" + str(self.helper), str(source)],
                               capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        if build.returncode != 0:
            raise RuntimeError("fake helper build failed: " + (build.stdout or "") + (build.stderr or ""))
        sha = hashlib.sha256(self.helper.read_bytes()).hexdigest()
        (self.home / "current.json").write_text(json.dumps({
            "schema": 1, "product": "PaperLibrary", "version": "1.7.1",
            "exe": "1.7.1/PaperLibraryUpdater.exe", "sha256": sha,
        }), encoding="utf-8")
        self.out.mkdir(parents=True, exist_ok=True)

    def reset_state(self):
        updates = self.program / "data" / "updates"
        if updates.exists():
            for name in ("apply.json", "journal.json", "state.json", "install.lock"):
                try: (updates / name).unlink()
                except OSError: pass
        for child in updates.glob("1.7.2"):
            shutil.rmtree(child, ignore_errors=True)
        staging = updates / "1.7.2" / "staging"
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "PaperLibrary.exe").write_bytes(b"fake new launcher")
        (staging / "version.json").write_text('{"product":"PaperLibrary","version":"1.7.2"}', encoding="utf-8")
        (updates / "1.7.2" / "ready.json").write_text(
            json.dumps({"schema": 1, "version": "1.7.2"}), encoding="utf-8")

    def write_apply(self):
        updates = self.program / "data" / "updates"
        staging = updates / "1.7.2" / "staging"
        (updates / "apply.json").write_text(json.dumps({
            "schema": 1, "version": "1.7.2", "fromVersion": "1.7.1",
            "stagingPath": str(staging), "backupPath": str(updates / "backups" / "app-1.7.1"),
        }), encoding="utf-8")

    def start(self, extra_env=None, wait_health=True):
        if not free_port(self.port):
            raise RuntimeError("port 4321 busy")
        env = os.environ.copy()
        env["PAPERLIB_UPDATER_HOME"] = str(self.home)
        env["PAPERLIB_WEBVIEW_DEBUG_PORT"] = str(self.debug)
        if extra_env:
            env.update(extra_env)
        self.proc = subprocess.Popen([str(self.program / "PaperLibrary.exe")],
                                     cwd=str(self.program), env=env)
        if not wait_health:
            time.sleep(2.5)
            return
        if not wait_url("http://127.0.0.1:4321/api/health", 30):
            raise RuntimeError("service not ready")
        if not wait_url("http://127.0.0.1:%d/json/list" % self.debug, 20):
            raise RuntimeError("debug endpoint not ready")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                           capture_output=True, timeout=20)
            try: self.proc.wait(timeout=10)
            except Exception: pass
        self.proc = None
        deadline = time.time() + 10
        while time.time() < deadline and not free_port(self.port):
            time.sleep(0.2)

    def cdp(self, name, mock_apply=True):
        target = self.out / name
        target.mkdir(parents=True, exist_ok=True)
        args = [str(NODE), str(ROOT / "tools" / "update_install_cdp.mjs"),
                "--port", str(self.debug), "--out", str(target)]
        if mock_apply: args.append("--mock-apply")
        proc = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60)
        (target / "cdp.txt").write_text((proc.stdout or "") + "\n" + (proc.stderr or ""), encoding="utf-8")
        return proc.returncode, (proc.stdout or "")

    def scenario_click_to_failure(self, name, env=None, expect=""):
        self.reset_state()
        self.start(env)
        try:
            self.write_apply()
            code, stdout = self.cdp(name)
            if code != 0:
                raise RuntimeError("CDP failed: " + stdout)
            status = ""
            for line in stdout.splitlines():
                if line.startswith("status: "): status = line[8:]
            if expect and expect not in status:
                raise AssertionError("status %r does not contain %r" % (status, expect))
            return status
        finally:
            self.stop()

    def scenario_native_prompt(self, name, setup):
        self.reset_state()
        setup()
        self.start(wait_health=False)
        try:
            time.sleep(2.5)
            screenshot_desktop(self.out / name / "native-dialog.png",
                               self.proc.pid if self.proc else None)
        finally:
            self.stop()

    def run(self):
        self.setup()
        results = {}
        # 1) P2-09R click + P2-11R helper failure result
        results["helper-fail"] = self.scenario_click_to_failure(
            "01-click-helper-fail", {"FAKE_HELPER_FAIL": "1"}, "helper-start-failed")
        # 2) P2-18 disk low
        results["disk-low"] = self.scenario_click_to_failure(
            "02-disk-low", {"PAPERLIB_TEST_FREE_BYTES": "1"}, "disk-low")
        # 3) P2-18 Program Files
        results["program-files"] = self.scenario_click_to_failure(
            "03-program-files", {"PAPERLIB_TEST_PROGRAM_FILES_ROOT": "C:\\"}, "program-files")
        # 4) P2-10R startup apply prompt (native)
        def apply_prompt():
            self.write_apply()
        self.scenario_native_prompt("04-startup-apply-prompt", apply_prompt)
        # 5) P2-16 recover prompt (fake helper writes state recovered)
        def recover_prompt():
            updates = self.program / "data" / "updates"
            (updates / "journal.json").write_text(json.dumps({
                "schema": 1, "product": "PaperLibrary", "phase": "replace",
                "version": "1.7.1", "fromVersion": "1.6.0",
                "backupDir": str(updates / "backups" / "app-1.6.0"),
            }), encoding="utf-8")
        self.scenario_native_prompt("05-recover-prompt", recover_prompt)
        (self.out / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(results, ensure_ascii=False, indent=2))
        print("SHOTS", self.out)
        return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--out", default=str(ROOT / "docs" / "update-p2-20261116" / "p2-19-shots"))
    args = parser.parse_args()
    if not NODE:
        raise SystemExit("需要 node")
    Acceptance(Path(args.package), Path(args.out)).run()


if __name__ == "__main__":
    main()
