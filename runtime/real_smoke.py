import json, os, subprocess, sys, tempfile, time, urllib.request, socket
ROOT = os.getcwd()
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
def free():
    s=socket.socket(); s.bind(("127.0.0.1",0)); p=s.getsockname()[1]; s.close(); return p
def call(base, m, path, body=None, headers=None):
    data=json.dumps(body,ensure_ascii=False).encode() if body is not None else None
    hdrs=dict(headers or {})
    if data: hdrs["Content-Type"]="application/json"
    req=urllib.request.Request(base+path,data=data,method=m,headers=hdrs)
    try:
        with urllib.request.urlopen(req,timeout=60) as r: return r.status, r.headers.get("Content-Type",""), r.read()
    except urllib.error.HTTPError as e: return e.code, e.headers.get("Content-Type",""), e.read()
port=free()
env=dict(os.environ,PAPERLIB_SKIP_MIGRATE="1",PYTHONUTF8="1")
start=time.time()
p=subprocess.Popen([sys.executable,"-m","paperlib","--port",str(port),"--no-open","--no-migrate","--data-dir","data-py-migration"],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
base="http://127.0.0.1:%d"%port
try:
    for _ in range(200):
        try:
            st,ct,b=call(base,"GET","/api/health")
            if st==200: break
        except Exception: pass
        time.sleep(0.2)
    health=json.loads(b); print("boot to health ms", round((time.time()-start)*1000), "papers", health["papers"], "bootMs", health["bootMs"])
    st,ct,b=call(base,"GET","/api/figures?pageSize=5"); d=json.loads(b); print("figures total", d["total"], "first", (d["items"][0]["paperTitle"][:30], d["items"][0]["caption"][:40]))
    fid=d["items"][0]["id"]
    st,ct,b=call(base,"GET","/api/figures/%d/image"%fid); print("figure image", st, ct, len(b))
    st,ct,b=call(base,"GET","/api/papers?pageSize=3"); d=json.loads(b); pid=d["items"][0]["id"]
    print("list", d["total"], d["items"][0]["title"][:30])
    st,ct,b=call(base,"GET","/api/papers/%s"%pid); pp=json.loads(b); print("detail", st, "pdfExists", pp["pdfExists"], "fulltext", pp["fulltext"])
    st,ct,b=call(base,"GET","/api/papers?q=supramolecular&scope=fulltext"); d=json.loads(b); print("fulltext search supramolecular ->", d["total"], (d["items"][0].get("fulltextExcerpt") or "")[:60] if d.get("items") else "")
    st,ct,b=call(base,"POST","/api/papers/%s/deep-analysis"%pid,{}); a=json.loads(b)["analysis"]; print("deep fields", len(a["fields"]), "scores", a["scores"])
    ids=[x["id"] for x in json.loads(call(base,"GET","/api/papers?pageSize=3")[2])["items"]]
    st,ct,b=call(base,"POST","/api/compare",{"paperIds":ids[:2],"template":"general"}); r=json.loads(b)["result"]; print("compare sections", len(r["sections"]), "usage", r["usage"])
    st,ct,b=call(base,"GET","/api/terms/search?q=%E5%82%AC%E5%8C%96"); d=json.loads(b); print("terms", d["total"])
    st,ct,b=call(base,"GET","/api/export/obsidian"); print("obsidian", st, ct, len(b))
    # UI 截图：图表画廊
    profile=tempfile.mkdtemp(prefix="edge2-")
    shots=[]
    for route,name in [("/#/figures","figures"),("/#/","home")]:
        shot=os.path.join(profile,name+".png")
        subprocess.run([EDGE,"--headless=new","--disable-gpu","--no-first-run","--user-data-dir="+profile,
                        "--window-size=1440,900","--virtual-time-budget=8000","--screenshot="+shot, base+route],
                       capture_output=True, timeout=120)
        shots.append((name, os.path.exists(shot), os.path.getsize(shot) if os.path.exists(shot) else 0, shot))
    print("shots", shots)
finally:
    p.terminate(); p.wait(timeout=10)
