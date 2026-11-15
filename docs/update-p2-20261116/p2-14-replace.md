# P2-14 程序文件替换范围（保留 data/）与旧程序备份

日期：2026-11-16　阶段 2E-2 / P2-14　状态：已完成并核验

## 1. 实现

扩展包外 helper（`tools/updater/PaperLibraryUpdater.cs`）三个安装命令：

- `plan-replace`：只读校验 staging（存在、含 `PaperLibrary.exe`/`version.json`、版本一致、
  不含 `data/`、不位于程序目录内）并输出替换范围/文件数/字节数/备份目录；
- `backup-program`：把程序根**除顶层 `data/` 外**的所有条目移动到
  `<program-root>/data/updates/backups/app-<oldVersion>/`，失败自动回滚已移动条目，
  写 `backup.json`（含旧 exe/version.json 是否已备份、data 是否保留）；
- `replace-program`：程序根除 `data/` 外必须为空（强制先备份），把 staging 顶层条目复制回
  程序根，校验 exe/version.json 存在且版本正确、程序文件数/字节数与 staging 一致。

契约：`shared/contracts/update-install.md`（替换范围、命令、backup.json 字段、安全约束、
与 P2-15/P2-13/P2-16/P2-17 的边界）。

关键点：

1. `data/` 原地保留：备份/替换都按顶层条目处理，`data` 被显式跳过；备份目录本身在
   `data/updates/backups/` 下，因此移动程序文件不会碰用户数据。
2. 先备份后替换：替换前要求程序根只剩 `data/`（`program-root-not-empty` 拒绝跳过备份）。
3. 失败可回滚：备份中途失败按已移动列表逆序移回；备份目录限定在 `data/updates/backups/`，
   已存在且非空时拒绝覆盖。
4. 一致性校验：替换后逐项统计文件数与字节数（含隐藏文件，如 `PaperLibrary.exe.config`），
   并与 staging 对比。

## 2. 真实验证（真实发布包，仓库外临时目录）

用 `python tools/release.py --out <TEMP>` 生成真实发布包（766 个文件 / 105,332,960 字节，
含 runtime 的 `__pycache__`；zip 分发时会跳过这些缓存，所以 P2-04 的 zip staging 是 714 个文件）。
把包复制为 `program`（加入 `data/library.db`、`data/prefs.json` 作为用户数据标记）与
`staging`，用部署到包外的 helper 执行：

### 2.1 plan-replace

```text
$ <updater>/1.7.1/PaperLibraryUpdater.exe plan-replace --program-root <program> --staging <staging> --new-version 1.7.1 --old-version 1.6.0
{"ok":true,"command":"plan-replace",…,"version":"1.7.1",
 "backupDir":"…\program\data\updates\backups\app-1.6.0",
 "files":766,"bytes":105332960,"dataExcluded":true,"stagingHasData":false,
 "topLevel":["Microsoft.Web.WebView2.Core.dll",…,"runtime","shared","tools","vendor","version.json","WebView2Loader.dll","使用说明.txt"]}
plan exit code: 0
```

### 2.2 backup-program

```text
$ … backup-program --program-root <program> --backup <program>\data\updates\backups\app-1.6.0 --old-version 1.6.0
{"ok":true,"command":"backup-program",…,"files":766,"bytes":105332960,
 "dataPreserved":true,"oldExeBackedUp":true,"oldVersionBackedUp":true,
 "entries":[{"name":"paperlib","type":"dir","files":98,"bytes":822487},…]}
backup exit code: 0
program root top-level after backup: data
data marker after backup: REAL USER DATABASE MARKER
backup contains PaperLibrary.exe: True
backup contains version.json: True
backup old exe equals original: True
```

备份目录内同时写入 `backup.json`（schema/oldVersion/files/bytes/dataExcluded/dataPreserved/
oldExeBackedUp/oldVersionBackedUp/entries/createdAt）。

### 2.3 replace-program

```text
$ … replace-program --program-root <program> --staging <staging> --new-version 1.7.1
{"ok":true,"command":"replace-program",…,"version":"1.7.1","files":766,"bytes":105332960,
 "dataPreserved":true,"topLevel":[…]}
replace exit code: 0
data marker after replace: REAL USER DATABASE MARKER
data/library.db still present: True
data/prefs.json still present: True
```

### 2.4 程序文件与 staging 逐文件一致

```text
program files (incl hidden, excl data): 766 / staging: 766
missing=0 extra=0 hashDiffers=0 identical=True
PaperLibrary.exe(config) attributes: Hidden, Archive
```

说明：PowerShell `Get-ChildItem` 默认不列隐藏文件，`PaperLibrary.exe.config` 必须用
`-Force` 才能纳入比对；helper 自身的统计包含隐藏文件（766 = 765 + config）。

## 3. 单测（test/test_update_helper_install.py，12 条）

- plan 输出范围/版本/备份目录；staging 含 `data/`、缺 exe、版本不符、staging 在程序目录内均拒绝；
- backup 移动程序文件、保留 `data/`、备份含旧 exe/version.json、backup.json 字段正确；
- backup 拒绝 `data/updates/backups/` 之外的路径、拒绝非空备份目录；
- replace 必须先备份；备份后替换与 staging 逐文件（相对路径 + SHA-256）一致；
- replace 拒绝 staging 含 `data/`、拒绝版本不符；拒绝时程序目录不被破坏。

## 4. 验收对照

| P2-14 验收项 | 结果 | 证据 |
|---|---|---|
| 只替换程序文件，`data/` 原地保留 | PASS | 备份后程序根只剩 `data`；替换后 data marker/prefs 原样；真实包 766 文件替换 |
| 程序文件与 staging 一致 | PASS | 766/766 文件逐个 SHA-256 相同（含隐藏 config） |
| 旧程序备份到 `data/updates/backups/app-<oldVersion>/` | PASS | 备份含 PaperLibrary.exe/version.json，旧 exe 哈希与替换前一致；backup.json 记录 |
| 备份含旧 exe/版本标识 | PASS | `oldExeBackedUp=true`、`oldVersionBackedUp=true`、`oldVersion=1.6.0` |

## 5. 测试

- `python -m unittest test.test_update_helper_install -v` → **12 tests OK**；
- 全量：**667 tests OK（1 skip）**（P2-12 后 655 + 12，无新增失败）；
- 契约：**PASS 61 / FAIL 0 / SKIP 13（共 74）**，无回归；
- 版本：`python tools/version_check.py` 通过。

## 6. 未做与待确认

- 本步不做数据备份（P2-15）、完整时序（P2-13）、journal/回滚（P2-16）、健康检查（P2-17）、
  启动器集成（P2-10/P2-11）。
- 待确认：备份保留时长/数量（P2-16 定）；文件被占用时的等待/重试参数（P2-13/P2-18 定）；
  本步在真实包上直接以“发布包目录”作 staging（含 runtime `__pycache__`），正式流程的
  staging 来自 zip 解压（714 文件），文件数差异不影响替换逻辑。
