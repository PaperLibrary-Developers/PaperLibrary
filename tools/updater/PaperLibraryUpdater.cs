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
using System.Reflection;
using System.Security.Cryptography;
using System.Text;
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
                case "help":
                    Usage();
                    return 0;
                default:
                    Console.Error.WriteLine("error: unknown command " + command);
                    Usage();
                    return 2;
            }
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

    // ---------- infrastructure ----------

    private static string ResolveCommand(Dictionary<string, string> options, List<string> positional)
    {
        if (options.ContainsKey("version")) return "version";
        if (options.ContainsKey("self-check")) return "self-check";
        if (options.ContainsKey("help")) return "help";
        if (options.ContainsKey("hold")) return "hold";
        if (options.ContainsKey("wait-pid")) return "wait-pid";
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
        Console.Error.WriteLine("options:");
        Console.Error.WriteLine("  --updater-home DIR   override %LOCALAPPDATA%\\PaperLibrary\\updater");
        Console.Error.WriteLine("  --log FILE           log file (default <updater-home>\\logs\\updater.log)");
        Console.Error.WriteLine("  --help               show this help");
    }
}
