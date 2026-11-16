using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
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
    // Window title = product + release tag from version.json (single source of truth).
    // ReadVersion() runs before the mutex/window so a missing file never breaks startup.
    static string Title = "PaperLibrary Nexus";
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
    static string UpdaterHome = "";
    static Mutex appMutex;
    static bool mutexHeld;
    static bool installStarted;
    static Process installHelper;
    static System.Windows.Forms.Timer closeTimer;

    [STAThread]
    static int Main(string[] args)
    {
        ReadVersion();
        UpdaterHome = LauncherCore.DefaultUpdaterHome();
        bool createdNew;
        appMutex = new Mutex(true, MutexName, out createdNew);
        mutexHeld = createdNew;
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
            if (mutexHeld)
            {
                try { appMutex.ReleaseMutex(); } catch (Exception) { }
                mutexHeld = false;
            }
            try { appMutex.Close(); } catch (Exception) { }
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

    // Read the release tag from the package root version.json so the window title matches
    // the sidebar (/api/version) without hard-coding the version in a second place.
    static void ReadVersion()
    {
        try
        {
            string path = Path.Combine(BaseDir, "version.json");
            if (!File.Exists(path))
            {
                Log("version.json missing; window title without version");
                return;
            }
            string text = File.ReadAllText(path, Encoding.UTF8);
            Match match = Regex.Match(text, "\"tag\"\\s*:\\s*\"(v[0-9]+\\.[0-9]+)\"");
            if (!match.Success)
            {
                Log("version.json has no valid tag; window title without version");
                return;
            }
            Title = "PaperLibrary " + match.Groups[1].Value + " Nexus";
            Log("window title from version.json: " + Title);
        }
        catch (Exception ex)
        {
            Log("read version.json failed: " + ex.Message);
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
            if (!HandleStartupDecision())
            {
                if (!closing) form.Close();
                return;
            }
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
            core.WebMessageReceived += OnWebMessage;
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

    static bool HandleStartupDecision()
    {
        StartupDecision decision = LauncherCore.DecideStartup(BaseDir, UpdaterHome);
        Log("startup decision: action=" + decision.Action + " code=" + decision.Code
            + (decision.Path.Length > 0 ? " path=" + decision.Path : ""));
        if (decision.Action == "clean-stale-lock")
        {
            try
            {
                if (File.Exists(decision.Path)) File.Delete(decision.Path);
                Log("stale install lock removed: " + decision.Path);
            }
            catch (Exception ex)
            {
                Log("remove stale install lock failed: " + ex.Message);
            }
            return true;
        }
        if (decision.Action == "recover")
        {
            return RunRecover(decision);
        }
        if (decision.Action == "install-in-progress")
        {
            MessageBox.Show(decision.Message, Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return false;
        }
        if (decision.Action == "prompt-apply")
        {
            Log("startup apply prompt: " + decision.Message);
            DialogResult answer = MessageBox.Show(decision.Message, Title,
                MessageBoxButtons.YesNo, MessageBoxIcon.Information);
            if (answer != DialogResult.Yes) return true;
            PrecheckResult pre = LauncherCore.Precheck(BaseDir, UpdaterHome);
            Log("startup install precheck: " + pre.Code + " ok=" + pre.Ok);
            if (!pre.Ok)
            {
                MessageBox.Show("无法立即安装：\r\n" + pre.Message, Title,
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return true;
            }
            InstallRequest request = new InstallRequest();
            request.RequestId = "startup-" + DateTime.Now.Ticks.ToString();
            request.Version = pre.Version;
            if (StartInstall(request, pre)) return false;
            return true;
        }
        if (decision.Action == "notice-success" || decision.Action == "notice-failed")
        {
            Log("startup state notice: " + decision.Action + " " + decision.Code);
            MessageBox.Show(decision.Message, Title, MessageBoxButtons.OK,
                decision.Action == "notice-failed" ? MessageBoxIcon.Warning : MessageBoxIcon.Information);
            return true;
        }
        return true;
    }

    static bool RunRecover(StartupDecision decision)
    {
        string code;
        string message;
        string helper = LauncherCore.FindHelper(UpdaterHome, out code, out message);
        if (helper.Length == 0)
        {
            Log("recover helper unavailable: " + code + " " + message);
            MessageBox.Show("检测到未完成的更新，但更新助手不可用：\r\n" + message,
                Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }
        string recoverLog = Path.Combine(BaseDir, "data", "logs", "recover.log");
        try { Directory.CreateDirectory(Path.GetDirectoryName(recoverLog)); } catch (Exception) { }
        ReleaseMutexForRecovery();
        Log("running recover: " + helper);
        int exitCode = -1;
        try
        {
            ProcessStartInfo info = new ProcessStartInfo(helper,
                "recover --program-root " + LauncherCore.QuoteArgument(BaseDir)
                + " --log " + LauncherCore.QuoteArgument(recoverLog));
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            Process process = Process.Start(info);
            if (process == null)
            {
                MessageBox.Show("无法启动更新助手进行恢复。\r\n日志：" + recoverLog,
                    Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
                return false;
            }
            process.WaitForExit(120000);
            try { exitCode = process.ExitCode; } catch (Exception) { }
            Log("recover exit=" + exitCode);
        }
        catch (Exception ex)
        {
            Log("recover failed: " + ex.ToString());
            MessageBox.Show("恢复上次更新失败：\r\n" + ex.Message + "\r\n\r\n日志：" + recoverLog,
                Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }

        string statePath = Path.Combine(BaseDir, "data", "updates", "state.json");
        string state = "";
        try { if (File.Exists(statePath)) state = File.ReadAllText(statePath, Encoding.UTF8); }
        catch (Exception) { }
        if (LauncherCore.JsonBool(state, "recovered", false))
        {
            MessageBox.Show("上次更新未完成，已恢复旧版本并重新启动。",
                Title, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return false;
        }
        if (exitCode != 0)
        {
            MessageBox.Show("恢复上次更新失败（" + exitCode + "）。\r\n日志：" + recoverLog,
                Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return false;
        }
        if (!TryReacquireMutex())
        {
            Log("recover completed but another instance owns the mutex");
            return false;
        }
        return true;
    }

    static void ReleaseMutexForRecovery()
    {
        if (!mutexHeld || appMutex == null) return;
        try { appMutex.ReleaseMutex(); } catch (Exception) { }
        mutexHeld = false;
    }

    static bool TryReacquireMutex()
    {
        if (appMutex == null) return true;
        try
        {
            if (appMutex.WaitOne(0))
            {
                mutexHeld = true;
                return true;
            }
        }
        catch (Exception) { }
        return false;
    }

    static bool StartInstall(InstallRequest request, PrecheckResult pre)
    {
        if (installStarted)
        {
            PostInstallResult(request.RequestId, false, "install-busy", "安装已经启动，请稍候");
            return false;
        }
        string logPath = pre.LogPath;
        try { Directory.CreateDirectory(Path.GetDirectoryName(logPath)); } catch (Exception) { }
        string[] args = LauncherCore.BuildInstallArgs(pre, BaseDir,
            Process.GetCurrentProcess().Id, logPath);
        string commandLine = LauncherCore.JoinArgs(args);
        Log("install start: helper=" + pre.HelperPath + " args=" + commandLine);
        string errorCode;
        string errorMessage;
        Process helper = StartHelper(pre.HelperPath, commandLine, out errorCode, out errorMessage);
        if (helper == null)
        {
            Log("install helper start failed: " + errorCode + " " + errorMessage);
            PostInstallResult(request.RequestId, false, errorCode, errorMessage);
            return false;
        }
        installStarted = true;
        installHelper = helper;
        Log("install helper started pid=" + helper.Id);
        if (!ShutdownServiceForInstall())
        {
            Log("install aborted: service did not stop within 5s");
            installStarted = false;
            KillInstallHelper();
            PostInstallResult(request.RequestId, false, "service-stop-timeout",
                "服务在 5 秒内没有停止，安装已取消；窗口保持打开，可重试。");
            return false;
        }
        Log("install prepared: service stopped, closing launcher");
        if (!string.IsNullOrEmpty(request.RequestId))
            PostInstallResult(request.RequestId, true, "started", "安装已启动，应用即将关闭");
        ScheduleClose();
        return true;
    }

    static Process StartHelper(string helper, string args, out string code, out string message)
    {
        code = "";
        message = "";
        try
        {
            ProcessStartInfo info = new ProcessStartInfo(helper, args);
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.WorkingDirectory = BaseDir;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            Process process = Process.Start(info);
            if (process == null)
            {
                code = "helper-start-failed";
                message = "无法启动更新助手：" + helper;
                return null;
            }
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs ev)
            {
                if (ev.Data != null) Log("helper: " + ev.Data);
            };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs ev)
            {
                if (ev.Data != null) Log("helper error: " + ev.Data);
            };
            try
            {
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
            }
            catch (Exception) { }
            Thread.Sleep(300);
            if (process.HasExited)
            {
                int exitCode = -1;
                try { exitCode = process.ExitCode; } catch (Exception) { }
                code = "helper-start-failed";
                message = "更新助手启动后立即退出（退出码 " + exitCode + "），请查看日志。";
                return null;
            }
            return process;
        }
        catch (Exception ex)
        {
            code = "helper-start-failed";
            message = "启动更新助手失败：" + ex.Message;
            return null;
        }
    }

    static bool ShutdownServiceForInstall()
    {
        Log("install: requesting python service shutdown");
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
                    if (stream != null)
                    {
                        using (StreamReader reader = new StreamReader(stream)) reader.ReadToEnd();
                    }
                }
            }
        }
        catch (Exception ex)
        {
            Log("install shutdown request failed: " + ex.Message);
        }
        for (int i = 0; i < 50; i++)
        {
            bool serverAlive = false;
            Process proc = server;
            if (proc != null)
            {
                try { serverAlive = !proc.HasExited; }
                catch (Exception) { serverAlive = false; }
            }
            if (!serverAlive && !PortOpen())
            {
                lock (ServerLock)
                {
                    server = null;
                    startedServer = false;
                }
                Log("install: service stopped");
                return true;
            }
            Thread.Sleep(100);
        }
        Log("install: service still running after 5s");
        return false;
    }

    static void KillInstallHelper()
    {
        if (installHelper == null) return;
        try
        {
            if (!installHelper.HasExited)
            {
                installHelper.Kill();
                Log("install helper killed");
            }
        }
        catch (Exception ex) { Log("kill install helper failed: " + ex.Message); }
        try { installHelper.Dispose(); } catch (Exception) { }
        installHelper = null;
    }

    static void ScheduleClose()
    {
        closeTimer = new System.Windows.Forms.Timer();
        closeTimer.Interval = 300;
        closeTimer.Tick += delegate(object sender, EventArgs ev)
        {
            try
            {
                closeTimer.Stop();
                closeTimer.Dispose();
                closeTimer = null;
            }
            catch (Exception) { }
            try { form.Close(); } catch (Exception) { }
        };
        closeTimer.Start();
    }

    static bool HasPendingApply()
    {
        return File.Exists(Path.Combine(BaseDir, "data", "updates", "apply.json"));
    }

    static void OnWebMessage(object sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        string source = e.Source;
        if (!LauncherCore.IsAllowedSource(source))
        {
            Log("rejected web message source: " + source);
            return;
        }
        string json;
        try { json = e.WebMessageAsJson; }
        catch (Exception ex)
        {
            Log("read web message failed: " + ex.Message);
            PostInstallResult("", false, "invalid-message", "无法读取安装请求");
            return;
        }
        string error;
        InstallRequest request = LauncherCore.ParseInstallRequest(json, out error);
        if (request == null)
        {
            Log("invalid install request: " + error);
            PostInstallResult("", false, error, "安装请求格式不正确");
            return;
        }
        if (installStarted)
        {
            PostInstallResult(request.RequestId, false, "install-busy", "安装已经启动，请稍候");
            return;
        }
        PrecheckResult pre = LauncherCore.Precheck(BaseDir, UpdaterHome);
        Log("install request " + request.RequestId + " version=" + request.Version
            + " precheck=" + pre.Code + " ok=" + pre.Ok);
        if (!pre.Ok)
        {
            PostInstallResult(request.RequestId, false, pre.Code, pre.Message);
            return;
        }
        StartInstall(request, pre);
    }

    static void PostInstallResult(string requestId, bool ok, string code, string message)
    {
        try
        {
            if (webView == null || webView.CoreWebView2 == null) return;
            webView.CoreWebView2.PostWebMessageAsJson(
                LauncherCore.InstallResultJson(requestId, ok, code, message));
            Log("install result posted: request=" + requestId + " ok=" + ok + " code=" + code);
        }
        catch (Exception ex)
        {
            Log("post install result failed: " + ex.Message);
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
        if (!installStarted && HasPendingApply())
        {
            e.Cancel = true;
            Log("manual close with pending apply; asking user");
            DialogResult answer = MessageBox.Show(
                "有已准备好的更新，是否立即安装？\r\n\r\n"
                + "点击「是」将立即关闭应用并安装，完成后自动重新打开；数据保留。\r\n"
                + "点击「否」保持当前窗口，稍后再说。",
                Title, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes) return;
            PrecheckResult pre = LauncherCore.Precheck(BaseDir, UpdaterHome);
            Log("manual close install precheck: " + pre.Code + " ok=" + pre.Ok);
            if (!pre.Ok)
            {
                MessageBox.Show("无法立即安装：\r\n" + pre.Message, Title,
                    MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            InstallRequest request = new InstallRequest();
            request.RequestId = "manual-" + DateTime.Now.Ticks.ToString();
            request.Version = pre.Version;
            StartInstall(request, pre);
            return;
        }
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