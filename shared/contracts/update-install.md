# 整包替换与旧程序备份契约（P2-14）

版本：1　状态：P2-14/P2-15/P2-13 已实现；journal/回滚（P2-16）与健康检查细化（P2-17）
在此基础上增补，不改变本契约的替换范围与备份布局。

## 1. 替换范围

包根下**除顶层 `data/` 之外的所有条目**都是程序文件，允许被替换：

```text
PaperLibrary.exe / PaperLibrary.exe.config
Microsoft.Web.WebView2.Core.dll / Microsoft.Web.WebView2.WinForms.dll / WebView2Loader.dll
runtime/  paperlib/  public/  shared/  vendor/  tools/
README.md  使用说明.txt  requirements.txt  version.json  release-manifest.json
```

- 顶层 `data/` 是用户数据（`library.db`、`prefs.json`、`settings.json`、`sources.json`、
  全文/备份等），**不搬移、不复制覆盖、不删除**；备份目录本身也在 `data/` 下。
- staging 中**不得出现** `data/`（P2-04 已拒绝；helper 在执行前再次拒绝）。
- 正式 staging 位于 `<program-root>/data/updates/<version>/staging`（在 `data/` 内，
  属于更新目录），helper 允许该位置；只拒绝直接放在程序根或其它程序文件区的 staging。
- 旧程序文件整体移入 `<program-root>/data/updates/backups/app-<oldVersion>/`，因此新包中
  不存在的旧文件不会残留在程序根（仍可从备份恢复）。
- 替换是“先备份、再复制”：备份完成后程序根只剩 `data/`，复制 staging 后程序根 =
  staging 内容 + `data/`。

## 2. helper 命令

```text
PaperLibraryUpdater.exe plan-replace    --program-root DIR --staging DIR [--new-version X] [--old-version Y]
PaperLibraryUpdater.exe backup-program  --program-root DIR --backup DIR   [--old-version Y]
PaperLibraryUpdater.exe replace-program --program-root DIR --staging DIR  [--new-version X]
PaperLibraryUpdater.exe backup-data     --program-root DIR --new-version X [--keep N]
PaperLibraryUpdater.exe install         --program-root DIR --staging DIR [--old-version Y] [--new-version X]
                                        [--launcher-pid PID] [--backup DIR] [--port 4321] [--log FILE]
```

退出码：0 成功；3 校验/安全问题拒绝（stdout 输出 `{ok:false, code, error}`）；2 参数错误；5 内部异常。

### plan-replace

只读校验并输出计划：staging 存在、含 `PaperLibrary.exe` 与 `version.json`、版本与
`--new-version` 一致（若给出）、不含 `data/`、位于 `data/updates/` 或程序目录外；输出文件数/字节数、
顶层条目、备份目录（给出 `--old-version` 时）。错误码：`staging-missing`、
`staging-inside-program`、`missing-exe`、`missing-version`、`data-directory`、
`version-mismatch`、`invalid-version`、`backup-path-invalid`。

### backup-program

1. 校验备份目录必须位于 `<program-root>/data/updates/backups/` 下且不是该根目录本身
   （`backup-path-invalid`）；备份目录已存在且非空 → `backup-exists`（不覆盖已有备份）；
2. 按顶层条目移动（目录用 `Directory.Move`，文件用 `File.Move`），跳过 `data`；
3. 任一步失败：把已移动条目回滚回程序根（`backup-failed`），避免半备份；
4. 校验 `data/` 仍在（`data-lost`）、程序根不再有 `PaperLibrary.exe`；
5. 写 `backup.json`（原子写），字段：

```json
{
  "schema": 1, "product": "PaperLibrary", "oldVersion": "1.6.0",
  "programRoot": "...", "backupDir": "...\data\updates\backups\app-1.6.0",
  "files": 766, "bytes": 105332960, "dataExcluded": true, "dataPreserved": true,
  "oldExeBackedUp": true, "oldVersionBackedUp": true, "createdAt": "2026-11-15T17:42:04Z",
  "entries": [{"name": "paperlib", "type": "dir", "files": 98, "bytes": 822487}, ...]
}
```

### replace-program

1. 校验 staging（同 plan）；程序根除 `data` 外必须为空（`program-root-not-empty`），
   防止跳过备份直接覆盖；
2. 复制 staging 顶层条目到程序根（`File.Copy` 不覆盖；已存在 → `target-exists`）；
3. 校验：`PaperLibrary.exe`、`version.json` 存在且版本等于目标版本；程序文件总数/总字节
   与 staging 完全一致（含隐藏文件）；`data/` 仍存在；
