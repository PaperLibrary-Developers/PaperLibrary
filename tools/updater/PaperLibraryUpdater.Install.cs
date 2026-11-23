// PaperLibrary Updater Helper -- full install sequence, health checks and rollback (P2-13).
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;

internal static partial class UpdaterHelper
{
    // ---------- full install sequence (P2-13) ----------
    //
    // Inputs come from the launcher (which already validated apply.json):
    //   --program-root, --staging, --old-version, --new-version, --launcher-pid,
    //   --backup (optional), --port, --log
    // Default waits are the values confirmed in the plan; the --timeout-* / --health-*
    // switches exist for deterministic tests and must not be passed by the launcher.
    //
    // Order: install lock + journal prepare -> wait launcher PID / port / file locks ->
    // backup data -> backup program files -> replace -> verify -> launch new app ->
    // health check (health + /api/version, 2 consecutive) -> state.json success + clear apply.
    // Any failure after program files moved restores the backup and launches the old app.

    private const int DefaultLauncherWaitSeconds = 60;
    private const int DefaultPortWaitSeconds = 30;
    private const int DefaultLockWaitSeconds = 30;
    private const int DefaultTotalSeconds = 300;
    private const int DefaultHealthTimeoutSeconds = 60;
    private const int DefaultHealthIntervalMs = 1000;
    private const int DefaultHealthRequestMs = 2000;
    private const int DefaultHealthSuccesses = 2;
    // P2-18（2026-11-17 确认）：安装期只重试共享/锁冲突，按文件每 300ms 一次，最多 21 秒。
    private const int DefaultFileRetryMaxMs = 21000;
    private const int DefaultFileRetryDelayMs = 300;

