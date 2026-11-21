"""领域层 · 全库智能问答助手（Copilot）。

双引擎：
  ① 离线引擎（offline_answer）：内置规则表 + 库藏统计 + 库内关键词检索，
     不联网、不花钱、不需要 API Key。系统操作 / 检索技巧 / 引文导出 / PDF 阅读 /
     库藏概况都能即时回答；答不上来时给能力清单与入口，而不是报错。
  ② 在线引擎（LLM）：把「使用说明 + 库藏统计 + 检索到的文献」拼成资料包交给模型，
     支持跨文献问答、检索词设计、文献联想。调用失败时自动退回离线答案（降级不中断）。

和单篇对话（domain/chat.py）的区别：
  · 不绑定某一篇文献：先在库里检索最相关的几篇，把题录 / 摘要 / 正文节选拼成资料包；
  · 系统提示词里同时放「软件使用说明 + 库藏统计」，所以既能答「怎么用」，也能答「库里有什么」；
  · 会话只在前端保留（切页还在、刷新清空），不写回 papers.chat，避免污染单篇对话历史。

本层不 import api、不决定 HTTP 状态码；校验失败返回 {"ok": False, "status": n, "error": msg}。
"""
from __future__ import annotations

import re
from urllib.parse import quote

from paperlib.domain.ai import build_excerpt
from paperlib.domain.chat import STOP_WORDS
from paperlib.util import CJK_RE, tokenize

MAX_SOURCES = 5          # 最多带几篇文献进资料包（再多容易超上下文，也帮不上忙）
EXCERPT_CHARS = 900      # 每篇正文节选上限
SUMMARY_CHARS = 420      # 每篇摘要上限
HISTORY_LIMIT = 8        # 最多带几轮历史，防止越聊越贵
QUESTION_MAX = 2000

# 统计里的类型键转成界面上用的中文（stats 给的是 journal/preprint 这些原始键）
TYPE_LABELS = {
    "journal": "期刊", "conference": "会议", "preprint": "预印本", "book": "图书",
    "thesis": "学位论文", "report": "报告", "web": "网页", "other": "其他",
}

# 「问库藏」的兜底判定：没配模型时这类问题也能直接算出来回答
STATS_RE = re.compile(r"概况|统计|多少篇|几篇|总量|库藏|数据概览|库里有多少|多少个标签|总共有", re.I)

FEATURE_BRIEF = """\
PaperLibrary 是一个纯本地的文献资料库（数据都在 data/ 里，不上云）。主要功能与入口：

1. 导入文献：左侧「＋ 新增文献」→ #/new。支持把 PDF 拖进虚线框（可多选，自动解析题录与正文）、
   粘贴 DOI 回车抓取题录、手动录入；「设置 → 资产与备份 → 原文与补充材料」里还能扫描
   data/article/papers/ 目录批量入库。
2. 整理分类：左侧栏「分类文件夹」可折叠的目录树。新建用表头的 ＋，管理（重命名/删除）用 ⚙。
   改单篇分类：列表每行最左边的文件夹按钮（星标右边）、卡片底部的分类胶囊、详情页「文献信息 → 分类」。
   批量改：列表顶部「批量」→ 勾选 → 底部浮动条「移动到目录」。分类只存在库里，不会在磁盘上建文件夹。
3. 检索：顶部搜索框（Ctrl+K）。支持 #标签、author:、year:、venue: 等字段限定，AND/OR/NOT（中文可用
   与/或/非），引号短语整体匹配；搜索页把范围切到「全文」即可查正文（需要 PDF 已抽取正文）。
4. 图表：左侧「图表画廊」#/figures，按图注跨文献找图；详情页可把图发给模型识别。
5. 横向对比：左侧「横向对比」#/compare，选 2–8 篇同类文献，按模板把同样字段排成一张表（不需要模型）。
6. 深度分析：左侧「深度分析」#/deep，单篇 22 个字段 + 五维评分，结果缓存在本地。
7. 引用导出：详情页的引用面板可复制/导出 GB/T 7714、BibTeX 等；「设置 → 资产与备份 → 导出与备份」有整库导出。
8. 备份与迁移：设置 → 资产与备份，可导出/导入 JSON 备份，换电脑时用它。
9. 大模型（可选）：设置 → API服务 → 大模型，填服务商 / 模型 id / 接口地址 / API Key 后，才有 AI 总结、
   中文摘要、单篇对话和本助手。没有配置时这些按钮会提示去配置，其余功能不受影响。
10. 自动停机：关掉所有网页 30 秒后服务自动保存并停止，不需要手动点停止。
11. 使用文档：左侧栏最下方「使用文档」#/guide，有完整的新手手册与排错。
"""

