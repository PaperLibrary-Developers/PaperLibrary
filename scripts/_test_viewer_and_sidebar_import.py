import os
import re


def test_sidebar_merged_import_and_folder():
    """侧边栏「导入新文献」与「设定文献库总大文件夹」必须合并为同一个折叠面板。"""
    with open("app.py", "r", encoding="utf-8") as f:
        src = f.read()

    merged_title = "📥 导入新文献 (上传并选择分类)  ·  ⚙️ 设定文献库总大文件夹"

    # 1. 合并后的唯一侧边栏面板存在
    assert merged_title in src, "侧边栏缺少合并面板「导入新文献 + 设定文献库总大文件夹」"
    assert src.count(merged_title) == 1, "合并面板标题出现多次，存在重复入口"

    # 2. 导入新文献的全部控件都在这个合并面板里（原 key 保留）
    assert "sb_upload_pdfs" in src, "合并面板缺少文件上传组件 sb_upload_pdfs"
    assert "sb_btn_do_import" in src, "合并面板缺少导入按钮 sb_btn_do_import"
    assert "开始导入上传文献" in src, "合并面板缺少导入按钮文案"

    head = src.index(merged_title)
    tail = src.index("btn_pick_win_folder")
    assert head < tail, "「设定文献库总大文件夹」不在合并面板内部"

    # 3. 合并面板内部不得再出现第二个 expander（两半必须在同一个折叠框里）
    between = src[head:tail]
    assert "st.expander(" not in between, "合并面板内部仍残留独立的折叠框，两半没有真正合并"

    # 4. 主页面正中央搜索栏上方的快速导入保持不变
    assert "📥 快速导入文献（拖入 PDF 并指定分类）" in src, "主页面搜索栏上方缺少快速导入卡片！"
    assert "tab1_upload_pdfs" in src, "主页面缺少 tab1_upload_pdfs 文件上传组件！"

    print("  [OK] app.py 侧边栏「导入新文献 + 设定文献库总大文件夹」合并为单一展开面板验证通过！")


def test_workbench_giant_viewer():
    """研读工作台：巨幕视窗 + 整页适配。"""
    with open("app.py", "r", encoding="utf-8") as f:
        src = f.read()

    assert "wb_layout" in src, "缺少工作台版式控制 wb_layout"
    assert "wb_fit" in src, "缺少页面适配控制 wb_fit"
    assert "wb_height" in src, "缺少视窗高度控制 wb_height"
    assert "沉浸巨幕阅读" in src, "缺少沉浸巨幕阅读模式"
    assert "黄金双栏研读" in src, "缺少黄金双栏研读模式"
    assert "fit=page" in src, "缺少 fit=page 参数传递"
    assert "跟随浏览器铺满（一屏一页）" in src, "缺少「跟随浏览器铺满（一屏一页）」高度档位"
    assert "PDF_HEIGHT_AUTO" in src, "缺少自动铺满视窗的高度哨兵"
    assert "_pdf_fill_viewport_css" in src, "缺少视窗铺满 CSS 注入"
    assert "PDF_VIEWER_HEIGHTS = [1280, 1080, 1440, 1680, 1920]" in src, "视窗高度档位未放大（应为 1280~1920）"
    assert "height=wb_height" in src, "阅读器未按所选高度渲染"

    print("  [OK] app.py 研读工作台巨幕全页视窗控制验证通过！")


def test_viewer_html_scaling():
    with open("data/pdfjs/viewer.html", "r", encoding="utf-8") as f:
        html = f.read()

    assert "fitParam === 'width'" in html, "viewer.html 未解析 fit URL 参数"
    assert "fit-page" in html, "viewer.html 缺少 fit-page 模式"
    assert "fit-width" in html, "viewer.html 缺少 fit-width 模式"
    assert "btn-fit-page" in html, "viewer.html 缺少整页适配按钮"
    assert "btn-fit-width" in html, "viewer.html 缺少撑满宽度按钮"
    assert "scaleMode === 'fit-page'" in html, "viewer.html 缺少整页缩放计算"

    print("  [OK] data/pdfjs/viewer.html 整页充满与无截断缩放验证通过！")


if __name__ == "__main__":
    test_sidebar_merged_import_and_folder()
    test_workbench_giant_viewer()
    test_viewer_html_scaling()
    print("ALL SIDEBAR MERGE AND VIEWER ENHANCEMENT TESTS PASSED!")
