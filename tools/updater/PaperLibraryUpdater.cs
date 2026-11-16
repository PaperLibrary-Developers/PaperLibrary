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
    };

    private static int BackupDataCommand(Dictionary<string, string> options, HelperLog log)
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
            if (IsDataBackupCurrent(dataDir, finalDir))
            {
                List<string> missingNow = new List<string>();
                foreach (string name in DataBackupNames)
                    if (!File.Exists(System.IO.Path.Combine(dataDir, name))) missingNow.Add(name);
                Console.WriteLine(Obj(
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
            if (File.Exists(existingMarker))
                throw new HelperFailure("data-backup-exists",
                    "已存在内容不同的数据备份（不覆盖）：" + finalDir);
            throw new HelperFailure("data-backup-exists", "数据备份目录已存在且非空：" + finalDir);
        }

        Directory.CreateDirectory(backupsRoot);
        string tempDir = System.IO.Path.Combine(
            backupsRoot, "update-" + newVersion + ".tmp-" + Guid.NewGuid().ToString("N"));
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
                File.Copy(source, target, false);
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

            if (Directory.Exists(finalDir))
                throw new HelperFailure("data-backup-exists", "数据备份目录已存在：" + finalDir);
            Directory.Move(tempDir, finalDir);
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
        Console.WriteLine(Obj(
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

    private static int BackupProgramCommand(Dictionary<string, string> options, HelperLog log)
    {
        string programRoot = RequiredPath(options, "program-root");
        string backupDir = RequiredPath(options, "backup");
        string oldVersion = Opt(options, "old-version");
        ValidateProgramRoot(programRoot);
        ValidateBackupPath(programRoot, backupDir);

        if (Directory.Exists(backupDir) && Directory.GetFileSystemEntries(backupDir).Length > 0)
            throw new HelperFailure("backup-exists", "备份目录已存在且非空：" + backupDir);

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
                    Directory.Move(entry, target);
                    entries.Add(Obj(J("name", name), J("type", "dir"),
                                    J("files", counts.Files), J("bytes", counts.Bytes)));
                }
                else
                {
                    if (File.Exists(target) || Directory.Exists(target))
                        throw new HelperFailure("backup-target-exists", "备份目标已存在：" + target);
                    File.Move(entry, target);
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

        Console.WriteLine(Obj(
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

    private static int ReplaceProgramCommand(Dictionary<string, string> options, HelperLog log)
    {
        string programRoot = RequiredPath(options, "program-root");
        string staging = RequiredPath(options, "staging");
        string newVersion = Opt(options, "new-version");
        ValidateProgramRoot(programRoot);
        string version = ValidateStaging(staging, programRoot, newVersion);

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
                    CopyDirectory(source, target, ref copiedFiles, ref copiedBytes);
                else
                {
                    File.Copy(source, target, false);
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

        Console.WriteLine(Obj(
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

        string updatesDir = System.IO.Path.Combine(programRoot, "data", "updates");
        Directory.CreateDirectory(updatesDir);
        string lockPath = System.IO.Path.Combine(updatesDir, "install.lock");
        string journalPath = System.IO.Path.Combine(updatesDir, "journal.json");
        string statePath = System.IO.Path.Combine(updatesDir, "state.json");
        string applyPath = System.IO.Path.Combine(updatesDir, "apply.json");

        if (File.Exists(lockPath))
            throw new HelperFailure("install-locked", "已有安装任务在进行（install.lock 存在）：" + lockPath);

        string startedAt = NowIso();
        bool lockCreated = false;
        try
        {
            CreateInstallLock(lockPath, newVersion, oldVersion, launcherPid, log);
            lockCreated = true;
            WriteJournal(journalPath, "prepare", newVersion, oldVersion, backupDir, staging, "", "", log);

            Stopwatch total = Stopwatch.StartNew();
            bool programBackupDone = false;
            bool replaceDone = false;
            Process newProc = null;
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

                int rc = RunSelf("backup-data", log, "--program-root", programRoot,
                                 "--new-version", newVersion, "--log", log.Path);
                if (rc != 0)
                    throw new HelperFailure("data-backup-failed", "数据备份失败（backup-data 退出码 " + rc + "）");
                CheckBudget(total, totalSeconds, "backup-data");

                rc = RunSelf("backup-program", log, "--program-root", programRoot,
                             "--backup", backupDir, "--old-version", oldVersion, "--log", log.Path);
                if (rc != 0)
                    throw new HelperFailure("program-backup-failed",
                        "程序文件备份失败（backup-program 退出码 " + rc + "）");
                programBackupDone = true;
                CheckBudget(total, totalSeconds, "backup-program");

                rc = RunSelf("replace-program", log, "--program-root", programRoot,
                             "--staging", staging, "--new-version", newVersion, "--log", log.Path);
                if (rc != 0)
                    throw new HelperFailure("replace-failed", "程序文件替换失败（replace-program 退出码 " + rc + "）");
                replaceDone = true;
                VerifyInstalledProgram(programRoot, newVersion);
                CheckBudget(total, totalSeconds, "verify");

                newProc = LaunchApplication(programRoot, log);
                CheckBudget(total, totalSeconds, "launch");

                string healthDetail;
                if (!WaitForHealthy(port, newVersion, healthTimeout, healthInterval, healthRequest,
                                    healthSuccesses, total, totalSeconds, log, out healthDetail))
                    throw new HelperFailure("health-failed", "新版本健康检查失败：" + healthDetail);

                int healthSeconds = (int)(total.ElapsedMilliseconds / 1000);
                WriteJournal(journalPath, "success", newVersion, oldVersion, backupDir, staging, "", "", log);
                WriteState(statePath, "success", newVersion, oldVersion, backupDir, staging,
                           newProc.Id, false, 0, healthSeconds, startedAt, "", "", log.Path, log);
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
            catch (HelperFailure failure)
            {
                string failCode = failure.Code;
                string failMessage = failure.Message;
                log.Write("install failed: " + failCode + ": " + failMessage);

                bool restored = false;
                int oldPid = 0;

                if (newProc != null)
                {
                    try { RequestShutdown(port); }
                    catch (Exception) { }
                    bool exited = false;
                    try { exited = newProc.WaitForExit(5000); }
                    catch (Exception) { }
                    if (!exited) KillProcessTree(newProc.Id);
                }

                if (programBackupDone || replaceDone)
                {
                    string restoreError;
                    restored = TryRestoreProgram(programRoot, backupDir, log, out restoreError);
                    if (!restored) log.Write("restore failed: " + restoreError);
                }

                // 只有程序文件已被搬动（备份/替换开始）才回滚并重启旧版；
                // 等待/端口/锁/数据备份失败发生在改动之前，直接中止，文件保持原样。
                bool shouldRelaunch = (programBackupDone || replaceDone)
                    && File.Exists(System.IO.Path.Combine(programRoot, "PaperLibrary.exe"));
                if (shouldRelaunch)
                {
                    try
                    {
                        Process oldProc = LaunchApplication(programRoot, log);
                        oldPid = oldProc.Id;
                    }
                    catch (Exception ex)
                    {
                        log.Write("old version relaunch failed: " + ex.Message);
                    }
                }

                try
                {
                    WriteJournal(journalPath, "failed", newVersion, oldVersion, backupDir, staging,
                                 failCode, failMessage, log);
                    WriteState(statePath, "failed", newVersion, oldVersion, backupDir, staging,
                               0, restored, oldPid, 0, startedAt, failCode, failMessage, log.Path, log);
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

    private static int RunSelf(string command, HelperLog log, params string[] args)
    {
        string exe = ExePath();
        StringBuilder commandLine = new StringBuilder(command);
        foreach (string arg in args)
        {
            if (arg == null) continue;
            commandLine.Append(' ').Append(QuoteArgument(arg));
        }
        ProcessStartInfo info = new ProcessStartInfo(exe, commandLine.ToString());
        info.UseShellExecute = false;
        info.CreateNoWindow = true;
        info.RedirectStandardOutput = true;
        info.RedirectStandardError = true;
        info.WorkingDirectory = System.IO.Path.GetDirectoryName(exe);
        try
        {
            using (Process process = Process.Start(info))
            {
                string stdout = process.StandardOutput.ReadToEnd();
                string stderr = process.StandardError.ReadToEnd();
                process.WaitForExit();
                if (!string.IsNullOrEmpty(stdout.Trim())) log.Write("[" + command + "] " + stdout.Trim());
                if (!string.IsNullOrEmpty(stderr.Trim())) log.Write("[" + command + "] stderr: " + stderr.Trim());
                return process.ExitCode;
            }
        }
        catch (Exception ex)
        {
            throw new HelperFailure("subcommand-failed", "无法执行 " + command + "：" + ex.Message);
        }
    }

    private static string QuoteArgument(string value)
    {
        if (value.Length > 0 && value.IndexOf(' ') < 0 && value.IndexOf('"') < 0) return value;
        return "\"" + value.Replace("\"", "\\\"") + "\"";
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

    private static string HttpGet(string url, int timeoutMs)
    {
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
                if ((int)response.StatusCode != 200) return null;
                using (Stream stream = response.GetResponseStream())
                {
                    if (stream == null) return null;
                    using (StreamReader reader = new StreamReader(stream, Encoding.UTF8))
                    {
                        char[] buffer = new char[65536];
                        int read = reader.Read(buffer, 0, buffer.Length);
                        return read <= 0 ? "" : new string(buffer, 0, read);
                    }
                }
            }
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static bool HealthCheck(int port, string targetVersion, int requestMs, out string detail)
    {
        detail = "";
        string health = HttpGet("http://127.0.0.1:" + port + "/api/health", requestMs);
        if (health == null) { detail = "health-unreachable"; return false; }
        if (!Regex.IsMatch(health, "\"ok\"\\s*:\\s*true")) { detail = "health-not-ok"; return false; }
        if (!Regex.IsMatch(health, "\"app\"\\s*:\\s*\"literature-vault\""))
        { detail = "health-app-mismatch"; return false; }

        string versionJson = HttpGet("http://127.0.0.1:" + port + "/api/version", requestMs);
        if (versionJson == null) { detail = "version-unreachable"; return false; }
        if (!Regex.IsMatch(versionJson, "\"product\"\\s*:\\s*\"PaperLibrary\""))
        { detail = "version-product-mismatch"; return false; }
        Match match = Regex.Match(versionJson, "\"version\"\\s*:\\s*\"([^\"]*)\"");
        if (!match.Success || match.Groups[1].Value != targetVersion)
        { detail = "version-mismatch"; return false; }
        detail = "ok";
        return true;
    }

    private static bool WaitForHealthy(int port, string targetVersion, int timeoutSeconds,
                                       int intervalMs, int requestMs, int successes,
                                       Stopwatch total, int totalSeconds, HelperLog log,
                                       out string lastDetail)
    {
        lastDetail = "";
        Stopwatch watch = Stopwatch.StartNew();
        int streak = 0;
        int attempts = 0;
        while (watch.ElapsedMilliseconds < (long)timeoutSeconds * 1000)
        {
            CheckBudget(total, totalSeconds, "health");
            attempts++;
            string detail;
            if (HealthCheck(port, targetVersion, requestMs, out detail))
            {
                streak++;
                log.Write("health attempt " + attempts + ": ok (streak " + streak + "/" + successes + ")");
                if (streak >= successes) return true;
            }
            else
            {
                streak = 0;
                lastDetail = detail;
                log.Write("health attempt " + attempts + ": " + detail);
            }
            Thread.Sleep(intervalMs);
        }
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
                                     string backupDir, string staging, string code, string error,
                                     HelperLog log)
    {
        string record = Obj(
            J("schema", 1L),
            J("product", HelperInfo.Product),
            J("phase", phase),
            J("version", version),
            J("fromVersion", fromVersion),
            J("backupDir", backupDir),
            J("staging", staging),
            J("updatedAt", NowIso()),
            J("code", code),
            J("error", error));
        WriteTextAtomic(path, record);
        log.Write("journal: " + phase);
    }

    private static void WriteState(string path, string phase, string version, string fromVersion,
                                   string backupDir, string staging, int launchedPid, bool restored,
                                   int oldLaunchedPid, int healthSeconds, string startedAt,
                                   string code, string error, string logPath, HelperLog log)
    {
        string record = Obj(
            J("schema", 1L),
            J("product", HelperInfo.Product),
            J("phase", phase),
            J("version", version),
            J("fromVersion", fromVersion),
            J("backupDir", backupDir),
            J("staging", staging),
            J("launchedPid", (long)launchedPid),
            JB("restored", restored),
            J("oldLaunchedPid", (long)oldLaunchedPid),
            J("healthSeconds", (long)healthSeconds),
            J("startedAt", startedAt),
            J("finishedAt", NowIso()),
            J("code", code),
            J("error", error),
            J("log", logPath));
        WriteTextAtomic(path, record);
        log.Write("state: " + phase);
    }

    private static bool TryRestoreProgram(string programRoot, string backupDir, HelperLog log,
                                          out string error)
    {
        error = "";
        try
        {
            if (!Directory.Exists(backupDir))
            {
                error = "backup-missing";
                return false;
            }
            string dataDir = System.IO.Path.Combine(programRoot, "data");
            foreach (string entry in Directory.GetFileSystemEntries(programRoot))
            {
                if (string.Equals(System.IO.Path.GetFileName(entry), "data", StringComparison.OrdinalIgnoreCase))
                    continue;
                if (Directory.Exists(entry)) Directory.Delete(entry, true);
                else File.Delete(entry);
            }
            foreach (string entry in Directory.GetFileSystemEntries(backupDir))
            {
                string name = System.IO.Path.GetFileName(entry);
                if (string.Equals(name, "backup.json", StringComparison.OrdinalIgnoreCase)) continue;
                string target = System.IO.Path.Combine(programRoot, name);
                if (Directory.Exists(entry)) Directory.Move(entry, target);
                else File.Move(entry, target);
            }
            if (!File.Exists(System.IO.Path.Combine(programRoot, "PaperLibrary.exe")))
            {
                error = "restore-missing-exe";
                return false;
            }
            if (!Directory.Exists(dataDir))
            {
                error = "restore-data-missing";
                return false;
            }
            log.Write("program restored from " + backupDir);
            return true;
        }
        catch (Exception ex)
        {
            error = ex.GetType().Name + ": " + ex.Message;
            return false;
        }
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

    private static void CopyDirectory(string source, string target, ref long files, ref long bytes)
    {
        Directory.CreateDirectory(target);
        foreach (string file in Directory.GetFiles(source))
        {
            string destination = System.IO.Path.Combine(target, System.IO.Path.GetFileName(file));
            File.Copy(file, destination, false);
            files++;
            bytes += new FileInfo(destination).Length;
        }
        foreach (string dir in Directory.GetDirectories(source))
        {
            CopyDirectory(dir, System.IO.Path.Combine(target, System.IO.Path.GetFileName(dir)),
                          ref files, ref bytes);
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
        File.WriteAllText(temp, text, new UTF8Encoding(false));
        if (File.Exists(path)) File.Replace(temp, path, null);
        else File.Move(temp, path);
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
        Console.Error.WriteLine("options:");
        Console.Error.WriteLine("  --updater-home DIR   override %LOCALAPPDATA%\\PaperLibrary\\updater");
        Console.Error.WriteLine("  --log FILE           log file (default <updater-home>\\logs\\updater.log)");
        Console.Error.WriteLine("  --help               show this help");
    }
}