# ---------------------------------------------------------------- 离线知识库
# 每条规则 = 一组关键词 + 一段能照着做的回答 + 直达入口。
# 写作要求（侧栏只有 200 多像素宽）：结论先说、步骤不超 4 条、不写客套话；
# 关键词全部小写、不带空格，匹配时问题会先做同样的归一化。

OFFLINE_RULES = [
    {
        "id": "import",
        "title": "导入文献",
        "keys": ("导入", "入库", "新增文献", "添加文献", "导进来", "拖进去", "拖入", "doi", "抓取",
                 "扫描目录", "批量识别", "导入pdf", "pdf导入", "下载", "加进"),
        "answer": (
            "**导入有三条路，任选一条：**\n"
            "1. 左侧「＋ 新增文献」→ 把 PDF 拖进虚线框，可多选，自动解析题录与正文；\n"
            "2. 同一页粘贴 DOI 回车，自动抓题录；\n"
            "3. 设置 → 资产与备份 → 原文与补充材料 →「重新扫描」，把已放进 `data/article/papers/` 的 PDF 批量入库。\n\n"
            "导入时可以顺手选分类文件夹；题录识别不全就手动补，保存后还能改。"
        ),
        "actions": [{"label": "去导入", "hash": "#/new"}, {"label": "扫描目录", "hash": "#/settings"}],
    },
    {
        "id": "search",
        "title": "检索技巧",
        "keys": ("搜索", "检索", "查找", "找文献", "关键词", "#标签", "过滤器", "author:", "year:", "全文"),
        "answer": (
            "**检索的四个层次：**\n"
            "- 顶部搜索框或 `Ctrl+K`，输入即搜；\n"
            "- `#标签` 直接筛标签（输入 `#` 有联想）；\n"
            "- 字段限定：`author:Smith`、`year:2024`、`venue:Nature`、`title:钙钛矿`；\n"
            "- 布尔组合：`钙钛矿 AND 稳定性`、`(CV OR 循环伏安) NOT 综述`，引号包短语整体匹配。\n\n"
            "想搜正文内容：搜索页（#/search）把范围切到「全文」（需要 PDF 已抽取正文）。"
        ),
        "actions": [{"label": "去搜索", "hash": "#/search"}],
    },
    {
        "id": "cite",
        "title": "引文导出",
        "keys": ("bibtex", "引用", "引文", "参考文献", "导出", "gb/t", "7714", "endnote", "zotero", "latex"),
        "answer": (
            "**引用导出的三个位置：**\n"
            "- 单篇：详情页右上「引用」，可复制 GB/T 7714、BibTeX、APA 等格式；\n"
            "- 整库：设置 → 资产与备份 → 导出与备份 →「导出 BibTeX（.bib）」，可直接进 Zotero / EndNote / LaTeX；\n"
            "- 顺手备份：同一块的「导出全部（JSON 备份）」含题录、标签、笔记。\n\n"
            "BibTeX 键在详情页「更多信息」里能看到，复制后可以自己改。"
        ),
        "actions": [{"label": "设置里导出", "hash": "#/settings"}],
    },
    {
        "id": "pdf",
        "title": "PDF 阅读",
        "keys": ("pdf", "阅读", "阅读器", "翻页", "缩放", "选中", "框选", "打不开", "原文"),
        "answer": (
            "**PDF 阅读与提问：**\n"
            "- 详情页点「打开 PDF」进阅读器，可翻页、缩放、适应宽度，文字能选中复制；\n"
            "- 在阅读器里**框选一块图/表**会弹出提问框，默认问「解释这张图/表」，可直接发给模型；\n"
            "- 详情页也能贴图提问，适合图片版 PDF 或审稿图；\n"
            "- 提示「PDF 缺失」时，到编辑页重新关联 `papers/` 里的文件即可。"
        ),
        "actions": [{"label": "回文献库", "hash": "#/"}],
    },
    {
        "id": "figures",
        "title": "图表检索",
        "keys": ("图表", "图注", "插图", "曲线", "图片", "sem", "怎么搜图", "找图"),
        "answer": (
            "**按图注找图：**\n"
            "左侧「图表画廊」会把全库图注抽出来，按关键词跨文献检索，比如「CV 曲线」「SEM」「阻抗」。\n"
            "点一张图能回到原文献的对应位置；配置模型后还能把图发给它识别、解释趋势。"
        ),
        "actions": [{"label": "图表画廊", "hash": "#/figures"}],
    },
    {
        "id": "compare",
        "title": "横向对比",
        "keys": ("对比", "横向", "比较", "差异", "放在一起"),
        "answer": (
            "**横向对比（不需要模型）：**\n"
            "左侧「横向对比」→ 选 2–8 篇同类文献 → 按模板把研究问题、方法、数据、结论排成一张表。\n"
            "适合开题、组会和写综述前快速扫一遍；配上在线模型，还能基于这张表做综合分析。"
        ),
        "actions": [{"label": "去对比", "hash": "#/compare"}],
    },
    {
        "id": "deep",
        "title": "深度分析",
        "keys": ("深度分析", "评分", "五维", "结构化", "字段"),
        "answer": (
            "**深度分析：**\n"
            "左侧「深度分析」对单篇文献抽 22 个结构化字段 + 五维评分，结果缓存在本地，重复打开不重复花钱。\n"
            "它和「单篇对话」不同：前者是固定字段的卡片，后者是自由问答。"
        ),
        "actions": [{"label": "去分析", "hash": "#/deep"}],
    },
    {
        "id": "folder",
        "title": "分类文件夹",
        "keys": ("分类", "文件夹", "目录", "归类", "整理", "移动"),
        "answer": (
            "**分类文件夹：**\n"
            "- 侧栏「分类文件夹」表头的 ＋ 新建、⚙ 管理（重命名 / 删除），支持多层；\n"
            "- 单篇改：列表行首的文件夹按钮、卡片底部胶囊、详情页「文献信息 → 分类」；\n"
            "- 批量改：列表点「批量」→ 勾选 → 底部「移动到目录」。\n\n"
            "分类只存在库里，不会在磁盘上真的建文件夹，移动文献也不会动 PDF 文件。"
        ),
        "actions": [{"label": "回列表", "hash": "#/"}],
    },
    {
        "id": "backup",
        "title": "备份与迁移",
        "keys": ("备份", "迁移", "换电脑", "恢复", "存档", "还原"),
        "answer": (
            "**备份与迁移：**\n"
            "设置 → 资产与备份 → 导出与备份，「导出全部（JSON 备份）」是完整存档（题录、标签、笔记、AI 总结）。\n"
            "换电脑时在新机器上「导入备份」即可原样恢复；`article/` 目录可以直接拷走，两者合起来就是全量备份。"
        ),
        "actions": [{"label": "去备份", "hash": "#/settings"}],
    },
    {
        "id": "model",
        "title": "离线 / 在线",
        "keys": ("大模型", "模型", "apikey", "api密钥", "deepseek", "claude", "gpt", "ollama", "区别", "在线", "离线"),
        "answer": (
            "**离线 vs 在线：**\n"
            "- 不填 Key：现在就能用——系统操作、检索技巧、引文导出、库藏统计都由本地规则即时回答，不联网、不花钱；\n"
            "- 填 Key：设置 → API服务 → 大模型，选 DeepSeek / Claude / OpenAI / Kimi，或本地 Ollama（无需密钥）。之后能做跨文献问答、检索词设计、文献联想、图表解释。\n\n"
            "在线调用失败会自动退回离线回答，不会把对话卡住。"
        ),
        "actions": [{"label": "去配置", "hash": "#/settings"}],
    },
    {
        "id": "idle",
        "title": "服务与端口",
        "keys": ("端口", "自动停", "停机", "关闭服务", "常驻", "启动", "4321", "占用"),
        "answer": (
            "**服务与端口：**\n"
            "关掉所有网页后，服务会在空闲 30 秒后自动保存并退出，不需要手动停；想让它常驻，启动时加 `--idle-exit 0`。\n"
            "端口默认 4321，可用 `--port` 改；启动窗口会打印实际地址。"
        ),
        "actions": [{"label": "看使用文档", "hash": "#/guide"}],
    },
    {
        "id": "tips",
        "title": "快捷键",
        "keys": ("快捷键", "主题", "夜间", "深色", "字体", "侧栏", "排序", "每页"),
        "answer": (
            "**顺手的小功能：**\n"
            "- `Ctrl+K` 聚焦搜索，`Ctrl+B` 收起 / 展开侧栏；\n"
            "- 右上角 ☀ / ☾ 切换纸感与夜间主题；\n"
            "- 搜索框输入 `#` 会出现标签联想；\n"
            "- 列表可在卡片 / 紧凑之间切换，排序与每页条数会被记住。"
        ),
        "actions": [{"label": "看使用文档", "hash": "#/guide"}],
    },
    {
        "id": "stats",
        "title": "库藏概况",
        "keys": ("概况", "统计", "多少篇", "几篇", "库藏", "总量", "概览", "多少个标签", "多少文献", "数据情况"),
        "dynamic": "stats",
        "answer": "",
        "actions": [{"label": "回文献库", "hash": "#/"}, {"label": "看时间线", "hash": "#/timeline"}],
    },
]

