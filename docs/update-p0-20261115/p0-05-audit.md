# P0-05 公开前安全审计（源码 / 全历史 / 文档配置 / 旧 Release 资产）

日期：2026-11-15　阶段 0 / P0-05
状态：**审计完成；发现 1 类阻塞问题（用户数据在公开 git 历史），需用户决策后才能处置**。源码侧问题已在本步修复。

## 1. 范围与方法

- 工具：`tools/audit_sensitive.py`（本步新增，可复现）——
  工作区 284 个文本文件、全部 git 引用可达的 978 个 blob（`runtime/`、`vendor/` 里第三方依赖仅计数、
  不做内容判定，避免上游测试向量假阳性）、11 个发布 zip（v1.0-final、v1.1、v1.2、v1.3、v1.4、v1.5、v1.6
  的 Release 资产 + 本地历史构建 + 最新构建）。
- 规则：HIGH = 常见服务密钥格式（sk-*、GitHub token、AWS、Google、Slack、JWT、私钥块）+ 历史用户数据路径；
  MEDIUM = 疑似凭据赋值、本机用户目录绝对路径。输出**脱敏**（不打印完整密钥）。
- 人工核对：引用可达性（`git for-each-ref --contains`）、Release 资产清单与内容、
  数据提交内容结构（只读检查表/数量，不导出用户正文）。
- 证据：`docs/update-p0-20261115/p0-05-audit-summary.json`（脱敏摘要）。

## 2. 结论摘要

| 检查项 | 结论 |
|---|---|
| API Key / 私钥 / 令牌 | **未发现真实密钥**。HIGH 规则命中的 4 条 `sk-*` 与 1 条私钥块全部来自旧提交里 vendored 的 `runtime/Lib/site-packages/cryptography/.../ssh.py`（`sk-ssh-*` 算法常量与测试向量），不是本项目密钥 |
| 用户数据（数据库/配置/全文/日志） | **发现，且在公开历史中可直接访问（阻塞）**，见第 3 节 |
| 本机路径（工作区跟踪文件） | **已清理**（本步提交）；工作区跟踪文件 0 命中，仅 `HANDOFF.md`（gitignored 本地文件）与 `docs/` 本地证据仍有路径 |
| 本机路径（git 历史） | 仍有 27 处 MEDIUM：旧 `docs/工程报告v1.md`、旧 `test/test_migrate.py`、`tools/migrate_fixtures.py`、`test_title_extract.py`、`回归测试摘要.md` 等（只有用户名/目录名，无数据/密钥） |
| 旧 Release 资产 | 11 个 zip 均**无** `data/`、settings、数据库、密钥；旧包内 `tools/migrate_fixtures.py` 含本机路径字符串（20 处 MEDIUM），新构建已清理 |
| 更新签名私钥 | 在仓库外（`%USERPROFILE%\.paperlibrary\update-signing-key.json`），仓库/历史/资产中未出现（P0-03 已验证） |

## 3. 用户数据暴露明细（阻塞项）

仓库当前 **public**（0 fork），实测匿名访问 commit 页面返回 200。暴露点全部在**非 main、非 v1.x 发布标签**的旧分支/旧 tag 历史里：

| 提交 | 内容 | 暴露引用 | 敏感内容 |
|---|---|---|---|
| `b9d1514`（2026-11-07 `上传了sci lib的源码`） | `data/library.json`、`data/prefs.json`、`data/server.log`、`data/backups/*.json`（5 个）、`data/fulltext/*.txt` | 已推送 tag：`baseline-20261107`、`step-P0-done` … `step-P10-summary-done`（12 个）；分支 `sci-lib-refactor/p0-safety-net` | 1 篇文献元数据与摘要、阅读偏好、启动日志（含本机路径）、正文全文文本 |
| `c86fa598`（`推送完整的PaperLibrary到相应分支`） | `app/data/` 共 1476 个文件：`papers.db`（8,949,760 字节）、`.app_config.json`、`.pdf_index.json`、1441 张图、`thumb_cache/`、`pdfjs/` | 公开分支 `PaperLibrary`（该分支后续提交 `23f4308` 删除了 `app/data/`，但历史仍可访问） | SQLite 库含 **76 篇文献元数据 + 76 篇正文全文 + 1441 张图表图片**；配置仅含本机文献扫描根目录（路径略）；**无 API Key、无 PDF 原件、无笔记** |

