# P2-05 POST /api/update/download（启动下载/续传）

日期：2026-11-16　阶段 2B / P2-05　状态：已完成并核验

## 1. 端点契约

@POST /api/update/download@（无请求体）：

- 200 @{ok, started, phase, running, version, progress:{downloadedBytes,totalBytes,percent},
  ready, stagingVersion, stagingPath, error, code, updatedAt, lastCheckAt}@
  - 后台执行“检查清单 → 下载 → 校验 → 解压 staging”；返回时任务可能仍在 @downloading@；
  - **同一时间只允许一个任务**：重复调用返回当前状态且 @started=false@（不并发重复下载）；
  - 该版本已有可用 staging（ready）时直接返回 @ready=true@、@started=false@，不重复下载；
- 400 @{ok:false, code, error}@：当前已是最新/更新源版本不高于当前（code=current/older/unsupported-*）；
- 502：清单拉取/验签失败（timeout/network/no-manifest/invalid-manifest/signature 等）；
- 500：本地 @version.json@ 损坏。

服务实现：@paperlib/update/service.py: UpdateService@；复用阶段 2A 的
@store/download/verify/staging@，未重写下载/校验/解压逻辑。

## 2. 关键行为

- **检查先于下载**：启动前同步拉取并验签清单（最多 ~9s），失败直接返回 502，不会留下半启动任务；
  下载使用同一份已验签清单（写入 @manifest.json@），不信任客户端传入的 URL/版本。
- **单任务锁**：内存锁 + @_active/thread@ 判定；并发/重复 POST 只返回快照。
- **进度与阶段落盘**：@download-state.json@ 依次 @checking → downloading → verifying → extracting → ready@；
  失败 @failed@ + code/error，取消 @cancelled@。
- **失败可重试**：再次 POST 会复用同版本 @.part@（2A 的 Range 续传）；失败状态不会被当成“已完成”。
- **重启恢复**：服务启动时若发现上次停在 @downloading/verifying/extracting@，标记
  @failed/interrupted@ 并提示可续传；已有 @ready.json@ 的版本直接识别为 ready，不重复下载。
- 不暴露取消端点（阶段 2A 的 cancel_event 保留给后续 UI/接口）；不自动安装。

## 3. 真实验证（临时 1.6.0 副本 + 公网 v1.7.1）

- POST #1 → 200 @started=true, phase=downloading, running=true@；
- 立刻 POST #2 → 200 @started=false, phase=downloading, running=true@（**未并发重复下载**）；
- @download-state.json@ 阶段变化：@downloading(1.51s) → verifying(10.85s) → extracting(11.05s) →
  ready(13.06s)@，最终 @downloadedBytes=99,436,397@（staging 解压体积）；
- ready 后 POST #3 → 200 @started=false, ready=true, stagingVersion=1.7.1, percent=100@；
  staging 内 @PaperLibrary.exe@ 与 @version.json@ 存在；
- 当前版本已是 v1.7.1 的真实副本：POST → **400 code=current**「已是最新版本」。

## 4. 单测（test/test_update_service.py，14 条）

完整流程到 ready；重复调用不重复下载（含 ready 后）；失败后重试成功；已是最新 → 400；
清单网络错误传播；检查中状态可见且重复调用被拒；ready 跨服务重启保留；
中断状态标记 failed/interrupted 且 .part 保留；check_with_manifest 返回已验签清单；
API handler 的 200/400/502/500 映射。

## 5. 测试与契约

- @python -m unittest test.test_update_service -v@ → **14 tests OK**；
- 全量：**609 tests OK（1 skip）**（P2-04 后 595 + 14）；
- 契约：**PASS 60 / FAIL 0 / SKIP 12（共 72）**；@/api/update/download@ 为网络/有副作用端点，快照标记 skip，
  契约登记在 @api.md@。

## 6. 说明

- @phase@ 集合新增 @checking@；状态文件字段沿用 P2-01 定义。
- 取消后半成品策略（保留 .part）仍是待确认项（同 P2-02），本步未改变。
