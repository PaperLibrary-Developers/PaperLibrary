"""PaperLibrary 能力移植包（PaperLibrary → paperlib/legacy）。

移植原则（见 docs/内部接口-模块边界-20261107.md §2 T6）：
  · 去掉 Streamlit 与直接 sqlite3 连接：数据访问一律由调用方经 store / store.repo 注入；
  · PDF 解析权威实现统一为 PyMuPDF（pymupdf），不再用 pdfjs / pdfplumber；
  · 每个模块只保留「可复用算法」，UI 与网络副作用上移到 api/ 或 adapters/。

模块与来源（PaperLibrary/app/*.py）：
  caption_matcher  图注 bbox 空间匹配        ← caption_matcher.py
  figure_anchor    图注身份识别 + 正文讨论定位 ← figure_anchor.py
  figures          PDF 图表提取（PyMuPDF）    ← parser.py（extract_figures）+ figures_arxiv 的落盘规则
  lexicon          中英术语表（155 条）        ← local_rank.py 的 GLOSSARY + 补充
  compare_engine   横向对比模板/矩阵/报告      ← compare_engine.py
  deep_prompt      深度分析系统提示词 + 24 语义槽位表 ← deep_prompt.py
  deep_local       深度分析的本地降级（无 Key）        ← deep_analysis.py
  deep_analysis    深度分析编排 + LLM 结果映射          ← deep_analysis.py
  obsidian_note    Obsidian 笔记拼装           ← obsidian_export.py（落盘改由 adapters/obsidian.py）
"""
from __future__ import annotations

__all__ = [
    "caption_matcher",
    "figure_anchor",
    "figures",
    "lexicon",
    "compare_engine",
    "deep_prompt",
    "deep_local",
    "deep_analysis",
    "obsidian_note",
]
