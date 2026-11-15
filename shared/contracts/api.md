# Sci-lib HTTP API 契约

> 状态：P5 契约固化。**这份文档是行为事实的规格化**，不是新设计。
> 机器可校验的版本是 `tools/api-snapshot.json`（74 条响应形状快照，`npm test` 强制零差异）；
> 本文补上快照里没有的东西：入参、错误码、SSE 帧、上传与 Range 语义。
> 任何实现改动都必须让 `npm test` 的 74 条快照保持零差异，除非显式修订本文件与快照。

## 1. 通用约定

| 项 | 约定 |
|---|---|
| 地址 | 只监听 `127.0.0.1:4321`（`--host` 可改）；所有接口前缀 `/api/` |
| 请求体 | JSON（`Content-Type: application/json`）；上传接口用原始字节流 |
| 成功响应 | `200` / `201`，JSON 对象且 `ok: true` |
| 失败响应 | `{ "ok": false, "error": "<中文可读消息>" }`，状态码见下 |
| 方法不匹配 | `405` `{"ok":false,"error":"不支持的请求方法"}` |
| 未知路径 | `404` `{"ok":false,"error":"接口不存在：<path>"}` |
| 未捕获异常 | `500`，由 HTTP 层兜底，不泄漏堆栈 |

### 1.1 状态码

| 码 | 含义 |
|---|---|
| 200 | 成功（GET/PUT/DELETE/POST 的非创建操作） |
| 201 | 创建成功（`POST /api/papers`、`POST /api/import/pdf` 预检） |
| 400 | 参数或业务校验失败（含「还没配置大模型」） |
| 404 | 资源不存在 |
| 405 | 方法不支持（**只有设置类端点**：`/api/prefs`、`/api/ai/settings`、`/api/sources/settings`、`/api/websearch/settings`；其余路径的方法不匹配按「未知路径」返回 404 `接口不存在：<path>`） |
| 409 | 冲突（如打包任务已在运行） |
| 403 | 非本机回环地址调用停机接口（`POST /api/shutdown`） |
| 413 | 上传体超限 |
| 500 | 内部错误 |
| 501 | 可选依赖缺失（如 pdfjs-dist 未安装时提取全文） |
| 502 | 外部服务失败（题录抓取、外部检索、模型调用失败） |
| 304 | 条件请求命中（`If-None-Match` 与 ETag 一致，由 `sendFile` 返回，无响应体） |
| 416 | Range 越界（`GET /api/papers/:id/pdf` 的 `Content-Range` 不满足） |

### 1.2 SSE（Server-Sent Events）

三处流式接口统一格式：

```
HTTP/1.1 200
Content-Type: text/event-stream; charset=utf-8
Cache-Control: no-cache
Connection: keep-alive
X-Accel-Buffering: no

data: {"type":"start", ...}

data: {"type":"delta","text":"..."}

data: {"type":"done", ...}

```

- 每帧一行 `data: <JSON>\n\n`；JSON 内不含换行。
- 帧类型：`start` / `delta`（增量文本）/ `sources`（引用来源）/ `done` / `error`。
- 客户端断开时服务端停止推送并落库已完成部分。
- **参数校验错误发生在开流之前**，此时返回普通 JSON + 4xx（例如 `ai/summarize` 对不存在的 paperId 返回 404）。

### 1.3 上传

| 接口 | 传输方式 | 头/参数 |
|---|---|---|
| `POST /api/papers/upload` | 原始字节流（`application/octet-stream`） | `X-Filename`（URL 编码）、`X-Paper-Id` |
| `POST /api/papers/:id/attachments` | 原始字节流 | `X-Filename`（URL 编码） |
| `POST /api/import/pdf` | 原始字节流 | query `batch`、`name`；文件须以 `%PDF-` 开头 |

### 1.4 文件下载与 Range

- `GET /api/papers/:id/pdf` 返回 `application/pdf`，`Content-Disposition: inline`，**支持 HTTP Range**（`Accept-Ranges: bytes`、`206 Partial Content`）——内置阅读器按需分片拉取。
- `GET /api/papers/:id/attachments/:file` 与 `GET /api/export/*` 走 `sendFile` / `sendText`，附件下载带 `attachment` disposition。

