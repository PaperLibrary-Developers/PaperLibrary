// PaperLibrary Updater Helper -- user data backup and restore (P2-15).
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

}
