// PaperLibrary Updater Helper -- journal, install lock and crash recovery (P2-16).
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
        Dictionary<string, object> lockObj = TryParseJsonObject(text);
        long pid = JsonLongOrDefault(lockObj, "pid", 0);
        if (pid <= 0) return true;
        if (!ProcessExists((int)pid)) return true;
        string created;
        if (!TryJsonString(lockObj, "createdAt", out created))
            return false;
        DateTime createdUtc;
        if (!DateTime.TryParseExact(created, "yyyy-MM-ddTHH:mm:ssZ",
                                    CultureInfo.InvariantCulture,
                                    DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal,
                                    out createdUtc))
            return false;
        return (DateTime.UtcNow - createdUtc).TotalMinutes > InstallLockStaleMinutes;
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

}
