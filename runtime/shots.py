import json, os, subprocess, sys, tempfile, time, urllib.request, socket
ROOT=os.getcwd(); EDGE=r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
def free():
    s=socket.socket(); s.bind(("127.0.0.1",0)); p=s.getsockname()[1]; s.close(); return p
def get(base, path):
    with urllib.request.urlopen(base+path, timeout=30) as r: return json.loads(r.read().decode())
port=free(); env=dict(os.environ,PAPERLIB_SKIP_MIGRATE="1",PYTHONUTF8="1")
p=subprocess.Popen([sys.executable,"-m","paperlib","--port",str(port),"--no-open","--no-migrate","--data-dir","data-py-migration"],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
base="http://127.0.0.1:%d"%port
try:
    for _ in range(200):
        try:
            get(base,"/api/health"); break
        except Exception: time.sleep(0.2)
    papers=get(base,"/api/papers?pageSize=3")["items"]
    ids=",".join(x["id"] for x in papers[:2])
    pid=papers[0]["id"]
    profile=tempfile.mkdtemp(prefix="edge3-")
    routes=[("/#/compare?ids="+ids,"compare"),("/#/deep/"+pid,"deep"),("/#/paper/"+pid,"detail")]
    for route,name in routes:
        shot=os.path.join(profile,name+".png")
        subprocess.run([EDGE,"--headless=new","--disable-gpu","--no-first-run","--user-data-dir="+profile,
                        "--window-size=1440,1000","--virtual-time-budget=9000","--screenshot="+shot, base+route],
                       capture_output=True, timeout=120)
        print(name, os.path.exists(shot), os.path.getsize(shot) if os.path.exists(shot) else 0, shot)
    print("PROFILE", profile)
finally:
    p.terminate(); p.wait(timeout=10)
