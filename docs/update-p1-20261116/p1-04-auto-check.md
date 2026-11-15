# P1-04 自动检查（只通知，不自动下载）

日期：2026-11-16　阶段 1 / P1-04　状态：已完成并核验；**默认开关仍为待确认项**（见第 5 节）

## 1. 实现

| 文件 | 改动 |
|---|---|
| @public/js/update-auto.mjs@（新增） | 纯调度模块：启动后 10–30 秒随机延迟检查一次；之后每 24 小时最多一次；失败静默；只在新版本且未被跳过时通知；不启动任何子进程/后端任务 |
| @public/js/app.mjs@ | 页面启动后创建 autoChecker：check=@/api/update/check@，notify=轻量 toast，lastCheckAt/skippedVersion 存服务端 prefs；设置页开关变化时 @setEnabled@ 立即生效 |
| @public/js/views/settings.mjs@ | 「关于与更新」增加自动检查开关（默认关，待确认）、提示文案、已跳过版本与“取消跳过”；检查到新版本时提供“跳过此版本” |
| @public/styles.css@ | 开关样式 |
| @test/js/update-auto.test.mjs@（新增） | Node 11 条：启动延迟范围、24h 节流、失败静默、通知/跳过、stop、启用/停用重排 |
| @test/test_update_auto.py@（新增） | 调起 Node 测试（无 Node 时 skip），纳入 unittest 全量 |
| @docs/update-p1-20261116/scripts/update-auto-check.mjs@ | CDP 真实验收脚本（旧版本副本服务 → 触发真实新版本通知、节流、跳过、关闭） |

关键行为：

- **启动后检查**：页面加载后延迟 10–30 秒（随机）异步检查一次；
- **24 小时节流**：用服务端 prefs 的 @updateLastCheckAt@ 记录（成功/失败都记），刷新/重开也遵守；
- **失败静默**：不弹错误、不阻塞应用，只记录尝试时间；
- **只通知**：新版本且未跳过时 toast「发现新版本 vX」，不下载、不安装；
- **关闭后不检查**：定时器只活在页面里；页面/应用关闭即结束，没有后台进程或计划任务；
- **跳过版本**：@updateSkippedVersion@ 记录，自动检查不再提示该版本。

## 2. 真实验收（不是 mock：临时副本的 version.json=1.6.0，线上 latest=v1.7.1）

服务：临时副本（public/shared/paperlib/vendor + version.json 1.6.0）在 4396 启动；
浏览器：headless Edge/Chrome（CDP），报告 JSON：

| 场景 | 结果 |
|---|---|
| 开启后首次检查 | @runNow() = checked@；toast「发现新版本 v1.7.1，可在「设置 → 关于与更新」查看」 |
| 立刻再检查 | @throttled@；无重复 toast（24 小时节流生效） |
| 把 lastCheckAt 设为 25 小时前 | 再次 @checked@ 并通知（边界生效） |
| 已跳过 v1.7.1 | @checked@ 但不通知 |
| 关闭自动检查 | @state.scheduled=false@；@runNow() = disabled@ |
| 页面 JS 错误 | @errors.js = []@；网络错误 @[]@ |
| 设置页开关可见 | @#update-auto-toggle@ 存在（默认未勾选） |

截图：@docs/update-p1-20261116/screenshots/update-auto-toast.png@（轻量通知）、
@settings-update-auto-toggle.png@（面板与开关，裁剪到面板避免带本机路径）。

“关闭后无检查进程”核对：验收后仅剩 DSH 自身的 node 进程；无 headless Edge 残留、4396/9402 无监听；
代码层面 autoChecker 无子进程、无后端线程/计划任务。

## 3. 测试

| 项目 | 命令 | 结果 |
|---|---|---|
| Node 调度测试 | @node test/js/update-auto.test.mjs@ | **11 tests passed** |
| Python 包装测试 | @python -m unittest test.test_update_auto -v@ | 1 test OK |
| 全量测试 | @python -m unittest discover -s test@ | **545 tests OK（1 skip）**（P1-03 后 544 + 1） |
| 前端语法 | @node --check public/js/update-auto.mjs@app.mjs@ / @settings.mjs@ | 退出码 0 |
| 契约 | 本步无后端改动，仍 71 条（PASS 60 / SKIP 11） | 无回归 |

## 4. 与原文 §4.1/§8 的对应

- 启动后 10–30 秒异步检查：实现（随机延迟）；
- 运行期间每 24 小时一次：实现（prefs 时间戳节流）；
- 应用关闭后不检查、不做计划任务：实现（页面定时器，无后台进程）；
- 自动检查只通知、不自动下载：实现（仅 toast + 面板信息）；
- 记录“跳过此版本”：实现；
- 默认开关：**未定**，见第 5 节。

## 5. 待确认项（不猜值）

**自动检查默认开还是关**（原文 §8：「默认开或关由产品决定」）：

- 当前实现：**默认关**（避免未经同意的联网检查；用户可在设置页开启）。
- 可选方案：
  - A（建议）：确认后改为**默认开**，只通知、不下载——更符合“有更新能及时知道”，且不产生下载流量；
  - B：保持默认关，把开关作为显式选择（隐私最保守）。
- 改动成本：默认值只在 @app.mjs@ 的 enabled 判定与设置页初值两处调整（一行级），可随 P1-05 或后续版本调整。
