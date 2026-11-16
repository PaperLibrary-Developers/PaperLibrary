# P2-13 Helper 完整安装时序

日期：2026-11-16　阶段 2E-3 / P2-13　状态：已完成并核验

## 1. 实现

新增 helper `install` 命令（`tools/updater/PaperLibraryUpdater.cs`）：

1. 校验 staging/版本；创建 `data/updates/install.lock`（存在 → `install-locked`，不覆盖）；
   写 `journal.json` 阶段 `prepare`；
2. 等待启动器 PID 退出（60 秒，200 ms）、4321 端口释放（30 秒，250 ms）、
   关键文件锁释放（30 秒，500 ms：exe/config/WebView2 DLL/runtime python）；
   超时只中止并写失败状态，**不强制结束残留进程**；
3. 通过同一 helper 的子命令执行：数据备份（P2-15）→ 程序文件备份（P2-14）→
   staging 替换（P2-14）→ 校验 exe/version.json/文件数；
4. 启动新 `PaperLibrary.exe`；
5. 健康检查口径 B：`/api/health` HTTP 200 + `ok=true` + `app=="literature-vault"`，
   再 `GET /api/version` 要求 `product=="PaperLibrary"` 且 `version==目标版本`；
   单次 2 秒、间隔 1 秒、总 60 秒、连续成功 2 次；
6. 成功：`state.json` success、清理 `apply.json`、保留备份、释放安装锁；
7. 失败：先 `POST /api/shutdown`，5 秒后仍未退出则 `taskkill /T /F` **本次新启动的**
   启动器/Python 进程树；恢复程序备份 → 启动旧版本 → 写 `state.json` failed
   （code/error/restored/oldLaunchedPid）；恢复失败保留 journal/备份；`apply.json` 保留供重试。

整体预算 5 分钟；未开始替换 → 中止，已开始替换 → 回滚。测试可用
`--timeout-pid/--timeout-port/--timeout-lock/--timeout-total/--health-*` 覆盖，
生产启动器不传，使用固定默认。

同一步修正了 staging 位置规则：正式 staging 位于
`<program-root>/data/updates/<version>/staging`（在 data/ 内），helper 允许该位置；
只拒绝直接放在程序根或其它程序文件区的 staging（`staging-inside-program`）。

契约同步：`shared/contracts/update-install.md` 新增第 5 节“完整安装时序（P2-13）”，
并更新 staging 位置、install 命令、失败码与与 P2-16/P2-17 的边界。

## 2. 单测（test/test_update_helper_install_sequence.py，11 条）

真实编译 helper + 假 `PaperLibrary.exe`（本地 HTTP 服务），在临时程序目录执行：

- 正常：staging 在 `data/updates/1.7.1/staging` → 备份/替换/启动/健康成功；state success；
  apply 清理；安装锁释放；data/library.db 保留；备份 app-1.6.0 含 backup.json；
- 等待启动器 PID 退出（成功路径确实等待约 2 秒）；
- 启动器超时、端口占用、文件锁占用 → 中止且程序文件未动；
- 数据备份失败（缺 library.db）→ 在程序备份前中止，apply 保留；
- 健康失败（ok=false）→ 回滚旧版并重新启动旧版；
- 健康版本不符（/api/version 返回 1.7.0）→ 回滚；
- 替换失败（staging 文件被锁）→ 回滚；
- install.lock 已存在 → `install-locked`，不改程序文件；
- staging 直接放程序根 → `staging-inside-program`。

## 3. 真实运行证据（真实发布包 + 真实启动器/Python）

用 `python tools/release.py --out <TEMP>` 构建真实包（95.1 MB），复制为程序目录并把
`version.json` 改为 **1.6.0/v1.6**；启动真实 `PaperLibrary.exe`，等待 `/api/health` 后确认
`/api/version=1.6.0`，`data/library.db` 已创建；再把真实包复制到
`program/data/updates/1.7.1/staging`，写入 `apply.json`，部署 helper 到包外。

停止旧服务/启动器（模拟用户关闭）后执行（默认参数，无测试覆盖）：

