"""
验证全篇图表全景画廊重构排版与逻辑
"""
import os
import sys

def test_gallery_structure():
    with open("app.py", "r", encoding="utf-8") as f:
        src = f.read()

    # 1. 验证没有遗留的 database.update_figure_conclusion 调用
    assert "database.update_figure_conclusion" not in src, "发现遗留的 database.update_figure_conclusion，应全部使用 figure_anchor"

    # 2. 验证排版模式切换
    assert 'gal_view_mode = st.radio("画廊排版"' in src, "缺少画廊排版切换单选按钮"
    assert '"📖 精读并排"' in src and '"🖼️ 紧凑矩阵"' in src, "缺少精读并排或紧凑矩阵模式选项"

    # 3. 验证精读并排模式下左右两栏比例：图列收窄到约 1/4，把横向空间让给右侧图注
    assert 'c_fig_left, c_fig_right = st.columns([1, 2.85], gap="medium")' in src, "精读并排模式未按窄图列比例分栏"

    # 4. 验证长图注折叠保护 (len(cap) > 220)
    assert 'len(cap) > 220' in src, "缺少长图注智能折叠保护"
    assert '展开完整图注全文' in src, "缺少图注展开 expander"

    # 5. 验证图表定位与正文讨论跳转
    assert '📑 第 {pg_num} 页' in src, "缺少图表定位页码按钮"
    assert 'jump_p_l_' in src, "缺少精读栏跳转键"

    # 6. 验证窄栏下的「看原图」入口（原图另开标签查看，不挤占卡片空间）
    assert '🔍 看原图' in src, "缺少原图新标签查看入口"
    assert 'localhost:8502/?path=' in src, "原图链接未走本地文件服务"

    print("ALL GALLERY REDESIGN CHECKS PASSED!")

if __name__ == "__main__":
    test_gallery_structure()
