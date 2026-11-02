# -*- coding: utf-8 -*-
"""
测试文献目录与文献研读子页面（主子页面路由导航）的交互流程
"""
import os
import sys
from streamlit.testing.v1 import AppTest

def test_subpage_navigation():
    print("=== 测试文献目录与研读子页面导航 ===")
    app_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "app.py"))
    at = AppTest.from_file(app_path)
    at.run(timeout=30)
    assert not at.exception, f"目录页加载异常: {at.exception}"
    print("  [OK] 目录页首次渲染正常")

    # 1. 查找目录中的工作台进入按钮
    enter_buttons = [b for b in at.button if "进入研读工作台" in (b.label or "")]
    assert len(enter_buttons) > 0, "目录中未找到'进入研读工作台'按钮"
    print(f"  [OK] 目录中找到 {len(enter_buttons)} 个进入研读工作台按钮")

    # 2. 点击第一个文献的进入按钮
    first_btn = enter_buttons[0]
    first_btn.click()
    at.run(timeout=30)
    assert not at.exception, f"点击进入研读工作台后异常: {at.exception}"
    assert at.session_state["selected_paper_id"] is not None, "session_state 未记录选中的文献 ID"
    print(f"  [OK] 点击成功，进入子页面工作台 (Paper ID: {at.session_state['selected_paper_id']})")

    # 3. 校验子页面核心组件渲染
    back_btns = [b for b in at.button if "返回文献目录" in (b.label or "")]
    assert len(back_btns) > 0, "子页面中未找到'返回文献目录'按钮"
    print("  [OK] 子页面顶栏'返回文献目录'按钮存在")

    # 4. 点击返回按钮，验证回到目录页
    back_btns[0].click()
    at.run(timeout=30)
    assert not at.exception, f"点击返回后页面异常: {at.exception}"
    assert at.session_state["selected_paper_id"] is None, "返回后 session_state 未清空"
    print("  [OK] 点击返回成功，顺利退出子页面回到文献库目录")

    # 5. 再次验证目录页卡片恢复展示
    enter_buttons_after = [b for b in at.button if "进入研读工作台" in (b.label or "")]
    assert len(enter_buttons_after) > 0, "回到目录后未重新渲染卡片列表"
    print("  [OK] 目录页卡片列表重新正常展示")

    print("\n[ALL PASS] Subpage navigation all 5 tests passed successfully!")

if __name__ == "__main__":
    test_subpage_navigation()
