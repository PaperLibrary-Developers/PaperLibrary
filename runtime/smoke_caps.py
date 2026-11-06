import json, os, subprocess, sys, tempfile, time, urllib.request, urllib.error, socket, io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def call(base, method, path, body=None, raw=None, headers=None):
    data = None
    hdrs = dict(headers or {})
    if raw is not None:
        data, extra = raw; hdrs.update(extra)
    elif body is not None:
        data = json.dumps(body, ensure_ascii=False).encode(); hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", ""), e.read()

port = free_port()
data_dir = tempfile.mkdtemp(prefix="caps-")
env = dict(os.environ, PAPERLIB_SKIP_MIGRATE="1", PYTHONUTF8="1")
proc = subprocess.Popen([sys.executable, "-m", "paperlib", "--port", str(port), "--no-open", "--data-dir", data_dir],
                        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = "http://127.0.0.1:%d" % port
try:
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as r:
                if r.status == 200: break
        except Exception: time.sleep(0.2)
    print("health", call(base, "GET", "/api/health")[0])
    # 造两篇文献
    ids = []
    for i, title in enumerate(["Capability Smoke 论文一", "Capability Smoke 论文二"]):
        st, ct, body = call(base, "POST", "/api/papers", {"title": title, "year": 2024, "authors": ["A. Author"], "summary": "abstract " + str(i)})
        print("create", st)
        ids.append(json.loads(body)["paper"]["id"])
    # 生成一页带文字的 PDF，上传到第一篇
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Figure 1. Test caption for capability smoke.", fontsize=12)
    page.insert_text((72, 120), "Main text about host-guest chemistry and cucurbituril.", fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    st, ct, body = call(base, "POST", "/api/papers/upload", raw=(pdf_bytes, {"Content-Type": "application/pdf", "X-Filename": "smoke.pdf", "X-Paper-Id": ids[0]}))
    print("upload", st, body[:160])
    for path, method, body2 in [
        ("/api/papers/%s/figures/extract" % ids[0], "POST", {}),
        ("/api/papers/%s/figures" % ids[0], "GET", None),
        ("/api/figures", "GET", None),
        ("/api/compare/templates", "GET", None),
        ("/api/compare", "POST", {"paperIds": ids, "template": ""}),
        ("/api/papers/%s/deep-analysis" % ids[0], "POST", {}),
        ("/api/papers/%s/deep-analysis" % ids[0], "GET", None),
        ("/api/terms/search?q=%E5%8F%96%E4%BB%A3", "GET", None),
    ]:
        st, ct, b = call(base, method, path, body2)
        print(method, path, "->", st, ct.split(";")[0], b[:200])
    st, ct, b = call(base, "GET", "/api/figures")
    if st == 200:
        items = json.loads(b).get("items") or []
        if items:
            st2, ct2, img = call(base, "GET", "/api/figures/%s/image" % items[0]["id"])
            print("figure image", st2, ct2, len(img))
    st, ct, b = call(base, "GET", "/api/export/obsidian")
    print("obsidian", st, ct, len(b), b[:4])
    # 前端静态
    for path in ["/main.mjs", "/js/views/figures.mjs", "/js/views/compare.mjs", "/js/views/deep.mjs"]:
        st, ct, b = call(base, "GET", path)
        print("static", path, st, ct, len(b))
finally:
    proc.terminate(); proc.wait(timeout=5)
