# P2-06 GET /api/update/status（进度/阶段/ready/错误/暂存版本）

日期：2026-11-16　阶段 2B / P2-06　状态：已完成并核验

## 1. 端点契约

@GET /api/update/status@（无参数，只读，**不会启动下载**）：

@200 {ok, phase, running, version, progress:{downloadedBytes,totalBytes,percent},
ready, stagingVersion, stagingPath, error, code, updatedAt, lastCheckAt}@

- @phase@：@idle@ / @checking@ / @downloading@ / @verifying@ / @verified@ / @extracting@ /
  @ready@ / @failed@ / @cancelled@（@verified@ = 下载已校验、等待解压）；
- @progress.percent@：0–100 的整数（total 未知时为 0）；
- @running@：是否有后台任务在跑（checking 也算）；
- @ready@ + @stagingVersion@ + @stagingPath@：staging 已通过 P2-04 全部校验时给出；
- @error@ + @code@：仅 failed/cancelled 时非空；
- @version@：当前目标版本；
- @updatedAt@/@lastCheckAt@：状态更新时间/最近一次检查时间。

实现：@paperlib/api/update.py: status@ → @UpdateService.snapshot()@；状态读内存快照（线程锁），
后台线程只通过锁更新快照并原子写 @download-state.json@，因此查询与写入无竞态。

## 2. 验收对照

| P2-06 验收项 | 证据 | 结果 |
|---|---|---|
| 下载进度 | 真实运行：downloading 时 percent 0→100，ready 时 100 | PASS |
| 校验/解压阶段 | 真实运行阶段序列含 verifying/verified/extracting/ready | PASS |
| 是否 ready | idle ready=false；完成后 ready=true + stagingVersion | PASS |
| 错误信息 | 失败任务 status 返回 failed/code/error（单测） | PASS |
| 当前暂存版本 | ready 时 stagingVersion=1.7.1、stagingPath 非空 | PASS |
| 只读、不触发下载 | 单测：调用 status 后未创建 updates 目录、start_download 调用数 0 | PASS |
| 并发查询无竞态 | 12 线程×40 次服务快照（单测）+ 20 线程×10 次真实 HTTP 并发查询 | PASS（无异常、全部 200、字段齐全） |

## 3. 真实验证（临时 1.6.0 副本 + 公网 v1.7.1）

- idle：200，字段齐全，phase=idle、ready=false；
- 下载中并发查询：**200 次请求（20 线程 × 10）全部 200、字段齐全、无异常**；
- 通过 status 轮询到阶段序列：@downloading → verified → ready@；
- 完成后：@phase=ready, running=false, ready=true, stagingVersion=1.7.1, version=1.7.1,
  percent=100, error/code 为空@；
- 最新版本服务（v1.7.1）上的 idle：phase=idle、ready=false、progress 全 0。

## 4. 单测（test/test_update_status.py，5 条）

idle 只读字段/不创建目录；ready 字段；failed 字段；12 线程并发快照无异常且字段合法；
API handler 返回快照且不调用 start_download。

## 5. 测试与契约

- @python -m unittest test.test_update_status -v@ → **5 tests OK**；
- 全量：**614 tests OK（1 skip）**（P2-05 后 609 + 5）；
- 契约：**PASS 61 / FAIL 0 / SKIP 12（共 73）**；status 为 GET 只读端点，快照登记为 PASS 行。

## 6. 说明

- @verified@ 阶段在 P2-03 引入，本步 status 会短暂展示（真实运行已观测到）。
- 状态在服务重启后可从 ready.json / download-state.json 恢复（P2-05 已实现并测试）。
