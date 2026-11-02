# -*- coding: utf-8 -*-
"""_smoke_detail.py — 验证详情页（含新的深度分析面板 / Obsidian 导出预览）能渲染。"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import sys
import io
import traceback

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import database
from streamlit.testing.v1 import AppTest

rows = database.get_all_papers()[:3]
print("[papers]", [(r["id"][:8], (r.get("title") or "")[:40]) for r in rows])
if not rows:
    print("no papers, skip")
    sys.exit(0)

pid = rows[0]["id"]
at = AppTest.from_file(_os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), "app.py"), default_timeout=180)
at.session_state["selected_paper_id"] = pid
try:
    at.run()
except Exception:
    traceback.print_exc()
    sys.exit(1)

if at.exception:
    for e in at.exception:
        print("EXC:", e.value)
        print(e.stack_trace)
    sys.exit(1)

labels = [b.label for b in at.button]
print("[deep buttons]", [l for l in labels if any(k in l for k in ("深度分析", "高清图表", "Obsidian"))])
print("[errors]", [e.value for e in at.error])
print("[expanders]", [x.label for x in at.expander][:12])
print("DETAIL SMOKE OK")
