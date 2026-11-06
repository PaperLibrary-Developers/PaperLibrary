# Sci-lib 数据模型契约

> 状态：P5 契约固化。**本阶段不迁 SQLite、不改 JSON 格式**（终局由 Python 引擎持 SQLite，一次性迁移）。
> 这里记录的是**当前事实** + 为合并预留的 union 字段，改实现时必须保持读写兼容。

## 1. 目录布局（`--data-dir` 之下）

```
data/
├── library.json              主数据：{ version, papers[], meta }
├── prefs.json                界面与导入偏好（可调项、批量上限、主题…）
├── settings.json             大模型配置（apiKey / baseUrl / model / 开关）
├── websearch.json            网络检索（Exa）配置与配额
├── sources.json              外部题录源配置（OpenAlex key 等）
├── server.log                运行日志
├── server.runtime.json       运行时标记（pid / port），供「停止」脚本识别
├── article/
│   ├── papers/               原文 PDF，文件名 `<文献id>-<标题>.pdf`
│   └── attachments/          补充材料 SI，摊平一层，`<文献id>-<标题> SI<序号>.<ext>`
├── fulltext/
│   ├── <paperId>.txt         该文献提取出的正文
│   └── att-<附件文件基名>.txt 该附件的抽取文本
└── backups/                  每次落盘留最近 10 份 library-<ISO>.json
```

旧布局（`data/papers/`、`data/attachments/<paperId>/`）由 `store.migrateLegacyDirs()` 幂等迁移，不要删这段逻辑。

## 2. library.json

```json
{
  "version": 1,
  "papers": [ /* Paper[]，见 §3 */ ],
  "meta": { "createdAt": "<ISO>", "updatedAt": "<ISO>" }
}
```

写入规则（`store`）：
- 200ms 防抖合并写；**原子替换**（临时文件 + rename）；
- 每次落盘在 `backups/` 保留最近 10 份；
- 不并发写（单进程，禁止并行多任务）。

## 3. Paper（`normalizePaper` 是唯一权威）

所有写入都必须过 `normalizePaper(input, existing)`。关键规则：

- **区分「字段没传」与「传了空值」**：局部更新只覆盖显式传入的键（`hasOwnProperty && !== undefined`）；`year/rating` 传空值才清空，没传则保留。
- `id` 缺省用 `uid()`（16 位 hex）；`addedAt` 首次生成后不再变；`updatedAt` 每次写都刷新。
- 白名单枚举：`type ∈ {journal, conference, preprint, book, thesis, report, web, other}`；`status ∈ {unread, reading, read, archived}`；非法值回落到旧值/默认值。

| 字段 | 类型 / 上限 | 归一化 | 说明 |
|---|---|---|---|
| `id` | string(16 hex) | `uid()` | 主键 |
| `title` | string ≤500 | `cleanTitle`（去 HTML/实体/换行） | 空则「（未命名文献）」 |
| `authors` | string[] | `toStringArray` | 保持录入顺序 |
| `year` | int 1001–2999 或 null | `parseInt` + 范围校验 | 非法 → null |
| `type` | enum | 白名单 | 默认 journal |
| `venue` | string ≤300 | trim | 期刊/会议名 |
| `volume` / `issue` / `pages` | string ≤50 | trim | |
| `publisher` | string ≤200 | trim | |
| `doi` | string | `normalizeDoi`（去前缀、小写） | |
| `url` | string ≤1000 | trim | |
| `tags` | string[] | `toStringArray` | 我的标签 |
| `keywords` | string[] | `toStringArray` | 作者给的关键词 |
| `status` | enum | 白名单 | 默认 unread |
| `rating` | int 0–5 | clamp | 默认 0 |
| `starred` / `starredAt` | bool / ISO | 取消收藏时 `starredAt=''` | 收藏 ≠ 评分 |
| `summary` | string ≤4000 | 原样截断 | 原文摘要 |
| `summarySource` | string ≤20 | trim | `crossref`/`pdf`/`openalex` |
| `summaryZh` | string ≤4000 | | 中文一句话总结 |
| `summaryZhAt` / `summaryZhModel` | ISO / string ≤100 | | 缓存标记，避免重复调模型 |
| `notes` | string ≤200000 | | 手写笔记 |
| `aiSummary` | string ≤200000 | | AI 总结（独立于 notes） |
| `aiSummaryAt` / `aiSummaryModel` | ISO / string ≤100 | | |
| `attachments` | Attachment[] | `normalizeAttachments` | 见 §4 |
| `chat` | ChatMessage[] | `normalizeChat`（只留最近 200 条） | 见 §5 |
| `pdfFile` | string ≤300 | | `article/papers/` 下的文件名 |
| `bibtexKey` | string ≤100 | 缺省 `bibtexKey(paper)`（姓+年） | |

