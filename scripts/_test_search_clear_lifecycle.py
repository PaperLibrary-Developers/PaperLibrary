"""Test search clear buttons and Streamlit widget lifecycle safety."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


def test_codebase_widget_lifecycle_safety():
    app_path = os.path.join(os.path.dirname(__file__), "..", "app.py")
    with open(app_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. 确保已消除对已实例化 widget key 的后置直接赋值（只允许出现在前置回调中，禁止出现在 if st.button 内部）
    assert 'if st.button("✕ 清空", key="btn_clear_sq"' not in content, (
        "Found old button block for btn_clear_sq without on_click"
    )
    assert 'if st.button("✕ 清空", key="btn_clear_fq"' not in content, (
        "Found old button block for btn_clear_fq without on_click"
    )
    assert 'st.session_state["fs"] = tag.split()[0]' not in content, (
        "Found forbidden direct assignment to instantiated widget key 'fs' in tag chips!"
    )

    # 2. 确保配置了 on_click 回调函数与 pending 消费缓冲
    assert "_clear_paper_search" in content, "Missing _clear_paper_search callback"
    assert "_clear_figure_search" in content, "Missing _clear_figure_search callback"
    assert "_set_figure_search_tag" in content, "Missing _set_figure_search_tag callback"
    assert "pending_ps" in content, "Missing pending_ps buffer handling"
    assert "pending_fs" in content, "Missing pending_fs buffer handling"

    # 3. 确保按钮绑上了 on_click
    assert 'on_click=_clear_paper_search' in content, "btn_clear_sq must use on_click"
    assert 'on_click=_clear_figure_search' in content, "btn_clear_fq must use on_click"
    assert 'on_click=_set_figure_search_tag' in content, "qtag chips must use on_click"


def test_runtime_apptest_clear():
    from streamlit.testing.v1 import AppTest

    code = """
import streamlit as st

if "pending_ps" in st.session_state:
    st.session_state["ps"] = st.session_state.pop("pending_ps")

def _clear_paper_search():
    st.session_state["ps"] = ""

sq = st.text_input("搜索", key="ps")
if sq:
    st.button("✕ 清空", key="btn_clear_sq", on_click=_clear_paper_search)
"""

    at = AppTest.from_string(code)
    at.run()
    # 模拟输入搜索词
    at.text_input(key="ps").input("chirality").run()
    assert at.text_input(key="ps").value == "chirality"
    assert not at.exception, "Exception after typing search query"

    # 模拟点击清空按钮
    at.button(key="btn_clear_sq").click().run()
    assert not at.exception, f"StreamlitAPIException on clear: {at.exception}"
    assert at.text_input(key="ps").value == "", "Search query not cleared"


if __name__ == "__main__":
    test_codebase_widget_lifecycle_safety()
    test_runtime_apptest_clear()
    print("ALL SEARCH CLEAR LIFECYCLE TESTS PASSED!")
