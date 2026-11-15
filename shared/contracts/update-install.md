# 整包替换与旧程序备份契约（P2-14）

版本：1　状态：P2-14 已实现；数据备份（P2-15）、完整时序（P2-13）、journal/回滚（P2-16）、
健康检查（P2-17）在此基础上增补，不改变本契约的替换范围与备份布局。

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
- 旧程序文件整体移入 `<program-root>/data/updates/backups/app-<oldVersion>/`，因此新包中
  不存在的旧文件不会残留在程序根（仍可从备份恢复）。
- 替换是“先备份、再复制”：备份完成后程序根只剩 `data/`，复制 staging 后程序根 =
  staging 内容 + `data/`。

## 2. helper 命令

```text
PaperLibraryUpdater.exe plan-replace    --program-root DIR --staging DIR [--new-version X] [--old-version Y]
PaperLibraryUpdater.exe backup-program  --program-root DIR --backup DIR   [--old-version Y]
PaperLibraryUpdater.exe replace-program --program-root DIR --staging DIR  [--new-version X]
```

退出码：0 成功；3 校验/安全问题拒绝（stdout 输出 `{ok:false, code, error}`）；2 参数错误；5 内部异常。

### plan-replace

只读校验并输出计划：staging 存在、含 `PaperLibrary.exe` 与 `version.json`、版本与
`--new-version` 一致（若给出）、不含 `data/`、不位于程序目录内；输出文件数/字节数、
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

- 所有路径使用 `Path.GetFullPath`，staging 不得位于程序目录；复制目标必须仍在程序目录内；
- 备份目录限定在 `data/updates/backups/`（不接受任意路径）；
- staging 含 `data/` 一律拒绝（防止用户数据被新包覆盖）；
- helper 不读取/执行 manifest 中的脚本；staging 已由 Python 端完成签名、大小、SHA-256 与
  安全解压校验（P2-02~P2-04），helper 只做文件操作与关键文件一致性校验。

## 4. 与后续步骤的边界

- P2-15：在备份程序文件前把 `library.db` 与设置文件备份到
  `data/backups/update-<newVersion>/`；失败中止安装。
- P2-13：把“等待退出/端口/文件锁 → backup-program → replace-program → 启动新版本”串成时序。
- P2-16：`journal.json` 记录 `prepare → backup → replace → verify → launch → success/fail`，
  启动器下次启动检测未完成 journal 时恢复旧版本；本契约的 `backup.json` 是回滚依据。
- P2-17：新版本健康检查失败时用备份恢复程序文件。

## 5. 待确认（不猜值）

1. 备份保留时长/数量（原文建议 1–7 天或一个旧版本）：P2-14 只创建备份，清理策略待 P2-16 确定。
2. 杀软/索引器占用导致 `Directory.Move`/`File.Copy` 失败时的等待/重试参数（P2-13/P2-18 定）。
