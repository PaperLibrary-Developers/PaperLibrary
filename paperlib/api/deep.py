"""深度分析端点（合并后新增，PaperLibrary 能力）。

  GET  /api/papers/:id/deep-analysis    取已存结果
  POST /api/papers/:id/deep-analysis    运行 22 字段 + 五维评分（无 Key 时本地降级）
"""
from __future__ import annotations

from paperlib.todo import todo

get_analysis = todo("deep.get")
run_analysis = todo("deep.run")