### 1.5 静态资源与版本化 URL

| 路径 | 内容 |
|---|---|
| `GET /`、`/index.html` | 前端外壳；HTML 不缓存，内部 `src/href` 被替换成 `/__v/<mtime36>/...` |
| `GET /styles.css`、`/js/**` | 前端静态文件，可带 `?v=` 或 `/__v/<ver>/` 前缀 |
| `GET /vendor/pdfjs/**` | 来自可选依赖 `pdfjs-dist`；缺失时 404 + 明确提示 |
| `GET /vendor/katex/**` | 来自可选依赖 `katex`（`dist/` 为根）；缺失时 404，前端降级为原文 |
| `GET /shared/**` | 共享内核（`shared/`，如 `/shared/html-text.mjs`）：前后端唯一实现；依赖方向 public/ → shared/，shared/ 不引用任何层 |

## 2. 六大契约（与 PaperLibrary 合并的接口面）

| 契约 | 本地落点 | 语义 |
|---|---|---|
| `metadata.resolve(doi \| title) → Paper` | `server/adapters/metadata.js`（收编 metadata.js + doi-lookup.js） | 多源合并题录，返回 provenance |
| `search.external(query, filters) → ExternalItem[]` | `server/adapters/external.js` | 5 源检索 + 跨源折叠 + 统一字段 |
| `citation.format(paper, style) → string` | `shared/pure/citation.js` | 纯函数，6 种样式 |
| `llm.chat(messages)` / `llm.stream(messages)` | `server/adapters/llm.js` | 多供应商预设，流式回调 |
| `pdf.extract_text(path) → Text` | `server/adapters/pdf.js`（全文缓存与抽取编排）+ `server/adapters/pdf-text.js`（pdfjs legacy 文本层封装） | pdfjs legacy 抽取，含中文 cmaps；契约名注释在 adapters/pdf.js 头部 |
| `repository(Paper / Attachment / Tag)` | `server/domain/repository.js` + `server/store/accessors.js` | 唯一写入口：只有访问器能改 `library.json` |

命名在 P5 之后固定为上述六个，不再变动。六个契约名同时写在对应文件**头部的 `契约名：` 注释**里，代码与文档一一对得上。

> 说明：`external.js` / `llm.js` / `websearch.js` / `sources.js` / `metadata.js` 已物理归位到 `server/adapters/`；
> `fulltext.js` 与 `text-layer.js` 暂留 `server/` 原位（低风险优先），只补契约名注释，不动实现。
> `shared/pure/` 下 7 个纯函数文件（boolean-query / passages / text-repair / citation / abstract / abstract-extract / text）全部 CJS、零 I/O。

### 2.1 API 域的实现落点（与手册 §2.1「7 个文件」的差异说明）

手册 §2.1 写的是 `server/api/{papers,tags,ai,search,import,settings,system}.js` 七个域文件。P2 实施时按**职责**拆成 9 个（+1 辅助），端点与路由表完全不变：

| 实际文件 | 行数 | 说明 |
|---|---:|---|
| `api/papers.js` | — | 列表/检索/建卡/批量/整理/facets/导出 |
| `api/files.js` | — | 原文上传、全文、附件、引用、PDF（Range） |
| `api/ai.js` | 68 | 中文总结 / AI 总结（**编排已下沉 domain/ai.js**） |
| `api/chat.js` | 83 | 对话 / 识图 / 抄录（**编排已下沉 domain/chat.js**） |
| `api/search.js` | — | 外部源检索 / 网络检索 / 题录抓取 |
| `api/import.js` | — | PDF 建档（预检/确认/放弃）与 JSON 导入 |
| `api/settings.js` | — | prefs / ai / sources / websearch 四组设置 |
| `api/tags.js` | — | 标签列表 / 重命名 / 删除 |
| `api/system.js` | — | health / stats / shutdown / release |
| `api/paper-scope.js` | 19 | 辅助：取 `:id` 或回 404（4 个域共用） |

`ai` 与 `chat` 拆开、`files` 与 `papers` 拆开，是为了让每个文件都 ≤450 行且各自只做一类事；**路由表（server/routes.js）才是端点的唯一事实来源**，域文件只是它的实现落点。

