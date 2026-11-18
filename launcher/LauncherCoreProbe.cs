using System;
using System.Text;

// 测试探针：编译 LauncherCore.cs + 本文件，验证启动器纯逻辑（不启动 WinForms/WebView2）。
public static class LauncherCoreProbe
{
    public static int Main(string[] args)
    {
        if (args.Length == 0) { Console.WriteLine("{\"ok\":false,\"code\":\"no-command\"}"); return 2; }
        string command = args[0];
        if (command == "source" && args.Length >= 2)
        {
            Console.WriteLine("{\"allowed\":" + (LauncherCore.IsAllowedSource(args[1]) ? "true" : "false") + "}");
            return 0;
        }
        if (command == "request" && args.Length >= 2)
        {
            string error;
            InstallRequest request = LauncherCore.ParseInstallRequest(args[1], out error);
            if (request == null)
            {
                Console.WriteLine("{\"ok\":false,\"code\":\"" + LauncherCore.Escape(error) + "\"}");
                return 3;
            }
            Console.WriteLine("{\"ok\":true,\"requestId\":\"" + LauncherCore.Escape(request.RequestId)
                + "\",\"version\":\"" + LauncherCore.Escape(request.Version) + "\"}");
            return 0;
        }
        if (command == "result" && args.Length >= 5)
        {
            bool ok = args[2] == "true" || args[2] == "1";
            Console.WriteLine(LauncherCore.InstallResultJson(args[1], ok, args[3], args[4]));
            return 0;
        }
        if (command == "precheck" && args.Length >= 3)
        {
            long freeOverride = -1;
            if (args.Length >= 4) long.TryParse(args[3], out freeOverride);
            string programFilesOverride = args.Length >= 5 ? args[4] : null;
            PrecheckResult result = LauncherCore.Precheck(args[1], args[2], freeOverride, programFilesOverride);
            Console.WriteLine(PrecheckJson(result));
            return result.Ok ? 0 : 3;
        }
        if (command == "startup" && args.Length >= 3)
        {
            StartupDecision decision = LauncherCore.DecideStartup(args[1], args[2]);
            Console.WriteLine("{\"action\":\"" + LauncherCore.Escape(decision.Action)
                + "\",\"code\":\"" + LauncherCore.Escape(decision.Code)
                + "\",\"message\":\"" + LauncherCore.Escape(decision.Message)
                + "\",\"path\":\"" + LauncherCore.Escape(decision.Path) + "\"}");
            return 0;
        }
        if (command == "lock" && args.Length >= 2)
        {
            Console.WriteLine("{\"active\":" + (LauncherCore.IsInstallLockActive(args[1]) ? "true" : "false") + "}");
            return 0;
        }
        if (command == "update-probe" && args.Length >= 3)
        {
            string state = LauncherCore.UpdateProbeState(args[1], int.Parse(args[2]));
            Console.WriteLine("{\"state\":\"" + LauncherCore.Escape(state) + "\"}");
            return 0;
        }
        if (command == "helper" && args.Length >= 2)
        {
            string code;
            string message;
            string path = LauncherCore.FindHelper(args[1], out code, out message);
            Console.WriteLine("{\"ok\":" + (path.Length > 0 ? "true" : "false")
                + ",\"code\":\"" + LauncherCore.Escape(code)
                + "\",\"message\":\"" + LauncherCore.Escape(message)
                + "\",\"path\":\"" + LauncherCore.Escape(path) + "\"}");
            return path.Length > 0 ? 0 : 3;
        }
        if (command == "build-args" && args.Length >= 5)
        {
            PrecheckResult pre = new PrecheckResult();
            pre.Version = args[1];
            pre.FromVersion = args[2];
            pre.StagingPath = args[3];
            pre.BackupPath = args[4];
            string log = args.Length >= 6 ? args[5] : "";
            string[] installArgs = LauncherCore.BuildInstallArgs(pre, args.Length >= 7 ? args[6] : "", 1234, log);
            Console.WriteLine(LauncherCore.JoinArgs(installArgs));
            return 0;
        }
        Console.WriteLine("{\"ok\":false,\"code\":\"bad-command\"}");
        return 2;
    }

    static string PrecheckJson(PrecheckResult result)
    {
        StringBuilder sb = new StringBuilder();
        sb.Append("{\"ok\":").Append(result.Ok ? "true" : "false");
        sb.Append(",\"code\":\"").Append(LauncherCore.Escape(result.Code));
        sb.Append("\",\"message\":\"").Append(LauncherCore.Escape(result.Message));
        sb.Append("\",\"version\":\"").Append(LauncherCore.Escape(result.Version));
        sb.Append("\",\"fromVersion\":\"").Append(LauncherCore.Escape(result.FromVersion));
        sb.Append("\",\"stagingPath\":\"").Append(LauncherCore.Escape(result.StagingPath));
        sb.Append("\",\"backupPath\":\"").Append(LauncherCore.Escape(result.BackupPath));
        sb.Append("\",\"helperPath\":\"").Append(LauncherCore.Escape(result.HelperPath));
        sb.Append("\",\"logPath\":\"").Append(LauncherCore.Escape(result.LogPath));
        sb.Append("\"}");
        return sb.ToString();
    }
}
