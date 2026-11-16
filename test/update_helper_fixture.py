"""P2-16/P2-17 测试共用：构建 helper 与可配置的假 PaperLibrary.exe（本地 HTTP 服务）。"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
    static int HttpStatus = 200;
    static int HealthDelayMs;
    static string Version = "";
    static string AppName = "literature-vault";
    static string Product = "PaperLibrary";
    static bool FailHealth;
    static bool BadJson;
    static bool BadVersionJson;
    static string BaseDir = "";

    static void Main(string[] args)
    {
        BaseDir = AppDomain.CurrentDomain.BaseDirectory;
        string config = File.ReadAllText(Path.Combine(BaseDir, "fake-app.json"), Encoding.UTF8);
        Port = int.Parse(Regex.Match(config, "\"port\"\\s*:\\s*(\\d+)").Groups[1].Value);
        Version = Regex.Match(config, "\"version\"\\s*:\\s*\"([^\"]*)\"").Groups[1].Value;
        Match app = Regex.Match(config, "\"app\"\\s*:\\s*\"([^\"]*)\"");
        if (app.Success) AppName = app.Groups[1].Value;
        Match product = Regex.Match(config, "\"product\"\\s*:\\s*\"([^\"]*)\"");
        if (product.Success) Product = product.Groups[1].Value;
        FailHealth = Regex.IsMatch(config, "\"failHealth\"\\s*:\\s*true");
        BadJson = Regex.IsMatch(config, "\"badJson\"\\s*:\\s*true");
        BadVersionJson = Regex.IsMatch(config, "\"badVersionJson\"\\s*:\\s*true");
        Match status = Regex.Match(config, "\"httpStatus\"\\s*:\\s*(\\d+)");
        if (status.Success) HttpStatus = int.Parse(status.Groups[1].Value);
        Match delay = Regex.Match(config, "\"healthDelayMs\"\\s*:\\s*(\\d+)");
        if (delay.Success) HealthDelayMs = int.Parse(delay.Groups[1].Value);
        Append("start pid=" + Process.GetCurrentProcess().Id + " version=" + Version
               + " port=" + Port + " app=" + AppName);
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
                    if (HttpStatus != 200)
                    {
                        Write(stream, HttpStatus, "{\"ok\":false}");
                        return;
                    }
                    if (HealthDelayMs > 0) Thread.Sleep(HealthDelayMs);
                    if (BadJson)
                    {
                        Write(stream, 200, "not-a-json");
                        return;
                    }
                    string body = "{\"ok\":" + (FailHealth ? "false" : "true")
                        + ",\"app\":\"" + AppName + "\",\"version\":1,\"pid\":"
                        + Process.GetCurrentProcess().Id + ",\"port\":" + Port + "}";
                    Write(stream, 200, body);
                    return;
                }
                if (path.StartsWith("/api/version"))
                {
                    if (BadVersionJson)
                    {
                        Write(stream, 200, "not-a-json");
                        return;
                    }
                    string body = "{\"ok\":true,\"product\":\"" + Product
                        + "\",\"version\":\"" + Version + "\",\"tag\":\"v" + Version + "\"}";
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
    import socket
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path, skip_data: bool = True) -> dict:
    result = {}
    for dirpath, dirnames, filenames in os.walk(root):
        if skip_data and Path(dirpath) == root:
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


def build_helper(temp_root: Path) -> Path:
    pkg = temp_root / "pkg"
    (pkg / "tools" / "updater").mkdir(parents=True)
    (pkg / "version.json").write_text(json.dumps({
        "product": "PaperLibrary", "version": "1.7.1", "tag": "v1.7.1"}), encoding="utf-8")
    shutil.copy2(ROOT / "tools" / "make_updater.ps1", pkg / "tools" / "make_updater.ps1")
    shutil.copy2(ROOT / "tools" / "updater" / "PaperLibraryUpdater.cs",
                 pkg / "tools" / "updater" / "PaperLibraryUpdater.cs")
    proc = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass",
         "-File", str(pkg / "tools" / "make_updater.ps1"), "-OutDir", str(pkg)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(pkg), timeout=180)
    if proc.returncode != 0:
        raise AssertionError("helper 构建失败：" + (proc.stdout or "") + (proc.stderr or ""))
    return pkg / "tools" / "updater" / "PaperLibraryUpdater.exe"


def build_fake_app(temp_root: Path) -> Path:
    fake_dir = temp_root / "fake"
    fake_dir.mkdir()
    (fake_dir / "FakeApp.cs").write_text(FAKE_APP_SOURCE, encoding="utf-8")
    exe = fake_dir / "FakeApp.exe"
    proc = subprocess.run(
        [str(CSC), "/nologo", "/target:exe", "/out:" + str(exe),
         "/r:System.dll", str(fake_dir / "FakeApp.cs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise AssertionError("fake app 构建失败：" + (proc.stdout or "") + (proc.stderr or ""))
    return exe


def write_fake_config(root: Path, port: int, version: str, **kwargs):
    payload = {"port": port, "version": version}
    payload.update(kwargs)
    (root / "fake-app.json").write_text(json.dumps(payload), encoding="utf-8")
