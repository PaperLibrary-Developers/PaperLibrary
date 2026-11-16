# P2-10R 启动器宿主消息桥、预检与启动判定

日期：2026-11-17　阶段 2D / P2-10R　状态：已完成并核验

## 1. 实现

- 启动器 C# 源码从 `tools/make_launcher.ps1` 的内嵌 here-string 抽到
  `launcher/PaperLibraryLauncher.cs`（行为不变），新增纯逻辑
  `launcher/LauncherCore.cs`；构建脚本读取两个源文件并编译，便于自动化测试。
- WebView2 `WebMessageReceived` 桥：
  - 只接受本应用页面（http、127.0.0.1/localhost、4321、无 userinfo）；
  - 解析 `paperlibrary:install.request`，校验 requestId/version；
  - 回 `paperlibrary:install.result`（requestId 原样、ok/code/message）；
  - P2-10R 只做协议与预检，预检通过后回 `install-not-wired`，P2-11R 接入启动。
- apply/helper 预检（基础版）：
  - apply schema/version/stagingPath；staging 必须在 data 内；
  - staging 的 `PaperLibrary.exe`/`version.json` 存在且版本一致；
  - 包外 helper `current.json` 的 exe 路径/存在/SHA-256；
  - 程序目录不在 Program Files、可写；
  - 失败回 ok:false，不关闭窗口。
- 启动判定（在启动 Python 服务前）：
  1. 未完成 journal → 调 helper `recover`，恢复后退出；
  2. 有效 install.lock → 提示“正在安装，请稍候”，不启动第二个应用；过期锁清理；
  3. 有 apply → 提示“有已准备好的更新”，不静默安装；
  4. state 成功/失败 → 启动提示（失败附 code/日志）；
  5. 正常启动。
- 日志记录 source 拒绝、requestId/precheck code、startup decision、recover 退出码/结果。

## 2. 测试（test/test_launcher_core.py，7 条）

编译 `LauncherCore.cs + LauncherCoreProbe.cs` 为测试探针，覆盖：

- 允许来源/拒绝 https、外域；
- 请求协议合法/非法 type、非法 requestId；result JSON；
- apply/helper 预检成功；apply 缺失、版本不符、staging 越界/缺失、exe 缺失；
- helper 缺失/哈希不符；
- 启动判定：apply→prompt-apply、journal→recover、状态成功/失败提示；
- install.lock 活跃→install-in-progress、死 PID→clean-stale-lock；
- state 成功/失败提示含版本/code/日志。

命令：

    python -m unittest test.test_launcher_core -v
    Ran 7 tests in 1.478s
    OK

启动器构建：

    powershell -NoProfile -ExecutionPolicy Bypass -File tools/make_launcher.ps1 -OutDir <temp>
    launcher built: ...\PaperLibrary.exe

## 3. 验收对照

| P2-10R 验收项 | 结果 | 证据 |
|---|---|---|
| helper 缺失/apply 无效不关闭 | PASS | 预检返回 apply-missing/helper-path/helper-sha256；host 回 ok:false，不调用关闭 |
| 启动有 apply 只提示 | PASS | DecideStartup=prompt-apply；host MessageBox 提示，不自动安装 |
| 未完成 journal 先恢复 | PASS | DecideStartup=recover；host 调 helper recover，恢复后退出 |
| install.lock 拦截安装中双开 | PASS | active→install-in-progress；host 提示并退出 |
| 成功/失败启动提示 | PASS | notice-success/notice-failed，失败附 code/日志 |
| 日志完整 | PASS | launcher.log 记录 startup decision/precheck/result/recover |
| 无静默安装 | PASS | apply 仅提示；无自动调用 helper install 的代码路径 |

## 4. 测试与契约

- 新增测试：7；全量待本轮确认（目标 706 tests OK，1 skip）；
- 契约：`shared/contracts/update-apply.md` 新增第 5 节消息协议、第 6 节启动判定；
- 构建：外部源码编译通过；P2-11R 将把 `install-not-wired` 替换为真实启动。

## 5. 遗留

- 真实 GUI 的 CDP/截图端到端验收留到 P2-19；本步已覆盖协议/预检/判定逻辑与启动器编译。
- 服务关闭后的 helper 启动时序由 P2-11R 实现。
