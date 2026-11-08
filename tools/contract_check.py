"""契约快照校验：起 Python 服务，逐条重放 shared/contracts/api-snapshot.json（69 行）。

比较项（与 Node tools/api-snapshot 同口径）：状态码类别 + 顶层 JSON 键集合。
network/skip 行（快照里没有 method 的行）标 SKIP；静态/vendor 行比较 content-type 与状态。

用法：
    python tools/contract_check.py                    # 自动起服务（端口 4398，临时数据目录）
    python tools/contract_check.py --base http://127.0.0.1:4321   # 打已有服务
    python tools/contract_check.py --json out.json    # 机器可读结果
退出码：0 全部 PASS/SKIP；1 有 FAIL。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "shared" / "contracts" / "api-snapshot.json"

# 快照只记响应，不记请求；这里按端点补最小合法/非法请求，以复现同一条快照
BODIES = {
    "prefs-put": {"theme": "paper"},
    "tags-rename": {},
    "tags-delete": {},
    "papers-create": {"title": "契约测试文献", "authors": ["A. Tester"], "year": 2024},
    "papers-bulk": {"ids": [], "patch": {}},
    "papers-associate-pdf": {},
    "papers-cleanup-duplicates": {},
    "papers-summary-zh-batch": {},
    "ai-settings-put": {},
    "ai-summarize": {"paperId": "0000000000000000"},
    "ai-summarize-sync": {"paperId": "0000000000000000"},
    "ai-summarize-sync-missing": {"paperId": "0000000000000000"},
    "sources-settings-put": {},
    "websearch-settings-put": {},
    "import-pdf-confirm": {},
    "import-json": {},
    "shutdown": {},
}
RAW = {
    "papers-upload": (b"not a pdf", {"Content-Type": "application/octet-stream", "X-Filename": "x.pdf"}),
    "import-pdf-preflight": (b"not a pdf", {"Content-Type": "application/octet-stream"}),
}


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_health(base, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def request(base, method, path, body=None, headers=None, raw=None):
    url = base + path
    data = None
    hdrs = dict(headers or {})
    if raw is not None:
        data = raw[0]
        hdrs.update(raw[1])
    elif method in ("POST", "PUT", "PATCH", "DELETE") and body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw_body = resp.read()
            return resp.status, resp.headers.get("Content-Type", ""), raw_body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()
    except Exception as exc:  # noqa: BLE001
        return 0, "", str(exc).encode("utf-8")


def status_class(code):
    if 200 <= code < 300:
        return "2xx"
    if 300 <= code < 400:
        return "3xx"
    if 400 <= code < 500:
        return "4xx"
    if 500 <= code < 600:
        return "5xx"
    return "err"


def top_keys(status, ctype, body):
    text = body.decode("utf-8", "replace") if isinstance(body, bytes) else str(body)
    if "json" not in (ctype or ""):
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    if isinstance(data, dict):
        return sorted(data.keys())
    return ["<array>"]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="")
    parser.add_argument("--port", type=int, default=4398)
    parser.add_argument("--data-dir", default="")
    parser.add_argument("--json", default="")
    parser.add_argument("--only", default="")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    rows = snapshot["endpoints"]
    if args.only:
        wanted = {x.strip() for x in args.only.split(",") if x.strip()}
        rows = [r for r in rows if r.get("id") in wanted]

    proc = None
    data_dir = None
    base = args.base.rstrip("/")
    if not base:
        base = "http://127.0.0.1:%d" % args.port
        data_dir = args.data_dir or tempfile.mkdtemp(prefix="pl-contract-")
        env = dict(os.environ, PAPERLIB_SKIP_MIGRATE="1", PYTHONUTF8="1")
        proc = subprocess.Popen(
            [sys.executable, "-m", "paperlib", "--port", str(args.port), "--no-open", "--idle-exit", "0", "--data-dir", data_dir],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if not wait_health(base):
            print("服务启动失败：%s" % base)
            proc.kill()
            return 1

    results = []
    failures = 0
    try:
        for row in rows:
            rid = row.get("id")
            method = row.get("method")
            if not method:
                results.append({"id": rid, "result": "SKIP", "reason": "network/skip"})
                if args.verbose:
                    print("SKIP %-32s（快照标注 network/skip）" % rid)
                continue
            path = row.get("path") or ""
            expected_class = row.get("statusClass") or status_class(row.get("status") or 0)
            expected_keys = row.get("jsonKeys")
            body = BODIES.get(rid)
            raw = RAW.get(rid)
            status, ctype, payload = request(base, method, path, body=body, raw=raw)
            got_class = status_class(status)
            got_keys = top_keys(status, ctype, payload)
            ok = got_class == expected_class
            key_diff = []
            kind = row.get("kind")
            if ok and expected_keys is not None and got_keys is not None:
                missing = [k for k in expected_keys if k not in got_keys]
                extra = [k for k in got_keys if k not in expected_keys]
                if missing or extra:
                    ok = False
                    key_diff = [("missing", k) for k in missing] + [("extra", k) for k in extra]
            if ok and kind in ("html",):
                ok = "text/html" in (ctype or "")
            if ok and kind == "optional":
                # 可选依赖（pdfjs/katex）必须随包内置：200
                ok = status == 200
            entry = {
                "id": rid, "method": method, "path": path,
                "expected": expected_class, "got": got_class, "status": status,
                "result": "PASS" if ok else "FAIL", "keyDiff": key_diff,
            }
            results.append(entry)
            if not ok:
                failures += 1
                if args.verbose:
                    print("FAIL %-32s %s %s  期望 %s 实际 %s  键差异 %s"
                          % (rid, method, path, expected_class, got_class, key_diff))
            elif args.verbose:
                print("PASS %-32s %s %s" % (rid, method, path))
    finally:
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if data_dir:
            shutil.rmtree(data_dir, ignore_errors=True)

    passed = sum(1 for r in results if r["result"] == "PASS")
    skipped = sum(1 for r in results if r["result"] == "SKIP")
    total = len(results)
    print("\n契约快照：PASS %d / FAIL %d / SKIP %d（共 %d）" % (passed, failures, skipped, total))
    if args.json:
        Path(args.json).write_text(json.dumps({"generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                               "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