FALLBACK_ANSWER = (
    "这句我还接不住——换个更具体的说法就能答到点上，比如：\n"
    "- 「怎么导入 PDF」\n"
    "- 「BibTeX 在哪导出」\n"
    "- 「怎么用 #标签 检索」\n"
    "- 「库藏概况」\n\n"
    "也可以直接打一个关键词：导入、检索、引用、PDF、图表、对比、备份、分类。\n"
    "想要跨文献的深度分析（检索词设计、主题联想、比较结论），到设置里填一个大模型 Key 就能开启。"
)

SYSTEM_PROMPT = """\
你是 PaperLibrary 的智能助手，服务对象是正在用这个本地文献库做研究的人。
你能看到两类材料：A) 这个软件的功能与操作说明；B) 从用户文献库里检索出来的若干篇文献（题录 + 摘要 + 正文节选）。

回答规则：
1. 先判断问题类型：
   · 问「怎么用 / 在哪 / 有什么区别」→ 按功能说明回答，给出具体入口（左侧栏哪一项、页面哪个按钮）。
   · 问「库里有什么 / 哪篇讲过 X / 帮我比较这几篇」→ 只依据检索到的文献回答。
2. 涉及文献内容时，必须用 [1] [2] 这样的编号标注出处，编号对应资料包里的文献序号；
   资料里没有的信息（数据、结论、页码）明确说「库里检索到的内容里没有」，不要编造，也不要凭常识补。
3. 检索结果为空、而问题又是在问库里的内容时，直接说「库里没有检索到相关文献」，并建议换个关键词或先导入。
4. 用中文回答，简洁、专业；需要步骤时用有序列表，需要对比时用表格或小标题。不要客套话。
5. 不要在结尾重复列一遍参考来源清单（界面会自动显示来源卡片），正文里保留 [n] 即可。
6. 公式与化学式用 LaTeX：行内 $…$、独立成行 $$…$$、化学式 \\ce{…}；数学模式之外不要写 LaTeX 命令。
7. 当用户问「怎么搜 / 检索词 / 找相关文献 / 有什么方向」时，按学术检索的思路给可执行方案：
   · 先给 2–4 组检索表达式，直接可用本应用语法（`title:` `author:` `year:` `venue:` `#标签`，
     以及 AND / OR / NOT、引号短语），中英文同义词各给一条；
   · 再从检索到的库内文献里点名 2–4 篇，说明「为什么和这个问题相关」（方法、体系、结论上的对应关系）；
   · 最后给 2–3 个可延伸的方向（相邻方法、综述、对标工作），并注明这是推断/建议，不是库内已有结论。
8. 用户的问题如果只是「怎么用」，不要硬扯文献内容；如果既问操作又问内容，先答操作再答内容。
"""