```text
install exit: 0
{"ok":true,"command":"install","version":"1.7.1","fromVersion":"1.6.0",
 "launchedPid":103484,"backupDir":"...\\data\\updates\\backups\\app-1.6.0",
 "healthSeconds":5,"log":"...\\install.log"}

state.json:
{"schema":1,"product":"PaperLibrary","phase":"success","version":"1.7.1",
 "fromVersion":"1.6.0","launchedPid":103484,"restored":false,"oldLaunchedPid":0,
 "healthSeconds":5,"startedAt":"2026-11-16T13:19:39Z","finishedAt":"2026-11-16T13:19:45Z"}

install log:
22:19:39 install lock created: ...\\data\\updates\\install.lock
22:19:39 journal: prepare
22:19:39 backup-data: 3 files / 271736 bytes -> ...\\data\\backups\\update-1.7.1
22:19:40 backup-program: moved 872 files / 106791206 bytes -> ...\\backups\\app-1.6.0
22:19:41 replace-program: copied 767 files / 105401974 bytes; version=1.7.1
22:19:41 launched ...\\program\\PaperLibrary.exe pid=103484
22:19:43 health attempt 1: health-unreachable
22:19:44 health attempt 2: ok (streak 1/2)
22:19:45 health attempt 3: ok (streak 2/2)
22:19:45 journal: success
22:19:45 state: success
22:19:45 install success: 1.6.0 -> 1.7.1 launchedPid=103484 health=5s
```

安装后独立验证（真实 HTTP）：

```text
GET /api/health   -> {"ok":true,"app":"literature-vault","port":4321,...}
GET /api/version  -> {"ok":true,"product":"PaperLibrary","version":"1.7.1","tag":"v1.7.1",...}
program/version.json -> 1.7.1 / v1.7.1
data/library.db preserved: True
apply.json removed: True
install.lock removed: True
backup app-1.6.0/backup.json: True
backup app-1.6.0/version.json -> 1.6.0 / v1.6
清理后 port 4321 free: True
```

说明：真实运行用 PowerShell `Start-Process -Wait` 启动 helper 时，会一并等待 helper 启动的
新应用（新应用保持运行），因此脚本在安装成功后停住；已通过 `install.log`/`state.json` 与
独立 HTTP 检查取证，并在验证后结束新应用。安装本身 exit 0、state success、`/api/version=1.7.1`。

## 4. 验收对照

| P2-13 验收项 | 结果 | 证据 |
|---|---|---|
| 正常路径一次成功并自动重开 | PASS | 真实包：exit 0、state success、新 `/api/version=1.7.1`；假应用单测同样通过 |
| 启动器未退出/端口占用/文件锁占用 → 等待后中止，文件未动 | PASS | 单测 3 例；超时码 launcher-timeout/port-timeout/file-lock-timeout；程序文件哈希不变 |
| 关键文件校验失败 → 回滚 | PASS | 单测：替换失败（staging 锁）回滚旧版并重启；健康版本不符回滚 |
| 健康检查超时 → 回滚旧版 | PASS | 单测 health-failed（ok=false/版本不符）；真实路径健康 5 秒成功 |
| 成功清理 apply.json，失败保留供重试 | PASS | 单测成功路径 apply 不存在；数据备份失败/健康失败 apply 保留 |
| 数据与备份完整 | PASS | 真实 data/library.db 保留；备份 app-1.6.0 含 backup.json 与旧 1.6.0 version.json；数据备份含 library.db |

## 5. 测试

- `python -m unittest test.test_update_helper_install_sequence -v` → **11 tests OK**；
- 全量：**690 tests OK（1 skip）**（P2-15 后 679 + 11，无新增失败）；
- 契约：**PASS 61 / FAIL 0 / SKIP 13（共 74）**，无回归；
- 版本：`python tools/version_check.py` 通过。

## 6. 未做与遗留

- 本步只写 journal 的 `prepare/success/failed`；完整状态机、安装锁过期、启动恢复由 P2-16 完成；
- 健康检查错误分类/重试细化由 P2-17；启动器/UI 的立即安装链路由 P2-10R/P2-09R/P2-11R；
- 程序备份保留时长仍未定（P2-16 确认）。
