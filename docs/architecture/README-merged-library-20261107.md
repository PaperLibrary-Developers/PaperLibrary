# 架构图：PaperLibrary 合并后的文献库（2026-11-07）

> 这张图回答一个问题：**Sci-lib 的契约面与 PaperLibrary 的能力合并之后，这套东西是由哪些部分组成的、彼此怎么连。**
> 受众是想接手/评审这个仓库的人——不是逐端点 API 文档，也不是部署手册。

## 文件

| 文件 | 用途 |
|---|---|
| `architecture-merged-library-20261107.html` | **主产物**：standalone 图（约 880KB）。双击即开，暗/亮主题按钮、Live/Still 动效、右上「导出」可出 SVG/PNG，整页离线可用（无任何外链资源；文件里的 http(s) 只出现在内嵌第三方许可证文本里） |
| `architecture-merged-library-20261107-preview.png` | 亮色预览（2048×1320，viewer 截图，含顶部工具栏） |
| `architecture-merged-library-20261107-preview-dark.png` | 暗色预览（同上） |
| `architecture-merged-library-20261107.finalize.json` / `.finalize-summary.json` | 生成回执：validate / deliver / check / browser-check 四道闸门 |
| `architecture-merged-library-20261107.browser-check.json` | 浏览器实测证据（1440×900 与 2048×1320，两套主题） |
| `architecture-merged-library-20261107.delivery.json` | 交付记录（产物哈希、来源候选） |
| `.archify/architecture-merged-library-20261107-032337/` | 候选 JSON（图的数据源，可改后重新 finalize）、visual-check 的 4 张截图与接触表 |

图的数据源是 `candidate.json`（Archify architecture schema v1）——改图请改它再重新生成，不要手改 HTML。

## 怎么读

- **主干（自上而下）**：启动.cmd → 便携 Python → `paperlib/app.py` → HTTP 服务(127.0.0.1:4321) → `routes.py` 路由表 → API 域 → 领域层 → data 写穿层 → 磁盘。
  浏览器从左侧进入 HTTP 服务（`/api/*`），`public/` 前端由同一个 HTTP 服务出静态资源（右侧注入）。
- **两条能力来源**：API 域下面分两支——`domain/`（Sci-lib 移植过来的业务规则）与 `legacy/`（PaperLibrary 原样搬来的图表/对比/深度分析/术语/Obsidian）。10 条合并新增端点就是后者挂上来的。
- **边界**：橙色大框 = 单进程 Python（所有分层都在一个进程里，端口 4321）；右上独立小框 = 验收与发布工具链，**开发期**跑，不在运行时拓扑里，所以不画连线。
- **底部四张卡片**：运行时入口、数据落点、合并的两套能力、检索/阅读/验收 —— 图里放不下但必须交代的事实都在这里（表名、目录、Range/SSE、验收工具）。
- 图是"每层一个方框"的抽象：**没有**展开 68 条路由的逐条端点，也**没有**展开 10 张表的列；这两类细节分别看 `shared/contracts/api.md` 与 `paperlib/data/schema.py`。

## 图里每条事实的出处

生成时以 HEAD `242e724cf3019b22f45b59d7da0ddd0ef99891af`（`github.com/yijiey53-svg/paper-library-app`）为准，节点上的 "sources" 就是下面这些行：