## 3. 端点清单（74 条，与 `tools/api-snapshot.json` 逐条对齐）

快照 id 与本文行一一对应；`network=true` 的端点只做状态类别比较（外部网络波动）。

### 3.1 基础 / 运维（12）

> `POST /api/release/build` 在快照里标记为 `skip`（它会真的跑 `tools/make-release.js` 打包），因此只在这里登记契约。

| 方法 | 路径 | 入参 | 出参 | 错误 |
|---|---|---|---|---|
| GET | `/api/health` | — | `{ok, app, papers, dataDir, pid, port, uptimeSeconds, updatedAt, bootMs, rssMB, version}` | — |
| GET | `/api/version` | — | `{ok, product, version, tag, ui, builtAt, commit}`（唯一来源：包根 `version.json`；`version` 用于更新比较，`ui` 为 `v<major>.<minor>`） | 500（version.json 缺失/损坏，带中文原因） |
| GET | `/api/update/check` | — | `{ok, available, reason, current:{version,tag}, latest:{version,tag,publishedAt,size,notesUrl}, checkedAt, message?}`（reason: newer/current/older/unsupported-win/unsupported-arch；只检查，不下载；清单 256 KB 上限、超时 9s、重试 2 次） | 502 `{ok:false, code, error}`（timeout/network/no-manifest/invalid-manifest/signature/manifest-too-large/redirect-host/bad-url）；500（本地 version.json 损坏） |
| POST | `/api/update/download` | — | 200 `{ok, started, phase, running, version, progress:{downloadedBytes,totalBytes,percent}, ready, stagingVersion, stagingPath, error, code, updatedAt, lastCheckAt}`（后台执行 下载→校验→staging；同一时间只允许一个任务，重复调用返回当前状态且 `started=false`；已有 ready staging 时不重复下载） | 400（已是最新/无需下载，code=current/older/unsupported-*）；502（清单 timeout/network/no-manifest/invalid-manifest/signature 等）；500（本地 version.json 损坏） |
| GET | `/api/update/status` | — | `{ok, phase, running, version, progress:{downloadedBytes,totalBytes,percent}, ready, stagingVersion, stagingPath, error, code, updatedAt, lastCheckAt}`（只读、不启动下载；phase: idle/checking/downloading/verifying/verified/extracting/ready/failed/cancelled） | — |
| POST | `/api/update/apply` | — | 200 `{ok, applied, idempotent, apply:{schema,version,tag,fromVersion,fromTag,stagingPath,stagingRel,backupPath,backupRel,packageSha256,manifestSha256,files,bytes,createdAt}}`（确认 staging 完整后写 `data/updates/apply.json`，供启动器退出时安装；重复调用幂等） | 409（staging-missing/staging-incomplete/version-mismatch/version-same/downgrade/busy）；500（本地 version.json 损坏） |
| GET | `/api/stats` | — | `{ok, stats:{total, withPdf, starred, pdfMissing, avgRating, noteChars, byYear[], byType[], byStatus[], tags, updatedAt}}` | — |
| GET | `/api/prefs` | — | `{ok, prefs}` | — |
| PUT/POST | `/api/prefs` | `{...}` 局部覆盖 | `{ok, prefs}` | — |
| GET | `/api/shutdown/status` | — | `{ok, app, papers, dataDir, pid, port, uptimeSeconds}` | — |
| POST | `/api/shutdown` | — | `{ok, message}` | 403（非回环地址） |
| POST | `/api/release/build` | — | 200 `{ok, name, path, relPath, size, mtime, papers, files, seconds}`（`papers` 恒 0：发布包按设计不含 data/ 用户数据；`size` 取 release-manifest 口径） | 400（打包失败，仅回中文消息，详情写 data/logs/）/ 409（已有打包在跑）/ 500 |

### 3.2 标签（3）

| 方法 | 路径 | 入参 | 出参 | 错误 |
|---|---|---|---|---|
| GET | `/api/tags` | — | `{ok, tags:[{name,count}]}` | — |
| POST | `/api/tags/rename` | `{from, to}` | `{ok, result}` | 400 |
| POST | `/api/tags/delete` | `{name}` | `{ok, result}` | 400 |

### 3.3 文献 CRUD / 检索 / 全文 / 附件 / 对话（29）

