using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;

// 启动器的纯逻辑核心（无 WinForms / WebView2 依赖），便于用 csc 编译后做自动化验收。
// P2-10R：apply/helper 预检、启动判定、WebView2 消息协议解析。
// P2-11R：helper 启动参数。
public class InstallRequest
{
    public string RequestId = "";
    public string Version = "";
}

public class PrecheckResult
{
    public bool Ok;
    public string Code = "";
    public string Message = "";
    public string Version = "";
    public string FromVersion = "";
    public string StagingPath = "";
    public string BackupPath = "";
    public string HelperPath = "";
    public string LogPath = "";
}

public class StartupDecision
{
    public string Action = "start";
    public string Code = "";
    public string Message = "";
    public string Path = "";
}

public static class LauncherCore
{
    public const string InstallRequestType = "paperlibrary:install.request";
    public const string InstallResultType = "paperlibrary:install.result";
    public const int InstallLockStaleMinutes = 30;

    // ---------- JSON helpers ----------

    public static string JsonString(string json, string field)
    {
        Match match = Regex.Match(json, "\"" + Regex.Escape(field) + "\"\\s*:\\s*\"((?:\\\\.|[^\"\\\\])*)\"");
        return match.Success ? Unescape(match.Groups[1].Value) : "";
    }

    public static long JsonLong(string json, string field, long fallback)
    {
        Match match = Regex.Match(json, "\"" + Regex.Escape(field) + "\"\\s*:\\s*(-?[0-9]+)");
        long value;
        if (match.Success && long.TryParse(match.Groups[1].Value, NumberStyles.Integer,
                                           CultureInfo.InvariantCulture, out value))
            return value;
        return fallback;
    }

    public static bool JsonBool(string json, string field, bool fallback)
    {
        Match match = Regex.Match(json, "\"" + Regex.Escape(field) + "\"\\s*:\\s*(true|false)");
        if (!match.Success) return fallback;
        return match.Groups[1].Value == "true";
    }

    public static string Unescape(string value)
    {
        StringBuilder sb = new StringBuilder(value.Length);
        for (int i = 0; i < value.Length; i++)
        {
            char ch = value[i];
            if (ch != '\\' || i + 1 >= value.Length) { sb.Append(ch); continue; }
            char next = value[++i];
            if (next == 'n') sb.Append('\n');
            else if (next == 'r') sb.Append('\r');
            else if (next == 't') sb.Append('\t');
            else if (next == '"' || next == '\\' || next == '/') sb.Append(next);
            else if (next == 'u' && i + 4 < value.Length)
            {
                int code;
                if (int.TryParse(value.Substring(i + 1, 4), NumberStyles.HexNumber,
                                 CultureInfo.InvariantCulture, out code))
                {
                    sb.Append((char)code);
                    i += 4;
                }
            }
            else sb.Append(next);
        }
        return sb.ToString();
    }

    public static string Escape(string value)
    {
        if (value == null) return "";
        StringBuilder sb = new StringBuilder();
        foreach (char ch in value)
        {
            if (ch == '\\') sb.Append("\\\\");
            else if (ch == '"') sb.Append("\\\"");
            else if (ch == '\r') sb.Append("\\r");
            else if (ch == '\n') sb.Append("\\n");
            else if (ch == '\t') sb.Append("\\t");
            else if (ch < 32) sb.Append("\\u" + ((int)ch).ToString("x4"));
            else sb.Append(ch);
        }
        return sb.ToString();
    }

    // ---------- WebView2 消息协议 ----------

    public static bool IsAllowedSource(string source)
    {
        if (string.IsNullOrEmpty(source)) return false;
        Uri uri;
        if (!Uri.TryCreate(source, UriKind.Absolute, out uri)) return false;
        if (uri.Scheme != "http") return false;
        if (uri.Port != 4321) return false;
        if (uri.UserInfo.Length > 0) return false;
        return uri.Host == "127.0.0.1" || uri.Host == "localhost";
    }

    public static InstallRequest ParseInstallRequest(string json, out string error)
    {
        error = "";
        if (string.IsNullOrEmpty(json)) { error = "empty-message"; return null; }
        string type = JsonString(json, "type");
        if (type != InstallRequestType) { error = "not-install-request"; return null; }
        string requestId = JsonString(json, "requestId");
        if (!Regex.IsMatch(requestId, "^[A-Za-z0-9._:-]{1,128}$"))
        { error = "invalid-request-id"; return null; }
        string version = JsonString(json, "version");
        if (version.Length > 0 && !Regex.IsMatch(version, "^[0-9]+\\.[0-9]+\\.[0-9]+$"))
        { error = "invalid-version"; return null; }
        InstallRequest request = new InstallRequest();
        request.RequestId = requestId;
        request.Version = version;
        return request;
    }

