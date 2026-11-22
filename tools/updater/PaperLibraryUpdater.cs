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

internal static partial class UpdaterHelper
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

}
