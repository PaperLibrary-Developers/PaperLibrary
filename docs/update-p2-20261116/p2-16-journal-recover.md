# P2-16 journal、安装锁与启动恢复

日期：2026-11-16　阶段 2E-3 / P2-16　状态：已完成并核验

## 1. 实现

### journal.json（状态机）

\`prepare → backup → replace → verify → launch → success/fail\`，每个阶段原子写入：

\`\`\`json
{"schema":1,"product":"PaperLibrary","phase":"...","version":"1.7.1",
 "fromVersion":"1.6.0","staging":"...","dataBackupDir":"...","backupDir":"...",
 "launcherPid":0,"startedAt":"...","launchedPid":0,"updatedAt":"...",
 "code":"","error":"","log":"..."}
\`\`\`

### state.json

\`schema/product/phase/version/fromVersion/staging/dataBackupDir/backupDir/launchedPid/
oldLaunchedPid/restored/recovered/healthSeconds/healthChecks/startedAt/finishedAt/code/error/log\`。

### install.lock

字段：\`schema/product/pid/launcherPid/version/fromVersion/createdAt\`；已存在且未过期 →
\`install-locked\`；**记录 PID 不存在或年龄 > 30 分钟 → 过期**，\`install\`/\`recover\` 可清理。

### recover

\`PaperLibraryUpdater.exe recover --program-root DIR [--log FILE]\`：

- 无 journal / journal=success → no-op；
- 锁仍有效 → \`install-locked\`；
- prepare/backup：有备份则恢复，无备份确认旧程序仍在；
- replace/verify/launch：停止 journal 记录的 \`launchedPid\`，恢复程序备份，启动旧版本；
- 写 state \`phase=failed, recovered=true, code=recovered\`，journal \`failed\`，清理锁，保留 apply；
- 恢复失败 → \`recovery-failed\`，保留 journal/备份，退出码 3。

### 程序备份保留

按产品确认（2026-11-16）：只保留最近一个 \`app-<oldVersion>\`；安装成功或 recover 成功后
清理更早的 \`app-*\`，数据备份 \`update-*\` 不受影响。

## 2. 单测（test/test_update_helper_recover.py，6 条）

- journal/state schema 与成功阶段；
- 活跃 install.lock 阻止安装；PID 不存在的过期锁可清理；
- recover 无 journal / journal=success 的 no-op；
- **prepare/backup/replace/verify/launch 五阶段强制退出后 recover**：旧版 1.6.0 恢复、
  数据保留、apply 保留、state recovered=true、journal failed；
- 程序备份保留最近一个。

阶段强杀用 helper 测试专用参数 \`--exit-after-phase <phase>\`（写完该阶段 journal 后
\`Environment.Exit(77)\`，模拟断电/强杀；生产不传）。测试覆盖真实文件移动、进程启动/停止。

## 3. 真实运行证据（强制退出 replace 阶段 → recover）

\`\`\`text
$ python - <<'PY'  # 用 fake PaperLibrary.exe 构造旧版 1.6.0 + staging 1.7.1
... install --exit-after-phase replace ...
INSTALL_EXIT 77
JOURNAL_AFTER_FORCED_EXIT {"schema":1,...,"phase":"replace","version":"1.7.1",
 "fromVersion":"1.6.0",...}
STATE_EXISTS_AFTER_FORCED_EXIT False

$ PaperLibraryUpdater.exe recover --program-root <program> --log <install.log>
RECOVER_EXIT 0
RECOVER_STDOUT {"ok":true,"command":"recover","recovered":true,"phase":"replace",
 "version":"1.7.1","fromVersion":"1.6.0","restored":true,"oldLaunchedPid":80424,...}
STATE_AFTER_RECOVER {"phase":"failed","version":"1.7.1","fromVersion":"1.6.0",
 "restored":true,"recovered":true,"code":"recovered",
 "error":"recovered from phase replace",...}
PROGRAM_VERSION {"product": "PaperLibrary", "version": "1.6.0"}
DATA_DB user database
APPLY_STILL_THERE True
\`\`\`

同一测试文件还覆盖 prepare/backup/replace/verify/launch 五个阶段的强制退出恢复。

## 4. 验收对照

| P2-16 验收项 | 结果 | 证据 |
|---|---|---|
| 各阶段强杀后重启恢复旧版本 | PASS | 5 阶段强制退出 + recover；程序版本回 1.6.0、数据保留、apply 保留 |
| journal 阶段与日志可追溯 | PASS | journal 阶段字段完整；state recovered=true；log 记录 phase/restored/pid |
| install.lock 阻止双开、过期锁可清理 | PASS | 活跃锁 → install-locked；死 PID 锁 → 清理后安装成功 |
| 恢复后应用可用、数据完整 | PASS | recover 启动旧版（oldLaunchedPid>0）；library.db 原内容保留 |

## 5. 测试

- \`python -m unittest test.test_update_helper_recover -v\` → **6 tests OK**；
- 回归 P2-13：**11 tests OK**；
- 全量：**696 tests OK（1 skip）**（690 + 6）；
- 契约：**PASS 61 / FAIL 0 / SKIP 13（共 74）**，无回归。

## 6. 说明与遗留

- journal/state schema 已按确认冻结；P2-17 只增加健康失败码，不改结构；
- 启动器启动时调用 recover 的集成在 P2-10R；本步只提供 helper 入口；
- 程序备份保留策略已按确认落地（最近一个）。
