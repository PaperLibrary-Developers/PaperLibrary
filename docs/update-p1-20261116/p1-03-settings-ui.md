# P1-03 设置页「关于与更新」只读区

日期：2026-11-16　阶段 1 / P1-03　状态：已完成并核验

## 1. 实现

| 文件 | 改动 |
|---|---|
| @public/js/api.mjs@ | 新增 @api.checkUpdate()@ → @GET /api/update/check@ |
| @public/js/views/settings.mjs@ | 新增「关于与更新」面板：当前版本（@/api/version@）、「检查更新」按钮、状态行、最新版本/发布时间/大小/说明链接；三态（检查中/成功/失败）；失败给出可读原因与重试入口 |
| @public/styles.css@ | 面板样式（与设置页其他 panel 一致的留白/字号，无横向溢出） |
| @docs/update-p1-20261116/scripts/settings-update-check.mjs@ | CDP 验收脚本：打开 @#/settings@ → 读当前版本 → 点检查（加载态）→ 等结果（成功态）→ 拦截 API 请求（失败态）→ 三张截图 + JS 错误断言 |

行为边界（阶段 1）：

- 只检查、只展示：没有下载/安装按钮，不写文件、不后台安装；
- 手动检查始终可用；检查失败只在面板内提示，不弹窗、不影响启动；
- 当前版本来自 @version.json@（@/api/version@），最新版本/大小/说明来自签名清单（@/api/update/check@）。

## 2. 验收（真实浏览器驱动，非静态检查）

服务：临时 data 目录启动 @python -m paperlib --port 4397 --no-open --idle-exit 0@；
浏览器：headless Edge/Chrome（CDP，真实 @public/@ 页面）。

| P1-03 验收项 | 证据 | 结果 |
|---|---|---|
| 显示当前版本 | @#update-current-version@ = **v1.7.1** | PASS |
| 手动检查三态 | 加载：@正在检查更新…@（class @is-loading@）；成功：@已是最新版本@（@is-ok@）；失败：@检查失败：连不上本地服务…@（@is-err@） | PASS |
| 最新版本/说明/大小与清单一致 | 最新版本 @v1.7.1@、发布时间 @2026-11-15 22:52@、大小 @41.2 MB@（清单 43,238,599 B） | PASS |
| 失败可读、可重试 | 面板内展示真实原因 + 提示“自动检查失败不会打扰你”，按钮恢复可点 | PASS |
| 不弹窗打断启动 | 面板仅在设置页；启动时无更新弹窗 | PASS |
| 前端无 JS 错误 | CDP 收集：@errors.js = []@；网络错误 @[]@（失败态来自主动拦截） | PASS |

截图（真实页面）：

- @docs/update-p1-20261116/screenshots/settings-update-idle.png@（未检查）
- @docs/update-p1-20261116/screenshots/settings-update-loading.png@（检查中）
- @docs/update-p1-20261116/screenshots/settings-update-success.png@（已是最新 + 版本/时间/大小）
- @docs/update-p1-20261116/screenshots/settings-update-failure.png@（失败态）

报告 JSON：见本步验收日志（currentVersion=v1.7.1；三态文本/class；node_exit=0；关闭后 4397 无监听）。

## 3. 测试与构建

| 项目 | 命令 | 结果 |
|---|---|---|
| 前端语法 | @node --check public/js/api.mjs@ / @settings.mjs@ | 退出码 0 |
| 全量测试 | @python -m unittest discover -s test@ | **544 tests OK（1 skip）**（本步只改前端，无新增失败） |
| 契约 | @python tools/contract_check.py@ | 仍为 71 条（本步无后端改动） |

## 4. 说明

- 自动检查（启动后检查、24 小时节流、跳过版本）属于 P1-04，本步未实现；默认开关仍为待确认产品项。
- 面板中的“更新说明”链接在 WebView2 中通过新窗口请求交给系统默认浏览器打开（沿用启动器既有行为）。