`GET /api/papers` 的查询参数（可组合）：

| 参数 | 说明 |
|---|---|
| `q` | 查询串；支持布尔语法与字段限定（见 §4） |
| `scope` | `metadata`（默认）/ `fulltext` / `all` |
| `ids` / `fulltextIds` | 逗号分隔的 id 集合（全文命中集） |
| `tags` | 逗号分隔，多值 OR |
| `type` / `status` | 逗号分隔，多值 OR；`all` 表示不筛 |
| `author` / `venue` / `keyword` | 子串匹配 |
| `years` / `authors` / `venues` | 多选 facet（精确匹配，逗号分隔） |
| `yearFrom` / `yearTo` | 数值区间 |
| `minRating` | 评分下限 |
| `hasNotes` / `hasPdf` / `starred` | `true` 时过滤 |
| `excludeId` | 排除某条 |
| `sort` | `updatedAt`（默认）/ `addedAt` / `yearDesc` / `yearAsc` / `title` / `rating` / `starredAt`；非法值按默认处理 |
| `page` / `pageSize` | 页码（≥1）/ 每页（1–500，默认 50） |
| `all` | `true` 时忽略分页 |

| 方法 | 路径 | 入参 | 出参 | 错误 |
|---|---|---|---|---|
| GET | `/api/papers` | 见上表 | `{ok, items[], total, page, pageSize, pageCount, scope, boolean, fulltext}` | — |
| POST | `/api/papers` | paper 局部字段 | 201 `{ok, paper}` | —（字段缺省会被规整，不是错误） |
| POST | `/api/papers/bulk` | `{ids[], patch:{tags?, removeTags?/untags?, status?, rating?, starred?, type?}}` | `{ok, changed, papers[]}` | — |
| GET | `/api/papers/scan/pdfs` | — | `{ok, scan:{total, bytes, duplicateGroups[], duplicates, wastedBytes, orphans[], linked[]}}` | — |
| POST | `/api/papers/associate-pdf` | `{paperId, file}` | `{ok, paper, file}` | 400 |
| POST | `/api/papers/cleanup-duplicates` | `{files?: string[]}`（不传=清理全部无引用重复副本） | `{ok, moved, bytes, files[], quarantine, scan}`；无可清理时 `{ok, moved:0, files:[], hint}` | — |
| POST | `/api/papers/tidy-pdf-names` | — | `{ok, result, attachmentResult}` | — |
| POST | `/api/papers/fulltext/batch` | — | `{ok, total, succeeded, failed, results[]}` | — |
| POST | `/api/papers/upload` | 原始流 + `X-Filename`、`X-Paper-Id`（缺省=只存文件不建关联） | `{ok, pdfFile, size, extract:{ok, pages?, chars?, preview?, error?}}` | 400 |
| GET | `/api/papers/:id` | — | `{ok, paper, pdfExists, fulltext:{has, available}}` | 404 |
| PUT/PATCH | `/api/papers/:id` | 局部字段 | `{ok, paper}` | 404 |
| DELETE | `/api/papers/:id` | — | `{ok}` | 404 |
| POST | `/api/papers/:id/fulltext` | — | `{ok, pages, chars, preview}` | 400（无 PDF）/ 501（缺 pdfjs）/ 500 |
| GET | `/api/papers/:id/fulltext` | — | `{ok, chars, text}`（text 截断 200k） | 404 |
| POST | `/api/papers/:id/fulltext/text` | `{text}` | `{ok, chars}` | 400（正文为空）/ 404 |
| GET | `/api/papers/:id/attachments` | — | `{ok, attachments[]}` | 404 |
| POST | `/api/papers/:id/attachments` | 原始流 + `X-Filename` | `{ok, attachment, attachments}` | 404 |
| GET/HEAD | `/api/papers/:id/attachments/:file` | — | 文件流 | 404 |
| DELETE | `/api/papers/:id/attachments/:file` | — | `{ok, attachments[]}` | 404 |
| GET | `/api/papers/:id/chat` | — | `{ok, messages[]}` | 404 |
| DELETE | `/api/papers/:id/chat` | `pair?`（query，提问下标；缺省=清空全部） | `{ok, messages[], removed?}` | 400（pair 越界）/ 404 |
| POST | `/api/papers/:id/chat` | `{question, useLocal?, useExternal?}` | **SSE** | 404 |
| POST | `/api/papers/:id/vision` | `{question, images[], label?}` | **SSE** | 404 |
| POST | `/api/papers/:id/transcribe` | `{image, page?}` | `{ok, text}` | 404 |
| POST | `/api/papers/:id/summary-zh` | `{mode?: 'translate'\|'generate'}` | `{ok, paper, translated, mode, chars}`；原文已是中文时 `{ok, paper, translated:false, reason}` | 400（未配置大模型 / 无标题）/ 404 / 502 |
| POST | `/api/papers/summary-zh/batch` | — | `{ok, succeeded, failed, total, results[]}` | 400 |
| GET | `/api/papers/:id/cite` | `format=gb\|apa\|mla\|bibtex\|markdown` | `{ok, format, text, available[]}` | 404 |
| GET/HEAD | `/api/papers/:id/pdf` | Range 头 | PDF 流（206 分片） | 404 |
| GET | `/api/facets` | 与 `/api/papers` 同构的筛选参数 | `{ok, authors[], venues[], tags[], years[], query:{...}}` | — |