def _tokens(question):
    """问题里的检索词。中文按词切，英文丢掉虚词与过短的词。"""
    out = []
    seen = set()
    for token in tokenize(str(question or "")):
        low = str(token or "").strip().lower()
        if len(low) < 2 or low in seen:
            continue
        if not CJK_RE.search(low) and (len(low) < 3 or low in STOP_WORDS):
            continue
        seen.add(low)
        out.append(low)
        if len(out) >= 12:
            break
    return out


def _paper_year(paper):
    try:
        return int(paper.get("year") or 0)
    except (TypeError, ValueError):
        return 0


def _score(paper, tokens):
    """题录/摘要/标签的粗排。标题命中权重最高，其次标签，再摘要。"""
    title = str(paper.get("title") or "").lower()
    summary = " ".join(str(paper.get(k) or "") for k in ("summary", "summaryZh", "notes")).lower()
    tags = " ".join(str(tag) for tag in (paper.get("tags") or [])).lower()
    authors = " ".join(str(author) for author in (paper.get("authors") or [])).lower()
    venue = str(paper.get("venue") or "").lower()
    score = 0
    for token in tokens:
        if token in title:
            score += 8
        if token in tags:
            score += 5
        if token in summary:
            score += 3
        if token in authors:
            score += 2
        if token in venue:
            score += 1
    if paper.get("starred"):
        score += 0.5
    return score