    private static int InstallCommand(Dictionary<string, string> options, HelperLog log)
    {
        string programRoot = RequiredPath(options, "program-root");
        string staging = RequiredPath(options, "staging");
        ValidateProgramRoot(programRoot);

        string newVersion = Opt(options, "new-version");
        string stagingVersion = ValidateStaging(staging, programRoot, newVersion);
        if (string.IsNullOrEmpty(newVersion)) newVersion = stagingVersion;

        string oldVersion = Opt(options, "old-version");
        if (string.IsNullOrEmpty(oldVersion))
        {
            string currentVersionPath = System.IO.Path.Combine(programRoot, "version.json");
            if (!File.Exists(currentVersionPath))
                throw new HelperFailure("old-version-missing",
                    "缺少当前版本 version.json，且未提供 --old-version：" + currentVersionPath);
            oldVersion = ReadJsonString(currentVersionPath, "version");
        }
        ValidateVersionText(oldVersion, "old-version");

        string backupDir = Opt(options, "backup");
        if (string.IsNullOrEmpty(backupDir))
            backupDir = System.IO.Path.Combine(programRoot, "data", "updates", "backups", "app-" + oldVersion);
        backupDir = System.IO.Path.GetFullPath(backupDir);
        ValidateBackupPath(programRoot, backupDir);

        int launcherPid = IntOption(options, "launcher-pid", 0, 0, int.MaxValue);
        int port = IntOption(options, "port", 4321, 1, 65535);
        int pidWait = IntOption(options, "timeout-pid", DefaultLauncherWaitSeconds, 0, 3600);
        int portWait = IntOption(options, "timeout-port", DefaultPortWaitSeconds, 1, 3600);
        int lockWait = IntOption(options, "timeout-lock", DefaultLockWaitSeconds, 1, 3600);
        int totalSeconds = IntOption(options, "timeout-total", DefaultTotalSeconds, 10, 7200);
        int healthTimeout = IntOption(options, "health-timeout", DefaultHealthTimeoutSeconds, 1, 3600);
        int healthInterval = IntOption(options, "health-interval", DefaultHealthIntervalMs, 50, 60000);
        int healthRequest = IntOption(options, "health-request-timeout", DefaultHealthRequestMs, 100, 60000);
        int healthSuccesses = IntOption(options, "health-successes", DefaultHealthSuccesses, 1, 10);
        int phaseDelayMs = IntOption(options, "phase-delay-ms", 0, 0, 60000);
        int fileRetryMaxMs = IntOption(options, "file-retry-max-ms", DefaultFileRetryMaxMs, 0, 600000);
        int fileRetryDelayMs = IntOption(options, "file-retry-delay-ms", DefaultFileRetryDelayMs, 10, 5000);
        string exitAfterPhase = Opt(options, "exit-after-phase");  // test-only fault injection
        string dataBackupDir = System.IO.Path.Combine(programRoot, "data", "backups", "update-" + newVersion);

        string updatesDir = System.IO.Path.Combine(programRoot, "data", "updates");
        Directory.CreateDirectory(updatesDir);
        string lockPath = System.IO.Path.Combine(updatesDir, "install.lock");
        string journalPath = System.IO.Path.Combine(updatesDir, "journal.json");
        string statePath = System.IO.Path.Combine(updatesDir, "state.json");
        string applyPath = System.IO.Path.Combine(updatesDir, "apply.json");
        Dictionary<string, string> stepOptions = new Dictionary<string, string>
        {
            { "program-root", programRoot }, { "new-version", newVersion },
            { "old-version", oldVersion }, { "backup", backupDir }, { "staging", staging },
            { "file-retry-max-ms", fileRetryMaxMs.ToString(CultureInfo.InvariantCulture) },
            { "file-retry-delay-ms", fileRetryDelayMs.ToString(CultureInfo.InvariantCulture) },
        };

        if (File.Exists(lockPath))
        {
            if (!IsInstallLockStale(lockPath, log))
                throw new HelperFailure("install-locked", "已有安装任务在进行（install.lock 存在）：" + lockPath);
            log.Write("stale install lock removed: " + lockPath);
            TryDelete(lockPath);
        }

        if (File.Exists(journalPath))
        {
            string previousPhase = ReadJsonString(journalPath, "phase");
            if (previousPhase != "success" && previousPhase != "failed")
                throw new HelperFailure("recovery-required", "请先恢复上次未完成的更新");
        }

        string startedAt = NowIso();
        bool lockCreated = false;
        try
        {
            CreateInstallLock(lockPath, newVersion, oldVersion, launcherPid, log);
            lockCreated = true;
            WriteJournal(journalPath, "prepare", newVersion, oldVersion, backupDir,
                         dataBackupDir, staging, launcherPid, startedAt, 0, "", "", log, port);
            SleepPhase(phaseDelayMs);
            MaybeExitAfterPhase(exitAfterPhase, "prepare", log);

            Stopwatch total = Stopwatch.StartNew();
            bool programBackupDone = false;
            bool programBackupStarted = false;
            bool candidateStarting = false;
            bool installCommitted = false;
            Process newProc = null;
            int healthChecks = 0;
            int healthSeconds = 0;
            try
            {
                if (!WaitForPidExit(launcherPid, pidWait, 200, log))
                    throw new HelperFailure("launcher-timeout",
                        "等待启动器退出超时（" + pidWait + " 秒），未改动任何文件");
                CheckBudget(total, totalSeconds, "wait-launcher");

                if (!WaitForPortFree(port, portWait, 250, log))
                    throw new HelperFailure("port-timeout",
                        "等待端口 " + port + " 释放超时（" + portWait + " 秒），未改动任何文件");
                CheckBudget(total, totalSeconds, "wait-port");

                string lockedFile;
                if (!WaitForFileLocks(programRoot, lockWait, 500, log, out lockedFile))
                    throw new HelperFailure("file-lock-timeout",
                        "等待文件锁释放超时（" + lockWait + " 秒）：" + lockedFile + "，未改动任何文件");
                CheckBudget(total, totalSeconds, "wait-locks");

                if (Directory.Exists(backupDir))
                {
                    string oldMarker = System.IO.Path.Combine(backupDir, "backup.json");
                    string[] entries = Directory.GetFileSystemEntries(backupDir);
                    if (File.Exists(oldMarker) && entries.Length == 1)
                        File.Delete(oldMarker);
                    else if (entries.Length > 0)
                        throw new HelperFailure("program-backup-failed", "备份目录已存在且非空：" + backupDir);
                }
                WriteJournal(journalPath, "backup", newVersion, oldVersion, backupDir,
                             dataBackupDir, staging, launcherPid, startedAt, 0, "", "", log, port);
                SleepPhase(phaseDelayMs);
                MaybeExitAfterPhase(exitAfterPhase, "backup", log);

                RunInstallStep("data-backup-failed",
                    delegate { BackupDataCommand(stepOptions, log, false, true); });
                CheckBudget(total, totalSeconds, "backup-data");

                programBackupStarted = true;
                RunInstallStep("program-backup-failed",
                    delegate { BackupProgramCommand(stepOptions, log, false); });
                programBackupDone = true;
                CheckBudget(total, totalSeconds, "backup-program");

                WriteJournal(journalPath, "replace", newVersion, oldVersion, backupDir,
                             dataBackupDir, staging, launcherPid, startedAt, 0, "", "", log, port);
                SleepPhase(phaseDelayMs);
                MaybeExitAfterPhase(exitAfterPhase, "replace", log);

                RunInstallStep("replace-failed",
                    delegate { ReplaceProgramCommand(stepOptions, log, false); });
                WriteJournal(journalPath, "verify", newVersion, oldVersion, backupDir,
                             dataBackupDir, staging, launcherPid, startedAt, 0, "", "", log, port);
                SleepPhase(phaseDelayMs);
                MaybeExitAfterPhase(exitAfterPhase, "verify", log);
                VerifyInstalledProgram(programRoot, newVersion);
                CheckBudget(total, totalSeconds, "verify");

                WriteJournal(journalPath, "launch", newVersion, oldVersion, backupDir,
                             dataBackupDir, staging, launcherPid, startedAt, 0, "", "", log, port);
                candidateStarting = true;
                newProc = LaunchApplication(programRoot, log);
                WriteJournal(journalPath, "launch", newVersion, oldVersion, backupDir,
                             dataBackupDir, staging, launcherPid, startedAt, newProc.Id, "", "", log, port);
                SleepPhase(phaseDelayMs);
                MaybeExitAfterPhase(exitAfterPhase, "launch", log);
                CheckBudget(total, totalSeconds, "launch");

                string healthDetail;
                if (!WaitForHealthy(port, newVersion, healthTimeout, healthInterval, healthRequest,
                                    healthSuccesses, total, totalSeconds, log,
                                    out healthDetail, out healthChecks))
                    throw new HelperFailure(healthDetail, "新版本健康检查失败：" + healthDetail);

                healthSeconds = (int)(total.ElapsedMilliseconds / 1000);
                WriteState(statePath, "success", newVersion, oldVersion, staging, dataBackupDir,
                           backupDir, newProc.Id, 0, false, false, healthSeconds, healthChecks,
                           startedAt, "", "", log.Path, log);
                WriteJournal(journalPath, "success", newVersion, oldVersion, backupDir,
                             dataBackupDir, staging, launcherPid, startedAt, newProc.Id, "", "", log, port);
                installCommitted = true;
                PruneProgramBackups(programRoot, backupDir, log);
                TryDelete(applyPath);
                RemoveInstallLock(lockPath);
                lockCreated = false;

                Console.WriteLine(Obj(
                    JB("ok", true),
                    J("command", "install"),
                    J("programRoot", programRoot),
                    J("version", newVersion),
                    J("fromVersion", oldVersion),
                    J("launchedPid", (long)newProc.Id),
                    J("backupDir", backupDir),
                    J("healthSeconds", (long)healthSeconds),
                    J("log", log.Path)));
                log.Write("install success: " + oldVersion + " -> " + newVersion
                          + " launchedPid=" + newProc.Id + " health=" + healthSeconds + "s");
                return 0;
            }
            catch (Exception failure)
            {
                if (installCommitted) throw;
                HelperFailure knownFailure = failure as HelperFailure;
                string failCode = knownFailure == null ? "install-failed" : knownFailure.Code;
                string failMessage = failure.Message;
                log.Write("install failed: " + failCode + ": " + failMessage);

                bool restored = false;
                bool rollbackComplete = false;
                int oldPid = 0;
                int failedPid = newProc == null ? 0 : newProc.Id;
                try
                {
                    if (candidateStarting)
                    {
                        StopCandidate(programRoot, failedPid, port, log);
                        RestoreData(programRoot, newVersion, log);
                    }
                    if (programBackupDone || (programBackupStarted && HasProgramBackupEntries(backupDir)))
                    {
                        RestoreProgram(programRoot, backupDir, !programBackupDone, log);
                        restored = true;
                    }
                    WriteState(statePath, "failed", newVersion, oldVersion, staging, dataBackupDir,
                               backupDir, failedPid, 0, restored, restored,
                               (int)(total.ElapsedMilliseconds / 1000), healthChecks,
                               startedAt, failCode, failMessage, log.Path, log);
                    WriteJournal(journalPath, "failed", newVersion, oldVersion, backupDir,
                                 dataBackupDir, staging, launcherPid, startedAt, failedPid,
                                 failCode, failMessage, log, port);
                    rollbackComplete = true;
                }
                catch (Exception ex)
                {
                    failMessage += " | 回滚未完成：" + ex.Message;
                    failCode = "rollback-failed";
                    log.Write(failMessage);
                }

                try
                {
                    if (rollbackComplete && restored)
                    {
                        RemoveInstallLock(lockPath);
                        lockCreated = false;
                        oldPid = LaunchApplication(programRoot, log).Id;
                    }
                    WriteState(statePath, "failed", newVersion, oldVersion, staging, dataBackupDir,
                               backupDir, failedPid, oldPid, restored, rollbackComplete && restored,
                               (int)(total.ElapsedMilliseconds / 1000), healthChecks,
                               startedAt, failCode, failMessage, log.Path, log);
                }
                catch (Exception ex)
                {
                    log.Write("write failed state failed: " + ex.Message);
                }

                Console.WriteLine(Obj(
                    JB("ok", false),
                    J("command", "install"),
                    J("programRoot", programRoot),
                    J("version", newVersion),
                    J("fromVersion", oldVersion),
                    J("code", failCode),
                    J("error", failMessage),
                    JB("restored", restored),
                    J("oldLaunchedPid", (long)oldPid),
                    J("backupDir", backupDir),
                    J("log", log.Path)));
                return 3;
            }
        }
        finally
        {
            if (lockCreated) RemoveInstallLock(lockPath);
        }
    }

