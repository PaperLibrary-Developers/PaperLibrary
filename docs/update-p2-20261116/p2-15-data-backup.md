# P2-15 更新前数据备份（library.db + 设置）

日期：2026-11-16　阶段 2E-2 / P2-15　状态：已完成并核验

## 1. 实现

helper 新增 `backup-data --program-root DIR --new-version X [--keep N]`：

- 备份清单固定：`library.db`、`library.db-wal`、`library.db-shm`、`prefs.json`、
  `settings.json`、`sources.json`；存在则复制并校验 SHA-256，不存在记入 `missing`；
- **`library.db` 缺失即 `library-db-missing` 中止安装**；`data/` 缺失、备份项是目录、
  复制/校验失败均中止；
- 写临时目录 `update-<version>.tmp-<random>`，完成 `data-backup.json` 后一次性改名；
  失败清理临时目录，不留半份备份；
- 幂等：同版本备份与当前源文件哈希一致 → `idempotent=true`；源数据已变 → 
  `data-backup-exists`，不覆盖旧备份；
- 保留策略：默认保留最近 3 份 `update-*`（`--keep 1..50`），当前新备份始终保留。

契约：`shared/contracts/update-install.md` 第 4 节（备份清单、data-backup.json 字段、
失败/幂等/保留规则、恢复方式）。

## 2. 真实验证（真实 SQLite 数据库 + 设置文件）

临时 `data/` 内用 Python `sqlite3` 建真实数据库（12 篇文献 + prefs 表，20,480 B），
写入 `prefs.json`/`settings.json`/`sources.json`，另放一个不在备份范围的
`fulltext.txt`；helper 从 `tools/make_updater.ps1` 现场编译并部署到包外。

### 2.1 备份内容与哈希

```text
$ … backup-data --program-root <program> --new-version 1.7.1
{"ok":true,"command":"backup-data","newVersion":"1.7.1","idempotent":false,
 "files":4,"bytes":20573,"databaseIncluded":true,
 "missing":["library.db-wal","library.db-shm"],
 "items":[{"name":"library.db","bytes":20480,"sha256":"ad3fbee3…78843c"},
          {"name":"prefs.json","bytes":32,"sha256":"cc1b89c8…86b6d0"},
          {"name":"settings.json","bytes":26,"sha256":"0c2adb6c…0e8a89"},
          {"name":"sources.json","bytes":35,"sha256":"3711f6bf…8fa325"}]}
backup exit code: 0

data/backups/update-1.7.1/
  data-backup.json 900
  library.db 20480
  prefs.json 32
  settings.json 26
  sources.json 35

db hash unchanged in backup: True
prefs/settings/sources all backed up: True
fulltext excluded: True
```

### 2.2 幂等与“不覆盖已变化数据”

```text
$ … backup-data … --new-version 1.7.1        # 第二次
{"ok":true,"idempotent":true,"missing":["library.db-wal","library.db-shm"], …}

$ # 修改 prefs.json 后再备份同版本
{"ok":false,"code":"data-backup-exists","error":"已存在内容不同的数据备份（不覆盖）：…\update-1.7.1"}
conflict exit code: 3
old backup prefs still dark: True
```

### 2.3 备份可恢复

把备份目录中的 4 个文件复制回新的 `restored-data/`，哈希与源一致，SQLite 可打开并查询：

```text
restored db hash equals source: True
restored rows: 12 | first: ('p01', 'Paper 01')
```

### 2.4 保留策略

按 1.7.1 → 1.7.2 → 1.7.3 → 1.7.4 依次备份，`--keep 2`：

```text
remaining update backups: update-1.7.3, update-1.7.4
```

### 2.5 备份失败中止安装

删除 `library.db` 后再备份：

```text
{"ok":false,"code":"library-db-missing","error":"缺少 library.db，无法保证数据可恢复；中止安装：…"}
abort exit code: 3
1.7.5 backup created: False
```

## 3. 单测（test/test_update_helper_data_backup.py，11 条）

- 真实 SQLite DB + 3 个设置文件的备份、逐文件哈希一致、fulltext 不复制；
- 缺可选设置记入 missing；缺 `library.db`/缺 `data` 中止且不留备份目录；
- 备份项为目录 → `data-backup-failed` 且清理临时目录；
- 幂等重跑；数据变化后拒绝覆盖；WAL/SHM 存在时一并备份；
- 保留最近 N 份；恢复后 SQLite 可查询；非法版本号拒绝。

## 4. 验收对照

| P2-15 验收项 | 结果 | 证据 |
|---|---|---|
| 备份文件可恢复 | PASS | 复制回新目录后哈希一致，SQLite 12 行可查询 |
| `prefs.json`/`settings.json`/`sources.json` 未丢失 | PASS | 三者均在备份目录且哈希一致；缺失时记入 missing |
| 备份失败时中止安装 | PASS | 缺 library.db → 退出码 3、无备份目录；目录项/校验失败同理 |

## 5. 测试

- `python -m unittest test.test_update_helper_data_backup -v` → **11 tests OK**；
- 全量：**678 tests OK（1 skip）**（P2-14 后 667 + 11，无新增失败）；
- 契约：**PASS 61 / FAIL 0 / SKIP 13（共 74）**，无回归；
- 版本：`python tools/version_check.py` 通过。

## 6. 未做与待确认

- 本步不做完整安装时序（P2-13）、journal/自动恢复（P2-16）、健康检查（P2-17）、启动器集成；
- 待确认：数据备份保留数量/时长（当前默认最近 3 份，可 `--keep` 调整）；真实用户数据
  （约 143.6 MB）未在本步触碰，真实安装验收留待 P2-13/P2-17 在临时程序目录完成。
