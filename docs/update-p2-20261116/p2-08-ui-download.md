# P2-08 设置页更新区（检查/下载/取消/稍后/跳过交互）

日期：2026-11-16　阶段 2C / P2-08　状态：已完成并核验

## 1. 实现

| 文件 | 改动 |
|---|---|
| @public/js/api.mjs@ | 新增 @startUpdateDownload()@ / @cancelUpdateDownload()@ / @updateStatus()@ |
| @public/js/views/settings.mjs@ | 「关于与更新」区扩展：检查→发现新版本→下载并准备安装→进度（百分比/已下载/总量）→取消/稍后；ready 显示“更新已准备好（关闭应用后安装）”；失败可重试；进入设置页时只读恢复进行中/ready 状态 |
| @public/styles.css@ | 进度条与操作按钮样式 |
| @paperlib/api/update.py@ | @POST /api/update/download@ 支持可选 @{cancel:true}@（取消当前下载，方案 B 取消即删） |
| @paperlib/update/service.py@ | 取消支持（@cancel_download@）+ **进度快照修复**：查询时读取最新 @download-state.json@，否则 UI 只能看到启动时 0 字节 |
| @docs/update-p2-20261116/scripts/update-download-ui.mjs@ | CDP 真实验收脚本（检查→下载→取消→重新下载→ready→稍后→跳过） |
| @test/test_update_service.py@、@test/test_update_status.py@ | 取消动作 API 测试、进度快照回归测试 |

## 2. 真实 UI 验收（临时 1.6.0 副本 + 公网 v1.7.1，headless 浏览器）

| 状态 | 证据 |
|---|---|
| 可用更新 | 状态「发现新版本 v1.7.1」；最新版本/时间/大小（41.2 MB）/说明链接/跳过按钮 |
| 下载中 | 「下载中 **24%**（10.0 / 41.2 MB）」+ 进度条 + 取消/稍后按钮 |
| 取消（方案 B） | 「已取消下载（临时文件已清理）」；@data/updates@ 下**无任何版本目录/残包**（脚本核对 leftovers=[]） |
| 重新下载 → ready | 「更新已准备好：1.7.1（关闭应用后安装）」；显示待安装版本/当前版本；操作区“稍后”（安装按钮在 P2-09） |
| 稍后 | 「已稍后：更新任务在后台继续，下次打开设置页可查看进度」+ “查看进度”入口；恢复后仍显示 ready |
| 跳过此版本 | 「发现新版本 v1.7.1（已跳过）」，写入 prefs @updateSkippedVersion@ |
| JS 错误 | 0 |

截图：@docs/update-p2-20261116/screenshots/settings-update-available.png@、
@settings-update-downloading.png@、@settings-update-cancelled.png@、@settings-update-ready.png@。

## 3. 验收对照

| P2-08 验收项 | 结果 |
|---|---|
| 当前版本/检查按钮/自动检查开关/下载 | PASS（真实 UI） |
| 状态文案：检查中/发现新版本/下载中 42%/校验中/已准备重启 | PASS（进度真实显示百分比；校验/解压阶段文案就绪） |
| 查看更新说明/下载/稍后/跳过 | PASS（真实点击/链接） |
| 取消可用 | PASS（方案 B：取消即删，无残包） |
| 错误可诊断 | PASS（检查/下载失败在面板内显示错误，可重试） |
| 不直接下载/无 CORS/代理直连 | PASS（全部走本地 @/api/update/*@） |
| 安装按钮 | 留给 P2-09（本步只显示 ready 状态） |

## 4. 测试与契约

- @python -m unittest discover -s test@ → **631 tests OK（1 skip）**（基线 629 + 2：取消 API、进度快照）；
- 契约：仍 **PASS 61 / FAIL 0 / SKIP 13（共 74）**（download 行入参补充 @{cancel:true}@，无新增端点）。

## 5. 说明

- 取消后 @data/updates@ 空目录可能保留（版本目录与全部文件已删），不属于可续传残包。
- 自动检查默认开关仍是待确认项（P1-04），本步未改变。
