# Sci-lib 对照笔记（分析用，可删）

- 来源：`Sci-lib.zip`（774 MB），解压到 `_scilib_ref/Sci-lib/`（已剔除 node_modules / data/images / node.exe）
- 包内自带一份 **你 paper-library-app 的旧快照**：`_scilib_ref/Sci-lib/server/paper-library-app/`
- 分析目的：对比前端做法，挑可移植到 paper-library-app 的部分

---

## 0. 包内你的旧快照 vs 你的现版本

| 文件 | 包内 | 现在 | 结论 |
|---|---|---|---|
| parser.py / ai_engine.py / file_server.py / tunnel.py / launcher_gui.py / start_all.py / README.md / server.js | — | — | **完全相同**（MD5 一致） |
| app.py | 26383 B | 33740 B | 你之后又改过（CSS/PDF 阅读器/配置项） |
| database.py | 10708 B | 10750 B | 你之后又改过 |
| data/papers.db | 93 篇 / 3435 图 | 140 篇 / 4566 图 | 快照止于 2026-09-12 17:08 那次扫描 |

→ 说明 Sci-lib 作者是**直接打包了你当时的目录**，不是他自己写的 Python 部分。
→ Sci-lib 前端代码里**没有任何地方引用 paper-library-app / 8501 / Python**，
  这个子目录是**死重量（18.4 MB 的 papers.db + 一堆你的文件）**，对 Sci-lib 功能无用。

---

## 1. 前端技术栈对照

| 维度 | Sci-lib | 你的 paper-library-app |
|---|---|---|
| 渲染位置 | 浏览器（客户端 SPA） | 服务端（Streamlit 每次交互整脚本重跑） |
| 框架 | **零框架**，手写 `h()` hyperscript | Streamlit 组件树 + 一大段注入 CSS |
| 依赖 | 运行期只有 pdfjs-dist + katex（都可选） | streamlit / pymupdf / openai / anthropic |
| 路由 | hash 路由 `#/paper/:id`，筛选条件写进 URL，可前进后退/收藏 | 无路由，状态在 `st.session_state` |
| 视图 | list / detail / edit / import-pdf / timeline / search / settings 七个独立视图 | 两个 tab（文献库 / 图片检索） |
| PDF 阅读 | PDF.js 自建阅读器（71 KB JS）：搜索、缩放、反色、截图、**框选提问** | PyMuPDF 每页渲染成 PNG + 上/下一页（`app.py:200`） |
| 交付 | 自带 `runtime/node.exe`（89 MB），免装 Node | 需 Python 环境 / PyInstaller 打包 |

## 2. Sci-lib 值得直接抄的 10 个点

1. **CrossRef 自动补题录**（`server/metadata.js`）
   - `api.crossref.org/works/<doi>` + `query.bibliographic=<title>`
   - 一次拿到：标题、作者、年份、期刊、卷期页、DOI、出版社 —— 正好补你库里
     「47 篇无年份 / 74 篇无 DOI」的洞。
   - 你只需要 `requests.get`，30 行以内。

2. **DOI 抽取 + 跨行拼接**（`pdf-import.js:143`）
   - 页眉 DOI 被排版切断成两行的场景（`10.1002/anie.2023` + `15020`），
     用「本行只有 DOI + 下一行纯数字」判定并接回去。这个坑很真实。

3. **标题候选打分**（`pdf-import.js:104 scoreTitleLine`）
   - 现有 `title_extract.py` 是「按字号分档 + 规则否决」；
     Sci-lib 是**打分制**：位置越靠前越高分、含 of/for/and 加分、
     作者行/期刊行/句末句号扣到 -100。两者可以合起来用。

4. **中文 bigram 分词**（`server/utils.js:86 tokenize`）
   - 中文取「单字 + 双字 bigram」，英文整词，`/^\d+$/` 丢弃纯数字。
   - 这正好解决你 FTS5 短语匹配的死穴：`"循环伏安"` 现在必须字面连续命中，
     而 bigram 方案可以让「循环伏安」匹配到「循环伏安曲线」并给出相关度。

5. **布尔检索**（`server/boolean-query.js`，273 行，零依赖）
   - AND/OR/NOT、`&& || !`、甚至中文 `与/或/非`、引号短语、括号分组。
   - 同一份 AST 既用于本地求值，也能翻译给 OpenAlex / EuropePMC / arXiv。
   - 有它之后，「#标签 也算一种检索条件」的体验才成立。