def _summary_of(paper):
    text = str(paper.get("summaryZh") or paper.get("summary") or "").strip()
    return text[:SUMMARY_CHARS]


def _source_row(paper, index):
    return {
        "index": index,
        "id": str(paper.get("id") or ""),
        "title": str(paper.get("title") or "（无标题）"),
        "year": paper.get("year") or None,
        "venue": str(paper.get("venue") or ""),
        "folder": str(paper.get("folder") or ""),
    }


def build_context(services, question):
    """检索 + 拼资料包。返回 {sources, context, tokens}。"""
    tokens = _tokens(question)
    papers = services.library.all() or []
    scored = []
    for paper in papers:
        score = _score(paper, tokens)
        if score > 0:
            scored.append((score, paper))
    scored.sort(key=lambda item: (-item[0], -_paper_year(item[1]), str(item[1].get("title") or "")))

    picked = [paper for _, paper in scored[:MAX_SOURCES]]
    sources = []
    blocks = []
    for index, paper in enumerate(picked, 1):
        sources.append(_source_row(paper, index))
        lines = [
            "[%d] %s" % (index, str(paper.get("title") or "（无标题）")),
            "作者：%s　年份：%s　期刊/会议：%s" % (
                "、".join(str(a) for a in (paper.get("authors") or [])[:6]) or "（未填）",
                paper.get("year") or "（未填）",
                paper.get("venue") or "（未填）",
            ),
        ]
        if paper.get("tags"):
            lines.append("标签：" + "、".join(str(tag) for tag in paper["tags"][:8]))
        if paper.get("folder"):
            lines.append("分类：" + str(paper["folder"]))
        summary = _summary_of(paper)
        if summary:
            lines.append("摘要：" + summary)
        try:
            excerpt = build_excerpt(services, paper.get("id"), EXCERPT_CHARS)
        except Exception:  # noqa: BLE001 - 正文不可读时只问题录
            excerpt = ""
        if excerpt:
            lines.append("正文节选：\n" + excerpt)
        blocks.append("\n".join(lines))

    context = ("用户问题相关度最高的 %d 篇库内文献：\n\n" % len(blocks) + "\n\n---\n\n".join(blocks)) if blocks else ""
    return {"tokens": tokens, "sources": sources, "context": context}


