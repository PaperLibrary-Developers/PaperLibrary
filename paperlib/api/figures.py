"""图表端点（合并后新增，PaperLibrary 能力）。

  GET  /api/figures                图表画廊（q / paperId / type / page / pageSize）
  GET  /api/figures/:id/image      图表图片流（Range/ETag）
  GET  /api/papers/:id/figures     某篇文献的图表
  POST /api/papers/:id/figures/extract  从 PDF 重新提取（PyMuPDF + 图注 bbox 匹配）
"""
from __future__ import annotations

from paperlib.todo import todo

list_figures = todo("figures.list")
figure_image = todo("figures.image")
paper_figures = todo("figures.paperFigures")
extract_figures = todo("figures.extract")
