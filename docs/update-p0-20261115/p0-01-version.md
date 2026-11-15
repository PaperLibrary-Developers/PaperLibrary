# P0-01 统一版本来源（version.json）

日期：2026-11-15　阶段 0 / P0-01　状态：已完成并核验

## 1. 目标与方案

产品版本此前分散在 5 处硬编码：启动器窗口标题（`tools/make_launcher.ps1`）、
启动横幅（`paperlib/app.py`）、侧栏与页面标题（`public/js/app.mjs`、`public/index.html`）、
README、使用说明、requirements 注释。按方案一原文 §3.1，统一为包根 `version.json`：

- 仓库根 `version.json` 是唯一维护点：`product` / `version`（semver，如 1.6.0）/ `tag`（v1.6）。
- 发布时 `tools/release.py` 生成包内 `version.json`，补 `builtAt`（UTC）/ `commit`（git HEAD），
  并在 `release-manifest.json` 写入同样的 product/version/tag/builtAt/commit。
- 启动器、后端、前端运行时读取同一份：窗口标题、启动横幅、`GET /api/version`、侧栏版本号、
  页面 `<title>`、使用文档版本芯片全部来自它。

版本显示规则：界面显示 `tag`（v1.6）；更新比较用 semver `version`（1.6.0）。下一版版本号尚未确定，
本步沿用当前已发布版本 v1.6 / 1.6.0，没有猜测 v1.7。

## 2. 改动清单

| 文件 | 改动 |
|---|---|
| `version.json`（新增） | 唯一版本来源：`{product, version:1.6.0, tag:v1.6}` |
| `paperlib/version.py`（新增） | 读取/校验/归一化 version.json；semver、tag=v<major>.<minor>；构建记录（补 builtAt/commit） |
| `paperlib/api/system.py` | 新增 `GET /api/version`：返回 product/version/tag/ui/builtAt/commit；文件缺失/损坏时 500 + 中文原因 |
| `paperlib/routes.py` | 注册 `/api/version`（P0-01 验收明确要求后端该端点） |
| `paperlib/app.py` | 启动横幅改为读 version.json |
| `public/js/api.mjs`、`app.mjs`、`views/guide.mjs` | 启动时取 /api/version；侧栏版本、页面标题、文档版本芯片统一取自它；取不到时不显示写死版本 |
| `public/index.html`、`public/styles.css` | 去掉硬编码 v1.6（title/meta/alt/注释） |
| `tools/make_launcher.ps1` | 启动器启动时读包根 version.json 的 tag 组成窗口标题；文件缺失/非法时退化为无版本标题（不影响启动）。保持 UTF-8 BOM 与 LF 不变 |
| `tools/release.py` | 生成包内 version.json；manifest 增加 product/version/tag/commit 并与 version.json 对齐；校验二者逐字段一致 |
| `tools/package_zip.py` | zip 关键条目检查加入 version.json |
| `tools/version_check.py`（新增） | 版本一致性检查：version.json 合法；受控源码/前端/文档无当前版本号硬编码；`--package` 时包内 version.json 与 release-manifest.json 一致 |
| `test/test_version.py`（新增） | 10 条：version.json 合法、非法输入、构建字段、roundtrip、缺失文件、/api/version、一致性检查（含篡改检测、包内缺字段检测） |
| `shared/contracts/api.md`、`api-snapshot.json`、`tools/contract_check.py` | 端点清单 69→70 条，登记 /api/version 契约 |

交付物对应关系：生成逻辑（release.py）、读取封装（后端 paperlib/version.py + /api/version、前端 app.mjs、
启动器 ReadVersion）、版本一致性检查（tools/version_check.py + test/test_version.py）。

说明：P0-01 验收标准明确要求“UI、窗口标题、后端 /api/version 三处版本一致”，因此本步实现了只读的
`GET /api/version` 与契约登记；阶段 1 的“检查更新/下载/安装”等未触碰。

## 3. 核验记录

| 项目 | 命令 | 结果 |
|---|---|---|
| 单元测试（全量） | `python -m unittest discover -s test` | **458 tests OK**（基线 448 + 新增 10） |
| 契约快照 | `python tools/contract_check.py` | PASS 60 / FAIL 0 / SKIP 10（共 70） |
| 版本一致性（仓库） | `python tools/version_check.py` | 通过：无硬编码版本 |
| 前端语法 | `node --check public/js/app.mjs`（api/guide 同） | 退出码 0 |
| 发布构建 | `python tools/release.py` | 成功：version.json 已生成，manifest 与 version.json 一致，94.7 MB |
| 版本一致性（包） | `python tools/version_check.py --package dist/PaperLibrary-20261115-212130-90008` | 通过 |
| 真实程序 | 临时目录解压包 → 启动 PaperLibrary.exe | 窗口标题 **PaperLibrary v1.6 Nexus**（读 version.json）；`/api/version` → 1.6.0 / v1.6；界面侧栏显示 v1.6（见截图） |

真实程序证据：`p0-01-version-window.png`（1296×859，含标题栏与侧栏版本号）。
启动器日志：`window title from version.json: PaperLibrary v1.6 Nexus`、`page loaded`；
关闭后 4321 无监听、无 PaperLibrary 残留进程。

## 4. 遗留与说明

- 下一版版本号仍未确定（待确认项），本步不猜测；发版时只改 `version.json` 一处。
- 包内 `release-manifest.json` 是构建清单（不是更新清单 `update-manifest.json`，后者在 P0-02 定义）。
- 旧的 dist 构建产物不含 version.json，属于历史产物，不影响源码与后续发布。