说明：`main` 与 v1.0-final…v1.6 发布标签**不包含**这些提交（已核对 `git merge-base --is-ancestor`），
所以公开的发布 tag/Release 本身没有用户数据；暴露来自旧分支与旧整理 tag。

## 4. 已处置（随本步提交）

| 文件 | 改动 |
|---|---|
| `test/test_migrate.py` | 真实旧库路径改为环境变量 `PAPERLIB_SCILIB_REAL` / `PAPERLIB_PAPERLIBRARY_REAL`；未设置时跳过真实库测试（test_migrate 仍 13 通过 + 1 skip） |
| `tools/migrate_fixtures.py` | 同上；`--make-fixture` 未给 `--source` 且无环境变量时明确报错 |
| `paperlib/legacy/*.py`（9 个） | 注释里的本机用户目录路径改为“旧 PaperLibrary 工程”（保留说明、去掉本机路径） |
| `docs/fullread-e2e-20261115/README.md` | 测试数据路径改为“本机测试目录（路径略）”（该文件此前被强制提交） |
| `tools/release.py` | `release-manifest.json` 的 `target` 改为相对仓库根的路径——此前**每个分发包**都带本机用户目录的绝对路径 |
| `.gitignore` | 新增 `/.paperlibrary/`、`/keys/`、`update-signing-key*.json`、`*.ed25519.key` |
| `tools/audit_sensitive.py`（新增） | 可复现审计：工作区 + 全历史 + zip；输出脱敏；HIGH 时退出码 1 |

验证：最新构建包与 zip 审计**0 条本机路径、0 条用户数据、0 条密钥**；工作区跟踪文件 0 条本机路径。

## 5. 未处置（需用户确认）：公开历史中的用户数据

**本步没有删除分支/标签，也没有改写历史**：这是破坏性且影响公开仓库的操作，按交接约定须用户明确授权。

- **R1（建议先做，最小破坏）**：删除暴露 refs（旧的整理分支与整理 tag，与 v1.x Release 无关）：
  `git push origin --delete PaperLibrary sci-lib-refactor/p0-safety-net`
  `git push origin --delete baseline-20261107 step-P0-done step-P1-done step-P2-done step-P3-done step-P4-done step-P5-done step-P6-done step-P7-done step-P8-arch-done step-P9-search-fix-done step-P10-summary-done`
  然后向 GitHub Support 申请清理不可达对象缓存（或等待 GC）；当前 0 fork，没有下游副本需要通知。
  影响：`main`、v1.0-final…v1.6 标签与 Release 不受影响。
- **R2（彻底）**：用 `git filter-repo` 从全部历史删除 `data/`、`app/data/` 后 force-push 所有 refs，
  并重建 tag/Release；所有克隆/缓存失效，影响大。也可同时清掉 main 历史里的本机路径。
- **R3**：把仓库改回 private（逆转 2026-11-15 的公开决定）。
- 需要确认：选 R1/R2/R3；是否授权删除上述分支与标签；是否要我执行并复核。

## 6. 验收标准对照

| P0-05 验收项 | 结果 |
|---|---|
| 全历史检索无密钥 | **PASS**（无真实密钥；第三方误报已核实） |
| 全历史检索无用户数据 | **FAIL / 阻塞**（见第 3 节，待 R1/R2 授权） |
| 全历史检索无本机路径 | **部分**：跟踪文件已清理；历史 27 处、旧资产 20 处（历史需 R2） |
| 旧资产结论明确 | **PASS**（11 个 zip 无 data/、配置、数据库、密钥） |
| 问题项已处理或下架 | **部分**：源码问题已处理；公开历史暴露待授权处置 |

## 7. 复现命令

`
python tools/audit_sensitive.py --json <out.json> --zip <v1.0..v1.6 资产>
git for-each-ref --contains c86fa598
git merge-base --is-ancestor b9d1514 main        # 1 = 不在 main
git show c86fa598:app/data/.app_config.json      # 只读核对配置结构
`

注意：核对数据提交时不要导出用户正文/图片；审计脚本输出已脱敏。