> `facets` 语义（**不能改**）：`authors/venues/tags/years` 是全库联想清单；`query` 是当前结果集内的 facet 计数，**每类先剥掉自身条件再计数**（例如已选 tag=A，则 tags 计数按「不含 tag 过滤」的全集统计）。
>
> `scope=fulltext` 时 facets 必须与 `/api/papers` 走**同一套**处理：先把 `q` 挪到 `booleanQuery` 并清空 `q`，命中集用全文索引给出的 `ids`（空命中给 `idsEmpty`）；否则 q 会再被拿去筛题录索引，把「只出现在正文里」的文献计成 0。

### 3.4 AI（5）

| 方法 | 路径 | 入参 | 出参 | 错误 |
|---|---|---|---|---|
| GET | `/api/ai/settings` | — | `{ok, settings, ready}` | — |
| PUT/POST | `/api/ai/settings` | 局部字段（apiKey/baseUrl/model 等） | `{ok, settings, ready}` | — |
| POST | `/api/ai/test` | — | `{ok, ...}`（network） | 400 |
| POST | `/api/ai/summarize` | `{paperId, save?, useFulltext?}` | **SSE** | 404 / 400 |
| POST | `/api/ai/summarize-sync` | `{paperId, save?}` | `{ok, text, usedFulltext, usage}` | 400 / 404 / 502 |

### 3.5 外部源 / 网络检索（11）

| 方法 | 路径 | 入参 | 出参 | 错误 |
|---|---|---|---|---|
| GET | `/api/external/sources` | — | `{ok, sources[], settings}` | — |
| GET | `/api/external/search` | `q, limit, page, sources, yearFrom, yearTo, venue, author, minCitations, onlyOA` | `{ok, filters, query, items[], sources, page, hasMore, cached}`（network） | 400（既无 q 也无筛选条件） |
| GET | `/api/sources/settings` | — | `{ok, settings}` | — |
| PUT | `/api/sources/settings` | `{openalexApiKey?, contactEmail?}`（空串=清除，不传=保持） | `{ok, settings}` | — |
| POST | `/api/sources/abstracts/backfill` | — | `{ok, total, filled, results[]}`（network，逐篇） | 409（已有一次在跑）/ 500 |
| POST | `/api/sources/openalex/test` | — | 恒 200：`{ok:true, ...}` 或 `{ok:false, error, quotaExhausted, retryAfterSeconds, settings}`（network） | — |
| GET | `/api/websearch/settings` | — | `{ok, settings, ready}` | — |
| PUT/POST | `/api/websearch/settings` | 局部字段 | `{ok, settings, ready}` | — |
| POST | `/api/websearch/test` | — | `{ok, ...}`（network） | 400 |
| GET | `/api/websearch/search` | `q, num, category` | `{ok, items[], ...}`（network） | 400 |
| GET | `/api/websearch/answer` | `q` | `{ok, answer, ...}`（network，默认关闭） | 400 |

### 3.6 抓取题录（2）

