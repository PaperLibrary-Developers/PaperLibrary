# P2-02 下载策略与取消

日期：2026-11-16　阶段 2A / P2-02　状态：已完成并核验

## 1. 实现

@paperlib/update/download.py@：

- **仅 HTTPS + 主机白名单**：初始 URL 与每一次重定向都用 @release.allowed_download_url@
  （@github.com@、@release-assets.githubusercontent.com@）校验；HTTP 地址在发请求前拒绝。
- **流式写入**：1 MB/块写入 @package.zip.part@，不把整包读进内存；进度回调 + 状态文件同步
  @downloadedBytes/totalBytes@。
- **Range 续传**：已有 .part 时发 @Range: bytes=N-@；
  - 206 且 @Content-Range@ 起点等于本地偏移 → 追加；
  - 服务器返回 200（不支持 Range）或 416（区间无效）→ 丢弃 .part 从头下载；
  - 206 但区间起点不匹配 → 从头重下（不追加脏数据）。
- **磁盘预检**：下载前按 @required = zip + 暂存(≈2×zip) + 旧程序备份(≈2×zip) + 256 MB@ 估算，
  用 @shutil.disk_usage@ 读取真实可用空间；不足直接失败，**不发起网络请求**，状态置
  @failed/disk-full@ 并在消息里给出“需要约 X MB / 可用约 Y MB”。
- **取消**：调用方传入 @threading.Event@，每块检查；取消后保留 .part 以便续传，状态置
  @cancelled@，抛 @DownloadCancelled@。整个过程是同步函数调用，不启动子进程/后台线程。
- **传输完整性**：Content-Length/Content-Range 已知时，连接提前结束（字节数不足）判
  @network@ 失败；下载字节数超过清单 size 立即中止（size-mismatch）。
- 下载成功只落到 @.part@；大小/SHA-256/签名校验在 P2-03，解压在 P2-04。

## 2. 真实验证（公网 v1.7.1 + 真实磁盘）

| 场景 | 证据 |
|---|---|
| 真实资产下载 | manifest v1.7.1、asset 43,238,599 B；流式下载成功 |
| 取消 | 下载 2.45 s 后按进度取消：.part = 2,097,152 B，状态 @cancelled@，无残留线程（active 1→1） |
| 断点续传 | 再次调用：**resumedFrom=2,097,152**（GitHub 返回 206，Range 生效），最终 43,238,599 B，耗时 8.33 s |
| 磁盘不足提前失败 | 真实磁盘可用 29,313 MB；以 10 TB 需求触发预检：@disk-full@ 并在开始下载前失败，状态 @failed/disk-full@，无 .part |

说明：磁盘不足场景用“真实磁盘读数 + 超大需求（10 TB）”触发，失败发生在发起请求前；
另用 @free_bytes@ 注入的低空间单测覆盖同一代码路径。取消后半成品保留策略见待确认项。

## 3. 单测覆盖（test/test_update_download.py，13 条）

- 完整下载流式写 .part、进度单调、状态落盘；
- HTTP 地址默认拒绝（未发请求）；重定向到允许主机成功；重定向到非白名单主机拒绝；
- Range 续传（206、resumedFrom、内容一致）；服务器不支持 Range → 200 从头重下；
  Content-Range 起点不匹配 → 重下；
- 取消保留 .part + cancelled 状态；连接提前结束（drop）→ network 失败 + 保留 .part；
- 磁盘不足提前失败（注入低可用空间）+ 状态 failed/disk-full；空间公式；
- 下载字节超过清单 size → size-mismatch；404 → network 失败。

## 4. 验收对照

| P2-02 验收项 | 结果 |
|---|---|
| HTTP 地址被拒 | PASS（单测，未发请求） |
| 重定向到非允许主机被拒 | PASS（单测） |
| 断点续传生效 | PASS（真实 GitHub 206 + 单测） |
| 取消后无残留进程、半成品按策略处理 | PASS（同步调用无子进程/线程；保留 .part 供续传，策略待确认） |
| 磁盘不足提前失败并提示 | PASS（真实磁盘读数 + 单测注入） |
| 流式、不整读内存 | PASS（1 MB/块 + 进度回调；真实下载 43 MB） |

## 5. 测试

- @python -m unittest test.test_update_download -v@ → **13 tests OK**；
- 全量：**569 tests OK（1 skip）**（P2-01 后 556 + 13）。

## 6. 待确认项

**下载取消后的半成品处理策略**（原文 §5.2「按策略处理」，todolist 待确认第 9 项）：

- 当前实现：**保留 .part**（支持下次续传，避免重下 43 MB）；状态为 @cancelled@；
- 可选：A（建议）保留到用户清理/下次成功下载；B 取消即删除 .part；C 保留但超过 7 天自动清理。
- 若选 B/C，只需要在取消路径增加一次 @unlink@ 或清理任务，不影响其它流程。
