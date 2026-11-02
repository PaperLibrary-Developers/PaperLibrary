# -*- coding: utf-8 -*-
"""
多图横向对比幕布集成测试
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comparator
import app

def test_comparator_ui_integration():
    with open("app.py", "r", encoding="utf-8") as f:
        code = f.read()

    assert "import comparator" in code, "comparator module not imported in app.py"
    assert "def render_figure_comparator_section():" in code, "Missing comparator section renderer"
    assert "render_figure_comparator_section()" in code, "Comparator section not called in app.py"
    assert "btn_cmp_" in code or "fig_cmp_" in code, "Missing compare button in Tab 2 search cards"
    assert "wb_cmp_" in code, "Missing compare button in workbench gallery"
    assert "多图横向对比幕布" in code, "Missing comparator title in app.py"

    # 测试离线问答匹配
    ans = app.get_offline_assistant_reply("怎么多图并排对比实验曲线？", {}, "")
    assert "多图横向对比幕布" in ans, f"Unexpected reply: {ans}"
    assert "最多可同时比对 4 张" in ans

    print("ALL FIGURE COMPARATOR INTEGRATION TESTS PASSED!")

if __name__ == "__main__":
    test_comparator_ui_integration()