4. 输出 `{ok, version, files, bytes, dataPreserved, topLevel}`。

## 3. 安全约束

- 所有路径使用 `Path.GetFullPath`；staging 只能位于程序目录外或 `data/updates/` 下，
  不能直接放在程序根或其它程序文件区；复制目标必须仍在程序目录内；
- 备份目录限定在 `data/updates/backups/`（不接受任意路径）；
- staging 含 `data/` 一律拒绝（防止用户数据被新包覆盖）；
- helper 不读取/执行 manifest 中的脚本；staging 已由 Python 端完成签名、大小、SHA-256 与
  安全解压校验（P2-02~P2-04），helper 只做文件操作与关键文件一致性校验。

## 4. 数据备份（P2-15）

安装前必须先把用户数据复制到 `<program-root>/data/backups/update-<newVersion>/`：

```text
PaperLibraryUpdater.exe backup-data --program-root DIR --new-version X [--keep N]
```

- 备份清单：`library.db`、`library.db-wal`、`library.db-shm`、`library.db-journal`、
  `prefs.json`、`settings.json`、`sources.json`、`websearch.json`。存在的文件逐个复制并校验 SHA-256，
  不存在的可选文件记入 `missing`；
- **`library.db` 缺失 → `library-db-missing`，中止安装**（不能保证可恢复）；
  `data/` 缺失 → `data-missing`；备份项是目录/复制校验失败 → `data-backup-failed`；
- 先写临时目录 `update-<version>.tmp-<random>`，完成并写 `data-backup.json` 后
  一次性改名；失败清理临时目录，不留半份备份；
- 独立执行 `backup-data` 时，同版本、同内容的备份返回 `idempotent=true`，内容变化返回
  `data-backup-exists`。重试安装会先完成并校验一份新快照，再替换该目标版本的旧快照，
  保留用户在两次尝试之间的编辑。
- 保留策略：默认**只保留最近 1 份** `update-*`（产品确认，2026-11-16，减少磁盘占用），
  当前新备份始终保留（`--keep 1..50` 可调）；
- `data-backup.json` 字段：schema/product/newVersion/programRoot/backupDir/files/bytes/
  databaseIncluded/missing/items[{name,bytes,sha256}]/createdAt；
- 已尝试启动新版时，先确认新进程已结束，再校验整份快照，恢复清单上的文件。
  清单标记缺失的文件在恢复时移除，包括新版新建的 SQLite 边车文件。
  快照之外的文件仍保留在 `data/`。

## 5. 完整安装时序（P2-13）

`install` 由启动器在用户确认后调用（P2-10R/P2-11R），helper 已在包外（LOCALAPPDATA）：

1. 校验 staging/版本；创建 `data/updates/install.lock`（已存在且未过期 → `install-locked`；
   过期锁按 P2-16 规则清理后继续）；
   写 `journal.json`（阶段 `prepare`）；
2. 等待启动器 PID 退出（**60 秒**，200 ms 轮询）、4321 端口释放（**30 秒**，250 ms）、
   关键文件锁释放（**30 秒**，500 ms 重试：exe/config/WebView2 DLL/runtime python）；
   超时**只中止并写失败状态，不强制结束残留进程**，程序文件未动；
3. 数据备份（P2-15）→ 程序文件备份（P2-14）→ staging 替换（P2-14）→ 校验
   `PaperLibrary.exe`/`version.json`/文件数；
4. 记录 `launch` 阶段并启动新 `PaperLibrary.exe`，随后记录其 PID。验证期间，启动器
   等待提交结果，后端仅处理健康、版本和关停请求。
5. 健康检查（口径 B）：`/api/health` HTTP 200 + `ok=true` + `app=="literature-vault"`，
   再 `GET /api/version` 要求 `product=="PaperLibrary"` 且 `version==目标版本`；
   单次请求 **2 秒**、间隔 **1 秒**、总超时 **60 秒**、**连续成功 2 次**；
6. 成功：写 `state.json`，再提交 `journal.json` 的 `success` 阶段，放行读写并打开界面。
   清理 `apply.json`，保留备份并释放安装锁。
7. 失败：关停本次启动的新版进程并等待端口释放，恢复数据与程序备份。写入终态并释放安装锁后，
   重新启动旧版本。恢复失败保留未完成的 journal 和备份，供再次恢复。
   `apply.json` 保留供重试。

