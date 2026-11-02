# -*- coding: utf-8 -*-
# pdf_fetch 的测试：文件名安全、PDF 校验、候选排序、落盘防覆盖、下载各分支。
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import pdf_fetch as PF

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n%%EOF\n"

# ── 本地 HTTP 服务器：模拟各种下载结果 ──
class H(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/ok.pdf"):
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(PDF_BYTES)))
            self.end_headers()
            self.wfile.write(PDF_BYTES)
        elif self.path.startswith("/fake.pdf"):
            body = b"<html><body>landing page, not a pdf</body></html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/forbidden"):
            self.send_response(403)
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *a):
        pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{port}"

tmp = tempfile.mkdtemp(prefix="lvpdf_")
try:
    print("=== 1) 文件名安全 ===")
    check("非法字符被替换", "/" not in PF.safe_filename("a/b:c*d?e"))
    check("空标题有兜底", PF.safe_filename("") == "untitled")
    check("点结尾被去掉", not PF.safe_filename("abc...").endswith("."))
    check("保留名加下划线", PF.safe_filename("CON") == "CON_")
    long = PF.safe_filename("x" * 300)
    check("超长被截断", len(long) <= 120, len(long))
    check("正常标题保留", PF.safe_filename("Chiral molecules and the electron spin")
          == "Chiral molecules and the electron spin")

    print("\n=== 2) PDF 校验 ===")
    check("真 PDF 通过", PF.is_pdf_bytes(PDF_BYTES))
    check("HTML 不通过", not PF.is_pdf_bytes(b"<html>"))
    check("空内容不通过", not PF.is_pdf_bytes(b""))

    print("\n=== 3) 候选链接排序 ===")
    it_arxiv = {"source": "arxiv", "sourceId": "http://arxiv.org/abs/2401.12345v1",
                "url": "", "oaUrl": "", "doi": ""}
    c = PF.candidate_urls(it_arxiv, email="")
    check("arXiv 论文优先给 pdf 直链",
          c and c[0] == "https://arxiv.org/pdf/2401.12345", c)

    it_oa = {"source": "openalex", "sourceId": "W1", "doi": "",
             "oaUrl": "https://www.osti.gov/servlets/purl/1604937", "url": ""}
    c2 = PF.candidate_urls(it_oa, email="")
    check("OA 链接被采纳", "https://www.osti.gov/servlets/purl/1604937" in c2, c2)

    it_doi_only = {"source": "openalex", "sourceId": "W2", "doi": "10.1/x",
                   "oaUrl": "https://doi.org/10.1/x", "url": ""}
    c3 = PF.candidate_urls(it_doi_only, email="")
    check("doi.org 落地页被排除（实测 403 且非 PDF）",
          all("doi.org/" not in u for u in c3), c3)

    print("\n=== 4) 下载各分支（本地服务器）===")
    ok, payload, status = PF._download(f"{BASE}/ok.pdf")
    check("真 PDF 下载成功", ok and payload[:5] == b"%PDF-", (ok, status))
    ok2, why2, st2 = PF._download(f"{BASE}/fake.pdf")
    check("HTML 落地页判失败", not ok2, why2)
    ok3, why3, st3 = PF._download(f"{BASE}/forbidden")
    check("403 判失败并给出原因", (not ok3) and st3 == 403, why3)
    ok4, why4, st4 = PF._download(f"{BASE}/nope")
    check("404 判失败", (not ok4) and st4 == 404, why4)

    print("\n=== 5) fetch_pdf 逐个尝试 ===")
    item = {"source": "x", "sourceId": "", "doi": "", "url": "", "title": "t",
            "oaUrl": f"{BASE}/fake.pdf"}
    PF.unpaywall_links = lambda doi, email, timeout=20: [f"{BASE}/ok.pdf"]
    r = PF.fetch_pdf(item, email="a@b.com")
    check("第一个失败后自动尝试下一个并成功", r["ok"], r)
    check("记录尝试过程（2 条）", len(r["tried"]) == 2, r["tried"])

    item2 = {"source": "x", "sourceId": "", "doi": "", "url": "", "title": "t",
             "oaUrl": f"{BASE}/forbidden"}
    PF.unpaywall_links = lambda doi, email, timeout=20: []
    r2 = PF.fetch_pdf(item2, email="")
    check("全失败时 ok=False 且有原因", (not r2["ok"]) and r2["reason"], r2)

    print("\n=== 6) 落盘 ===")
    root = os.path.join(tmp, "lib")
    os.makedirs(root)
    p1 = PF.store_pdf(PDF_BYTES, root, "羰基还原", "Chiral molecules and the electron spin")
    check("存进分类文件夹", os.path.isfile(p1) and "羰基还原" in p1, p1)
    check("文件名用标题", os.path.basename(p1) == "Chiral molecules and the electron spin.pdf",
          os.path.basename(p1))
    p2 = PF.store_pdf(PDF_BYTES, root, "羰基还原", "Chiral molecules and the electron spin")
    check("同名不覆盖，加序号", os.path.basename(p2) != os.path.basename(p1) and os.path.isfile(p1),
          os.path.basename(p2))
    p3 = PF.store_pdf(PDF_BYTES, root, "", "root level paper")
    check("可存到根目录", os.path.dirname(p3) == os.path.abspath(root), p3)
    try:
        PF.store_pdf(PDF_BYTES, root, "../逃逸", "x")
        check("拒绝路径穿越", False, "本该报错")
    except ValueError:
        check("拒绝路径穿越", True)
    check("没有残留 .part 临时文件",
          not any(f.endswith(".part") for f in os.listdir(os.path.join(root, "羰基还原"))))

    print("\n=== 7) 导入一步到位 ===")
    item3 = {"source": "x", "sourceId": "", "doi": "", "url": "",
             "title": "A Fetched Paper", "oaUrl": f"{BASE}/ok.pdf"}
    PF.unpaywall_links = lambda doi, email, timeout=20: []
    res = PF.import_with_pdf(item3, root, "网络导入", email="")
    check("import_with_pdf 成功", res["ok"], res)
    check("文件确实落盘", res["ok"] and os.path.isfile(res["path"]))
    check("字节数正确", res.get("bytes") == len(PDF_BYTES), res.get("bytes"))

    bad = {"source": "x", "sourceId": "", "doi": "", "url": "",
           "title": "No OA", "oaUrl": f"{BASE}/forbidden"}
    res2 = PF.import_with_pdf(bad, root, "网络导入", email="")
    check("抓不到时如实返回失败原因", (not res2["ok"]) and "403" in res2["reason"],
          res2.get("reason"))

    print("\n=== 8) 真实 PDF 可被 PyMuPDF 打开 ===")
    try:
        import fitz
        d = fitz.open(p1)
        check("下载的 PDF 能被解析（页数可读）", d.page_count >= 0)
        d.close()
    except Exception as e:
        check("下载的 PDF 能被解析", False, e)

finally:
    srv.shutdown()
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