PROVIDER_LABELS = {
    "deepseek": "DeepSeek", "anthropic": "Claude", "openai": "OpenAI",
    "moonshot": "Kimi", "dashscope": "通义千问", "ollama": "本地 Ollama",
}


def engine_info(services):
    """给前端的引擎状态：离线 / 在线是哪家、哪个模型、为什么没就绪。"""
    readiness = services.llm.readiness()
    provider = str(services.llm.settings.get("provider") or "")
    return {
        "ready": bool(readiness.get("ready")),
        "engine": "online" if readiness.get("ready") else "offline",
        "provider": provider,
        "providerLabel": PROVIDER_LABELS.get(provider, provider or "大模型"),
        "model": str(services.llm.settings.get("model") or ""),
        "reason": str(readiness.get("reason") or ""),
    }


def stats_snapshot(services):
    """给提示词、接口和离线回答共用的库藏统计。"""
    stats = services.library.stats() or {}
    folders = {}
    try:
        folders = services.folders.list() or {}
    except Exception:  # noqa: BLE001 - 目录服务不可用时不影响问答
        folders = {}
    years = [row.get("year") for row in (stats.get("byYear") or []) if row.get("year")]
    top_tags = [row for row in (services.library.tags() or [])[:8]]
    top_folders = sorted(
        (row for row in (folders.get("folders") or []) if row.get("countDeep") or row.get("count")),
        key=lambda row: -(row.get("countDeep") or 0),
    )[:6]
    return {
        "total": stats.get("total") or 0,
        "withPdf": stats.get("withPdf") or 0,
        "pdfMissing": stats.get("pdfMissing") or 0,
        "starred": stats.get("starred") or 0,
        "tags": stats.get("tags") or 0,
        "folders": len(folders.get("folders") or []),
        "rootCount": folders.get("rootCount") or 0,
        "yearRange": [min(years), max(years)] if years else [],
        "byType": stats.get("byType") or [],
        "topTags": top_tags,
        "topFolders": top_folders,
        "updatedAt": stats.get("updatedAt") or "",
    }


def stats_markdown(snapshot):
    """离线也能回答「库藏概况」：把统计排成一段 Markdown。"""
    s = snapshot or {}
    total = s.get("total") or 0
    if not total:
        return "当前文献库还是空的。可以先到「＋ 新增文献」拖入 PDF，或在「设置 → 资产与备份 → 导出与备份」里选 JSON 文件导入备份。"

    lines = ["**当前库藏**：共 **%d** 篇文献" % total]
    bits = []
    if s.get("withPdf"):
        bits.append("已关联 PDF %d 篇" % s["withPdf"])
    if s.get("pdfMissing"):
        bits.append("PDF 文件缺失 %d 篇" % s["pdfMissing"])
    if s.get("starred"):
        bits.append("收藏 %d 篇" % s["starred"])
    if bits:
        lines.append("；".join(bits) + "。")
    if s.get("yearRange"):
        lines.append("年份范围：%s–%s。" % (s["yearRange"][0], s["yearRange"][1]))
    types = [row for row in (s.get("byType") or [])
             if row.get("type") and row.get("type") != "unknown"]
    if types:
        lines.append("类型分布：" + "、".join(
            "%s %d" % (TYPE_LABELS.get(str(row.get("type")), str(row.get("type"))), row.get("count") or 0)
            for row in types[:6]) + "。")
    if s.get("topTags"):
        lines.append("高频标签：" + "、".join(
            "#%s（%d）" % (row.get("name"), row.get("count") or 0) for row in s["topTags"][:6]) + "。")
    if s.get("topFolders"):
        lines.append("文献最多的分类：" + "、".join(
            "%s（%d 篇）" % (row.get("name") or row.get("path"), row.get("countDeep") or row.get("count") or 0)
            for row in s["topFolders"][:5]) + "。")
    if s.get("rootCount"):
        lines.append("另有 %d 篇还没归入分类文件夹。" % s["rootCount"])
    lines.append("\n想细分的话，可以到「年份时间线」「图表画廊」或某个分类文件夹里继续看。")
    return "\n".join(lines)


