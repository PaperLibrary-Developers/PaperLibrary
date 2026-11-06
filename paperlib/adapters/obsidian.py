"""Obsidian 导出（PaperLibrary 能力；移植自 app/obsidian_export.py）。

输出为 zip（或单目录），保留 frontmatter、标签、笔记与图表链接。
"""
from __future__ import annotations


def build_zip(papers, destination, options=None):
    raise NotImplementedError("待实现：obsidian.build_zip（移植 obsidian_export.py）")
