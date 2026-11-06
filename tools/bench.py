"""体积/启动/内存基线（对应 Sci-lib tools/bench.js）。

指标：
  · repoKB：仓库体积（排除 .git/data/runtime/dist）
  · releaseKB：dist/ 最新发布包体积（若有）
  · bootMs：子进程启动到 /api/health 200 的毫秒数（3 次取中位数）
  · rssMB：/api/health 上报的稳态 RSS
  · healthBootMs：服务自报 bootMs
写 tools/bench-baseline.json；--compare 与基线比较。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE = ROOT / "tools" / "bench-baseline.json"
SKIP = {".git", "data", "runtime", "dist", "node_modules", "__pycache__"}


def tree_size(path: Path, skip=None):
    skip = skip or SKIP
    total = 0
    for root, dirs, files in os.walk(path):
        # 排除运行数据目录：data、data-*（测试/冒烟）、runtime、dist、.git…
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith("data-")]
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def one_boot():
    port = free_port()
    data_dir = tempfile.mkdtemp(prefix="pl-bench-")
    env = dict(os.environ, PAPERLIB_SKIP_MIGRATE="1", PYTHONUTF8="1")
    started = time.time()
    proc = subprocess.Popen(
        [sys.executable, "-m", "paperlib", "--port", str(port), "--no-open", "--data-dir", data_dir],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    health = None
    boot = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port, timeout=1) as resp:
                    health = json.loads(resp.read().decode("utf-8"))
                    boot = round((time.time() - started) * 1000)
                    break
            except Exception:
                time.sleep(0.05)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
        shutil.rmtree(data_dir, ignore_errors=True)
    return boot, health


def load_baseline():
    try:
        return json.loads(BASELINE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--compare", action="store_true")
    parser.add_argument("--save", action="store_true")
    args = parser.parse_args(argv)

    repo_kb = round(tree_size(ROOT) / 1024)
    dist = ROOT / "dist"
    release_kb = 0
    if dist.is_dir():
        candidates = sorted([p for p in dist.iterdir() if p.is_dir()])
        if candidates:
            release_kb = round(tree_size(candidates[-1], skip={"__pycache__"}) / 1024)

    boots = []
    health = None
    for _ in range(3):
        boot, h = one_boot()
        if boot:
            boots.append(boot)
            health = h or health
    result = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repoKB": repo_kb,
        "releaseKB": release_kb,
        "bootMs": round(statistics.median(boots)) if boots else None,
        "bootSamples": boots,
        "healthBootMs": (health or {}).get("bootMs"),
        "rssMB": (health or {}).get("rssMB"),
        "papers": (health or {}).get("papers"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.compare:
        baseline = load_baseline()
        if baseline:
            print("\n对比基线：")
            for key in ("repoKB", "releaseKB", "bootMs", "rssMB"):
                old = baseline.get(key)
                new = result.get(key)
                if isinstance(old, (int, float)) and isinstance(new, (int, float)):
                    delta = new - old
                    print("  %-10s %8s → %8s  (%+d)" % (key, old, new, delta))
        else:
            print("没有基线（tools/bench-baseline.json）")
    if args.save or not args.compare:
        BASELINE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n已写入 %s" % BASELINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