def _squash(text):
    """问题归一化：去空白、转小写。规则关键词也按这个口径写。"""
    return re.sub(r"\s+", "", str(text or "")).lower()


def match_rule(question):
    """按关键词命中长度打分，取最高的一条；低于阈值视为没命中。"""
    text = _squash(question)
    best = None
    best_score = 0
    for rule in OFFLINE_RULES:
        score = 0
        for key in rule.get("keys") or ():
            if key and key in text:
                score += len(key) + 1
        if score > best_score:
            best, best_score = rule, score
    return best if best_score >= 3 else None


def library_hits(services, question, limit=3):
    """离线也能用库藏上下文：按问题里的词粗排题录 / 标签 / 摘要，取最相关几篇。"""
    tokens = _tokens(question)
    if not tokens:
        return []
    scored = []
    for paper in services.library.all() or []:
        score = _score(paper, tokens)
        # 只有摘要里蹭到一个词（3 分）不算相关；标题 8 分、标签 5 分才算
        if score >= 5:
            scored.append((score, paper))
    scored.sort(key=lambda item: (-item[0], -_paper_year(item[1]), str(item[1].get("title") or "")))
    return [paper for _, paper in scored[:limit]]


def offline_answer(services, question, snapshot=None):
    """离线引擎：规则表 → 统计 → 库内检索 → 兜底。返回 {intent, answer, sources, actions}。"""
    snapshot = snapshot or stats_snapshot(services)
    rule = match_rule(question)
    if rule:
        if rule.get("dynamic") == "stats":
            answer = stats_markdown(snapshot)
        else:
            answer = rule.get("answer") or ""
        return {
            "intent": rule.get("id") or "",
            "answer": answer,
            "sources": [],
            "actions": list(rule.get("actions") or []),
        }

    hits = library_hits(services, question)
    if hits:
        lines = ["**库里检索到 %d 篇相关文献：**" % len(hits)]
        for index, paper in enumerate(hits, 1):
            meta = " · ".join(str(x) for x in (paper.get("year") or "", paper.get("venue") or "") if x)
            lines.append("%d. %s%s" % (index, paper.get("title") or "（无标题）", ("（%s）" % meta) if meta else ""))
        lines.append("\n点下面的卡片能打开原文。想做跨文献比较或检索词扩展，配置在线模型后问同一句话即可。")
        return {
            "intent": "library",
            "answer": "\n".join(lines),
            "sources": [_source_row(paper, index) for index, paper in enumerate(hits, 1)],
            "actions": [{"label": "看全部结果", "hash": "#/search?q=" + quote(_tokens(question)[0] if _tokens(question) else "")}],
        }

    return {
        "intent": "fallback",
        "answer": FALLBACK_ANSWER,
        "sources": [],
        "actions": [{"label": "看使用文档", "hash": "#/guide"}, {"label": "去导入", "hash": "#/new"}],
    }


def _offline_frames(stream, snapshot, reply, reason="", degraded=False, prefix=""):
    """把离线答案按 SSE 协议的 start / delta / done 发出去（前端两种引擎用同一套回调）。"""
    payload = {
        "type": "start",
        "ready": False,
        "offline": True,
        "engine": "offline",
        "model": "",
        "reason": str(reason or ""),
        "stats": snapshot,
        "sources": reply.get("sources") or [],
        "actions": reply.get("actions") or [],
        "intent": reply.get("intent") or "",
        "degraded": bool(degraded),
    }
    send = stream.open()
    send(payload)
    text = (prefix or "") + str(reply.get("answer") or "")
    send({"type": "delta", "text": text})
    send({
        "type": "done",
        "text": text,
        "sources": reply.get("sources") or [],
        "actions": reply.get("actions") or [],
        "offline": True,
        "engine": "offline",
        "intent": reply.get("intent") or "",
        "degraded": bool(degraded),
    })
    return text


