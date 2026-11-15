param(
  [string]$OutDir = "dist",
  [string]$WebView2Dir = ""
)
$ErrorActionPreference = "Stop"

# IMPORTANT: this file must stay UTF-8 WITH BOM. It is executed by Windows
# PowerShell 5.1 (release.py uses powershell.exe -File); a BOM-less file is read
# as ANSI and the Chinese UI strings compiled into the launcher would be garbled.
#
# The launcher is a WinForms host: it starts the Python service in the background
# and shows the existing web UI (public/) inside a WebView2 window. No external
# browser is opened on the normal path.
#
# csc.exe from .NET Framework 4.x only supports C# 5: no string interpolation,
# no "?.", no "nameof" in the embedded source below.

$root = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $root "public\paperlibrary.ico"
if (-not (Test-Path -LiteralPath $icon)) {
  throw "Missing launcher icon: $icon (the package must include public/paperlibrary.ico)"
}

if (-not $WebView2Dir) { $WebView2Dir = Join-Path $root "tools\webview2" }
$wvCore = Join-Path $WebView2Dir "Microsoft.Web.WebView2.Core.dll"
$wvWinForms = Join-Path $WebView2Dir "Microsoft.Web.WebView2.WinForms.dll"
$wvLoader = Join-Path $WebView2Dir "WebView2Loader.dll"
foreach ($file in @($wvCore, $wvWinForms, $wvLoader)) {
  if (-not (Test-Path -LiteralPath $file)) {
    throw "Missing WebView2 SDK file: $file (see tools/webview2/README.txt)"
  }
}

$manifest = Join-Path $PSScriptRoot "launcher.manifest"
if (-not (Test-Path -LiteralPath $manifest)) {
  throw "Missing launcher manifest: $manifest"
}
$appConfig = Join-Path $PSScriptRoot "launcher.exe.config"

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) {
  $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $csc)) {
  throw "csc.exe (.NET Framework 4.x) not found; cannot build the launcher"
}

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$src = @'
using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

class Launcher
{
    static string BaseDir = AppDomain.CurrentDomain.BaseDirectory;
    static string LogFile = Path.Combine(BaseDir, "data", "logs", "launcher.log");
    const int Port = 4321;
    const string Url = "http://127.0.0.1:4321/";
    const string Title = "PaperLibrary v1.6 Nexus";
    const string MutexName = "PaperLibraryNexus.WebViewHost";

    static readonly object LogLock = new object();
    static readonly object ServerLock = new object();

    static Form form;
    static WebView2 webView;
    static Label loading;
    static Process server;
    static bool startedServer;
    static volatile bool closing;
    static bool stopping;
    static string failure = "";
    static string debugPort = "";