| 方法 | 路径 | 入参 | 出参 | 错误 |
|---|---|---|---|---|
| POST | `/api/fetch/doi` | `{doi}` | `{ok, paper, sources, errors, fieldsFrom, elapsedMs}` | 404（没找到）/ 502 |
| POST | `/api/fetch/title` | `{title, limit?}` | `{ok, items[]}` | 502 |

### 3.7 导入（4）

| 方法 | 路径 | 入参 | 出参 | 错误 |
|---|---|---|---|---|
| POST | `/api/import/pdf` | 原始流；query `batch, name` | 201 `{ok, batch, tempId, name, size, parsed, draft}` | 400 / 413 / 500（预检解析失败） |
| DELETE | `/api/import/pdf` | query `batch` | `{ok, removed}` | 400 |
| POST | `/api/import/pdf/confirm` | `{batch, papers[]}` | `{ok, imported[], failed[]}` | 400 |
| POST | `/api/import/json` | `{papers[], overwrite?}` 或直接数组 | `{ok, result}` | 400 |

### 3.8 导出（3）

| 方法 | 路径 | 入参 | 出参 |
|---|---|---|---|
| GET | `/api/export/json` | — | JSON 文件流 `{version, exportedAt, app, papers[]}` |
| GET | `/api/export/bibtex` | `ids?` | `text/plain`，`attachment; filename="library-<date>.bib"` |
| GET | `/api/export/markdown` | `ids?` | `text/markdown`，`attachment; filename="library-<date>.md"` |

> 实现上是**一条路由** `GET /api/export/:kind`（`kind ∈ json|bibtex|markdown`），未知 kind 返回 404 `不支持的导出格式`；`ids` 为逗号分隔的文献 id，缺省=全库。

## 4. 查询语法（布尔 + 字段限定）

`q` 与外部检索共用一套语法，纯函数实现见 `shared/pure/boolean-query.js`：

- `AND` / `OR` / `NOT`（大小写不敏感）、`&&` `||` `!`、中文 `与` `或` `非`；
- 相邻词默认 `AND`；
- 双引号/中文引号短语整段匹配（允许中间空白差异）；
- 括号分组；
- 字段限定：`title:` `author:` `abstract:` `journal:` `year:` `type:` `tag:` `doi:` `keyword:` `note:` `fulltext:`；
- `year:2013`、`year:2013-2018`、`year:2013-`、`year:-2018`（按数值比较）；
- 外部源能力不同：OpenAlex/EuropePMC/arXiv 原生布尔，Crossref/Semantic Scholar 走 `plainQuery`（只留正项词）。

**本地检索的粗筛（两段式，不能省）**：倒排索引先按「正项词」粗筛候选集，再由 `boolean-query` 求值器精确判定。

| 查询形态 | 粗筛方式 | 说明 |
|---|---|---|
| 普通关键词 / AND | 正项词取**交集**（`index.search(terms.join(' '))`） | 性能路径 |
| 含 `OR` 节点 | 正项词逐个检索后取**并集**（同一条取最高分） | 否则另一支命中的文献被整批砍掉（历史缺陷，已修） |
| 纯字段限定（`year:` / `doi:` 等，terms 为空） | 不做索引粗筛，**全量 + 求值器** | 字段值在倒排表里搜不到 |
| 空查询 | 不做粗筛 | — |

已知边界：`title:foo OR bar` 这类**字段词与普通词混进 OR** 的查询，粗筛只拿得到普通词，只命中字段项的那几篇仍可能漏；要彻底解决需「含字段项时放弃粗筛」，属 backlog。

## 5. 不变量（改代码时必须保持）

1. 响应体形状（键名与嵌套）——由 `tools/api-snapshot.json` 卡死。
2. 错误体恒为 `{ok:false, error}`，error 是中文可读消息。
3. SSE 帧格式与 `data: <json>\n\n` 分隔。
4. `facets` 的「先剥自身条件再计数」语义。
5. `ids` 为空集合时结果必须为空，不能退化成「全部」。
6. `scope=fulltext` 时清空 `q`、保留 `booleanQuery`（否则全文命中文献被题录索引误杀）。
7. 上传文件名与路径必须过 `safePathJoin`，禁止路径穿越。