    private static int IntOption(Dictionary<string, string> options, string name, int fallback,
                                 int min, int max)
    {
        string raw = Opt(options, name);
        int value = fallback;
        if (!string.IsNullOrEmpty(raw)
            && (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out value)
                || value < min || value > max))
            throw new HelperFailure("invalid-argument", "--" + name + " must be " + min + "-" + max);
        return value;
    }

    private static bool WaitForPidExit(int pid, int seconds, int intervalMs, HelperLog log)
    {
        if (pid <= 0) return true;
        Stopwatch watch = Stopwatch.StartNew();
        while (watch.ElapsedMilliseconds < (long)seconds * 1000)
        {
            if (!ProcessExists(pid)) return true;
            Thread.Sleep(intervalMs);
        }
        return !ProcessExists(pid);
    }

    private static bool PortInUse(int port)
    {
        try
        {
            IPGlobalProperties properties = IPGlobalProperties.GetIPGlobalProperties();
            foreach (IPEndPoint endpoint in properties.GetActiveTcpListeners())
            {
                if (endpoint.Port == port) return true;
            }
            return false;
        }
        catch (Exception)
        {
            // 探测失败时按“未占用”处理；真正的绑定失败会由后续启动/健康检查暴露。
            return false;
        }
    }

    private static bool WaitForPortFree(int port, int seconds, int intervalMs, HelperLog log)
    {
        Stopwatch watch = Stopwatch.StartNew();
        while (watch.ElapsedMilliseconds < (long)seconds * 1000)
        {
            if (!PortInUse(port)) return true;
            Thread.Sleep(intervalMs);
        }
        return !PortInUse(port);
    }

    private static bool IsFileLocked(string path)
    {
        if (!File.Exists(path)) return false;
        try
        {
            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.None))
            {
                stream.Close();
            }
            return false;
        }
        catch (IOException)
        {
            return true;
        }
        catch (UnauthorizedAccessException)
        {
            return true;
        }
    }

    private static string FirstLockedProgramFile(string programRoot)
    {
        string[] relative =
        {
            "PaperLibrary.exe", "PaperLibrary.exe.config",
            "Microsoft.Web.WebView2.Core.dll", "Microsoft.Web.WebView2.WinForms.dll",
            "WebView2Loader.dll", System.IO.Path.Combine("runtime", "portable", "python.exe"),
        };
        foreach (string name in relative)
        {
            string path = System.IO.Path.Combine(programRoot, name);
            if (IsFileLocked(path)) return path;
        }
        return "";
    }

    private static bool WaitForFileLocks(string programRoot, int seconds, int intervalMs,
                                         HelperLog log, out string lockedFile)
    {
        Stopwatch watch = Stopwatch.StartNew();
        while (watch.ElapsedMilliseconds < (long)seconds * 1000)
        {
            lockedFile = FirstLockedProgramFile(programRoot);
            if (lockedFile.Length == 0) return true;
            Thread.Sleep(intervalMs);
        }
        lockedFile = FirstLockedProgramFile(programRoot);
        return lockedFile.Length == 0;
    }

    private static void CheckBudget(Stopwatch watch, int totalSeconds, string phase)
    {
        if (watch.ElapsedMilliseconds > (long)totalSeconds * 1000)
            throw new HelperFailure("total-timeout",
                "安装超出总预算（" + totalSeconds + " 秒），阶段：" + phase);
    }

    private static void RunInstallStep(string failureCode, Action step)
    {
        try
        {
            // Run file mutations under the process that owns install.lock.
            step();
        }
        catch (Exception ex)
        {
            HelperFailure known = ex as HelperFailure;
            if (known != null && known.Code == "file-lock-timeout") throw;
            throw new HelperFailure(failureCode, ex.Message);
        }
    }

    private static void VerifyInstalledProgram(string programRoot, string newVersion)
    {
        string exePath = System.IO.Path.Combine(programRoot, "PaperLibrary.exe");
        string versionPath = System.IO.Path.Combine(programRoot, "version.json");
        if (!File.Exists(exePath))
            throw new HelperFailure("verify-failed", "替换后缺少 PaperLibrary.exe：" + exePath);
        if (!File.Exists(versionPath))
            throw new HelperFailure("verify-failed", "替换后缺少 version.json：" + versionPath);
        string installedVersion = ReadJsonString(versionPath, "version");
        if (installedVersion != newVersion)
            throw new HelperFailure("verify-failed",
                "替换后版本不符：" + installedVersion + " != " + newVersion);
    }

    private static Process LaunchApplication(string programRoot, HelperLog log)
    {
        string exePath = System.IO.Path.Combine(programRoot, "PaperLibrary.exe");
        if (!File.Exists(exePath))
            throw new HelperFailure("launch-failed", "缺少启动器：" + exePath);
        ProcessStartInfo info = new ProcessStartInfo(exePath);
        info.WorkingDirectory = programRoot;
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        // 不要让新应用继承 helper 的 stdout/stderr（helper 的输出被启动器/测试以管道读取；
        // 子进程若继承管道，helper 退出后管道仍不关闭，调用方会一直等不到 EOF）。
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        Process process = Process.Start(info);
        if (process == null)
            throw new HelperFailure("launch-failed", "无法启动：" + exePath);
        try
        {
            process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs ev) { };
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs ev) { };
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();
        }
        catch (Exception)
        {
        }
        log.Write("launched " + exePath + " pid=" + process.Id);
        return process;
    }

    private static void RequestShutdown(int port)
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(
                "http://127.0.0.1:" + port + "/api/shutdown");
            request.Method = "POST";
            request.Timeout = 2000;
            request.ReadWriteTimeout = 2000;
            request.Proxy = null;
            request.ContentLength = 0;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            {
                using (Stream stream = response.GetResponseStream())
                {
                    if (stream != null)
                    {
                        using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                            reader.ReadToEnd();
                    }
                }
            }
        }
        catch (Exception)
        {
        }
    }

    private static void KillProcessTree(int pid)
    {
        if (pid <= 0) return;
        try
        {
            ProcessStartInfo info = new ProcessStartInfo("taskkill", "/PID " + pid + " /T /F");
            info.UseShellExecute = false;
            info.CreateNoWindow = true;
            info.RedirectStandardOutput = true;
            info.RedirectStandardError = true;
            using (Process process = Process.Start(info))
            {
                if (process == null) return;
                process.StandardOutput.ReadToEnd();
                process.StandardError.ReadToEnd();
                process.WaitForExit(5000);
            }
        }
        catch (Exception)
        {
        }
    }

    private static bool TryHttpGet(string url, int timeoutMs, out string body, out string error)
    {
        body = "";
        error = "";
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
            request.Method = "GET";
            request.Timeout = timeoutMs;
            request.ReadWriteTimeout = timeoutMs;
            request.Proxy = null;
            request.KeepAlive = false;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
            {
                if ((int)response.StatusCode != 200)
                {
                    error = "health-http";
                    return false;
                }
                using (Stream stream = response.GetResponseStream())
                {
                    if (stream == null)
                    {
                        error = "health-http";
                        return false;
                    }
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                    {
                        char[] buffer = new char[65536];
                        int read = reader.Read(buffer, 0, buffer.Length);
                        body = read <= 0 ? "" : new string(buffer, 0, read);
                        return true;
                    }
                }
            }
        }
        catch (WebException ex)
        {
            error = ex.Status == WebExceptionStatus.Timeout ? "health-timeout" : "health-http";
            return false;
        }
        catch (Exception)
        {
            error = "health-http";
            return false;
        }
    }

    // 返回空字符串表示健康；否则返回已冻结的失败码之一：
    // health-timeout / health-http / health-json / health-not-ok / health-app / health-version
    private static string HealthCheck(int port, string targetVersion, int requestMs)
    {
        string body;
        string error;
        if (!TryHttpGet("http://127.0.0.1:" + port + "/api/health", requestMs, out body, out error))
            return error;

        Dictionary<string, object> healthObj = TryParseJsonObject(body);
        bool okVal;
        if (!TryJsonBool(healthObj, "ok", out okVal)) return "health-json";
        if (!okVal) return "health-not-ok";

        string appVal;
        if (!TryJsonString(healthObj, "app", out appVal)) return "health-json";
        if (appVal != "literature-vault") return "health-app";

        if (!TryHttpGet("http://127.0.0.1:" + port + "/api/version", requestMs, out body, out error))
            return error;

        Dictionary<string, object> versionObj = TryParseJsonObject(body);
        string productVal;
        if (!TryJsonString(versionObj, "product", out productVal)) return "health-json";
        if (productVal != "PaperLibrary") return "health-version";

        string versionVal;
        if (!TryJsonString(versionObj, "version", out versionVal)) return "health-json";
        if (versionVal != targetVersion) return "health-version";

        return "";
    }

    private static bool WaitForHealthy(int port, string targetVersion, int timeoutSeconds,
                                       int intervalMs, int requestMs, int successes,
                                       Stopwatch total, int totalSeconds, HelperLog log,
                                       out string lastDetail, out int attempts)
    {
        lastDetail = "";
        attempts = 0;
        Stopwatch watch = Stopwatch.StartNew();
        int streak = 0;
        while (watch.ElapsedMilliseconds < (long)timeoutSeconds * 1000)
        {
            CheckBudget(total, totalSeconds, "health");
            attempts++;
            string code = HealthCheck(port, targetVersion, requestMs);
            if (code.Length == 0)
            {
                streak++;
                log.Write("health attempt " + attempts + ": ok (streak " + streak + "/" + successes + ")");
                if (streak >= successes) return true;
            }
            else
            {
                streak = 0;
                lastDetail = code;
                log.Write("health attempt " + attempts + ": " + code);
            }
            Thread.Sleep(intervalMs);
        }
        if (lastDetail.Length == 0) lastDetail = "health-timeout";
        return false;
    }

    private static void CreateInstallLock(string path, string version, string fromVersion,
                                          int launcherPid, HelperLog log)
    {
        string dir = System.IO.Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        string record = Obj(
            J("schema", 1L),
            J("product", HelperInfo.Product),
            J("pid", (long)Process.GetCurrentProcess().Id),
            J("launcherPid", (long)launcherPid),
            J("version", version),
            J("fromVersion", fromVersion),
            J("createdAt", NowIso()));
        try
        {
            using (FileStream stream = new FileStream(path, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                byte[] bytes = new UTF8Encoding(false).GetBytes(record);
                stream.Write(bytes, 0, bytes.Length);
            }
        }
        catch (IOException)
        {
            throw new HelperFailure("install-locked", "已有安装任务在进行（install.lock 存在）：" + path);
        }
        log.Write("install lock created: " + path);
    }

    private static void RemoveInstallLock(string path)
    {
        try
        {
            if (File.Exists(path)) File.Delete(path);
        }
        catch (Exception)
        {
        }
    }

    private static void WriteJournal(string path, string phase, string version, string fromVersion,
                                     string backupDir, string dataBackupDir, string staging,
                                     int launcherPid, string startedAt, int launchedPid,
                                     string code, string error, HelperLog log, int port = 4321)
    {
        string record = Obj(
            J("schema", 1L),
            J("product", HelperInfo.Product),
            J("phase", phase),
            J("version", version),
            J("fromVersion", fromVersion),
            J("staging", staging),
            J("dataBackupDir", dataBackupDir),
            J("backupDir", backupDir),
            J("launcherPid", (long)launcherPid),
            J("startedAt", startedAt),
            J("launchedPid", (long)launchedPid),
            J("port", (long)port),
            J("updatedAt", NowIso()),
            J("code", code),
            J("error", error),
            J("log", log.Path));
        WriteTextAtomic(path, record);
        log.Write("journal: " + phase);
    }

    private static void WriteState(string path, string phase, string version, string fromVersion,
                                   string staging, string dataBackupDir, string backupDir,
                                   int launchedPid, int oldLaunchedPid, bool restored, bool recovered,
                                   int healthSeconds, int healthChecks, string startedAt,
                                   string code, string error, string logPath, HelperLog log)
    {
        string record = Obj(
            J("schema", 1L),
            J("product", HelperInfo.Product),
            J("phase", phase),
            J("version", version),
            J("fromVersion", fromVersion),
            J("staging", staging),
            J("dataBackupDir", dataBackupDir),
            J("backupDir", backupDir),
            J("launchedPid", (long)launchedPid),
            J("oldLaunchedPid", (long)oldLaunchedPid),
            JB("restored", restored),
            JB("recovered", recovered),
            J("healthSeconds", (long)healthSeconds),
            J("healthChecks", (long)healthChecks),
            J("startedAt", startedAt),
            J("finishedAt", NowIso()),
            J("code", code),
            J("error", error),
            J("log", logPath));
        WriteTextAtomic(path, record);
        log.Write("state: " + phase);
    }

    private static void RestoreProgram(string programRoot, string backupDir, bool partial, HelperLog log)
    {
        ValidateBackupPath(programRoot, backupDir);
        string marker = System.IO.Path.Combine(backupDir, "backup.json");
        if (File.Exists(marker))
        {
            // The marker lists all old entries, including those already moved back by a
            // previous interrupted restore. Keep the marker until the next install.
            HashSet<string> names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            foreach (object entry in (System.Collections.IList)ReadJsonObject(marker)["entries"])
            {
                string name = (string)((Dictionary<string, object>)entry)["name"];
                if (string.IsNullOrEmpty(name) || name == "." || name == ".."
                    || name.IndexOfAny(System.IO.Path.GetInvalidFileNameChars()) >= 0
                    || string.Equals(name, "data", StringComparison.OrdinalIgnoreCase)
                    || !names.Add(name))
                    throw new InvalidDataException("Invalid program backup entry: " + name);
                string source = System.IO.Path.Combine(backupDir, name);
                string target = System.IO.Path.Combine(programRoot, name);
                if (!File.Exists(source) && !Directory.Exists(source)
                    && !File.Exists(target) && !Directory.Exists(target))
                    throw new InvalidDataException("Missing program backup entry: " + name);
            }
            if (!names.Contains("PaperLibrary.exe"))
                throw new InvalidDataException("Program backup has no launcher");
            foreach (string entry in Directory.GetFileSystemEntries(programRoot))
            {
                string name = System.IO.Path.GetFileName(entry);
                if (!string.Equals(name, "data", StringComparison.OrdinalIgnoreCase) && !names.Contains(name))
                    DeleteProgramEntry(entry);
            }
            foreach (string name in names)
                RestoreProgramEntry(programRoot, backupDir, name, true);
        }
        else if (partial && Directory.Exists(backupDir))
        {
            // Backup was interrupted before replacement. Restore only files that were moved.
            foreach (string entry in Directory.GetFileSystemEntries(backupDir))
                RestoreProgramEntry(programRoot, backupDir, System.IO.Path.GetFileName(entry), false);
        }
        else
        {
            throw new InvalidDataException("Program backup marker missing: " + backupDir);
        }
        if (!File.Exists(System.IO.Path.Combine(programRoot, "PaperLibrary.exe")))
            throw new InvalidDataException("Restored launcher is missing");
        log.Write("program restored from " + backupDir);
    }

    private static void DeleteProgramEntry(string path)
    {
        if (Directory.Exists(path)) Directory.Delete(path, true);
        else File.Delete(path);
    }

    private static void RestoreProgramEntry(string programRoot, string backupDir, string name, bool replace)
    {
        string source = System.IO.Path.Combine(backupDir, name);
        if (!File.Exists(source) && !Directory.Exists(source)) return;
        string target = System.IO.Path.Combine(programRoot, name);
        if (replace) DeleteProgramEntry(target);
        if (Directory.Exists(source)) Directory.Move(source, target);
        else File.Move(source, target);
    }

    private static bool IsCandidateProcess(string programRoot, int pid)
    {
        if (pid <= 0) return false;
        try
        {
            using (Process process = Process.GetProcessById(pid))
                return string.Equals(process.MainModule.FileName,
                    System.IO.Path.Combine(programRoot, "PaperLibrary.exe"), StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static bool IsCandidateService(string programRoot, int port)
    {
        string health, error;
        if (!TryHttpGet("http://127.0.0.1:" + port + "/api/health", 2000, out health, out error))
            return false;
        try
        {
            Dictionary<string, object> info = new JavaScriptSerializer()
                .Deserialize<Dictionary<string, object>>(health);
            return object.Equals(info["app"], "literature-vault")
                && string.Equals(System.IO.Path.GetFullPath((string)info["dataDir"]),
                    System.IO.Path.Combine(programRoot, "data"), StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static void StopCandidate(string programRoot, int pid, int port, HelperLog log)
    {
        bool ownsLauncher = IsCandidateProcess(programRoot, pid);
        if (ownsLauncher || IsCandidateService(programRoot, port))
            RequestShutdown(port);
        if (ownsLauncher)
        {
            if (!WaitForPidExit(pid, 2, 100, log))
            {
                if (IsCandidateProcess(programRoot, pid)) KillProcessTree(pid);
                if (!WaitForPidExit(pid, 5, 100, log))
                    throw new IOException("Updated launcher is still running");
            }
        }
        if (!WaitForPortFree(port, 5, 100, log))
            throw new IOException("Updated application is still running");
    }

    private static void TryDelete(string path)
    {
        try
        {
            if (File.Exists(path)) File.Delete(path);
        }
        catch (Exception)
        {
        }
    }

}
