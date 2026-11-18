// PaperLibrary Updater Helper -- package-external single-file .NET Framework executable.
//
// Why a separate executable: Windows cannot reliably overwrite a running .exe/.dll, and
// the launcher cannot replace itself. This helper is copied by the app from the release
// package (tools/updater/PaperLibraryUpdater.exe) into
// %LOCALAPPDATA%\PaperLibrary\updater\<version>\ and started from there, so replacing the
// program directory never touches the helper that is doing the replacing.
//
// Stage 2E-1 (P2-12) builds the skeleton: version identity + diagnostics + wait/hold
// commands, plus the deployment contract. The install sequence (replace / backup /
// journal / health) is added by P2-14 .. P2-17 without changing this contract.
//
// csc.exe from .NET Framework 4.x only supports C# 5: no string interpolation, no "?.", no nameof.
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

internal sealed class HelperLog
{
    private readonly string path;

    public HelperLog(string path)
    {
        this.path = path ?? "";
    }

    public string Path
    {
        get { return path; }
    }

    public void Write(string message)
    {
        if (string.IsNullOrEmpty(path)) return;
        try
        {
            string dir = System.IO.Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            File.AppendAllText(
                path,
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture)
                    + " " + message + "\r\n",
                new UTF8Encoding(false));
        }
        catch (Exception)
        {
            // Logging must never abort an update; self-check reports unwritable logs.
        }
    }
}

internal sealed class HelperFailure : Exception
{
    public readonly string Code;

    public HelperFailure(string code, string message) : base(message)
    {
        Code = code;
    }
}

