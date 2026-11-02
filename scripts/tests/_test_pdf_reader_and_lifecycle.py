"""
验证 HTML5 PDF 阅读器及 Streamlit 生命周期状态保护
"""
import os
import sys
import re
import http.client
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import database

def test_file_server_range_and_headers():
    papers = database.get_all_papers()
    assert papers, "数据库中无文献"
    
    # 选取报错文献
    target = None
    for p in papers:
        if p["id"].startswith("ed5a9c75be87") or "3D qtz" in (p.get("title") or ""):
            target = p
            break
    if not target:
        target = papers[0]

    p_path = target["abs_path"]
    assert os.path.isfile(p_path), f"文件不存在: {p_path}"

    # 1. 验证静态资源
    conn = http.client.HTTPConnection("127.0.0.1", 8502, timeout=5)
    conn.request("GET", "/static/pdfjs/pdf.min.js")
    resp = conn.getresponse()
    assert resp.status == 200, f"/static/pdfjs/pdf.min.js 响应异常: {resp.status}"
    data = resp.read()
    assert len(data) > 300000, "pdf.min.js 内容大小异常"

    # 2. 验证 HTTP Range 请求 (模拟 PDF.js 首次加载)
    conn = http.client.HTTPConnection("127.0.0.1", 8502, timeout=5)
    url = "/?path=" + urllib.parse.quote(p_path)
    conn.request("GET", url, headers={"Range": "bytes=0-65535"})
    resp = conn.getresponse()
    assert resp.status == 206, f"Range 请求应返回 206，实际返回: {resp.status}"
    
    hdrs = dict(resp.getheaders())
    assert hdrs.get("Content-Range") == f"bytes 0-65535/{os.path.getsize(p_path)}", "Content-Range 头缺失或不正确"
    assert hdrs.get("Content-Length") == "65536", "Content-Length 头缺失或不正确"
    assert "Accept-Ranges" in hdrs.get("Access-Control-Expose-Headers", ""), "CORS Expose-Headers 缺失"

    range_data = resp.read()
    assert len(range_data) == 65536, f"实际读取长度 {len(range_data)} 与预期 65536 不符"
    assert range_data.startswith(b"%PDF"), "返回数据非 PDF 二进制"

    print("  [OK] 文件服务 HTTP Range 与 CORS 暴露头测试通过")

def test_streamlit_lifecycle_keys():
    with open("app.py", "r", encoding="utf-8") as f:
        src = f.read()

    # 验证没有直接在跳转按钮中写入 dt_pdf_page_
    # 允许的唯一定义是在 reader 组件渲染之前的 k_pg / st.number_input 绑定
    bad_assignments = re.findall(r'st\.session_state\[[f"\']dt_pdf_page_[^\]]+\]\s*=', src)
    assert len(bad_assignments) <= 1, f"发现多处直接写入 dt_pdf_page_，应统一使用 pending_pdf_page_: {bad_assignments}"

    # 验证 pending_pdf_page_ 状态消费机制存在
    assert "pending_pdf_page_" in src, "缺少 pending_pdf_page_ 状态缓冲机制"
    assert "pending_pdf_mode_" in src, "缺少 pending_pdf_mode_ 状态缓冲机制"

    print("  [OK] Streamlit 组件生命周期状态保护机制验证通过")

if __name__ == "__main__":
    print("=== 开始测试 HTML5 PDF 阅读器及生命周期健壮性 ===")
    test_file_server_range_and_headers()
    test_streamlit_lifecycle_keys()
    print("ALL TESTS PASSED!")
