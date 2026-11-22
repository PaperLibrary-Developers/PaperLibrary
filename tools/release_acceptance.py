"""P2-20 真实发布验收编排：旧客户端 1.6.0 → GitHub latest v1.7.1 → 自动重开。"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")


def free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", port)); return True
    except OSError:
        return False
    finally:
        s.close()


def wait_json(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return json.loads(r.read().decode("utf-8"))
        except Exception:
            pass
        time.sleep(0.3)
    return {}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--out", default=str(ROOT / "docs" / "update-p2-20261116" / "p2-20-shots"))
    args = parser.parse_args()
    package = Path(args.package)
    home = Path(args.home)
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    if not free(4321): raise SystemExit("port 4321 busy")
    env = os.environ.copy()
    env["PAPERLIB_UPDATER_HOME"] = str(home)
    env["PAPERLIB_WEBVIEW_DEBUG_PORT"] = "9235"
    started = time.time()
    proc = subprocess.Popen([str(package / "PaperLibrary.exe")], cwd=str(package), env=env)
    old_info = wait_json("http://127.0.0.1:4321/api/version", 60)
    if not old_info: raise SystemExit("old service not ready")
    print("OLD_VERSION", old_info)
    if not wait_json("http://127.0.0.1:9235/json/list", 20) and False:
        pass
    # debug endpoint is /json/list not JSON object; just poll TCP
    for _ in range(100):
        try:
            with urlopen("http://127.0.0.1:9235/json/list", timeout=1) as r:
                if r.status == 200: break
        except Exception: pass
        time.sleep(0.2)
    node = subprocess.run([str(NODE), str(ROOT / "tools" / "release_acceptance_cdp.mjs"),
                           "--port", "9235", "--out", str(out)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    print("CDP_RC", node.returncode)
    print(node.stdout)
    if node.stderr.strip(): print("CDP_ERR", node.stderr[-2000:])
    try:
        proc.wait(timeout=180)
    except subprocess.TimeoutExpired:
        print("OLD_LAUNCHER_NOT_EXITED")
        proc.kill()
    print("OLD_EXIT", proc.returncode)
    new_info = wait_json("http://127.0.0.1:4321/api/version", 180)
    print("NEW_VERSION", new_info)
    # screenshot new app if debug port inherited
    shot = out / "06-new-version.png"
    capture = subprocess.run([str(NODE), "-e", (
        "const fs=require('fs');const t=await fetch('http://127.0.0.1:9235/json/list').then(r=>r.json());"
        "const p=t.find(x=>x.type==='page'&&/127\\.0\\.0\\.1:4321/.test(x.url||''));"
        "if(!p)process.exit(2);const ws=new WebSocket(p.webSocketDebuggerUrl);"
        "await new Promise((res,rej)=>{ws.addEventListener('open',res,{once:true});ws.addEventListener('error',rej,{once:true})});"
        "let id=1;const pend=new Map();ws.addEventListener('message',e=>{const m=JSON.parse(e.data);if(!m.id||!pend.has(m.id))return;const q=pend.get(m.id);pend.delete(m.id);m.error?q.reject(new Error(JSON.stringify(m.error))):q.resolve(m.result)});"
        "const send=(method,params={})=>{const i=id++;ws.send(JSON.stringify({id:i,method,params}));return new Promise((res,rej)=>pend.set(i,{resolve:res,reject:rej}))};"
        "await send('Page.enable');await new Promise(r=>setTimeout(r,800));const s=await send('Page.captureScreenshot',{format:'png'});"
        "fs.writeFileSync(process.argv[1],Buffer.from(s.data,'base64'));ws.close();"
    ), str(shot)], capture_output=True, text=True, timeout=60)
    data = {
        "oldVersion": old_info,
        "newVersion": new_info,
        "oldExit": proc.returncode,
        "elapsedSeconds": round(time.time() - started, 1),
        "dataDb": (package / "data" / "library.db").is_file(),
        "newScreenshot": str(shot) if shot.is_file() else "",
        "cdpStdout": node.stdout,
        "cdpStderr": node.stderr[-2000:],
        "captureRc": capture.returncode,
        "captureErr": capture.stderr[-1000:],
    }
    (out / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(data, ensure_ascii=False, indent=2))
    # stop new app
    subprocess.run(["taskkill", "/IM", "PaperLibrary.exe", "/T", "/F"], capture_output=True)
    subprocess.run(["taskkill", "/IM", "PaperLibraryUpdater.exe", "/T", "/F"], capture_output=True)


if __name__ == "__main__":
    main()