6. **检索作用域三选一**：题录与摘要 / PDF 全文 / 两者都搜（`scope=metadata|fulltext|all`）
   - 你的搜索是「有 FTS 就 FTS，没有就 LIKE」隐式降级，用户不知道搜的是哪里。

7. **引用格式导出**（`server/citation.js`，197 行，零依赖）
   - GB/T 7714-2015 / APA 7 / MLA 9 / BibTeX / Markdown 五种。
   - 含中文文献判定（`isWestern`）、西文 et al. vs 中文「等」、`M<sup>II</sup>` → `MII`。
   - 对一个写论文的人，这是**投入产出比最高**的一块。

8. **数据安全三件套**（`server/store.js`）
   - JSON 保序写入 + 200 ms 防抖；
   - **原子替换**：写 `.tmp-<pid>` 再 `rename`，断电不写坏主库；
   - 每次落盘自动备份最近 10 份到 `data/backups/`，可一键回滚。
   - 你的 SQLite 有 WAL，但没有**自动可回滚备份**，也没有「误删记录」的恢复路径。

9. **`normalizePaper` 的「没传 vs 传空」语义**（`library.js:140`）
   - 用 `hasOwnProperty` 区分「字段没传」和「传了空值」，
     避免局部更新（只改 tags）时把 year/rating **静默清空**。
   - 你 app.py 的 AI 流水线里 `database.upsert_paper(pf)` 是整行覆盖，
     同类风险存在（`upsert_paper` 会把没读到的字段写回默认值）。

10. **`cleanTitle` 的化学式处理**（`utils.js:158`）
    - 剥 `<sup>/<sub>` 但**保留内容**（`Fe<sup>II</sup>` → `FeII`），
      再 `joinFormulaFragments` 把 `M II 4 L 6` 粘回 `MII4L6`，
      同时靠「必须有数字」避免把 `A Self-Assembled` 粘成 `ASelf-Assembled`。
    - 你的标题里同样有这类问题。

## 3. Sci-lib 前端设计里可直接搬的交互

- **筛选条件全写进 URL**（`app.js:queryToParams`）：可收藏、可前进后退。
- **`#标签` 写进搜索框**（`public/js/search.js`）：不再单独占一块标签面板，
  输入 `#` 有联想、`↑↓` 选择、`Enter` 补全。
- **卡片 / 紧凑双视图**，偏好存 localStorage + 同步服务端。
- **批量操作**（`components.js:selectHelpers`）：批量模式下整张卡片都是勾选热区，
  星标降级为装饰（`pointer-events:none`）避免误点。
- **`Ctrl/Cmd+K` 聚焦搜索、`Ctrl+S` 提交表单、`n` 新建、`t` 切主题、`/` 搜索**。
- **纸感 / 夜间双主题**，`:root` 与 `[data-theme="night"]` 两套变量。
- **空状态、toast、确认弹窗**都有明确中文文案与「为什么这么设计」的注释。

## 4. 不该抄的地方

- **纯 JSON 存储**：他的是几百条题录的规模，所以能在内存里建倒排索引；
  你有 4566 张图的元数据 + 全文，且要按图片检索，SQLite 更合适。
- **`data/library.json` 单文件**：没有事务、没有并发写保护（靠 200 ms 防抖掩盖）。
- **没有图片/图表级检索**：这是你相对他唯一的**结构性优势**，别丢。
- **前端 50 万行字节的手写 JS**：维护成本高，你没有必要为「换前端」重写。

## 5. 结论：该学什么、不该学什么

**学**：工程细节与数据模型 —— CrossRef 补题录、DOI 跨行拼接、标题打分、
bigram 中文分词、布尔检索、引用导出、原子写 + 自动备份、可见性字段语义。

**不学**：把 Streamlit 换成手写 SPA。你的核心价值在
「PyMuPDF 抽图 + AI 打标 + Figure 级检索」，重写前端不会让检索变准。

**建议的落地顺序**（按性价比）：
1. 引用格式导出（独立、零风险、马上能用）
2. CrossRef 补 DOI/年份/作者/期刊（同时修「年份 2074」这类脏数据）
3. 用 bbox 空间最近邻重做图注匹配（这是图片检索准确率的地基）
4. 把 `title_extract` 接进 `parser.parse_one`（新论文不用再手动 backfill）
5. 搜索：FTS5 之外加 bigram 分词 + 布尔语法
6. 图片描述换成真·多模态（Claude / GPT-4o / Qwen-VL），别再用 caption 猜
