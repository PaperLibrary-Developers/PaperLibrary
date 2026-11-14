param(
  [string]$OutDir = "dist"
)
$ErrorActionPreference = "Stop"

# Keep this file ASCII-only: Windows PowerShell 5.1 reads BOM-less scripts as ANSI,
# so CJK comments/messages here would corrupt parsing on non-UTF8 code pages.

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

# Icon: public/paperlibrary.ico inside the package (release.py copies public/ first)
$root = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $root "public\paperlibrary.ico"
if (-not (Test-Path -LiteralPath $icon)) {
  throw "Missing launcher icon: $icon (the package must include public/paperlibrary.ico)"
}

# Compile with .NET Framework 4.x csc.exe: Add-Type cannot embed a Win32 icon into an exe.
$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) {
  $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $csc)) {
  throw "csc.exe (.NET Framework 4.x) not found; cannot build the launcher"
}

$src = @'
using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using System.Windows.Forms;

class Launcher
{
    static string BaseDir = AppDomain.CurrentDomain.BaseDirectory;
    static string LogFile = Path.Combine(BaseDir, "data", "logs", "launcher.log");
    const int Port = 4321;
    const string Url = "http://127.0.0.1:4321/";
    const string Title = "PaperLibrary v1.4 Nexus";

    static void Log(string message)
    {
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(LogFile));
            File.AppendAllText(LogFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + message + "\r\n");
        }
        catch { }
    }

    static bool PortOpen()
    {
        try
        {
            using (var client = new TcpClient())
            {
                var task = client.ConnectAsync("127.0.0.1", Port);
                if (!task.Wait(400)) return false;
                return client.Connected;
            }
        }
        catch { return false; }
    }

    static void OpenUrl()
    {
        try
        {
            Process.Start(new ProcessStartInfo(Url) { UseShellExecute = true });
            Log("browser open requested (shell): " + Url);
            return;
        }
        catch (Exception ex) { Log("shell open failed: " + ex.Message); }
        try
        {
            var info = new ProcessStartInfo("cmd.exe", "/c start \"\" \"" + Url + "\"")
            {
                UseShellExecute = false,
                CreateNoWindow = true
            };
            Process.Start(info);
            Log("browser open requested (cmd start): " + Url);
        }
        catch (Exception ex) { Log("cmd start failed: " + ex.Message); }
    }

    static void RunPython(string fileName, string arguments)
    {
        var info = new ProcessStartInfo(fileName, arguments)
        {
            WorkingDirectory = BaseDir,
            UseShellExecute = false,
            CreateNoWindow = true
        };
        Process.Start(info);
    }

    static string Pythonw()
    {
        string portable = Path.Combine(BaseDir, "runtime", "portable", "pythonw.exe");
        if (File.Exists(portable)) return portable;
        return "pythonw.exe";
    }

    [STAThread]
    static int Main(string[] args)
    {
        // Single user entry: no stop branch. Stopping is handled by the server's
        // idle-exit logic (30s after the last page closes).
        if (PortOpen()) { OpenUrl(); return 0; }
        try { RunPython(Pythonw(), "-m paperlib --port 4321 --no-open"); }
        catch (Exception ex)
        {
            Log("start failed: " + ex.Message);
            MessageBox.Show("Start failed: " + ex.Message + "\r\nSee data\\logs\\launcher.log", Title);
            return 1;
        }
        for (int i = 0; i < 80; i++)
        {
            if (PortOpen()) { OpenUrl(); return 0; }
            Thread.Sleep(300);
        }
        Log("start timeout");
        MessageBox.Show("Startup timed out (24s).\r\nSee data\\logs\\launcher.log. If port 4321 is occupied, close the other program and try again.", Title);
        return 1;
    }
}
'@

$startExe = Join-Path $OutDir "PaperLibrary.exe"
$csPath = Join-Path ([System.IO.Path]::GetTempPath()) ("paperlibrary-launcher-" + [guid]::NewGuid().ToString("N") + ".cs")
try {
  [System.IO.File]::WriteAllText($csPath, $src, (New-Object System.Text.UTF8Encoding($true)))
  & $csc /nologo /target:winexe "/win32icon:$icon" "/out:$startExe" /r:System.dll /r:System.Windows.Forms.dll $csPath
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $startExe)) {
    throw "launcher compile failed (csc exit code $LASTEXITCODE)"
  }
} finally {
  Remove-Item -LiteralPath $csPath -Force -ErrorAction SilentlyContinue
}
Write-Output ("launcher built: " + $startExe)
