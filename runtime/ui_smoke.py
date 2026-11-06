import json, os, subprocess, sys, tempfile, time, urllib.request, socket, shutil
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

def call(base, method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"Content-Type": "application/json"} if data else {})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, r.read()

port = free_port()
data_dir = tempfile.mkdtemp(prefix="ui-")
profile = tempfile.mkdtemp(prefix="edge-")
env = dict(os.environ, PAPERLIB_SKIP_MIGRATE="1", PYTHONUTF8="1")
proc = subprocess.Popen([sys.executable, "-m", "paperlib", "--port", str(port), "--no-open", "--data-dir", data_dir],
                        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
base = "http://127.0.0.1:%d" % port
try:
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1); break
        except Exception: time.sleep(0.2)
    papers = [
        {"title": "UI 冒烟文献：超分子化学导论", "authors": ["张三", "Li Si"], "year": 2024,
         "venue": "化学学报", "type": "journal", "doi": "10.1000/ui.1",
         "summary": "本文综述超分子化学与葫芦脲的主客体化学。", "keywords": ["超分子", "葫芦脲"],
         "tags": ["超分子", "综述"], "status": "reading", "rating": 4, "starred": True},
        {"title": "UI Smoke Paper Two", "authors": ["Wang Wu"], "year": 2023,
         "venue": "Test Journal", "type": "conference", "summary": "Second abstract.", "tags": ["ui"]},
    ]
    for p in papers:
        call(base, "POST", "/api/papers", p)
    for route in ["/", "/#/", "/#/figures", "/#/compare", "/#/deep"]:
        out_dir = os.path.join(profile, route.strip("/").replace("#", "hash").replace("/", "_") or "root")
        os.makedirs(out_dir, exist_ok=True)
        cmd = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
               "--user-data-dir=" + profile, "--virtual-time-budget=6000", "--dump-dom", base + route]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        dom = r.stdout or ""
        checks = {
            "sidebar": "图表画廊" in dom,
            "app mounted": 'id="app"' in dom and "hidden" not in dom.split('id="app"')[1][:20],
            "view content": ("outlet" in dom) and len(dom) > 2000,
        }
        print(route, len(dom), checks, "stderr:", (r.stderr or "")[:120].replace("\n", " "))
        open(os.path.join(profile, "dom" + route.replace("/", "_").replace("#", "h") + ".html"), "w", encoding="utf-8").write(dom)
    # 截图（首页）
    shot = os.path.join(profile, "home.png")
    subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--user-data-dir=" + profile,
                    "--window-size=1440,900", "--screenshot=" + shot, base + "/#/"], capture_output=True, timeout=90)
    print("screenshot", os.path.exists(shot), os.path.getsize(shot) if os.path.exists(shot) else 0, shot)
    print("KEEP", profile)
finally:
    proc.terminate(); proc.wait(timeout=5)
