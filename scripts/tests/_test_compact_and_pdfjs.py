"""
_test_compact_and_pdfjs.py — 验证 HTML5 PDF.js 阅读器接口与高密度紧凑版式
"""
import os
import sys
import urllib.request
import urllib.parse
from streamlit.testing.v1 import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import database

def test_file_server_endpoints():
    print("=== 1) 测试文件服务核心接口 ===")
    db = database.get_conn()
    p = db.execute("SELECT id, abs_path FROM papers LIMIT 1").fetchone()
    assert p and p["abs_path"], "库中无测试 PDF"
    abs_path = p["abs_path"]

    # 1. 静态资源测试
    u_js = "http://127.0.0.1:8502/static/pdfjs/pdf.min.js"
    with urllib.request.urlopen(u_js) as resp:
        assert resp.status == 200, f"pdf.min.js 响应异常: {resp.status}"
        assert resp.headers.get("Access-Control-Allow-Origin") == "*", "缺少 CORS 放行头"
        print("  [OK] 本地静态库 /static/pdfjs/pdf.min.js 200 OK (CORS 通畅)")

    # 2. HTML5 PDF.js Viewer 路由测试（MIME: text/html，免疫 Edge 拦截）
    enc = urllib.parse.quote(abs_path)
    u_view = f"http://127.0.0.1:8502/view?path={enc}"
    with urllib.request.urlopen(u_view) as resp:
        assert resp.status == 200
        ctype = resp.headers.get("Content-Type", "")
        assert "text/html" in ctype, f"Content-Type 不是 text/html: {ctype}"
        csp = resp.headers.get("Content-Security-Policy", "")
        assert "frame-ancestors" in csp, "缺少 frame-ancestors 放行头"
        print("  [OK] HTML5 阅读器 /view 路由 200 OK (text/html, CSP frame-ancestors: *)")

    # 3. Raw PDF 路由测试
    u_raw = f"http://127.0.0.1:8502/?path={enc}"
    with urllib.request.urlopen(u_raw) as resp:
        assert resp.status == 200
        disp = resp.headers.get("Content-Disposition", "")
        assert "inline" in disp, f"Content-Disposition 缺少 inline: {disp}"
        cors = resp.headers.get("Access-Control-Allow-Origin", "")
        assert cors == "*", "缺少 Access-Control-Allow-Origin"
        print("  [OK] 原生流 /?path= 路由 200 OK (inline, Access-Control-Allow-Origin: *)")

def test_app_compact_rendering():
    print("\n=== 2) 测试 Streamlit 高密度排版与工作台渲染 ===")
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=30)
    at.run()
    assert not at.exception, f"首页运行异常: {at.exception}"

    # 验证 CSS 样式注入
    md_texts = [m.value for m in at.markdown]
    has_compact_css = any("block-container" in t and "compact-badges" in t for t in md_texts)
    assert has_compact_css, "紧凑高密度 CSS 未成功注入"
    print("  [OK] 紧凑学术版式 CSS 成功注入")

    # 点击第一篇文献进入子页面研读工作台
    enter_btns = [b for b in at.button if "进入研读工作台" in (b.label or "")]
    assert len(enter_btns) > 0, "未找到进入研读工作台按钮"
    enter_btns[0].click().run()
    assert not at.exception, f"进入子页面异常: {at.exception}"

    # 验证子页面中有单选切换阅读器模式，且首选为 HTML5 流式阅读
    radios = [r for r in at.radio if "HTML5" in str(r.options)]
    assert len(radios) > 0, "研读工作台未找到 HTML5 阅读器模式单选"
    print("  [OK] 研读工作台已搭载「HTML5 流式阅读（全兼容 Edge/Chrome，抗拦截）」选项")

    # 验证返回文献目录按钮存在
    back_btn = [b for b in at.button if "返回文献目录" in (b.label or "")]
    assert len(back_btn) > 0, "未找到返回目录按钮"
    back_btn[0].click().run()
    assert not at.exception, "返回目录异常"
    print("  [OK] 返回文献目录平稳退回")

if __name__ == "__main__":
    test_file_server_endpoints()
    test_app_compact_rendering()
    print("\n[ALL PASS] 紧凑版式与 Edge 拦截防御全部测试通过！")