internal static class UpdaterHelper
{
    private const string HelperExeName = "PaperLibraryUpdater.exe";
    private const int DefaultWaitTimeoutSeconds = 60;

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            Console.OutputEncoding = new UTF8Encoding(false);
        }
        catch (Exception)
        {
            // 重定向/无控制台时可能不支持设置编码；日志仍用 UTF-8 写入。
        }
        Dictionary<string, string> options;
        List<string> positional;
        string error;
        if (!ParseArgs(args, out options, out positional, out error))
        {
            Console.Error.WriteLine("error: " + error);
            Usage();
            return 2;
        }

        string command = ResolveCommand(options, positional);
        string home;
        try
        {
            home = ResolveUpdaterHome(Opt(options, "updater-home"));
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine("error: " + ex.Message);
            return 5;
        }

        string logPath = Opt(options, "log");
        if (string.IsNullOrEmpty(logPath)) logPath = System.IO.Path.Combine(home, "logs", "updater.log");
        HelperLog log = new HelperLog(logPath);

        try
        {
            switch (command)
            {
                case "version":
                    return VersionCommand(home, log);
                case "self-check":
                    return SelfCheckCommand(options, home, log);
                case "hold":
                    return HoldCommand(options, positional, home, log);
                case "wait-pid":
                    return WaitPidCommand(options, positional, home, log);
                case "plan-replace":
                    return PlanReplaceCommand(options, log);
                case "backup-program":
                    return BackupProgramCommand(options, log);
                case "replace-program":
                    return ReplaceProgramCommand(options, log);
                case "backup-data":
                    return BackupDataCommand(options, log);
                case "install":
                    return InstallCommand(options, log);
                case "recover":
                    return RecoverCommand(options, log);
                case "help":
                    Usage();
                    return 0;
                default:
                    Console.Error.WriteLine("error: unknown command " + command);
                    Usage();
                    return 2;
            }
        }
        catch (HelperFailure failure)
        {
            Console.WriteLine(Obj(
                JB("ok", false),
                J("command", command),
                J("code", failure.Code),
                J("error", failure.Message)));
            log.Write(command + " failed: " + failure.Code + ": " + failure.Message);
            return 3;
        }
        catch (Exception ex)
        {
            log.Write("fatal: " + ex);
            Console.Error.WriteLine("error: " + ex.Message);
            return 5;
        }
    }

    // ---------- commands ----------

    private static int VersionCommand(string home, HelperLog log)
    {
        string exe = ExePath();
        string json = Obj(
            JB("ok", true),
            J("command", "version"),
            J("product", HelperInfo.Product),
            J("schema", (long)HelperInfo.Schema),
            J("helperVersion", HelperInfo.Version),
            J("helperTag", HelperInfo.Tag),
            J("exe", exe),
            J("exeSha256", FileSha256(exe)),
            J("updaterHome", home),
            J("pid", (long)Process.GetCurrentProcess().Id),
            J("clr", Environment.Version.ToString()),
            J("os", Environment.OSVersion.VersionString),
            J("arch", Environment.Is64BitProcess ? "x64" : "x86"),
            J("log", log.Path));
        Console.WriteLine(json);
        log.Write("version check: " + HelperInfo.Version + " exe=" + exe);
        return 0;
    }

    private static int SelfCheckCommand(Dictionary<string, string> options, string home, HelperLog log)
    {
        string exe = ExePath();
        string programRoot = Opt(options, "program-root");
        if (string.IsNullOrEmpty(programRoot))
            programRoot = Environment.GetEnvironmentVariable("PAPERLIB_PROGRAM_ROOT") ?? "";
        if (!string.IsNullOrEmpty(programRoot)) programRoot = System.IO.Path.GetFullPath(programRoot);

        List<string> checks = new List<string>();
        bool all = true;

        bool notAtRoot = true;
        string notAtRootDetail = "program-root not provided";
        if (!string.IsNullOrEmpty(programRoot))
        {
            string rootExe = System.IO.Path.Combine(programRoot, HelperExeName);
            notAtRoot = !string.Equals(exe, rootExe, StringComparison.OrdinalIgnoreCase);
            notAtRootDetail = "exe=" + exe + " packageRootExe=" + rootExe;
        }
        checks.Add(Check("helper-not-at-package-root", notAtRoot, notAtRootDetail));
        all &= notAtRoot;

        bool outsideRoot = true;
        string outsideDetail = "program-root not provided";
        if (!string.IsNullOrEmpty(programRoot))
        {
            outsideRoot = !IsWithin(programRoot, exe);
            outsideDetail = "programRoot=" + programRoot;
        }
        checks.Add(Check("helper-outside-program-root", outsideRoot, outsideDetail));
        all &= outsideRoot;

        bool insideHome = IsWithin(home, exe);
        checks.Add(Check("helper-inside-updater-home", insideHome, "updaterHome=" + home));
        all &= insideHome;

        bool rootExists = string.IsNullOrEmpty(programRoot) || Directory.Exists(programRoot);
        checks.Add(Check("program-root-exists", rootExists, "programRoot=" + (programRoot ?? "")));
        all &= rootExists;

        bool writable = false;
        string writeDetail = "";
        string probe = System.IO.Path.Combine(home, ".write-probe-" + Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(home);
            File.WriteAllText(probe, "ok", new UTF8Encoding(false));
            File.Delete(probe);
            writable = true;
            writeDetail = "probe=" + probe;
        }
        catch (Exception ex)
        {
            writeDetail = ex.GetType().Name + ": " + ex.Message;
        }
        checks.Add(Check("updater-home-writable", writable, writeDetail));
        all &= writable;

        log.Write("self-check probe");
        bool logWritable = File.Exists(log.Path);
        checks.Add(Check("log-writable", logWritable, "log=" + log.Path));
        all &= logWritable;

        string json = Obj(
            JB("ok", all),
            J("command", "self-check"),
            J("product", HelperInfo.Product),
            J("helperVersion", HelperInfo.Version),
            J("helperTag", HelperInfo.Tag),
            J("exe", exe),
            J("exeSha256", FileSha256(exe)),
            J("updaterHome", home),
            J("programRoot", programRoot ?? ""),
            J("pid", (long)Process.GetCurrentProcess().Id),
            Raw("checks", Arr(checks.ToArray())));
        Console.WriteLine(json);
        log.Write("self-check result: ok=" + all.ToString().ToLowerInvariant());
        return all ? 0 : 3;
    }

    private static int HoldCommand(Dictionary<string, string> options, List<string> positional,
                                   string home, HelperLog log)
    {
        string raw = Opt(options, "hold");
        if (string.IsNullOrEmpty(raw) && positional.Count > 1) raw = positional[1];
        int seconds;
        if (!int.TryParse(raw, NumberStyles.Integer, CultureInfo.InvariantCulture, out seconds)
            || seconds < 1 || seconds > 3600)
        {
            Console.Error.WriteLine("error: hold needs a whole number of seconds (1-3600)");
            return 2;
        }

        int pid = Process.GetCurrentProcess().Id;
        log.Write("hold start pid=" + pid + " seconds=" + seconds + " home=" + home);
        for (int i = 0; i < seconds; i++)
        {
            Thread.Sleep(1000);
            log.Write("hold alive pid=" + pid + " t=" + (i + 1));
        }
        log.Write("hold end pid=" + pid);
        Console.WriteLine(Obj(
            JB("ok", true),
            J("command", "hold"),
            J("pid", (long)pid),
            J("seconds", (long)seconds),
            J("log", log.Path)));
        return 0;
    }

    private static int WaitPidCommand(Dictionary<string, string> options, List<string> positional,
                                      string home, HelperLog log)
    {
        string rawPid = Opt(options, "wait-pid");
        if (string.IsNullOrEmpty(rawPid) && positional.Count > 1) rawPid = positional[1];
        int pid;
        if (!int.TryParse(rawPid, NumberStyles.Integer, CultureInfo.InvariantCulture, out pid) || pid <= 0)
        {
            Console.Error.WriteLine("error: wait-pid needs a valid process id");
            return 2;
        }

        int timeout = DefaultWaitTimeoutSeconds;
        string rawTimeout = Opt(options, "timeout");
        if (!string.IsNullOrEmpty(rawTimeout))
        {
            if (!int.TryParse(rawTimeout, NumberStyles.Integer, CultureInfo.InvariantCulture, out timeout)
                || timeout < 1 || timeout > 3600)
            {
                Console.Error.WriteLine("error: timeout must be 1-3600 seconds");
                return 2;
            }
        }

        Stopwatch watch = Stopwatch.StartNew();
        log.Write("wait-pid start pid=" + pid + " timeout=" + timeout);
        bool exited = false;
        while (watch.ElapsedMilliseconds < (long)timeout * 1000)
        {
            if (!ProcessExists(pid))
            {
                exited = true;
                break;
            }
            Thread.Sleep(250);
        }
        long waited = watch.ElapsedMilliseconds;
        log.Write("wait-pid result pid=" + pid + " exited=" + exited.ToString().ToLowerInvariant()
                  + " waitedMs=" + waited);
        if (!exited)
        {
            Console.WriteLine(Obj(
                JB("ok", false),
                J("command", "wait-pid"),
                J("pid", (long)pid),
                JB("exited", false),
                J("waitedMs", waited),
                J("error", "timeout"),
                J("log", log.Path)));
            return 4;
        }
        Console.WriteLine(Obj(
            JB("ok", true),
            J("command", "wait-pid"),
            J("pid", (long)pid),
            JB("exited", true),
            J("waitedMs", waited),
            J("log", log.Path)));
        return 0;
    }

    // ---------- data backup (P2-15) ----------
    //
    // The database and settings live in <program-root>\data. A full package replacement
    // never touches data/, but a failed launch/data migration must still be recoverable,
    // so the update flow copies library.db + settings into data\backups\update-<newVersion>\
    // before any program file is moved. Missing library.db aborts the install; optional
    // settings that do not exist are recorded as missing instead of failing.

    private const string DataBackupMarker = "data-backup.json";

    private static readonly string[] DataBackupNames = new string[]
    {
        "library.db", "library.db-wal", "library.db-shm",
        "prefs.json", "settings.json", "sources.json",
        "library.db-journal", "websearch.json",
    };

    private static int BackupDataCommand(Dictionary<string, string> options, HelperLog log,
                                         bool report = true, bool refresh = false)
    {
        string programRoot = RequiredPath(options, "program-root");
        string newVersion = Opt(options, "new-version");
        if (string.IsNullOrEmpty(newVersion))
            throw new HelperFailure("missing-argument", "--new-version is required");
        ValidateVersionText(newVersion, "new-version");
        ValidateProgramRoot(programRoot);

        int keep = 1;  // 产品确认（2026-11-16）：默认只保留最近一份，减少磁盘占用；--keep 可调
        string rawKeep = Opt(options, "keep");
        if (!string.IsNullOrEmpty(rawKeep)
            && (!int.TryParse(rawKeep, NumberStyles.Integer, CultureInfo.InvariantCulture, out keep)
                || keep < 1 || keep > 50))
            throw new HelperFailure("invalid-argument", "--keep must be 1-50");

        int retryMaxMs;
        int retryDelayMs;
        ReadFileRetryOptions(options, out retryMaxMs, out retryDelayMs);

        string dataDir = System.IO.Path.Combine(programRoot, "data");
        if (!Directory.Exists(dataDir))
            throw new HelperFailure("data-missing", "数据目录不存在：" + dataDir);
        string backupsRoot = System.IO.Path.Combine(dataDir, "backups");
        string finalDir = System.IO.Path.Combine(backupsRoot, "update-" + newVersion);
        if (!IsWithin(backupsRoot, finalDir))
            throw new HelperFailure("unsafe-path", "数据备份目录越界：" + finalDir);

        string dbPath = System.IO.Path.Combine(dataDir, "library.db");
        if (Directory.Exists(dbPath))
            throw new HelperFailure("data-backup-failed", "library.db 不是文件：" + dbPath);
        if (!File.Exists(dbPath))
            throw new HelperFailure("library-db-missing",
                "缺少 library.db，无法保证数据可恢复；中止安装：" + dbPath);

        if (Directory.Exists(finalDir))
        {
            string existingMarker = System.IO.Path.Combine(finalDir, DataBackupMarker);
            if (!refresh && IsDataBackupCurrent(dataDir, finalDir))
            {
                List<string> missingNow = new List<string>();
                foreach (string name in DataBackupNames)
                    if (!File.Exists(System.IO.Path.Combine(dataDir, name))) missingNow.Add(name);
                if (report) Console.WriteLine(Obj(
                    JB("ok", true),
                    J("command", "backup-data"),
                    J("programRoot", programRoot),
                    J("backupDir", finalDir),
                    J("newVersion", newVersion),
                    JB("idempotent", true),
                    Raw("missing", Arr(ToJsonStrings(missingNow))),
                    Raw("kept", Arr(ToJsonStrings(PruneDataBackups(backupsRoot, finalDir, keep))))));
                log.Write("backup-data: idempotent, existing backup matches source: " + finalDir);
                return 0;
            }
            if (!refresh && File.Exists(existingMarker))
                throw new HelperFailure("data-backup-exists",
                    "已存在内容不同的数据备份（不覆盖）：" + finalDir);
            if (!File.Exists(existingMarker))
                throw new HelperFailure("data-backup-exists", "数据备份目录已存在且缺少清单：" + finalDir);
        }

        Directory.CreateDirectory(backupsRoot);
        string tempDir = System.IO.Path.Combine(
            backupsRoot, "update-" + newVersion + ".tmp-" + Guid.NewGuid().ToString("N"));
        string previousDir = tempDir + "-previous";
        List<string> items = new List<string>();
        List<string> missing = new List<string>();
        long totalBytes = 0;
        try
        {
            Directory.CreateDirectory(tempDir);
            foreach (string name in DataBackupNames)
            {
                string source = System.IO.Path.Combine(dataDir, name);
                if (Directory.Exists(source))
                    throw new HelperFailure("data-backup-failed", "数据项不是文件：" + source);
                if (!File.Exists(source))
                {
                    missing.Add(name);
                    continue;
                }
                string target = System.IO.Path.Combine(tempDir, name);
                RetryIo(delegate { File.Copy(source, target, false); }, source, "backup-data",
                        retryMaxMs, retryDelayMs, log);
                string sourceHash = FileSha256(source);
                string targetHash = FileSha256(target);
                if (sourceHash.Length == 0 || sourceHash != targetHash)
                    throw new HelperFailure("data-backup-failed", "数据备份校验失败：" + name);
                long size = new FileInfo(target).Length;
                totalBytes += size;
                items.Add(Obj(J("name", name), J("bytes", size), J("sha256", sourceHash)));
            }

            string record = Obj(
                J("schema", 1L),
                J("product", HelperInfo.Product),
                J("newVersion", newVersion),
                J("programRoot", programRoot),
                J("backupDir", finalDir),
                J("files", (long)(DataBackupNames.Length - missing.Count)),
                J("bytes", totalBytes),
                JB("databaseIncluded", true),
                Raw("missing", Arr(ToJsonStrings(missing))),
                J("createdAt", NowIso()),
                Raw("items", Arr(items.ToArray())));
            WriteTextAtomic(System.IO.Path.Combine(tempDir, DataBackupMarker), record);

            if (refresh && Directory.Exists(finalDir))
                Directory.Move(finalDir, previousDir);
            try
            {
                RetryIo(delegate { Directory.Move(tempDir, finalDir); }, tempDir, "backup-data",
                        retryMaxMs, retryDelayMs, log);
            }
            catch
            {
                if (Directory.Exists(previousDir) && !Directory.Exists(finalDir))
                    Directory.Move(previousDir, finalDir);
                throw;
            }
            CleanupDir(previousDir);
        }
        catch (HelperFailure)
        {
            CleanupDir(tempDir);
            throw;
        }
        catch (Exception ex)
        {
            CleanupDir(tempDir);
            throw new HelperFailure("data-backup-failed", "数据备份失败：" + ex.Message);
        }

        List<string> kept = PruneDataBackups(backupsRoot, finalDir, keep);
        if (report) Console.WriteLine(Obj(
            JB("ok", true),
            J("command", "backup-data"),
            J("programRoot", programRoot),
            J("backupDir", finalDir),
            J("newVersion", newVersion),
            JB("idempotent", false),
            J("files", (long)(DataBackupNames.Length - missing.Count)),
            J("bytes", totalBytes),
            JB("databaseIncluded", true),
            Raw("missing", Arr(ToJsonStrings(missing))),
            Raw("kept", Arr(ToJsonStrings(kept))),
            Raw("items", Arr(items.ToArray()))));
        log.Write("backup-data: " + (DataBackupNames.Length - missing.Count) + " files / "
                  + totalBytes + " bytes -> " + finalDir + " missing=" + missing.Count);
        return 0;
    }

    private static bool IsDataBackupCurrent(string dataDir, string backupDir)
    {
        bool sawAny = false;
        foreach (string name in DataBackupNames)
        {
            string source = System.IO.Path.Combine(dataDir, name);
            string target = System.IO.Path.Combine(backupDir, name);
            if (File.Exists(source))
            {
                sawAny = true;
                if (!File.Exists(target)) return false;
                string sourceHash = FileSha256(source);
                if (sourceHash.Length == 0 || sourceHash != FileSha256(target)) return false;
            }
            else if (File.Exists(target))
            {
                return false;
            }
        }
        return sawAny;
    }

    private static Dictionary<string, object> ReadJsonObject(string path)
    {
        return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(
            File.ReadAllText(path, Encoding.UTF8));
    }

    private static Dictionary<string, string> ReadDataBackup(string backupDir, string version)
    {
        Dictionary<string, object> marker = ReadJsonObject(System.IO.Path.Combine(backupDir, DataBackupMarker));
        if (!object.Equals(marker["schema"], 1) || !object.Equals(marker["product"], HelperInfo.Product)
            || !object.Equals(marker["newVersion"], version))
            throw new InvalidDataException("Invalid data backup identity");

        HashSet<string> allowed = new HashSet<string>(DataBackupNames, StringComparer.Ordinal);
        Dictionary<string, string> files = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (object entry in (System.Collections.IList)marker["items"])
        {
            Dictionary<string, object> item = (Dictionary<string, object>)entry;
            string name = (string)item["name"];
            string hash = (string)item["sha256"];
            if (!allowed.Contains(name) || !Regex.IsMatch(hash, "^[0-9a-f]{64}$"))
                throw new InvalidDataException("Invalid data backup item: " + name);
            files.Add(name, hash);
        }
        foreach (object entry in (System.Collections.IList)marker["missing"])
        {
            string name = (string)entry;
            if (!allowed.Contains(name))
                throw new InvalidDataException("Invalid missing data backup item: " + name);
            files.Add(name, null);
        }

        if (!files.ContainsKey("library.db") || files["library.db"] == null
            || !files.ContainsKey("library.db-wal") || !files.ContainsKey("library.db-shm")
            || !files.ContainsKey("prefs.json") || !files.ContainsKey("settings.json")
            || !files.ContainsKey("sources.json"))
            throw new InvalidDataException("Incomplete database backup");
        return files;
    }

    private static void RestoreData(string programRoot, string version, HelperLog log)
    {
        ValidateVersionText(version, "version");
        string dataDir = System.IO.Path.Combine(programRoot, "data");
        string backupDir = System.IO.Path.Combine(dataDir, "backups", "update-" + version);
        Dictionary<string, string> files = ReadDataBackup(backupDir, version);
        string tempDir = System.IO.Path.Combine(dataDir, "updates", "restore-data");
        Directory.CreateDirectory(tempDir);
        try
        {
            // Verify and stage the whole snapshot before touching the live database.
            foreach (KeyValuePair<string, string> item in files)
            {
                string target = System.IO.Path.Combine(dataDir, item.Key);
                if (Directory.Exists(target) || IsFileLocked(target))
                    throw new IOException("Data file unavailable: " + target);
                if (item.Value == null) continue;
                string staged = System.IO.Path.Combine(tempDir, item.Key);
                File.Copy(System.IO.Path.Combine(backupDir, item.Key), staged, true);
                if (FileSha256(staged) != item.Value)
                    throw new InvalidDataException("Data backup hash mismatch: " + item.Key);
            }
            foreach (KeyValuePair<string, string> item in files)
            {
                string target = System.IO.Path.Combine(dataDir, item.Key);
                if (item.Value == null)
                {
                    File.Delete(target);
                    continue;
                }
                string staged = System.IO.Path.Combine(tempDir, item.Key);
                if (File.Exists(target)) File.Replace(staged, target, null);
                else File.Move(staged, target);
            }
            log.Write("data restored from " + backupDir);
        }
        finally
        {
            CleanupDir(tempDir);
        }
    }

    private static List<string> PruneDataBackups(string backupsRoot, string keepName, int keep)
    {
        List<string> candidates = new List<string>();
        List<string> kept = new List<string>();
        if (!Directory.Exists(backupsRoot)) return kept;
        foreach (string dir in Directory.GetDirectories(backupsRoot))
        {
            string name = System.IO.Path.GetFileName(dir);
            if (!name.StartsWith("update-", StringComparison.OrdinalIgnoreCase)) continue;
            if (name.IndexOf(".tmp-", StringComparison.OrdinalIgnoreCase) >= 0) continue;
            if (!IsVersionText(name.Substring("update-".Length))) continue;
            candidates.Add(dir);
        }
        candidates.Sort(delegate(string left, string right)
        {
            int[] a = VersionKey(System.IO.Path.GetFileName(left));
            int[] b = VersionKey(System.IO.Path.GetFileName(right));
            for (int i = 0; i < 3; i++)
            {
                int cmp = b[i].CompareTo(a[i]);
                if (cmp != 0) return cmp;
            }
            return string.Compare(left, right, StringComparison.OrdinalIgnoreCase);
        });
        if (!string.IsNullOrEmpty(keepName) && Directory.Exists(keepName)) kept.Add(keepName);
        foreach (string dir in candidates)
        {
            if (kept.Count >= keep) break;
            if (!kept.Contains(dir)) kept.Add(dir);
        }
        foreach (string dir in candidates)
        {
            if (kept.Contains(dir)) continue;
            try { Directory.Delete(dir, true); } catch (Exception) { }
        }
        return kept;
    }

    private static int[] VersionKey(string backupName)
    {
        string version = backupName.StartsWith("update-", StringComparison.OrdinalIgnoreCase)
            ? backupName.Substring("update-".Length) : backupName;
        string[] parts = version.Split('.');
        int[] key = new int[3];
        for (int i = 0; i < 3 && i < parts.Length; i++)
            int.TryParse(parts[i], NumberStyles.Integer, CultureInfo.InvariantCulture, out key[i]);
        return key;
    }

    private static bool IsVersionText(string value)
    {
        return !string.IsNullOrEmpty(value) && Regex.IsMatch(value, "^[0-9]+\\.[0-9]+\\.[0-9]+$");
    }

    private static void CleanupDir(string path)
    {
        try
        {
            if (Directory.Exists(path)) Directory.Delete(path, true);
        }
        catch (Exception)
        {
        }
    }

    // ---------- install: plan / backup / replace (P2-14) ----------
    //
    // Scope: everything in the package root is a program file EXCEPT the top-level
    // "data" directory, which belongs to the user and must never be moved, copied over
    // or deleted. The sequence is: move every non-data top-level entry into
    // <program-root>\data\updates\backups\app-<oldVersion>\, then copy the staging tree
    // into the now-empty package root, then verify key files and totals.

    private static int PlanReplaceCommand(Dictionary<string, string> options, HelperLog log)
    {
        string programRoot = RequiredPath(options, "program-root");
        string staging = RequiredPath(options, "staging");
        string newVersion = Opt(options, "new-version");
        string oldVersion = Opt(options, "old-version");
        ValidateProgramRoot(programRoot);
        string version = ValidateStaging(staging, programRoot, newVersion);
        Counts stagingCounts = CountTree(staging);
        List<string> topLevel = TopLevelNames(staging);

        string backupDir = "";
        if (!string.IsNullOrEmpty(oldVersion))
        {
            ValidateVersionText(oldVersion, "old-version");
            backupDir = System.IO.Path.Combine(programRoot, "data", "updates", "backups",
                                               "app-" + oldVersion);
            ValidateBackupPath(programRoot, backupDir);
        }

        Console.WriteLine(Obj(
            JB("ok", true),
            J("command", "plan-replace"),
            J("programRoot", programRoot),
            J("staging", staging),
            J("version", version),
            J("oldVersion", oldVersion),
            J("backupDir", backupDir),
            J("files", stagingCounts.Files),
            J("bytes", stagingCounts.Bytes),
            JB("dataExcluded", true),
            JB("stagingHasData", false),
            Raw("topLevel", Arr(ToJsonStrings(topLevel)))));
        log.Write("plan-replace: version=" + version + " files=" + stagingCounts.Files
                  + " bytes=" + stagingCounts.Bytes + " backup=" + backupDir);
        return 0;
    }

    private static int BackupProgramCommand(Dictionary<string, string> options, HelperLog log,
                                            bool report = true)
    {
        string programRoot = RequiredPath(options, "program-root");
        string backupDir = RequiredPath(options, "backup");
        string oldVersion = Opt(options, "old-version");
        ValidateProgramRoot(programRoot);
        ValidateBackupPath(programRoot, backupDir);

        if (Directory.Exists(backupDir) && Directory.GetFileSystemEntries(backupDir).Length > 0)
            throw new HelperFailure("backup-exists", "备份目录已存在且非空：" + backupDir);

        int retryMaxMs;
        int retryDelayMs;
        ReadFileRetryOptions(options, out retryMaxMs, out retryDelayMs);

        string dataDir = System.IO.Path.Combine(programRoot, "data");
        bool hadData = Directory.Exists(dataDir);
        List<string> moved = new List<string>();
        List<string> entries = new List<string>();
        long files = 0;
        long bytes = 0;

        try
        {
            Directory.CreateDirectory(backupDir);
            string[] roots = Directory.GetFileSystemEntries(programRoot);
            Array.Sort(roots, StringComparer.OrdinalIgnoreCase);
            foreach (string entry in roots)
            {
                string name = System.IO.Path.GetFileName(entry);
                if (string.Equals(name, "data", StringComparison.OrdinalIgnoreCase)) continue;
                Counts counts = CountTree(entry);
                files += counts.Files;
                bytes += counts.Bytes;
                string target = System.IO.Path.Combine(backupDir, name);
                if (Directory.Exists(entry))
                {
                    if (File.Exists(target) || Directory.Exists(target))
                        throw new HelperFailure("backup-target-exists", "备份目标已存在：" + target);
                    RetryIo(delegate { Directory.Move(entry, target); }, entry, "backup-program",
                            retryMaxMs, retryDelayMs, log);
                    entries.Add(Obj(J("name", name), J("type", "dir"),
                                    J("files", counts.Files), J("bytes", counts.Bytes)));
                }
                else
                {
                    if (File.Exists(target) || Directory.Exists(target))
                        throw new HelperFailure("backup-target-exists", "备份目标已存在：" + target);
                    RetryIo(delegate { File.Move(entry, target); }, entry, "backup-program",
                            retryMaxMs, retryDelayMs, log);
                    entries.Add(Obj(J("name", name), J("type", "file"),
                                    J("files", 1), J("bytes", counts.Bytes)));
                }
                moved.Add(name);
            }
        }
        catch (HelperFailure)
        {
            RollbackMoves(programRoot, backupDir, moved);
            throw;
        }
        catch (Exception ex)
        {
            RollbackMoves(programRoot, backupDir, moved);
            throw new HelperFailure("backup-failed", "移动程序文件失败：" + ex.Message);
        }

        bool dataPreserved = !hadData || Directory.Exists(dataDir);
        if (!dataPreserved)
        {
            RollbackMoves(programRoot, backupDir, moved);
            throw new HelperFailure("data-lost", "备份过程中 data/ 丢失，已回滚");
        }
        if (File.Exists(System.IO.Path.Combine(programRoot, "PaperLibrary.exe")))
            throw new HelperFailure("backup-failed", "程序根仍存在 PaperLibrary.exe，备份未完成");
        bool oldExeBackedUp = File.Exists(System.IO.Path.Combine(backupDir, "PaperLibrary.exe"));
        bool oldVersionBackedUp = File.Exists(System.IO.Path.Combine(backupDir, "version.json"));

        string now = NowIso();
        string record = Obj(
            J("schema", 1L),
            J("product", HelperInfo.Product),
            J("oldVersion", oldVersion),
            J("programRoot", programRoot),
            J("backupDir", backupDir),
            J("files", files),
            J("bytes", bytes),
            JB("dataExcluded", true),
            JB("dataPreserved", dataPreserved),
            JB("oldExeBackedUp", oldExeBackedUp),
            JB("oldVersionBackedUp", oldVersionBackedUp),
            J("createdAt", now),
            Raw("entries", Arr(entries.ToArray())));
        WriteTextAtomic(System.IO.Path.Combine(backupDir, "backup.json"), record);

        if (report) Console.WriteLine(Obj(
            JB("ok", true),
            J("command", "backup-program"),
            J("programRoot", programRoot),
            J("backupDir", backupDir),
            J("oldVersion", oldVersion),
            J("files", files),
            J("bytes", bytes),
            JB("dataPreserved", dataPreserved),
            JB("oldExeBackedUp", oldExeBackedUp),
            JB("oldVersionBackedUp", oldVersionBackedUp),
            Raw("entries", Arr(entries.ToArray()))));
        log.Write("backup-program: moved " + files + " files / " + bytes + " bytes to " + backupDir
                  + " dataPreserved=" + dataPreserved.ToString().ToLowerInvariant());
        return 0;
    }

    private static int ReplaceProgramCommand(Dictionary<string, string> options, HelperLog log,
                                             bool report = true)
    {
        string programRoot = RequiredPath(options, "program-root");
        string staging = RequiredPath(options, "staging");
        string newVersion = Opt(options, "new-version");
        ValidateProgramRoot(programRoot);
        string version = ValidateStaging(staging, programRoot, newVersion);

        int retryMaxMs;
        int retryDelayMs;
        ReadFileRetryOptions(options, out retryMaxMs, out retryDelayMs);

        string dataDir = System.IO.Path.Combine(programRoot, "data");
        bool hadData = Directory.Exists(dataDir);
        foreach (string entry in Directory.GetFileSystemEntries(programRoot))
        {
            string name = System.IO.Path.GetFileName(entry);
            if (string.Equals(name, "data", StringComparison.OrdinalIgnoreCase)) continue;
            throw new HelperFailure("program-root-not-empty",
                "程序根仍有未备份的程序文件：" + name + "（必须先执行 backup-program）");
        }

        Counts stagingCounts = CountTree(staging);
        long copiedFiles = 0;
        long copiedBytes = 0;
        try
        {
            foreach (string name in TopLevelNames(staging))
            {
                string source = System.IO.Path.Combine(staging, name);
                string target = System.IO.Path.Combine(programRoot, name);
                if (!IsWithin(programRoot, target))
                    throw new HelperFailure("unsafe-path", "替换目标越界：" + target);
                if (File.Exists(target) || Directory.Exists(target))
                    throw new HelperFailure("target-exists", "替换目标已存在：" + target);
                if (Directory.Exists(source))
                    CopyDirectory(source, target, ref copiedFiles, ref copiedBytes,
                                  retryMaxMs, retryDelayMs, log);
                else
                {
                    RetryIo(delegate { File.Copy(source, target, false); }, source, "replace",
                            retryMaxMs, retryDelayMs, log);
                    copiedFiles++;
                    copiedBytes += new FileInfo(target).Length;
                }
            }
        }
        catch (HelperFailure)
        {
            throw;
        }
        catch (Exception ex)
        {
            throw new HelperFailure("replace-failed", "复制 staging 失败：" + ex.Message);
        }

        bool dataPreserved = !hadData || Directory.Exists(dataDir);
        if (!dataPreserved)
            throw new HelperFailure("data-lost", "替换过程中 data/ 丢失");

        string exePath = System.IO.Path.Combine(programRoot, "PaperLibrary.exe");
        string versionPath = System.IO.Path.Combine(programRoot, "version.json");
        if (!File.Exists(exePath))
            throw new HelperFailure("verify-failed", "替换后缺少 PaperLibrary.exe");
        if (!File.Exists(versionPath))
            throw new HelperFailure("verify-failed", "替换后缺少 version.json");
        string copiedVersion = ReadJsonString(versionPath, "version");
        if (copiedVersion != version)
            throw new HelperFailure("verify-failed",
                "替换后 version.json 版本不符：" + copiedVersion + " != " + version);

        Counts programCounts = CountProgramTree(programRoot);
        if (programCounts.Files != stagingCounts.Files || programCounts.Bytes != stagingCounts.Bytes)
            throw new HelperFailure("verify-failed",
                "替换后文件数/字节数与 staging 不一致：" + programCounts.Files + "/" + programCounts.Bytes
                + " != " + stagingCounts.Files + "/" + stagingCounts.Bytes);

        if (report) Console.WriteLine(Obj(
            JB("ok", true),
            J("command", "replace-program"),
            J("programRoot", programRoot),
            J("staging", staging),
            J("version", version),
            J("files", copiedFiles),
            J("bytes", copiedBytes),
            JB("dataPreserved", dataPreserved),
            Raw("topLevel", Arr(ToJsonStrings(TopLevelNames(staging))))));
        log.Write("replace-program: copied " + copiedFiles + " files / " + copiedBytes
                  + " bytes; version=" + version + " dataPreserved=" + dataPreserved.ToString().ToLowerInvariant());
        return 0;
    }

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

        Match ok = Regex.Match(body, "\"ok\"\\s*:\\s*(true|false)");
        if (!ok.Success) return "health-json";
        if (ok.Groups[1].Value != "true") return "health-not-ok";

        Match app = Regex.Match(body, "\"app\"\\s*:\\s*\"([^\"]*)\"");
        if (!app.Success) return "health-json";
        if (app.Groups[1].Value != "literature-vault") return "health-app";

        if (!TryHttpGet("http://127.0.0.1:" + port + "/api/version", requestMs, out body, out error))
            return error;

        Match product = Regex.Match(body, "\"product\"\\s*:\\s*\"([^\"]*)\"");
        if (!product.Success) return "health-json";
        if (product.Groups[1].Value != "PaperLibrary") return "health-version";

        Match version = Regex.Match(body, "\"version\"\\s*:\\s*\"([^\"]*)\"");
        if (!version.Success) return "health-json";
        if (version.Groups[1].Value != targetVersion) return "health-version";

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

    // ---------- journal / install lock / recovery (P2-16) ----------

    private const int InstallLockStaleMinutes = 30;

    private static void SleepPhase(int phaseDelayMs)
    {
        if (phaseDelayMs > 0) Thread.Sleep(phaseDelayMs);
    }

    private static void MaybeExitAfterPhase(string configured, string phase, HelperLog log)
    {
        if (!string.IsNullOrEmpty(configured)
            && string.Equals(configured, phase, StringComparison.OrdinalIgnoreCase))
        {
            log.Write("test fault injection: forced exit after phase " + phase);
            Environment.Exit(77);
        }
    }

    private static bool IsInstallLockStale(string path, HelperLog log)
    {
        string text;
        try
        {
            text = File.ReadAllText(path, Encoding.UTF8);
        }
        catch (FileNotFoundException)
        {
            return true;
        }
        catch (IOException)
        {
            return false;
        }
        long pid = RegexLong(text, "pid", 0);
        if (pid <= 0) return true;
        if (!ProcessExists((int)pid)) return true;
        string created = RegexString(text, "createdAt");
        DateTime createdUtc;
        if (!DateTime.TryParseExact(created, "yyyy-MM-ddTHH:mm:ssZ",
                                    CultureInfo.InvariantCulture,
                                    DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                                    out createdUtc))
            return false;
        return (DateTime.UtcNow - createdUtc).TotalMinutes > InstallLockStaleMinutes;
    }

    private static string RegexString(string text, string field)
    {
        Match match = Regex.Match(text, "\"" + Regex.Escape(field) + "\"\\s*:\\s*\"([^\"]*)\"");
        return match.Success ? match.Groups[1].Value : "";
    }

    private static long RegexLong(string text, string field, long fallback)
    {
        Match match = Regex.Match(text, "\"" + Regex.Escape(field) + "\"\\s*:\\s*(-?[0-9]+)");
        long value;
        if (match.Success && long.TryParse(match.Groups[1].Value, NumberStyles.Integer,
                                           CultureInfo.InvariantCulture, out value))
            return value;
        return fallback;
    }

    private static bool HasProgramBackupEntries(string backupDir)
    {
        if (!Directory.Exists(backupDir)) return false;
        foreach (string entry in Directory.GetFileSystemEntries(backupDir))
        {
            if (!string.Equals(System.IO.Path.GetFileName(entry), "backup.json",
                               StringComparison.OrdinalIgnoreCase))
                return true;
        }
        return false;
    }

    private static void PruneProgramBackups(string programRoot, string keepDir, HelperLog log)
    {
        string root = System.IO.Path.Combine(programRoot, "data", "updates", "backups");
        if (!Directory.Exists(root)) return;
        string keep = System.IO.Path.GetFileName(System.IO.Path.GetFullPath(keepDir));
        foreach (string dir in Directory.GetDirectories(root))
        {
            string name = System.IO.Path.GetFileName(dir);
            if (!name.StartsWith("app-", StringComparison.OrdinalIgnoreCase)) continue;
            if (string.Equals(name, keep, StringComparison.OrdinalIgnoreCase)) continue;
            try
            {
                Directory.Delete(dir, true);
                log.Write("pruned old program backup: " + name);
            }
            catch (Exception ex)
            {
                log.Write("prune program backup failed: " + name + ": " + ex.Message);
            }
        }
    }

    private static int RecoverCommand(Dictionary<string, string> options, HelperLog log)
    {
        string programRoot = RequiredPath(options, "program-root");
        ValidateProgramRoot(programRoot);
        string updatesDir = System.IO.Path.Combine(programRoot, "data", "updates");
        Directory.CreateDirectory(updatesDir);
        string lockPath = System.IO.Path.Combine(updatesDir, "install.lock");
        string journalPath = System.IO.Path.Combine(updatesDir, "journal.json");
        string statePath = System.IO.Path.Combine(updatesDir, "state.json");

        if (File.Exists(lockPath))
        {
            if (!IsInstallLockStale(lockPath, log))
                throw new HelperFailure("install-locked", "安装仍在进行，不能恢复：" + lockPath);
            log.Write("stale install lock removed: " + lockPath);
            TryDelete(lockPath);
        }

        if (!File.Exists(journalPath))
        {
            Console.WriteLine(Obj(
                JB("ok", true),
                J("command", "recover"),
                JB("recovered", false),
                J("reason", "no-journal"),
                J("log", log.Path)));
            return 0;
        }

        Dictionary<string, object> journal;
        try
        {
            journal = ReadJsonObject(journalPath);
        }
        catch (Exception)
        {
            throw new HelperFailure("recovery-failed", "journal.json 损坏：" + journalPath);
        }
        string phase = (string)journal["phase"];
        object failureCode;
        if (phase == "failed" && journal.TryGetValue("code", out failureCode)
            && (object.Equals(failureCode, "recovery-failed") || object.Equals(failureCode, "rollback-failed")))
            throw new HelperFailure("recovery-failed", "上次恢复未完成，请检查日志与保留的备份：" + journalPath);
        if (phase == "success" || phase == "failed")
        {
            Console.WriteLine(Obj(
                JB("ok", true),
                J("command", "recover"),
                JB("recovered", false),
                J("reason", "journal-" + phase),
                J("phase", phase),
                J("log", log.Path)));
            return 0;
        }

        string version = (string)journal["version"];
        string fromVersion = (string)journal["fromVersion"];
        string staging = (string)journal["staging"];
        string dataBackupDir = (string)journal["dataBackupDir"];
        string backupDir = (string)journal["backupDir"];
        string startedAt = (string)journal["startedAt"];
        int launcherPid = (int)journal["launcherPid"];
        int launchedPid = (int)journal["launchedPid"];
        int port = journal.ContainsKey("port") ? (int)journal["port"] : 4321;

        log.Write("recover start: phase=" + phase + " version=" + version
                  + " launchedPid=" + launchedPid + " backup=" + backupDir);

        bool restored = false;
        int oldPid = 0;
        string reason = "recovered from phase " + phase;
        CreateInstallLock(lockPath, version, fromVersion, launcherPid, log);
        try
        {
            ValidateBackupPath(programRoot, backupDir);
            if (phase == "launch")
            {
                StopCandidate(programRoot, launchedPid, port, log);
                RestoreData(programRoot, version, log);
            }
            if (phase == "replace" || phase == "verify" || phase == "launch")
            {
                RestoreProgram(programRoot, backupDir, false, log);
                restored = true;
            }
            else if (phase == "backup" && HasProgramBackupEntries(backupDir))
            {
                RestoreProgram(programRoot, backupDir, true, log);
                restored = true;
            }
            else if (phase != "prepare" && phase != "backup")
            {
                throw new InvalidDataException("Unknown install phase: " + phase);
            }
            if (!File.Exists(System.IO.Path.Combine(programRoot, "PaperLibrary.exe")))
                throw new InvalidDataException("Restored launcher is missing");
            WriteState(statePath, "failed", version, fromVersion, staging, dataBackupDir, backupDir,
                       0, 0, restored, true, 0, 0, startedAt, "recovered", reason, log.Path, log);
            WriteJournal(journalPath, "failed", version, fromVersion, backupDir, dataBackupDir,
                         staging, launcherPid, startedAt, launchedPid, "recovered", reason, log, port);
        }
        catch (Exception failure)
        {
            try
            {
                WriteState(statePath, "failed", version, fromVersion, staging, dataBackupDir,
                           backupDir, 0, 0, restored, false, 0, 0, startedAt,
                           "recovery-failed", failure.Message, log.Path, log);
            }
            catch (Exception ex)
            {
                log.Write("write recovery-failed state failed: " + ex.Message);
            }
            Console.WriteLine(Obj(
                JB("ok", false),
                J("command", "recover"),
                J("code", "recovery-failed"),
                J("error", failure.Message),
                J("phase", phase),
                J("backupDir", backupDir),
                J("log", log.Path)));
            return 3;
        }
        finally
        {
            RemoveInstallLock(lockPath);
        }

        if (File.Exists(System.IO.Path.Combine(programRoot, "PaperLibrary.exe")))
        {
            try
            {
                Process oldProc = LaunchApplication(programRoot, log);
                oldPid = oldProc.Id;
            }
            catch (Exception ex)
            {
                log.Write("recover relaunch failed: " + ex.Message);
            }
        }

        PruneProgramBackups(programRoot, backupDir, log);
        WriteState(statePath, "failed", version, fromVersion, staging, dataBackupDir, backupDir,
                   0, oldPid, restored, true, 0, 0, startedAt, "recovered", reason, log.Path, log);

        Console.WriteLine(Obj(
            JB("ok", true),
            J("command", "recover"),
            JB("recovered", true),
            J("phase", phase),
            J("version", version),
            J("fromVersion", fromVersion),
            JB("restored", restored),
            J("oldLaunchedPid", (long)oldPid),
            J("backupDir", backupDir),
            J("log", log.Path)));
        log.Write("recover success: phase=" + phase + " restored=" + restored.ToString().ToLowerInvariant()
                  + " oldLaunchedPid=" + oldPid);
        return 0;
    }

    private static string RequiredPath(Dictionary<string, string> options, string name)
    {
        string value = Opt(options, name);
        if (string.IsNullOrEmpty(value))
            throw new HelperFailure("missing-argument", "--" + name + " is required");
        try
        {
            return System.IO.Path.GetFullPath(value);
        }
        catch (Exception ex)
        {
            throw new HelperFailure("invalid-path", "--" + name + " is invalid: " + ex.Message);
        }
    }

    private static void ValidateProgramRoot(string programRoot)
    {
        if (!Directory.Exists(programRoot))
            throw new HelperFailure("program-root-missing", "程序目录不存在：" + programRoot);
    }

    private static string ValidateStaging(string staging, string programRoot, string newVersion)
    {
        if (!Directory.Exists(staging))
            throw new HelperFailure("staging-missing", "staging 目录不存在：" + staging);
        // 正式流程的 staging 位于 <program-root>\data\updates\<version>\staging（P2-01/P2-04），
        // 属于程序目录内的 data/，替换时必须允许；只拒绝直接放在程序目录或其它程序文件区的 staging。
        string dataUpdatesRoot = System.IO.Path.Combine(programRoot, "data", "updates");
        bool insideProgram = IsWithin(programRoot, staging)
            && !string.Equals(System.IO.Path.GetFullPath(staging),
                              System.IO.Path.GetFullPath(programRoot), StringComparison.OrdinalIgnoreCase);
        if (insideProgram)
        {
            bool underDataUpdates = IsWithin(dataUpdatesRoot, staging);
            bool underBackups = IsWithin(System.IO.Path.Combine(dataUpdatesRoot, "backups"), staging);
            if (!underDataUpdates || underBackups)
                throw new HelperFailure("staging-inside-program",
                    "staging 只能位于 data\\updates\\ 下（或程序目录外）：" + staging);
        }
        if (string.Equals(System.IO.Path.GetFullPath(staging),
                          System.IO.Path.GetFullPath(programRoot), StringComparison.OrdinalIgnoreCase))
            throw new HelperFailure("staging-inside-program", "staging 不能等于程序目录：" + staging);
        if (!File.Exists(System.IO.Path.Combine(staging, "PaperLibrary.exe")))
            throw new HelperFailure("missing-exe", "staging 缺少 PaperLibrary.exe");
        if (!File.Exists(System.IO.Path.Combine(staging, "version.json")))
            throw new HelperFailure("missing-version", "staging 缺少 version.json");
        if (Directory.Exists(System.IO.Path.Combine(staging, "data")))
            throw new HelperFailure("data-directory", "staging 不得包含 data/（用户数据）");
        string version = ReadJsonString(System.IO.Path.Combine(staging, "version.json"), "version");
        ValidateVersionText(version, "staging version.json version");
        if (!string.IsNullOrEmpty(newVersion) && version != newVersion)
            throw new HelperFailure("version-mismatch",
                "staging 版本与目标版本不符：" + version + " != " + newVersion);
        return version;
    }

    private static void ValidateBackupPath(string programRoot, string backupDir)
    {
        string backupsRoot = System.IO.Path.Combine(programRoot, "data", "updates", "backups");
        string full = System.IO.Path.GetFullPath(backupDir);
        string root = System.IO.Path.GetFullPath(backupsRoot);
        if (!IsWithin(root, full)
            || string.Equals(full.TrimEnd(System.IO.Path.DirectorySeparatorChar),
                             root.TrimEnd(System.IO.Path.DirectorySeparatorChar),
                             StringComparison.OrdinalIgnoreCase))
            throw new HelperFailure("backup-path-invalid",
                "备份目录必须位于 data\\updates\\backups\\ 下：" + backupDir);
    }

    private static void ValidateVersionText(string value, string field)
    {
        if (string.IsNullOrEmpty(value) || !Regex.IsMatch(value, "^[0-9]+\\.[0-9]+\\.[0-9]+$"))
            throw new HelperFailure("invalid-version", field + " 必须是 x.y.z：" + (value ?? ""));
    }

    private static void RollbackMoves(string programRoot, string backupDir, List<string> moved)
    {
        for (int i = moved.Count - 1; i >= 0; i--)
        {
            try
            {
                string source = System.IO.Path.Combine(backupDir, moved[i]);
                string target = System.IO.Path.Combine(programRoot, moved[i]);
                if (Directory.Exists(source) && !Directory.Exists(target)) Directory.Move(source, target);
                else if (File.Exists(source) && !File.Exists(target)) File.Move(source, target);
            }
            catch (Exception)
            {
            }
        }
    }

    private static List<string> TopLevelNames(string root)
    {
        List<string> names = new List<string>();
        foreach (string entry in Directory.GetFileSystemEntries(root))
            names.Add(System.IO.Path.GetFileName(entry));
        names.Sort(StringComparer.OrdinalIgnoreCase);
        return names;
    }

    private static string[] ToJsonStrings(List<string> values)
    {
        string[] result = new string[values.Count];
        for (int i = 0; i < values.Count; i++) result[i] = Quote(values[i]);
        return result;
    }

    private sealed class Counts
    {
        public long Files;
        public long Bytes;
    }

    private static Counts CountTree(string path)
    {
        Counts counts = new Counts();
        if (File.Exists(path))
        {
            counts.Files = 1;
            counts.Bytes = new FileInfo(path).Length;
            return counts;
        }
        if (Directory.Exists(path))
        {
            foreach (string file in Directory.GetFiles(path))
            {
                counts.Files++;
                counts.Bytes += new FileInfo(file).Length;
            }
            foreach (string dir in Directory.GetDirectories(path))
            {
                Counts child = CountTree(dir);
                counts.Files += child.Files;
                counts.Bytes += child.Bytes;
            }
        }
        return counts;
    }

    // 程序文件统计：跳过顶层 data/（用户数据，不属于替换范围）
    private static Counts CountProgramTree(string programRoot)
    {
        Counts counts = new Counts();
        foreach (string entry in Directory.GetFileSystemEntries(programRoot))
        {
            if (string.Equals(System.IO.Path.GetFileName(entry), "data", StringComparison.OrdinalIgnoreCase))
                continue;
            Counts child = CountTree(entry);
            counts.Files += child.Files;
            counts.Bytes += child.Bytes;
        }
        return counts;
    }

    private static void ReadFileRetryOptions(Dictionary<string, string> options,
                                             out int retryMaxMs, out int retryDelayMs)
    {
        retryMaxMs = IntOption(options, "file-retry-max-ms", DefaultFileRetryMaxMs, 0, 600000);
        retryDelayMs = IntOption(options, "file-retry-delay-ms", DefaultFileRetryDelayMs, 10, 5000);
    }

    private static bool IsRetryableLock(Exception ex)
    {
        if (!(ex is IOException)) return false;
        int code = ex.HResult & 0xFFFF;
        return code == 32 || code == 33;  // ERROR_SHARING_VIOLATION / ERROR_LOCK_VIOLATION
    }

    private static void RetryIo(Action action, string path, string phase,
                                int retryMaxMs, int retryDelayMs, HelperLog log)
    {
        Stopwatch watch = Stopwatch.StartNew();
        int attempt = 0;
        while (true)
        {
            try
            {
                action();
                if (attempt > 0)
                    log.Write("retry " + phase + " recovered after " + attempt + " retries: " + path);
                return;
            }
            catch (Exception ex)
            {
                if (!IsRetryableLock(ex)) throw;
                attempt++;
                if (watch.ElapsedMilliseconds >= retryMaxMs)
                    throw new HelperFailure("file-lock-timeout",
                        phase + " 文件被占用超过 " + (retryMaxMs / 1000.0).ToString("0.0", CultureInfo.InvariantCulture)
                        + " 秒：" + path + "（" + ex.Message + "）");
                log.Write("retry " + phase + " attempt=" + attempt + " wait=" + retryDelayMs
                          + "ms path=" + path + " error=" + ex.Message);
                Thread.Sleep(retryDelayMs);
            }
        }
    }

    private static void CopyDirectory(string source, string target, ref long files, ref long bytes,
                                      int retryMaxMs, int retryDelayMs, HelperLog log)
    {
        Directory.CreateDirectory(target);
        foreach (string file in Directory.GetFiles(source))
        {
            string destination = System.IO.Path.Combine(target, System.IO.Path.GetFileName(file));
            RetryIo(delegate { File.Copy(file, destination, false); }, file, "replace",
                    retryMaxMs, retryDelayMs, log);
            files++;
            bytes += new FileInfo(destination).Length;
        }
        foreach (string dir in Directory.GetDirectories(source))
        {
            CopyDirectory(dir, System.IO.Path.Combine(target, System.IO.Path.GetFileName(dir)),
                          ref files, ref bytes, retryMaxMs, retryDelayMs, log);
        }
    }

    private static string ReadJsonString(string path, string field)
    {
        string text;
        try
        {
            text = File.ReadAllText(path, Encoding.UTF8);
        }
        catch (Exception ex)
        {
            throw new HelperFailure("read-failed", "无法读取 " + path + ": " + ex.Message);
        }
        Match match = Regex.Match(text, "\"" + Regex.Escape(field) + "\"\\s*:\\s*\"([^\"]*)\"");
        if (!match.Success)
            throw new HelperFailure("invalid-json", path + " 缺少字符串字段 " + field);
        return match.Groups[1].Value;
    }

    private static void WriteTextAtomic(string path, string text)
    {
        string dir = System.IO.Path.GetDirectoryName(path);
        if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
        string temp = path + ".tmp-" + Guid.NewGuid().ToString("N");
        try
        {
            File.WriteAllText(temp, text, new UTF8Encoding(false));
            for (int attempt = 0; ; attempt++)
            {
                try
                {
                    if (File.Exists(path)) File.Replace(temp, path, null);
                    else File.Move(temp, path);
                    break;
                }
                catch (IOException)
                {
                    if (attempt == 9) throw;
                    // Python readers briefly deny delete sharing on Windows.
                    Thread.Sleep(50);
                }
            }
        }
        finally
        {
            TryDelete(temp);
        }
    }

    private static string NowIso()
    {
        return DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ", CultureInfo.InvariantCulture);
    }

    // ---------- infrastructure ----------

    private static string ResolveCommand(Dictionary<string, string> options, List<string> positional)
    {
        if (options.ContainsKey("version")) return "version";
        if (options.ContainsKey("self-check")) return "self-check";
        if (options.ContainsKey("help")) return "help";
        if (options.ContainsKey("hold")) return "hold";
        if (options.ContainsKey("wait-pid")) return "wait-pid";
        if (options.ContainsKey("plan-replace")) return "plan-replace";
        if (options.ContainsKey("backup-program")) return "backup-program";
        if (options.ContainsKey("replace-program")) return "replace-program";
        if (options.ContainsKey("backup-data")) return "backup-data";
        if (options.ContainsKey("install")) return "install";
        if (options.ContainsKey("recover")) return "recover";
        if (positional.Count > 0) return positional[0].ToLowerInvariant();
        return "help";
    }

    private static bool ParseArgs(string[] args, out Dictionary<string, string> options,
                                  out List<string> positional, out string error)
    {
        options = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        positional = new List<string>();
        error = "";
        for (int i = 0; i < args.Length; i++)
        {
            string arg = args[i] ?? "";
            if (arg == "--")
            {
                for (i++; i < args.Length; i++) positional.Add(args[i]);
                break;
            }
            if (arg.StartsWith("--", StringComparison.Ordinal))
            {
                string name = arg.Substring(2);
                string value = "";
                int eq = name.IndexOf('=');
                if (eq >= 0)
                {
                    value = name.Substring(eq + 1);
                    name = name.Substring(0, eq);
                }
                if (name.Length == 0)
                {
                    error = "empty option name";
                    return false;
                }
                if (value.Length == 0 && i + 1 < args.Length && !(args[i + 1] ?? "").StartsWith("--", StringComparison.Ordinal))
                {
                    value = args[i + 1];
                    i++;
                }
                if (options.ContainsKey(name))
                {
                    error = "duplicate option --" + name;
                    return false;
                }
                options[name] = value;
            }
            else if (arg.StartsWith("-", StringComparison.Ordinal) && arg.Length > 1)
            {
                error = "unknown option " + arg;
                return false;
            }
            else
            {
                positional.Add(arg);
            }
        }
        return true;
    }

    private static string Opt(Dictionary<string, string> options, string name)
    {
        string value;
        return options.TryGetValue(name, out value) && value != null ? value : "";
    }

    private static string ResolveUpdaterHome(string option)
    {
        string value = option;
        if (string.IsNullOrEmpty(value))
            value = Environment.GetEnvironmentVariable("PAPERLIB_UPDATER_HOME") ?? "";
        if (string.IsNullOrEmpty(value))
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            if (string.IsNullOrEmpty(local))
                throw new InvalidOperationException("LOCALAPPDATA is not available");
            value = System.IO.Path.Combine(local, "PaperLibrary", "updater");
        }
        return System.IO.Path.GetFullPath(value);
    }

    private static string ExePath()
    {
        try
        {
            return System.IO.Path.GetFullPath(Assembly.GetExecutingAssembly().Location);
        }
        catch (Exception)
        {
            return Process.GetCurrentProcess().MainModule.FileName;
        }
    }

    private static bool ProcessExists(int pid)
    {
        try
        {
            using (Process process = Process.GetProcessById(pid))
            {
                return !process.HasExited;
            }
        }
        catch (ArgumentException)
        {
            return false;
        }
        catch (InvalidOperationException)
        {
            return false;
        }
    }

    private static bool IsWithin(string basePath, string path)
    {
        if (string.IsNullOrEmpty(basePath) || string.IsNullOrEmpty(path)) return false;
        try
        {
            string b = System.IO.Path.GetFullPath(basePath)
                .TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar);
            string p = System.IO.Path.GetFullPath(path)
                .TrimEnd(System.IO.Path.DirectorySeparatorChar, System.IO.Path.AltDirectorySeparatorChar);
            if (string.Equals(b, p, StringComparison.OrdinalIgnoreCase)) return true;
            return p.StartsWith(b + System.IO.Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase);
        }
        catch (Exception)
        {
            return false;
        }
    }

    private static string FileSha256(string path)
    {
        try
        {
            using (FileStream stream = File.OpenRead(path))
            {
                using (SHA256 sha = SHA256.Create())
                {
                    byte[] hash = sha.ComputeHash(stream);
                    StringBuilder text = new StringBuilder(hash.Length * 2);
                    for (int i = 0; i < hash.Length; i++)
                        text.Append(hash[i].ToString("x2", CultureInfo.InvariantCulture));
                    return text.ToString();
                }
            }
        }
        catch (Exception)
        {
            return "";
        }
    }

    private static string Check(string name, bool ok, string detail)
    {
        return Obj(J("name", name), JB("ok", ok), J("detail", detail ?? ""));
    }

    // ---------- JSON ----------

    private static string Obj(params string[] parts)
    {
        return "{" + string.Join(",", parts) + "}";
    }

    private static string Arr(params string[] parts)
    {
        return "[" + string.Join(",", parts) + "]";
    }

    private static string J(string name, string value)
    {
        return Quote(name) + ":" + Quote(value);
    }

    private static string J(string name, long value)
    {
        return Quote(name) + ":" + value.ToString(CultureInfo.InvariantCulture);
    }

    private static string JB(string name, bool value)
    {
        return Quote(name) + ":" + (value ? "true" : "false");
    }

    private static string Raw(string name, string raw)
    {
        return Quote(name) + ":" + raw;
    }

    private static string Quote(string value)
    {
        if (value == null) return "null";
        StringBuilder text = new StringBuilder(value.Length + 2);
        text.Append('"');
        for (int i = 0; i < value.Length; i++)
        {
            char c = value[i];
            switch (c)
            {
                case '\\': text.Append("\\\\"); break;
                case '"': text.Append("\\\""); break;
                case '\r': text.Append("\\r"); break;
                case '\n': text.Append("\\n"); break;
                case '\t': text.Append("\\t"); break;
                default:
                    if (c < ' ') text.Append("\\u").Append(((int)c).ToString("x4", CultureInfo.InvariantCulture));
                    else text.Append(c);
                    break;
            }
        }
        text.Append('"');
        return text.ToString();
    }

    private static void Usage()
    {
        Console.Error.WriteLine("PaperLibrary Updater Helper " + HelperInfo.Version + " (" + HelperInfo.Tag + ")");
        Console.Error.WriteLine("usage: PaperLibraryUpdater.exe <command> [options]");
        Console.Error.WriteLine("  version                         print helper identity as JSON");
        Console.Error.WriteLine("  self-check [--program-root DIR] diagnose deployment (exit 3 when a check fails)");
        Console.Error.WriteLine("  hold <seconds>                  stay alive (used to prove out-of-package operation)");
        Console.Error.WriteLine("  wait-pid <pid> [--timeout SEC]  wait for a process to exit (exit 4 on timeout)");
        Console.Error.WriteLine("  plan-replace --program-root DIR --staging DIR [--new-version X] [--old-version Y]");
        Console.Error.WriteLine("                                  validate staging and print the replacement plan");
        Console.Error.WriteLine("  backup-program --program-root DIR --backup DIR [--old-version Y]");
        Console.Error.WriteLine("                                  move program files (not data/) into the backup dir");
        Console.Error.WriteLine("  replace-program --program-root DIR --staging DIR [--new-version X]");
        Console.Error.WriteLine("                                  copy staging over the empty program root");
        Console.Error.WriteLine("  backup-data --program-root DIR --new-version X [--keep N]");
        Console.Error.WriteLine("                                  copy library.db + settings into data/backups/update-X (default keep 1)");
        Console.Error.WriteLine("  install --program-root DIR --staging DIR [--old-version Y] [--new-version X] [--launcher-pid PID] [--backup DIR] [--port 4321]");
        Console.Error.WriteLine("                                  full install: wait, backup data/program, replace, launch, health, rollback");
        Console.Error.WriteLine("  recover --program-root DIR     resume an interrupted install from journal.json");
        Console.Error.WriteLine("options:");
        Console.Error.WriteLine("  --updater-home DIR   override %LOCALAPPDATA%\\PaperLibrary\\updater");
        Console.Error.WriteLine("  --log FILE           log file (default <updater-home>\\logs\\updater.log)");
        Console.Error.WriteLine("  --help               show this help");
    }
}