def _history_of(body):
    raw = body.get("history") if isinstance(body, dict) else None
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw[-HISTORY_LIMIT:]:
        if not isinstance(item, dict):
            continue
        role = "assistant" if str(item.get("role")) == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            out.append({"role": role, "content": content[:4000]})
    return out


def ask_stream(services, body, stream):
    """POST /api/copilot/ask：检索 → 拼资料包 → 调模型 → SSE。"""
    options = body or {}
    question = str(options.get("question") or "").strip()
    if not question:
        return {"ok": False, "status": 400, "error": "请输入问题"}
    if len(question) > QUESTION_MAX:
        question = question[:QUESTION_MAX]

    snapshot = stats_snapshot(services)
    readiness = services.llm.readiness()
    ready = bool(readiness.get("ready"))

    # 没配模型：走离线引擎。系统操作 / 检索技巧 / 引文导出 / PDF 阅读 / 库藏统计
    # 都能直接答出来——以前这里只放行「统计类」问题，其余一律 400，等于把新手挡在门外。
    if not ready:
        reply = offline_answer(services, question, snapshot)
        _offline_frames(stream, snapshot, reply, reason=str(readiness.get("reason") or ""))
        return {"ok": True}

    built = build_context(services, question)
    history = _history_of(options)

    send = stream.open()
    send({"type": "start", "ready": True, "offline": False, "engine": "online",
          "model": str(services.llm.settings.get("model") or ""),
          "stats": snapshot, "sources": built["sources"]})

    message_lines = [
        "库藏统计（回答「库里有多少/最近情况」时用）：",
        "- 总篇数 %d，有 PDF %d，缺 PDF %d，收藏 %d，标签 %d 个，分类文件夹 %d 个"
        % (snapshot["total"], snapshot["withPdf"], snapshot["pdfMissing"], snapshot["starred"],
           snapshot["tags"], snapshot["folders"]),
    ]
    if snapshot.get("yearRange"):
        message_lines.append("- 年份范围 %s–%s" % (snapshot["yearRange"][0], snapshot["yearRange"][1]))
    if snapshot.get("topTags"):
        message_lines.append("- 高频标签：" + "、".join(
            "%s(%d)" % (row.get("name"), row.get("count") or 0) for row in snapshot["topTags"][:6]))
    message_lines.append("")
    message_lines.append("功能说明：")
    message_lines.append(FEATURE_BRIEF)
    message_lines.append("")
    message_lines.append(built["context"] or "（这次没有检索到与问题相关的库内文献。）")

    collected = []

    def on_delta(chunk):
        collected.append(chunk)
        send({"type": "delta", "text": chunk})

    try:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "可用材料如下：\n\n" + "\n".join(message_lines)},
        ] + history + [{"role": "user", "content": question}]
        result = services.llm.chat_stream(on_delta, {"messages": messages})
        answer = str((result or {}).get("text") or "".join(collected)).strip()
        send({"type": "done", "text": answer, "sources": built["sources"],
              "engine": "online", "usage": (result or {}).get("usage") or None})
    except Exception as exc:  # noqa: BLE001
        # 降级：模型挂了不等于助手挂了。已经吐出来的半截回答保留，后面接离线知识库的答案，
        # 并说明原因——比只弹一句「请求失败」有用得多。
        reply = offline_answer(services, question, snapshot)
        note = "**模型调用失败**（%s），已切换到离线知识库：\n\n" % str(exc)
        tail = note + str(reply.get("answer") or "")
        if collected:
            tail = "\n\n---\n\n" + tail
        send({"type": "delta", "text": tail})
        send({"type": "done", "text": "".join(collected) + tail,
              "sources": built["sources"] + (reply.get("sources") or []),
              "actions": reply.get("actions") or [],
              "offline": True, "engine": "offline", "degraded": True,
              "intent": reply.get("intent") or ""})
    return {"ok": True}
