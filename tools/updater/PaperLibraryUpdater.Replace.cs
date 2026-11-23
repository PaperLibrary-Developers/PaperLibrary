// PaperLibrary Updater Helper -- program replacement plan, backup and copy (P2-14).
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

}
