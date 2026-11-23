// PaperLibrary Updater Helper -- validation, file I/O retry, CLI parsing and JSON helpers.
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

    private static Dictionary<string, object> TryParseJsonObject(string text)
    {
        if (string.IsNullOrEmpty(text)) return null;
        try
        {
            string trimmed = text.Trim();
            if (trimmed.Length < 2 || trimmed[0] != '{' || trimmed[trimmed.Length - 1] != '}')
                return null;
            return new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(trimmed);
        }
        catch (Exception)
        {
            return null;
        }
    }

    private static bool TryJsonString(IDictionary<string, object> obj, string field, out string value)
    {
        value = "";
        if (obj == null || string.IsNullOrEmpty(field)) return false;
        object raw;
        if (!obj.TryGetValue(field, out raw) || !(raw is string)) return false;
        value = (string)raw;
        return true;
    }

    private static bool TryJsonBool(IDictionary<string, object> obj, string field, out bool value)
    {
        value = false;
        if (obj == null || string.IsNullOrEmpty(field)) return false;
        object raw;
        if (!obj.TryGetValue(field, out raw) || !(raw is bool)) return false;
        value = (bool)raw;
        return true;
    }

    private static long JsonLongOrDefault(IDictionary<string, object> obj, string field, long fallback)
    {
        if (obj == null || string.IsNullOrEmpty(field)) return fallback;
        object raw;
        if (!obj.TryGetValue(field, out raw) || raw == null) return fallback;
        if (raw is int) return (int)raw;
        if (raw is long) return (long)raw;
        if (raw is short) return (short)raw;
        if (raw is byte) return (byte)raw;
        if (raw is decimal)
        {
            decimal d = (decimal)raw;
            if (decimal.Truncate(d) == d && d >= long.MinValue && d <= long.MaxValue)
                return (long)d;
        }
        return fallback;
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
        Dictionary<string, object> obj = TryParseJsonObject(text);
        string value;
        if (!TryJsonString(obj, field, out value))
            throw new HelperFailure("invalid-json", path + " 缺少字符串字段 " + field);
        return value;
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