| 图上写的 | 事实 | 代码位置 |
|---|---|---|
| 启动.cmd / 便携 Python | `PYTHONUTF8=1`、选 `runtime\portable\python.exe`（缺则系统 python），执行 `-m paperlib --port 4321` | `启动.cmd:8-14`、`paperlib/config.py:13` |
| app.py 进程装配 | `build()` = Store + services + StaticHandler + Router(ROUTES)；`main()` new HttpServer 并起后台线程 | `paperlib/app.py:80-107`、`110-140` |
| HTTP 服务 | stdlib ThreadingHTTPServer；`send_file` 支持 Range 206 / ETag 304 / 416 | `paperlib/http/server.py:280-296`、`122-180` |
| public/ 静态 | 同源出 `public/` 与 `/shared/`，HTML 里 css/js 换成 `/__v/<mtime36>/` | `paperlib/http/static.py:74-111` |
| 前端 | ESM 单入口 `main.mjs` → `app.mjs` 注册 13 条路由（9 个视图处理器 + `/import-pdf` 转发），新增 `#/figures #/compare #/deep` | `public/main.mjs:1-14`、`public/js/app.mjs:403-425` |
| 路由表 | 68 条路由记录，顺序即优先级（静态段排在 `:id` 前）；`Router.match/handle` | `paperlib/routes.py:30-100`、`paperlib/http/router.py:27-70` |
| API 域 | 9 个 Sci-lib 域（papers/files/ai/chat/search/import_/settings/tags/system）+ 5 个新增模块（figures/compare/deep/terms/export_extra） | `paperlib/api/`（如 `papers.py:137`、`figures.py:177`、`files.py:280`） |
| 领域层 | `library` 门面 + `query` 编排 + `search`（**bigram 倒排**）+ importer/pdf_import/ai/chat | `paperlib/domain/library.py:1-16`、`query.py:276`、`search.py:90-115` |
| shared/pure | 纯函数、零 I/O：布尔语法与字段限定求值、段落挑选、摘要兜底、文本修复、引文格式 | `shared/pure/boolean_query.py:84-98`、`191-200`、`passages.py:68` |
| legacy/ | 图注 bbox 匹配 + 抽图落盘、compare_engine、deep_analysis/deep_local/deep_prompt、lexicon+terms.json、caption_matcher/figure_anchor、obsidian_note | `paperlib/legacy/figures.py:100-111`、`compare_engine.py:1-10`、`deep_analysis.py:1-10` |
| 适配器 | PyMuPDF（`pymupdf`/`fitz` 均试）、LLM 供应商预设、Exa、5 个学术源（OpenAlex/Crossref/Europe PMC/arXiv/Semantic Scholar）、Obsidian zip | `paperlib/adapters/pdf_text.py:28-35`、`llm.py:26-31`、`external.py:41-51` |
| data 写穿层 | 10 张业务表 + meta，`PRAGMA user_version=3` 幂等迁移；**SQL 只在 repository.py**；store 负责目录与写穿 | `paperlib/data/schema.py:15-23`、`repository.py:1-10`、`store.py:33-42` |
| data/ 用户文件 | `article/papers`（`store.attach_file` 存 PDF 副本）、`article/attachments`、`fulltext`（`store.get_text/set_text`） | `paperlib/data/store.py:193-211`、`217-226` |
| data/images | `<paperId>/fig_NNN.<ext>`，抽图时写入；行里只存相对 data/ 的路径 | `paperlib/legacy/figures.py:44-49`、`paperlib/api/figures.py:211-216` |
| 外部服务（可选） | LLM：`baseURL+model` 必填、非本地地址还要 apiKey；Exa 要 Key；5 个学术源免 Key | `adapters/llm.py:202-210`、`adapters/websearch.py:177-184`、`26-33` |
| 验收工具链 | `contract_check.py` 重放契约快照 69 行；`e2e.py` 同场景对照 Node 参照逐字段 diff；`test/` 16 个文件 353 个 test 方法；`bench.py` 量体积/启动/RSS；`make_portable.py`+`release.py` 出便携包 | `tools/*.py`、`test/test_api_smoke.py:1-6` |

## 重新生成

```bash
# 技能包：<dsh-profile>/node_modules/@whisssssss/dsh-archify/skills/archify
ARCH=<archify-skill-dir>/bin/archify.mjs
cd <仓库根>

# 改完 candidate.json 后一次过四道闸门（validate → deliver → check → browser-check）
node "$ARCH" finalize architecture \
  .archify/architecture-merged-library-20261107-032337/candidate.json \
  docs/architecture/architecture-merged-library-20261107.html \
  --repo-root . --quality showcase --json

# 需要截图/接触表时（会写 4 张 PNG + contact sheet）
node "$ARCH" visual-check docs/architecture/architecture-merged-library-20261107.html \
  --out-dir .archify/architecture-merged-library-20261107-032337/review-2 \
  --summary --require-provenance
```

本次生成结果：四道闸门全 `pass`、`diagnostics: []`、无交叉线，浏览器实测 1440×900 与 2048×1320 两套主题通过可读性/包含性检查（automated，不是人眼结论）。

## 已知边界与未决点

1. **验收工具链不连边**：它们起临时服务/子进程、跑 Node 参照或用真实浏览器量测，属开发期行为；画成运行时的边会误导。所以只用一个独立区域的标签交代。
2. **抽象层级**：图是"层 + 关键组件"级别。68 条路由记录、10 张表的列、119 条术语、5 个学术源各自的字段映射都没有展开。
3. **本地降级的字段数**：无 Key 时 `deep` 实际只给 9 个字段，契约写的是 22；`compare` 本地模式也没有 `usage`。图里按**契约**标注"22 字段 + 五维评分"，差异已在 T7 前端做了兜底，后端是否补齐留给 Lead 决定。
4. **图注与正文锚定**（`figure_anchor`）与 **Obsidian 笔记拼装**（`legacy/obsidian_note.py` + `adapters/obsidian.py`）在图上合并进了 legacy 与 adapters 两个方框，没有再拆。
5. **生成时工作区不干净**：`test/test_search_adapters.py` 有未提交改动（不是本次任务），图上所有证据路径都不涉及它；引用的行号以 HEAD 为准。

维护：`docs/architecture/` 只放产物与回执；候选 JSON 与截图证据在 `.archify/`（未纳入 git，建议加进 `.gitignore` 或按需删除）。