    [STAThread]
    static int Main(string[] args)
    {
        bool createdNew;
        Mutex mutex = new Mutex(true, MutexName, out createdNew);
        if (!createdNew)
        {
            Log("second instance detected; focusing existing window");
            FocusExisting();
            return 0;
        }
        try
        {
            EnsureConfigHidden();
            debugPort = Environment.GetEnvironmentVariable("PAPERLIB_WEBVIEW_DEBUG_PORT");
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            RunHost();
            return 0;
        }
        catch (Exception ex)
        {
            Log("fatal: " + ex.ToString());
            MessageBox.Show(
                "启动失败：\r\n" + ex.Message + "\r\n\r\n日志：" + LogFile,
                Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        finally
        {
            StopServer();
            try { mutex.ReleaseMutex(); } catch (Exception) { }
            try { mutex.Close(); } catch (Exception) { }
        }
    }

    // The package's "<exe name>.config" carries supportedRuntime + the WinForms DPI
    // settings and cannot be renamed (the CLR only loads that exact file name). It is
    // shipped hidden so users do not mistake it for the launcher, but zip extraction
    // drops file attributes: restore the flag on every start (best effort, a read-only
    // package must still start normally).
    static void EnsureConfigHidden()
    {
        try
        {
            string config = Application.ExecutablePath + ".config";
            if (!File.Exists(config)) return;
            FileAttributes attrs = File.GetAttributes(config);
            if ((attrs & FileAttributes.Hidden) == 0)
            {
                File.SetAttributes(config, attrs | FileAttributes.Hidden);
                Log("launcher config marked hidden");
            }
        }
        catch (Exception ex)
        {
            Log("hide launcher config failed: " + ex.Message);
        }
    }

    static void RunHost()
    {
        form = new Form();
        form.Text = Title;
        form.ClientSize = new Size(1280, 820);
        form.StartPosition = FormStartPosition.CenterScreen;
        form.MinimumSize = new Size(900, 600);
        form.BackColor = Color.FromArgb(243, 241, 234);
        form.AutoScaleMode = AutoScaleMode.Dpi;
        try { form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
        catch (Exception) { }

        webView = new WebView2();
        webView.Dock = DockStyle.Fill;
        try { webView.DefaultBackgroundColor = Color.FromArgb(243, 241, 234); }
        catch (Exception) { }

        loading = new Label();
        loading.Dock = DockStyle.Fill;
        loading.TextAlign = ContentAlignment.MiddleCenter;
        loading.Font = new Font("Microsoft YaHei UI", 12f);
        loading.BackColor = Color.FromArgb(243, 241, 234);
        loading.Text = "正在启动文献库服务…";

        form.Controls.Add(webView);
        form.Controls.Add(loading);
        loading.BringToFront();

        form.Shown += OnShown;
        form.FormClosing += OnClosing;
        Application.Run(form);
    }

    static async void OnShown(object sender, EventArgs e)
    {
        try
        {
            SetLoading("正在启动文献库服务…");
            bool ok = false;
            await Task.Run(delegate { ok = EnsureServer(); });
            if (closing) return;
            if (!ok)
            {
                MessageBox.Show(failure, Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
                form.Close();
                return;
            }

            SetLoading("正在打开文献库…");
            CoreWebView2EnvironmentOptions options = null;
            if (!string.IsNullOrEmpty(debugPort))
            {
                options = new CoreWebView2EnvironmentOptions(
                    "--remote-debugging-port=" + debugPort + " --remote-allow-origins=*");
            }
            string userData = Path.Combine(BaseDir, "data", "webview2");
            Directory.CreateDirectory(userData);
            CoreWebView2Environment env = await CoreWebView2Environment.CreateAsync(null, userData, options);
            if (closing) return;
            await webView.EnsureCoreWebView2Async(env);
            if (closing) return;

            CoreWebView2 core = webView.CoreWebView2;
            core.Settings.AreDevToolsEnabled = !string.IsNullOrEmpty(debugPort);
            core.Settings.AreDefaultContextMenusEnabled = true;
            core.Settings.IsStatusBarEnabled = false;
            core.NewWindowRequested += OnNewWindow;
            core.NavigationCompleted += OnNavCompleted;
            core.ProcessFailed += OnProcessFailed;
            Log("webview ready; loading " + Url);
            core.Navigate(Url);
        }
        catch (Exception ex)
        {
            Log("window init failed: " + ex.ToString());
            if (closing) return;
            string message = "内置窗口创建失败，本机可能缺少 Microsoft Edge WebView2 运行时。\r\n\r\n"
                + ex.Message + "\r\n\r\n是否改用默认浏览器打开？";
            DialogResult answer = MessageBox.Show(message, Title, MessageBoxButtons.YesNo, MessageBoxIcon.Warning);
            if (answer == DialogResult.Yes) OpenBrowser();
            form.Close();
        }
    }

    static bool EnsureServer()
    {
        if (closing) return false;
        try
        {
            if (PortOpen())
            {
                if (WaitForOurs(6))
                {
                    startedServer = false;
                    Log("attached to an already running service");
                    return true;
                }
                failure = "端口 " + Port + " 已被其它程序占用（不是 PaperLibrary）。\r\n"
                    + "请关闭占用该端口的程序后再试，或释放端口后重新双击启动。";
                return false;
            }
            if (!StartServer()) return false;
            if (closing)
            {
                StopServer();
                return false;
            }
            return true;
        }
        catch (Exception ex)
        {
            failure = "启动文献库服务失败：\r\n" + ex.Message + "\r\n\r\n日志：" + LogFile;
            StopServer();
            return false;
        }
    }

    static bool StartServer()
    {
        string python = Path.Combine(BaseDir, "runtime", "portable", "python.exe");
        if (!File.Exists(python)) python = Path.Combine(BaseDir, "runtime", "portable", "pythonw.exe");
        if (!File.Exists(python)) python = "python.exe";

        ProcessStartInfo info = new ProcessStartInfo(python, "-m paperlib --port " + Port + " --no-open");
        info.WorkingDirectory = BaseDir;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;

        try
        {
            server = Process.Start(info);
        }
        catch (Exception ex)
        {
            failure = "找不到可用的 Python 运行时：\r\n" + python + "\r\n\r\n" + ex.Message
                + "\r\n\r\n请确认发布包里的 runtime\\portable 目录完整。";
            return false;
        }
        if (server == null)
        {
            failure = "无法启动 Python 服务：" + python;
            return false;
        }
        lock (ServerLock) { startedServer = true; }
        if (closing)
        {
            try { server.Kill(); } catch (Exception) { }
            return false;
        }
        server.OutputDataReceived += delegate(object sender, DataReceivedEventArgs ev) { };
        server.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs ev)
        {
            if (ev.Data != null) Log("server: " + ev.Data);
        };
        try
        {
            server.BeginOutputReadLine();
            server.BeginErrorReadLine();
        }
        catch (Exception) { }

        for (int i = 0; i < 100; i++)
        {
            if (closing)
            {
                try { server.Kill(); } catch (Exception) { }
                return false;
            }
            bool exited = false;
            try { exited = server.HasExited; } catch (Exception) { }
            if (exited) break;
            if (PortOpen() && IsOurs())
            {
                Log("service ready on port " + Port);
                return true;
            }
            Thread.Sleep(300);
        }

        int code = -1;
        try { if (server.HasExited) code = server.ExitCode; } catch (Exception) { }
        failure = "文献库服务没有启动成功（端口 " + Port + "）。\r\n退出码：" + code
            + "\r\n\r\n日志：" + LogFile;
        StopServer();
        return false;
    }

    static void StopServer()
    {
        Process proc = null;
        lock (ServerLock)
        {
            if (stopping) return;
            if (!startedServer || server == null) return;
            stopping = true;
            proc = server;
        }
        Log("stopping service");
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(Url + "api/shutdown");
            request.Method = "POST";
            request.Timeout = 3000;
            request.ReadWriteTimeout = 3000;
            request.Proxy = null;
            request.ContentLength = 0;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            {
                using (Stream stream = response.GetResponseStream())
                {
                    if (stream != null) { using (StreamReader reader = new StreamReader(stream)) { reader.ReadToEnd(); } }
                }
            }
        }
        catch (Exception ex)
        {
            Log("shutdown request failed: " + ex.Message);
        }

        for (int i = 0; i < 50; i++)
        {
            try { if (proc.HasExited) break; } catch (Exception) { break; }
            Thread.Sleep(100);
        }
        try
        {
            if (!proc.HasExited)
            {
                proc.Kill();
                Log("service process killed");
            }
        }
        catch (Exception ex) { Log("kill failed: " + ex.Message); }
        try { proc.WaitForExit(5000); } catch (Exception) { }
        for (int i = 0; i < 30; i++)
        {
            if (!PortOpen()) break;
            Thread.Sleep(100);
        }
        lock (ServerLock)
        {
            server = null;
            startedServer = false;
        }
        Log("service stopped");
    }

    static bool WaitForOurs(int attempts)
    {
        for (int i = 0; i < attempts; i++)
        {
            if (IsOurs()) return true;
            Thread.Sleep(250);
        }
        return false;
    }

    static bool IsOurs()
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(Url + "api/health");
            request.Timeout = 800;
            request.ReadWriteTimeout = 1200;
            request.Proxy = null;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            {
                using (Stream stream = response.GetResponseStream())
                {
                    if (stream == null) return false;
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                    {
                        string body = reader.ReadToEnd();
                        return body.IndexOf("literature-vault", StringComparison.Ordinal) >= 0;
                    }
                }
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    static bool PortOpen()
    {
        try
        {
            using (TcpClient client = new TcpClient())
            {
                Task task = client.ConnectAsync("127.0.0.1", Port);
                if (!task.Wait(400)) return false;
                return client.Connected;
            }
        }
        catch (Exception)
        {
            return false;
        }
    }

    static void OnNavCompleted(object sender, CoreWebView2NavigationCompletedEventArgs e)
    {
        if (e.IsSuccess)
        {
            Log("page loaded");
            if (loading != null) loading.Visible = false;
            return;
        }
        Log("navigation failed: " + e.WebErrorStatus);
        SetLoading("页面加载失败（" + e.WebErrorStatus + "）。请关闭窗口后重新打开。");
    }

    static void OnNewWindow(object sender, CoreWebView2NewWindowRequestedEventArgs e)
    {
        e.Handled = true;
        try
        {
            Process.Start(new ProcessStartInfo(e.Uri) { UseShellExecute = true });
            Log("external link opened in default browser: " + e.Uri);
        }
        catch (Exception ex)
        {
            Log("external link failed: " + ex.Message);
        }
    }

    static void OnProcessFailed(object sender, CoreWebView2ProcessFailedEventArgs e)
    {
        Log("webview process failed: " + e.ProcessFailedKind);
    }

    static void OnClosing(object sender, FormClosingEventArgs e)
    {
        if (closing) return;
        closing = true;
        Log("window closing");
        if (loading != null)
        {
            loading.Text = "正在保存并关闭…";
            loading.Visible = true;
            loading.BringToFront();
            try { loading.Refresh(); } catch (Exception) { }
            try { Application.DoEvents(); } catch (Exception) { }
        }
        try
        {
            if (webView != null)
            {
                webView.Dispose();
                webView = null;
            }
        }
        catch (Exception) { }
        StopServer();
    }

    static void SetLoading(string text)
    {
        if (loading == null) return;
        loading.Text = text;
        loading.Visible = true;
        loading.BringToFront();
        try { loading.Refresh(); } catch (Exception) { }
    }

    static void OpenBrowser()
    {
        try
        {
            Process.Start(new ProcessStartInfo(Url) { UseShellExecute = true });
            Log("fallback: default browser opened");
        }
        catch (Exception ex)
        {
            Log("fallback browser failed: " + ex.Message);
        }
    }

    static void FocusExisting()
    {
        try
        {
            for (int i = 0; i < 20; i++)
            {
                IntPtr handle = FindWindowNative(null, Title);
                if (handle != IntPtr.Zero)
                {
                    ShowWindowNative(handle, 9);
                    SetForegroundWindowNative(handle);
                    Log("existing window focused");
                    return;
                }
                Thread.Sleep(150);
            }
            Log("existing window not found");
        }
        catch (Exception ex)
        {
            Log("focus existing failed: " + ex.Message);
        }
    }

    static void Log(string message)
    {
        try
        {
            lock (LogLock)
            {
                Directory.CreateDirectory(Path.GetDirectoryName(LogFile));
                File.AppendAllText(LogFile, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + message + "\r\n");
            }
        }
        catch (Exception) { }
    }

    [DllImport("user32.dll", CharSet = CharSet.Unicode, EntryPoint = "FindWindowW")]
    static extern IntPtr FindWindowNative(string className, string windowName);

    [DllImport("user32.dll", EntryPoint = "ShowWindow")]
    static extern bool ShowWindowNative(IntPtr handle, int command);

    [DllImport("user32.dll", EntryPoint = "SetForegroundWindow")]
    static extern bool SetForegroundWindowNative(IntPtr handle);
}
'@

$startExe = Join-Path $OutDir "PaperLibrary.exe"
$csPath = Join-Path ([System.IO.Path]::GetTempPath()) ("paperlibrary-launcher-" + [guid]::NewGuid().ToString("N") + ".cs")
try {
  [System.IO.File]::WriteAllText($csPath, $src, (New-Object System.Text.UTF8Encoding($true)))
  $cscArgs = @(
    "/nologo", "/target:winexe", "/optimize+",
    "/win32icon:$icon", "/win32manifest:$manifest", "/out:$startExe",
    "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
    "/r:$wvCore", "/r:$wvWinForms", $csPath
  )
  & $csc $cscArgs
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $startExe)) {
    throw "launcher compile failed (csc exit code $LASTEXITCODE)"
  }
} finally {
  Remove-Item $csPath -Force -ErrorAction SilentlyContinue
}

Copy-Item -LiteralPath $wvCore -Destination $OutDir -Force
Copy-Item -LiteralPath $wvWinForms -Destination $OutDir -Force
Copy-Item -LiteralPath $wvLoader -Destination $OutDir -Force

# The .NET CLR only ever reads the application config as "<exe file name>.config"
# (AppDomainSetup.ConfigurationFile = exe path + ".config"), so this file cannot be
# renamed without silently dropping supportedRuntime + the WinForms DPI settings.
# With Explorer's "hide known extensions" on, "PaperLibrary.exe.config" is shown
# as "PaperLibrary.exe" right under the real launcher, and users double-click the
# wrong file. Keep the name, mark the file Hidden: the settings still load and the
# package root shows exactly one visible entry.
$startConfig = Join-Path $OutDir "PaperLibrary.exe.config"
if (-not (Test-Path -LiteralPath $appConfig)) {
  throw "Missing launcher config: $appConfig"
}
Copy-Item -LiteralPath $appConfig -Destination $startConfig -Force
$configItem = Get-Item -LiteralPath $startConfig -Force
$configItem.Attributes = $configItem.Attributes -bor [System.IO.FileAttributes]::Hidden
if (($configItem.Attributes -band [System.IO.FileAttributes]::Hidden) -eq 0) {
  throw "could not hide the launcher config: $startConfig"
}
Write-Output ("launcher built: " + $startExe)
Write-Output ("launcher config hidden: " + $startConfig)
Write-Output ("webview2 files copied: " + $OutDir)
