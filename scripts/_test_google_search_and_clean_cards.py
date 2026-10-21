"""Test Google-style hero search bar and card clean layout."""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import database


def test_clean_cards_and_search():
    # 1. 检查 app.py 源码确保每张卡片右侧的冗杂换分类已被彻底移除
    app_path = os.path.join(os.path.dirname(__file__), "..", "app.py")
    with open(app_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 确保首页卡片循环内没有换分类 popover
    assert "with st.popover(f\"📁 换分类\", use_container_width=True):" not in content, \
        "Found old '📁 换分类' popover in catalog card!"

    # 确保没有 PaperHub 命名残留
    assert "PaperHub" not in content, "Found 'PaperHub' in app.py! Must be 'PaperLibrary'"

    # 确保多余的操作行已被彻底删除
    assert "btn_feeling_lucky" not in content, "Found 'btn_feeling_lucky' which was requested to be removed!"
    assert "btn_hero_search" not in content, "Found 'btn_hero_search' which was requested to be removed!"

    # 确保 Google 药丸相关 class 存在
    assert "google-search-pill-card" in content, "Missing google-search-pill-card CSS class"
    assert "PaperLibrary" in content, "Missing PaperLibrary in app.py"

    # 确保研读工作台内部依然保留有换分类能力（在顶栏）
    assert "wb_btn_save_cat_" in content, "Workbench topbar category adjustment should be preserved"

    print("ALL GOOGLE HERO SEARCH & CLEAN CARD TESTS PASSED!")


if __name__ == "__main__":
    test_clean_cards_and_search()
