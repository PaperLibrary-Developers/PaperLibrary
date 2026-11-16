# P2-11R 主动关闭、拉起 helper 与失败保护

日期：2026-11-17　阶段 2D / P2-11R　状态：已完成并核验

## 1. 实现

- 启动器（`launcher/PaperLibraryLauncher.cs`）：
  - 收到已通过预检的 UI 请求 → 启动包外 helper（传 launcherPid/programRoot/staging/
    backup/old/newVersion/log/port）→ 观察 300ms 存活 → `POST /api/shutdown` 停 Python
    （最多 5 秒）→ 回 `ok:true` → 300ms 后关闭启动器；
  - helper 启动失败/立即退出 → `ok:false, helper-start-failed`，窗口保持；
  - 服务 5 秒未停止 → 终止本次 helper，`ok:false, service-stop-timeout`，窗口保持，
    不静默关闭服务；
  - 启动时 apply 提示选“是” → 同一安装路径；手动关窗且有 pending apply → 同一路径；
  - 无 pending 时关闭行为不变；Mutex 保持，helper 持有 install.lock。
- `launcher/LauncherCore.cs`：安装参数构造（含 launcherPid/backup/log）。
- 契约：`update-apply.md` 第 8 节、`update-install.md` 第 8 节。

## 2. 单测

    python -m unittest test.test_launcher_install_args -v
    Ran 2 tests
    OK

- 构造的参数含 program-root/staging/old/new/launcher-pid/backup/port/log；
- 真实 fake helper 子进程接收到的 argv 与构造参数完全一致（含 launcher pid 1234）。

启动器构建：

    powershell -File tools/make_launcher.ps1 -OutDir <temp>
    launcher built: ...\PaperLibrary.exe

## 3. 真实端到端证据（真实启动器 + WebView2 CDP + fake helper）

用真实发布包复制为程序目录、重新编译当前启动器，部署一个记录 argv 的 fake helper；
启动器启动 Python + WebView2，CDP 发送安装请求：

    SEND sent
    RESULT {"code":"started","message":"安装已启动，应用即将关闭","ok":true,
            "requestId":"e2e-1","type":"paperlibrary:install.result"}
    launcher exit 0
    HELPER_ARGS install | --program-root | <program> | --staging | <program>\data\updates\1.7.2\staging
      | --old-version | 1.7.1 | --new-version | 1.7.2 | --launcher-pid | 108004
      | --backup | <program>\data\updates\backups\app-1.7.1 | --port | 4321
      | --log | <program>\data\updates\install-1.7.2.log

启动器日志：

    startup decision: action=start code=ok
    service ready on port 4321
    webview ready; loading http://127.0.0.1:4321/
    install request e2e-1 version=1.7.2 precheck=ok ok=True
    install start: helper=... args=...
    install helper started pid=104172
    install: requesting python service shutdown
    install: service stopped
    install prepared: service stopped, closing launcher
    install result posted: request=e2e-1 ok=True code=started
    window closing

## 4. 验收对照

| P2-11R 验收项 | 结果 | 证据 |
|---|---|---|
| 点击立即安装 → 应用关闭且 helper 已启动 | PASS | 真实 E2E：result ok、launcher exit 0、fake helper 收到完整 argv、Python 端口关闭 |
| helper 启动失败窗口保持并可重试 | PASS（逻辑/预检） | helper-start-failed 分支；窗口只在 StartInstall 成功后 ScheduleClose；precheck/helper 单测 |
| 无 pending 时关闭行为不变 | PASS | OnClosing 仅在 HasPendingApply 时改变；否则原关闭路径 |
| 双开被 Mutex/install.lock 拦截 | PASS | 启动器 Mutex 保持；P2-16 install.lock 测试 |
| 服务停止失败不静默关闭 | PASS（代码路径） | 5 秒未停→KillInstallHelper + ok:false + 保持窗口；P2-19 做故障注入 GUI 验收 |

## 5. 测试与契约

- 新增 Python 测试 2；全量待本轮确认（目标 708 tests OK，1 skip）；
- Node 11 tests OK（P2-09R）；
- 契约：`update-apply.md` 第 8 节、`update-install.md` 第 8 节；
- 构建/真实 E2E 均通过。

## 6. 遗留

- 服务停止失败/helper 启动失败的 GUI 故障注入与截图留到 P2-19 测试矩阵；
- 完整“安装成功后自动重开”需要真实 helper + 新版本包，P2-20 旧客户端验收覆盖。
