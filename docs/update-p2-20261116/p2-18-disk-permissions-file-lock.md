# P2-18 磁盘、权限与文件占用约束

日期：2026-11-17　阶段 2G / P2-18　状态：已完成并核验

## 1. 决策（2026-11-17）

- 安装期只重试共享冲突/锁冲突（`ERROR_SHARING_VIOLATION` 32、`ERROR_LOCK_VIOLATION` 33）；
  `ACCESS_DENIED`、路径不存在等权限/结构错误不重试，直接失败；
- 每 **300 ms** 重试，单文件最多 **21 秒**，按文件重试；
- 超时取消更新、回滚旧版、弹窗写失败原因并写日志；`backup-data` 同规则；
- 安装前端口/文件锁等待仍按 30 秒，超时只中止，不强制结束残留进程。

## 2. 实现

### 启动器预检（P2-10R 扩展）

- 新增磁盘估算：`zip + staging + 程序备份 + data(不含 updates) + 300 MB`
  必须小于所在盘可用空间，否则 `disk-low`，不启动 helper、不关闭窗口；
- 保留 Program Files 拒绝、程序目录可写、apply/staging/helper 哈希预检；
- 新增测试专用参数：`freeBytesOverride`、`programFilesRootOverride`（生产不传）。

### helper 占用重试

- 新增 `RetryIo`：只捕获 IOException HResult 32/33；每 300ms，单文件最多 21s；
- 覆盖 `backup-data` 的 `File.Copy`、`backup-program` 的 `Directory.Move/File.Move`、
  `replace-program` 的 `File.Copy/Directory.Copy`；
- 超时抛 `file-lock-timeout`；install 把子命令的 `file-lock-timeout` 原样写入 journal/state，
  随后按 P2-13 恢复程序备份、启动旧版、保留 `apply.json`；
- 修正 helper 子命令 stdout 为 UTF-8，失败原因中文可读写入 state/log；
- 测试用 `--file-retry-max-ms/--file-retry-delay-ms` 覆盖（生产默认 21000/300）。

## 3. 测试

    python -m unittest test.test_launcher_core test.test_update_helper_retry -v
    Ran 12 tests
    OK

- 预检：磁盘 1 字节 → `disk-low`；大空间 → ok；Program Files 覆盖 → `program-files`；
- 重试：替换超时 → `file-lock-timeout`；锁释放后重试成功；backup-data 超时；backup-program 超时回滚；
- P2-13 回归：占用超时 → state.code=`file-lock-timeout`、error 含“文件被占用”、
  apply 保留、旧版恢复；
- Node 11 tests OK。

全量待本轮确认（目标 714 tests OK，1 skip）；契约目标 61/0/13。

## 4. 验收对照

| P2-18 验收项 | 结论 | 证据 |
|---|---|---|
| 磁盘不足提前失败且不关闭 | PASS | `disk-low` 预检返回 false；host 不启动 helper/不关闭 |
| Program Files 明确提示 | PASS | `program-files` 文案；测试覆盖 |
| 文件占用重试后仍失败 → 中止/回滚 | PASS | 300ms/21s 重试；超时 `file-lock-timeout`；state restored=true、apply 保留 |
| 超时取消更新并弹窗/写日志 | PASS | state code/error/log；下次启动 P2-10R notice-failed 弹窗 |
| 预检不通过不进入安装 | PASS | StartInstall 仅在 Precheck.Ok 后执行 |

## 5. 契约与文档

- `shared/contracts/update-install.md` 第 9 节（占用重试）；
- `shared/contracts/update-apply.md` 预检失败码补充 `disk-low/disk-check-failed` 与磁盘估算；
- 本记录。

## 6. 遗留

- GUI 级磁盘不足/Program Files 弹窗与截图归 P2-19；
- 自动下载开关（2026-11-17 决策）尚未实现，需后续 UI/后端扩展，不在本步范围。