    public static string InstallResultJson(string requestId, bool ok, string code, string message)
    {
        return "{\"type\":\"" + InstallResultType + "\",\"requestId\":\""
            + Escape(requestId) + "\",\"ok\":" + (ok ? "true" : "false")
            + ",\"code\":\"" + Escape(code) + "\",\"message\":\"" + Escape(message) + "\"}";
    }

    // ---------- 路径 / 哈希 ----------

    public static bool IsWithin(string root, string path)
    {
        if (string.IsNullOrEmpty(root) || string.IsNullOrEmpty(path)) return false;
        try
        {
            string rootFull = Path.GetFullPath(root).TrimEnd('\\', '/') + Path.DirectorySeparatorChar;
            string pathFull = Path.GetFullPath(path);
            return pathFull.StartsWith(rootFull, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception) { return false; }
    }

    public static string Sha256File(string path)
    {
        try
        {
            using (SHA256 sha = SHA256.Create())
            using (FileStream stream = File.OpenRead(path))
            {
                byte[] digest = sha.ComputeHash(stream);
                StringBuilder sb = new StringBuilder();
                foreach (byte b in digest) sb.Append(b.ToString("x2"));
                return sb.ToString();
            }
        }
        catch (Exception) { return ""; }
    }

    public static bool ProcessExists(int pid)
    {
        if (pid <= 0) return false;
        try
        {
            using (Process process = Process.GetProcessById(pid))
            {
                return !process.HasExited;
            }
        }
        catch (Exception) { return false; }
    }

    // ---------- helper 部署 ----------

    public static string FindHelper(string updaterHome, out string code, out string message)
    {
        code = "";
        message = "";
        if (string.IsNullOrEmpty(updaterHome))
        {
            code = "helper-missing";
            message = "未配置包外更新助手目录";
            return "";
        }
        string current = Path.Combine(updaterHome, "current.json");
        if (!File.Exists(current))
        {
            code = "helper-missing";
            message = "更新助手尚未部署：" + current;
            return "";
        }
        string text;
        try { text = File.ReadAllText(current, Encoding.UTF8); }
        catch (Exception ex)
        {
            code = "helper-json";
            message = "读取 helper current.json 失败：" + ex.Message;
            return "";
        }
        if (JsonLong(text, "schema", 0) != 1 || JsonString(text, "product") != "PaperLibrary")
        {
            code = "helper-json";
            message = "helper current.json schema/product 不符";
            return "";
        }
        string rel = JsonString(text, "exe").Replace('/', '\\');
        if (rel.Length == 0 || Path.IsPathRooted(rel) || rel.Contains(".."))
        {
            code = "helper-path";
            message = "helper current.json exe 路径不合法";
            return "";
        }
        string exe = Path.Combine(updaterHome, rel);
        if (!IsWithin(updaterHome, exe) || !File.Exists(exe))
        {
            code = "helper-path";
            message = "更新助手文件不存在：" + exe;
            return "";
        }
        string expected = JsonString(text, "sha256").ToLowerInvariant();
        string actual = Sha256File(exe);
        if (expected.Length != 64 || actual != expected)
        {
            code = "helper-sha256";
            message = "更新助手哈希不符：" + exe;
            return "";
        }
        return exe;
    }

    public static string DefaultUpdaterHome()
    {
        string overrideHome = Environment.GetEnvironmentVariable("PAPERLIB_UPDATER_HOME");
        if (!string.IsNullOrEmpty(overrideHome)) return overrideHome;
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        if (string.IsNullOrEmpty(local)) return "";
        return Path.Combine(local, "PaperLibrary", "updater");
    }

    // ---------- P2-10R 预检 ----------

    public static PrecheckResult Precheck(string baseDir, string updaterHome)
    {
        return Precheck(baseDir, updaterHome, -1, null);
    }

    // freeBytesOverride < 0 时读取真实磁盘剩余空间；programFilesRootOverride 仅用于自动化测试。
    public static PrecheckResult Precheck(string baseDir, string updaterHome, long freeBytesOverride)
    {
        return Precheck(baseDir, updaterHome, freeBytesOverride, null);
    }

    public static PrecheckResult Precheck(string baseDir, string updaterHome,
                                          long freeBytesOverride, string programFilesRootOverride)
    {
        // 仅供 P2-19 GUI 验收注入磁盘/Program Files 条件；生产不设置这些环境变量。
        if (freeBytesOverride < 0)
        {
            string testFree = Environment.GetEnvironmentVariable("PAPERLIB_TEST_FREE_BYTES");
            long parsed;
            if (!string.IsNullOrEmpty(testFree) && long.TryParse(testFree, out parsed))
                freeBytesOverride = parsed;
        }
        if (string.IsNullOrEmpty(programFilesRootOverride))
            programFilesRootOverride = Environment.GetEnvironmentVariable("PAPERLIB_TEST_PROGRAM_FILES_ROOT");

        PrecheckResult result = new PrecheckResult();
        try
        {
            string data = Path.Combine(baseDir, "data");
            string updates = Path.Combine(data, "updates");
            string applyPath = Path.Combine(updates, "apply.json");
            if (!File.Exists(applyPath))
                return Fail(result, "apply-missing", "没有待安装的 apply.json");
            string apply;
            try { apply = File.ReadAllText(applyPath, Encoding.UTF8); }
            catch (Exception ex) { return Fail(result, "apply-json", "读取 apply.json 失败：" + ex.Message); }
            if (JsonLong(apply, "schema", 0) != 1)
                return Fail(result, "apply-schema", "apply.json schema 必须是 1");
            string version = JsonString(apply, "version");
            string staging = JsonString(apply, "stagingPath");
            string backup = JsonString(apply, "backupPath");
            string fromVersion = JsonString(apply, "fromVersion");
            if (!Regex.IsMatch(version, "^[0-9]+\\.[0-9]+\\.[0-9]+$"))
                return Fail(result, "apply-invalid", "apply.json version 不合法");
            if (staging.Length == 0)
                return Fail(result, "apply-invalid", "apply.json stagingPath 为空");
            if (!IsWithin(data, staging))
                return Fail(result, "staging-outside-data", "staging 不在 data 目录内：" + staging);
            if (!Directory.Exists(staging))
                return Fail(result, "staging-missing", "staging 目录不存在：" + staging);
            string exe = Path.Combine(staging, "PaperLibrary.exe");
            string versionFile = Path.Combine(staging, "version.json");
            if (!File.Exists(exe) || !File.Exists(versionFile))
                return Fail(result, "staging-incomplete", "staging 缺少 PaperLibrary.exe 或 version.json");
            string packaged = JsonString(File.ReadAllText(versionFile, Encoding.UTF8), "version");
            if (packaged != version)
                return Fail(result, "version-mismatch", "staging 版本 " + packaged + " 与 apply 版本 " + version + " 不一致");

            string helperCode;
            string helperMessage;
            string helper = FindHelper(updaterHome, out helperCode, out helperMessage);
            if (helper.Length == 0)
                return Fail(result, helperCode, helperMessage);

            string programFiles = programFilesRootOverride ?? Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles);
            string programFilesX86 = programFilesRootOverride ?? Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86);
            if (IsWithin(programFiles, baseDir) || IsWithin(programFilesX86, baseDir))
                return Fail(result, "program-files", "程序位于 Program Files，第一阶段不支持提权安装，请放到用户可写目录");

            if (!IsWritable(baseDir))
                return Fail(result, "program-readonly", "程序目录不可写，无法替换：" + baseDir);

            long stagingBytes = DirectoryBytes(staging);
            long programBytes = DirectoryBytesExcluding(baseDir, "data");
            long userDataBytes = DirectoryBytesExcluding(data, "updates");
            long zipBytes = ZipBytes(updates);
            long required = stagingBytes + programBytes + userDataBytes + zipBytes + 300L * 1024 * 1024;
            long available;
            try
            {
                available = freeBytesOverride >= 0
                    ? freeBytesOverride
                    : new DriveInfo(Path.GetPathRoot(Path.GetFullPath(baseDir))).AvailableFreeSpace;
            }
            catch (Exception ex)
            {
                return Fail(result, "disk-check-failed", "无法读取磁盘剩余空间：" + ex.Message);
            }
            if (available < required)
                return Fail(result, "disk-low", "磁盘空间不足：需要约 " + FormatMb(required)
                    + "，当前可用约 " + FormatMb(available) + "（含 300 MB 余量）");

            result.Ok = true;
            result.Code = "ok";
            result.Message = "预检通过";
            result.Version = version;
            result.FromVersion = fromVersion;
            result.StagingPath = Path.GetFullPath(staging);
            result.BackupPath = backup;
            result.HelperPath = helper;
            result.LogPath = Path.Combine(updates, "install-" + version + ".log");
            return result;
        }
        catch (Exception ex)
        {
            return Fail(result, "precheck-error", "预检异常：" + ex.Message);
        }
    }

    private static PrecheckResult Fail(PrecheckResult result, string code, string message)
    {
        result.Ok = false;
        result.Code = code;
        result.Message = message;
        return result;
    }

    private static long DirectoryBytes(string dir)
    {
        long total = 0;
        try
        {
            foreach (string file in Directory.GetFiles(dir))
            {
                try { total += new FileInfo(file).Length; } catch (Exception) { }
            }
            foreach (string child in Directory.GetDirectories(dir))
                total += DirectoryBytes(child);
        }
        catch (Exception) { }
        return total;
    }

    private static long DirectoryBytesExcluding(string root, string excludedTopName)
    {
        long total = 0;
        try
        {
            foreach (string file in Directory.GetFiles(root))
            {
                try { total += new FileInfo(file).Length; } catch (Exception) { }
            }
            foreach (string child in Directory.GetDirectories(root))
            {
                if (string.Equals(Path.GetFileName(child), excludedTopName, StringComparison.OrdinalIgnoreCase))
                    continue;
                total += DirectoryBytes(child);
            }
        }
        catch (Exception) { }
        return total;
    }

    private static long ZipBytes(string root)
    {
        long total = 0;
        try
        {
            foreach (string file in Directory.GetFiles(root, "*.zip", SearchOption.AllDirectories))
            {
                try { total += new FileInfo(file).Length; } catch (Exception) { }
            }
        }
        catch (Exception) { }
        return total;
    }

    private static string FormatMb(long bytes)
    {
        return (bytes / (1024.0 * 1024.0)).ToString("0.0", CultureInfo.InvariantCulture) + " MB";
    }

    private static bool IsWritable(string dir)
    {
        try
        {
            string probe = Path.Combine(dir, ".paperlibrary-write-test-" + Guid.NewGuid().ToString("N"));
            using (FileStream stream = new FileStream(probe, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            {
                stream.WriteByte(0);
            }
            File.Delete(probe);
            return true;
        }
        catch (Exception) { return false; }
    }

    // ---------- P2-10R 启动判定 ----------

    public static bool IsInstallLockActive(string lockPath)
    {
        if (!File.Exists(lockPath)) return false;
        string text;
        try { text = File.ReadAllText(lockPath, Encoding.UTF8); }
        catch (Exception) { return false; }
        int pid = (int)JsonLong(text, "pid", 0);
        if (pid <= 0 || !ProcessExists(pid)) return false;
        string created = JsonString(text, "createdAt");
        DateTime createdUtc;
        if (!DateTime.TryParseExact(created, "yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture,
                                    DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                                    out createdUtc))
            return true;
        return (DateTime.UtcNow - createdUtc).TotalMinutes <= InstallLockStaleMinutes;
    }

    public static string UpdateProbeState(string baseDir, int processId)
    {
        string updates = Path.Combine(baseDir, "data", "updates");
        string journal = SafeRead(Path.Combine(updates, "journal.json"));
        string phase = JsonString(journal, "phase");
        long launchedPid = JsonLong(journal, "launchedPid", 0);
        if (phase == "success" && launchedPid == processId) return "committed";
        if (!IsInstallLockActive(Path.Combine(updates, "install.lock"))) return "";
        if (phase == "launch" && launchedPid == processId) return "validating";
        if ((phase == "verify" || phase == "launch") && launchedPid == 0) return "starting";
        return "";
    }

    public static StartupDecision DecideStartup(string baseDir, string updaterHome)
    {
        StartupDecision decision = new StartupDecision();
        string data = Path.Combine(baseDir, "data");
        string updates = Path.Combine(data, "updates");
        string journalPath = Path.Combine(updates, "journal.json");
        string lockPath = Path.Combine(updates, "install.lock");
        string applyPath = Path.Combine(updates, "apply.json");
        string statePath = Path.Combine(updates, "state.json");

        if (IsInstallLockActive(lockPath))
        {
            decision.Action = "install-in-progress";
            decision.Code = "install-locked";
            decision.Message = "更新助手正在安装新版本，请稍候；不要重复启动应用。";
            decision.Path = lockPath;
            return decision;
        }

        if (File.Exists(journalPath))
        {
            string journal = SafeRead(journalPath);
            string phase = JsonString(journal, "phase");
            if (phase != "success")
            {
                if (phase == "failed")
                {
                    string state = SafeRead(statePath);
                    if (JsonBool(state, "recovered", false)
                        || (JsonString(state, "phase") == "failed"
                            && JsonString(state, "version") == JsonString(journal, "version")
                            && JsonString(state, "code") == JsonString(journal, "code")
                            && JsonString(state, "code") != "recovery-failed"
                            && JsonString(state, "code") != "rollback-failed"))
                    {
                        decision.Action = "notice-failed";
                        decision.Code = JsonString(state, "code");
                        decision.Message = "上次更新未完成，已恢复旧版本。"
                            + NoticeSuffix(state);
                        decision.Path = JsonString(state, "log");
                        return decision;
                    }
                }
                decision.Action = "recover";
                decision.Code = "journal-" + (phase.Length == 0 ? "invalid" : phase);
                decision.Message = "检测到未完成的更新，正在恢复旧版本…";
                decision.Path = journalPath;
                return decision;
            }
        }

        if (File.Exists(lockPath))
        {
            decision.Action = "clean-stale-lock";
            decision.Code = "stale-install-lock";
            decision.Message = "发现过期的安装锁，已清理。";
            decision.Path = lockPath;
            return decision;
        }

        if (File.Exists(applyPath))
        {
            string apply = SafeRead(applyPath);
            string version = JsonString(apply, "version");
            decision.Action = "prompt-apply";
            decision.Code = "apply-ready";
            decision.Message = "有已准备好的更新" + (version.Length > 0 ? "（v" + version + "）" : "")
                + "，是否立即安装？\r\n\r\n不会静默安装；也可以稍后在设置页操作。";
            decision.Path = applyPath;
            return decision;
        }

        if (File.Exists(statePath))
        {
            string state = SafeRead(statePath);
            string phase = JsonString(state, "phase");
            if (phase != "success" && (phase == "failed" || JsonBool(state, "recovered", false)))
            {
                decision.Action = "notice-failed";
                decision.Code = JsonString(state, "code");
                decision.Message = "上次更新失败，已回滚/恢复旧版本。" + NoticeSuffix(state);
                decision.Path = JsonString(state, "log");
                return decision;
            }
        }

        decision.Action = "start";
        decision.Code = "ok";
        return decision;
    }

    private static string SafeRead(string path)
    {
        try
        {
            using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read,
                                                      FileShare.ReadWrite | FileShare.Delete))
            using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                return reader.ReadToEnd();
        }
        catch (Exception) { return ""; }
    }

    private static string NoticeSuffix(string state)
    {
        string code = JsonString(state, "code");
        string log = JsonString(state, "log");
        StringBuilder sb = new StringBuilder();
        if (code.Length > 0) sb.Append("\r\n原因：").Append(code);
        if (log.Length > 0) sb.Append("\r\n日志：").Append(log);
        return sb.ToString();
    }

    // ---------- P2-11R helper 启动参数 ----------

    public static string[] BuildInstallArgs(PrecheckResult pre, string baseDir, int launcherPid, string logPath)
    {
        return new string[] {
            "install",
            "--program-root", baseDir,
            "--staging", pre.StagingPath,
            "--old-version", pre.FromVersion,
            "--new-version", pre.Version,
            "--launcher-pid", launcherPid.ToString(CultureInfo.InvariantCulture),
            "--backup", pre.BackupPath,
            "--port", "4321",
            "--log", logPath
        };
    }

    public static string QuoteArgument(string value)
    {
        if (value == null) return "\"\"";
        if (value.Length > 0 && value.IndexOfAny(new char[] { ' ', '\t', '"' }) < 0) return value;
        StringBuilder sb = new StringBuilder();
        sb.Append('"');
        int backslashes = 0;
        foreach (char ch in value)
        {
            if (ch == '\\') { backslashes++; continue; }
            if (ch == '"')
            {
                sb.Append('\\', backslashes * 2 + 1);
                sb.Append('"');
                backslashes = 0;
            }
            else
            {
                sb.Append('\\', backslashes);
                backslashes = 0;
                sb.Append(ch);
            }
        }
        sb.Append('\\', backslashes * 2);
        sb.Append('"');
        return sb.ToString();
    }

    public static string JoinArgs(string[] args)
    {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < args.Length; i++)
        {
            if (i > 0) sb.Append(' ');
            sb.Append(QuoteArgument(args[i]));
        }
        return sb.ToString();
    }
}