> `refs`：前端提交/渲染但 `normalizePaper` 不收纳（死字段）。P6.1 按方案 A 收纳（≤200 条，字段白名单），是本阶段唯一允许的字段级功能修复。

## 4. Attachment

```json
{ "file": "<paperId>-<标题> SI1.pdf", "originalName": "supp.pdf", "size": 12345,
  "kind": "pdf|text|other", "hasText": true, "textChars": 4321, "addedAt": "<ISO>" }
```

- `file` 是 `article/attachments/` 下的**扁平**文件名，前缀 `<paperId>-` 标明归属。
- 改名（tidy-pdf-names）必须同步改它的抽取文本文件名（`fulltext/att-*.txt`）。

## 5. ChatMessage

```json
{ "role": "user|assistant", "content": "≤40000", "at": "<ISO>",
  "model": "≤100", "sources": ["≤40 × ≤8"] }
```

- 只保留最近 200 条；`content` 为空的条目丢弃。
- `sources` 是回答引用到的资料标签（正文/摘要/网络资料…），不是 URL 列表。
- P6.2：`images` / `usage` 目前被 `normalizeChat` 丢弃，需保留。

## 6. 配置对象

| 文件 | 内容 | 敏感 |
|---|---|---|
| `prefs.json` | `theme`、`pageSize`、`chatBodyChars`、`chatAttachChars`、`chatAttachTotalChars`、批量导入上限等（越界回落默认值） | 否 |
| `settings.json` | `apiKey`、`baseUrl`、`model`、`autoSummaryZh`、`visionModel` 等 | **是**（导出/发布时必须打码） |
| `websearch.json` | Exa key、`numResults`、`useInChat`、`allowDeep`、配额状态 | **是** |
| `sources.json` | OpenAlex key 与各源配额状态 | **是** |

## 7. 检索索引（内存，P6.3 可选持久化）

- 启动时由 `Library` 构造重建：对每篇的 title/authors/venue/summary/keywords/notes 分词建倒排表。
- 目标：`data/search-index.json` 缓存 + `meta.updatedAt`/`tokenizerVersion` 失效（P6.3，可选）。
- 语义不能变：`ids` 空集合 → 结果为空；`scope=fulltext` 时清 `q` 留 `booleanQuery`。

## 8. 为合并预留的 union 字段（只记录，不落库）

| union 字段 | 现状 | 合并时 |
|---|---|---|
| `venue` | 有 | PaperLibrary 的 `journal` 列做别名 |
| `tags[]` | 内嵌数组 | 合并体建 `tags`/`paper_tags` 多对多表 |
| `folder` | 无 | 新增单值列（单层分类） |
| `chat[]` | 内嵌 paper | 合并体建 `chat_messages` 表 |
| `attachments[]` + `pdfFile` | 数组 + 文件名 | union 定 `files[]` + `primaryFile` |
| `fulltext/*.txt` | 文件 | 合并体建 `paper_text` 表（或 FTS5） |
| `figures[]` / `compare_cache` | 无 | 独立表 |
| `deep_analysis` | 部分（`aiSummary`） | `paper_analysis(kind)` 表 |
| `arxiv_id` | 无 | 新增列 |

**本阶段只做接口预留（`shared/contracts/*`），不落库、不改 JSON 结构。**
