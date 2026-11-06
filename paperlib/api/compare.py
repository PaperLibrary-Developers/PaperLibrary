"""横向对比端点（合并后新增，PaperLibrary 能力）。

  GET  /api/compare/templates   4 套化学模板
  POST /api/compare             { paperIds[], template? } → Map-Reduce 对比结果
"""
from __future__ import annotations

from paperlib.todo import todo

templates = todo("compare.templates")
run = todo("compare.run")
