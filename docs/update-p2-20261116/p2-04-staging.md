# P2-04 解压与 staging 安全校验

日期：2026-11-16　阶段 2A / P2-04　状态：已完成并核验

## 1. 实现

@paperlib/update/staging.py@（@extract_package@）：

1. 读取本次 @manifest.json@ 并**重新验签**（内置公钥），确认清单版本 == 目标版本；
   要求 @package.zip@ 存在且大小等于清单 size；
2. **干净 staging**：每次解压前删除旧 @staging/@ 与旧 @ready.json@（重试不混入旧文件）；
3. 写盘前先逐条检查 zip（@inspect_entries@）：
   - 绝对路径、盘符（@C:@/@C:/@）、UNC、@..@ 路径穿越 → 拒绝；
   - 符号链接（Unix mode @0120000@）→ 拒绝；
   - 文件数上限 5000、单文件上限 512 MB、解压总量上限 1 GB；
   - 压缩比上限 200:1（且解压量 ≥20 MB 才判定）防 zip bomb；
   - 顶层 @data/@ → 拒绝（用户数据不得随包分发）；
4. 逐条安全解压（目标路径再次做“必须位于 staging 内”检查；1 MB/块复制，不整读）；
5. 要求包根存在 @PaperLibrary.exe@ 与 @version.json@，且 @version.json@ 的 version 与清单一致；
6. 全部通过：写 @ready.json@（version/tag/stagingPath/files/bytes/manifestSha256/packageSha256/createdAt），
   状态置 **ready**；任一失败：删除 staging 与 ready、状态 @failed@ + code，抛 @StagingError@。

错误码：@unsafe-path@、@symlink@、@too-many-files@、@too-large@、@zip-bomb@、@data-directory@、
@missing-exe@、@missing-version@、@version-mismatch@、@invalid-zip@、@invalid-version@、
@size-mismatch@、@manifest-missing@、@invalid-manifest@、@signature@、@extract-failed@、@disk-write@。

只操作本版本 @updates/<version>/staging/@：不覆盖程序目录、不碰用户数据、不做安装/替换/回滚。

## 2. 真实验证（公网 v1.7.1 完整包）

| 场景 | 结果 |
|---|---|
| 真实包（43,238,599 B）下载→校验→解压 | **714 个文件 / 99,436,397 字节**；staging 内 @PaperLibrary.exe@ 与 @version.json@ 存在；
@ready.json@ 写入；状态 @ready@ |
| 路径穿越 @../evil.txt@ | 拒绝 @unsafe-path@；staging/ready 均删除；staging 外无 @evil.txt@；状态 failed |
| 符号链接条目 | 拒绝 @symlink@；同上清理 |
| 顶层 @data/library.db@ | 拒绝 @data-directory@；清理 |
| 缺 @PaperLibrary.exe@ | 拒绝 @missing-exe@；清理 |
| @version.json@ 为 1.6.0（清单 1.7.1） | 拒绝 @version-mismatch@；清理 |
| zip bomb（25 MB 零、高压缩比） | 拒绝 @zip-bomb@；不产生 staging 内容 |

## 3. 单测（test/test_update_staging.py，14 条）

成功解压 + ready；重试清理旧 staging；6 种路径穿越/绝对路径/盘符；符号链接；zip bomb；
文件数超限；单文件超限；data/；缺 exe；缺 version.json；版本不符；非法 zip；
成功后失败会清掉旧 ready/staging；inspect_entries 统计。

## 4. 验收对照

| P2-04 验收项 | 结果 |
|---|---|
| 解压到 staging/<version>/，不直接覆盖 | PASS（本版本 updates/<version>/staging/） |
| 拒绝绝对路径/盘符/../路径穿越 | PASS（单测 6 种 + 真实样本） |
| 拒绝符号链接 | PASS |
| 拒绝异常文件数/体积（zip bomb） | PASS（文件数/单文件/总量/压缩比四道限制） |
| 必须含 PaperLibrary.exe 与 version.json 且版本一致 | PASS |
| 包内不得含 data/ | PASS |
| 写入 ready.json 后才可用 | PASS（成功才写；失败清理） |
| 失败清理临时产物 | PASS（staging/ready 删除、状态 failed） |

## 5. 测试

- @python -m unittest test.test_update_staging -v@ → **14 tests OK**；
- 全量：**595 tests OK（1 skip）**（P2-03 后 581 + 14）。

## 6. 说明

- 安全阈值（5000 文件 / 512 MB 单文件 / 1 GB 总量 / 200:1 压缩比）是本阶段选定的保守默认值，
  如后续包体积增长可调整，不影响接口。
- 本步止于“staging 可用（ready.json）”；不写下载/安装 API、不做 apply/替换/回滚/helper。
