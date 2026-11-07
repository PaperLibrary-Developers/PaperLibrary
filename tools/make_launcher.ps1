param(
  [string]$OutDir = "dist"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
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
        string name = Path.GetFileNameWithoutExtension(Application.ExecutablePath) ?? "";
        bool stop = name.Contains("stop") || name.Contains("Stop") || name.Contains("\u505c\u6b62");
        if (stop)
        {
            try { RunPython(Pythonw(), "-m paperlib --stop"); Log("stop requested"); }
            catch (Exception ex) { Log("stop failed: " + ex.Message); }
            return 0;
        }
        if (PortOpen()) { OpenUrl(); return 0; }
        try { RunPython(Pythonw(), "-m paperlib --port 4321 --no-open"); }
        catch (Exception ex)
        {
            Log("start failed: " + ex.Message);
            MessageBox.Show("Start failed: " + ex.Message + "\r\nSee data\\logs\\launcher.log", "PaperLibrary v1.0 Nexus");
            return 1;
        }
        for (int i = 0; i < 80; i++)
        {
            if (PortOpen()) { OpenUrl(); return 0; }
            Thread.Sleep(300);
        }
        Log("start timeout");
        MessageBox.Show("Startup timed out (24s).\r\nSee data\\logs\\launcher.log or run the debug cmd.", "PaperLibrary v1.0 Nexus");
        return 1;
    }
}
'@

$startExe = Join-Path $OutDir ([string]([char]0x542F) + [string]([char]0x52A8) + ".exe")
$stopExe = Join-Path $OutDir ([string]([char]0x505C) + [string]([char]0x6B62) + ".exe")
Add-Type -TypeDefinition $src -ReferencedAssemblies @("System.dll", "System.Windows.Forms.dll") -OutputAssembly $startExe -OutputType WindowsApplication
Copy-Item $startExe $stopExe -Force
Write-Output ("launcher built: " + $startExe + " / " + $stopExe)