整体安装预算 **5 分钟**：未开始替换 → 中止；已开始替换 → 回滚。测试可用
`--timeout-* / --health-*` 覆盖（生产启动器不传，使用上述固定默认）。
失败码：`launcher-timeout`、`port-timeout`、`file-lock-timeout`、`total-timeout`、
`data-backup-failed`、`program-backup-failed`、`replace-failed`、`verify-failed`、
`launch-failed`、`install-locked`、`health-timeout`、`health-http`、`health-json`、
`health-not-ok`、`health-app`、`health-version`。

## 6. journal、state 与安装锁（P2-16，已冻结）

### journal.json

`install` 从 `prepare` 到终态逐阶段原子写 `data/updates/journal.json`：

```text
prepare → backup → replace → verify → launch → success
                                              ↘ failed
```

字段（schema=1）：

| 字段 | 说明 |
|---|---|
| schema / product | 固定 `1` / `PaperLibrary` |
| phase | `prepare/backup/replace/verify/launch/success/failed` |
| version / fromVersion | 目标版本 / 旧版本 |
| staging / dataBackupDir / backupDir | 绝对路径（均在 data 内） |
| launcherPid | 启动器 PID（0 表示未传） |
| startedAt / updatedAt | UTC ISO8601 |
| launchedPid | 启动的新应用 PID（launch 阶段起） |
| port | 安装验证使用的服务端口，旧 journal 缺省为 4321 |
| code / error | 失败码与可读原因（成功为空） |
| log | helper 日志路径 |

#### phase × launchedPid 判定表（A1，2026-11-20 补齐）

`launch` 阶段 journal 是**先写 `launchedPid=0`、再冷启动新进程、最后回填真实 PID** 的
（`tools/updater/PaperLibraryUpdater.cs` install 的 launch 分支）。因此「读到 0」只表示
**尚未回填**，不表示验证窗口已关闭；三个读者必须按同一张表判定（数值以
`paperlib/app.py` 的 `PROBE_PID_BACKFILL_SECONDS` 与 `launcher/LauncherCore.cs` 为准）：

| phase | launchedPid | 新进程（`paperlib/app.py`） | 启动器（`LauncherCore.UpdateProbeState`） |
|---|---|---|---|
| launch | 0 / 缺失 | 视为「尚未回填」，最多重读 journal **2 秒**（50ms 轮询），回填后按下一行判定 | `starting`（容错，最长 5 秒） |
| launch | = 本进程 PID | 打开更新验证窗口：页面与写接口 503，等 journal 变 `success` | `validating` |
| launch | ≠ 本进程 PID | 窗口已关闭 → 退出码 1 | `""`（不参与判定） |
| prepare/backup/replace/verify | 任意 | 非 probe 启动被拦截 | 安装中 / `starting` |
| success | = 本进程 PID | 正常打开 | `committed` |
| success | 其它 | 正常打开 | `""` |
| failed | 任意 | 允许打开（恢复入口） | `""`（走 recover/notice-failed） |

安装锁（`install.lock`）仍是窗口的组成部分：锁不存在时两端都判定为窗口关闭。

### state.json

终态（成功/失败/恢复）写 `data/updates/state.json`，字段：

`schema/product/phase(success|failed)/version/fromVersion/staging/dataBackupDir/backupDir/
launchedPid/oldLaunchedPid/restored/recovered/healthSeconds/healthChecks/startedAt/finishedAt/
code/error/log`。

`recovered=true` 表示中断恢复或安装回滚已完成。失败时 `apply.json` 保留供重试。

### install.lock

`data/updates/install.lock` 字段：`schema/product/pid/launcherPid/version/fromVersion/createdAt`。

- 创建：`FileMode.CreateNew`，已存在且未过期 → `install-locked`（不覆盖）；
- **过期判定**：记录 PID 不存在，**或**锁文件年龄 > **30 分钟** → 过期；`install`/`recover`
  可安全清理后继续；
- 安装成功/失败/恢复结束都清理本 helper 创建的锁；不删除他人仍有效的锁。

### recover

```text
PaperLibraryUpdater.exe recover --program-root DIR [--log FILE]
```

- 无 journal 或 journal 已到达终态 → no-op（`recovered=false`）；
- 锁仍有效 → `install-locked`，拒绝并发恢复；
- journal phase=prepare：确认旧程序仍在。phase=backup：移回已备份的程序文件。
- phase=replace/verify：恢复程序备份。phase=launch：停止新版进程，先恢复数据，再恢复程序。
  全部恢复成功后启动旧版本。
- 恢复后写 `state.json`：`phase=failed, recovered=true, code="recovered"`；
  journal 置 `failed`；清理过期锁；保留 `apply.json`；
- 恢复失败：`phase=failed, recovered=false, code="recovery-failed"`，保留 journal/备份，
  退出码 3。

### 程序备份保留

产品确认（2026-11-16）：**只保留最近一个** `app-<oldVersion>` 程序备份；安装成功或 recover
成功后清理更早的 `app-*`，数据备份 `data/backups/update-*` 不受影响。

## 7. 健康判定（P2-17）

在启动新版本后按**口径 B**判定（不修改后端 `/api/health` 字段）：

1. `GET /api/health`：HTTP 200、`ok=true`、`app=="literature-vault"`；
2. 再 `GET /api/version`：`product=="PaperLibrary"` 且 `version==目标版本`；
3. 单次请求 **2 秒**、间隔 **1 秒**、总超时 **60 秒**、**连续成功 2 次**；
   任何一次失败都会重置连续计数，全部成功才写 `state.json` success。

失败码（写入日志、journal/state 的 `code`，并触发回滚）：

| code | 含义 |
|---|---|
| `health-timeout` | 单次请求超时，或总超时且没有更具体失败 |
| `health-http` | 连接失败/HTTP 非 200 |
| `health-json` | 返回不是预期 JSON/缺字段 |
| `health-not-ok` | 应用返回 `ok=false` |
| `health-app` | `app` 不是 `literature-vault`（端口被别的程序占用） |
| `health-version` | `product` 或 `version` 与目标不符 |

失败后沿用 P2-13 的回滚路径：关停本次新应用、恢复程序备份、启动旧版本、写
state failed（含具体 code）；`apply.json` 保留供重试。

## 8. 启动器拉起 helper（P2-11R）

UI 确认后，启动器执行：

1. 再次调用 P2-10R 预检（apply/staging/helper/程序目录）；
2. 启动包外 helper（参数见 `update-apply.md` 第 8 节），传入当前启动器 PID、
   staging、旧/新版本、backupPath、端口 4321 和 install 日志；
3. helper 存活检查通过后，启动器 `POST /api/shutdown` 并等待 Python 最多 5 秒；
4. 服务停止 → 回 UI `ok:true` 并关闭启动器；helper 等待启动器 PID 退出（60 秒超时）后
   进入 `install` 等待/备份/替换/健康检查；
5. helper 启动失败/预检失败 → UI `ok:false`，窗口保持；服务 5 秒未停止 → 终止本次 helper，
   UI `ok:false, code=service-stop-timeout`，窗口保持；
6. 手动关窗且存在 pending apply 时，用户可安装后关闭、保留更新并关闭，或取消关窗；
   安装启动失败后也可保留更新并关闭。无 pending 时直接关闭；
7. 启动器 Mutex 贯穿；helper 创建 `data/updates/install.lock`，双开被 Mutex/锁拦截。

## 9. 安装期文件占用重试（P2-18，已冻结）

只对 Windows 共享/锁冲突重试：

- 可重试：`ERROR_SHARING_VIOLATION(32)`、`ERROR_LOCK_VIOLATION(33)`；
- 不重试：`ACCESS_DENIED(5)`、路径不存在、磁盘满等权限/结构错误，直接失败；
- 按文件重试，每 **300 ms** 一次，单文件最多 **21 秒**；
- 适用于 `backup-data` 的复制、`backup-program` 的 Move、`replace-program` 的 Copy；
- 超时抛 `file-lock-timeout`：安装中止、按 P2-13 回滚旧版、保留 `apply.json`；
  `state.json` 写具体 code/error/log，下次启动由 P2-10R 弹窗提示失败原因；
- 安装前的端口/文件锁等待仍按 30 秒、超时只中止，不强制结束残留进程。

## 10. 与后续步骤的边界

- P2-15（已完成）：数据备份到 `data/backups/update-<newVersion>/`；失败中止安装。
- P2-13（已完成）：安装锁 + 等待 + 数据/程序备份 + 替换 + 启动 + 健康检查 + 失败回滚。
- P2-16（已完成）：journal 完整状态机、state schema、安装锁过期、`recover` 启动恢复、
  分阶段强杀恢复、程序备份保留最近一个；`backup.json` 是回滚依据。
- P2-17（已完成）：健康检查错误分类与具体失败码；失败仍回滚。
- P2-10R/P2-11R（已完成）：启动器预检/启动判定、WebView2 握手、helper 启动与关闭时序。
- P2-18（已完成）：300 MB 余量/Program Files/磁盘预检；安装期共享/锁冲突 300ms×21s 按文件重试，
  超时中止/回滚并写 state。

## 11. 待确认（不猜值）

无。P2-18 占用重试参数已于 2026-11-17 确认，见第 9 节。
