"""跨语言端到端对照：同一场景分别跑 Node 参考（Sci-lib）与 Python 实现，逐字段 diff。

- 两边都用全新临时数据目录 + .seeded 标记（不播种），从空库开始，保证确定性；
- 每一步比较状态码 + 响应体（JSON 深比较，或文本逐字比较）；易变字段归一化
  （时间戳/pid/port/dataDir/绝对路径/随机 id/用量数字）；
- 输出字段级差异（JSON path / node 值 / py 值），有差异退出码 1。

用法：
    python tools/e2e.py                       # 自动起两端
    python tools/e2e.py --node-base http://127.0.0.1:4396 --py-base http://127.0.0.1:4398
    python tools/e2e.py --keep                # 保留临时目录便于排查
"""
from __future__ import annotations

import argparse
import json
import os
import re
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
SCILIB = ROOT.parent / "Sci-lib"
TIME_KEYS = {"updatedAt", "addedAt", "starredAt", "summaryZhAt", "aiSummaryAt", "createdAt",
             "exportedAt", "at", "seededAt", "generatedAt"}
VOLATILE_KEYS = {"pid", "port", "bootMs", "rssMB", "uptimeSeconds", "dataDir", "dataFile",
                 "papersDir", "attachmentsDir", "dir", "quota", "usage", "timestamp", "elapsedMs"}
HEX16 = re.compile(r"\b[0-9a-f]{16}\b")
ISO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z?")
WINPATH = re.compile(r"[A-Za-z]:\\\\[^\s\"]+")


class Client:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def call(self, method, path, body=None, raw=None, headers=None):
        url = self.base + path
        data = None
        hdrs = dict(headers or {})
        if raw is not None:
            data, extra = raw
            hdrs.update(extra)
        elif method in ("POST", "PUT", "PATCH", "DELETE") and body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                return resp.status, resp.headers.get("Content-Type", ""), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.headers.get("Content-Type", ""), exc.read()
        except Exception as exc:  # noqa: BLE001
            return 0, "", str(exc).encode("utf-8")


def free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def wait_health(base, timeout=25.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(base + "/api/health", timeout=1) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.15)
    return False


def prepare_data_dir(prefix):
    path = Path(tempfile.mkdtemp(prefix=prefix))
    (path / ".seeded").write_text("{}", encoding="utf-8")
    return path


def start_node(data_dir, port):
    env = dict(os.environ, QUIET="1")
    return subprocess.Popen(
        ["node", "server/server.js", "--port", str(port), "--no-open", "--data-dir", str(data_dir)],
        cwd=str(SCILIB), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def start_python(data_dir, port):
    env = dict(os.environ, PAPERLIB_SKIP_MIGRATE="1", PYTHONUTF8="1", QUIET="1")
    return subprocess.Popen(
        [sys.executable, "-m", "paperlib", "--port", str(port), "--no-open", "--idle-exit", "0", "--data-dir", str(data_dir)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def normalize(value, ids, key=None):
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if k in TIME_KEYS and isinstance(v, str):
                out[k] = "<ts>"
            elif k in VOLATILE_KEYS:
                out[k] = "<volatile>"
            else:
                out[k] = normalize(v, ids, k)
        return out
    if isinstance(value, list):
        return [normalize(v, ids, key) for v in value]
    if isinstance(value, str):
        text = value
        for token, real in ids.items():
            if real and real in text:
                text = text.replace(real, "<" + token + ">")
        text = HEX16.sub("<id>", text)
        text = ISO.sub("<ts>", text)
        text = WINPATH.sub("<path>", text)
        return text
    return value


def diff(node, py, path="", out=None):
    out = [] if out is None else out
    if isinstance(node, (int, float)) and isinstance(py, (int, float)):
        if float(node) == float(py):
            return out
        out.append((path or "$", node, py, "value"))
        return out
    if type(node) is not type(py):
        out.append((path or "$", node, py, "type"))
        return out
    if isinstance(node, dict):
        for k in node:
            if k not in py:
                out.append((path + "." + k, node[k], "<missing>", "missing"))
            else:
                diff(node[k], py[k], path + "." + k, out)
        for k in py:
            if k not in node:
                out.append((path + "." + k, "<missing>", py[k], "extra"))
        return out
    if isinstance(node, list):
        if len(node) != len(py):
            out.append((path + ".length", len(node), len(py), "length"))
        for i in range(min(len(node), len(py))):
            diff(node[i], py[i], path + "[" + str(i) + "]", out)
        return out
    if isinstance(node, (int, float)) and isinstance(py, (int, float)):
        if float(node) == float(py):
            return out
        out.append((path or "$", node, py, "value"))
        return out
    if node != py:
        out.append((path or "$", node, py, "value"))
    return out


def classify(ctype, body):
    text = body.decode("utf-8", "replace")
    if "application/json" in (ctype or ""):
        try:
            return "json", json.loads(text)
        except ValueError:
            return "text", text
    return "text", text


def scenario():
    yield ("health", "GET", "/api/health", None, None, "keys")
    yield ("prefs-put", "PUT", "/api/prefs", {"theme": "night", "pageSize": 40}, None, "json")
    yield ("prefs-get", "GET", "/api/prefs", None, None, "json")
    yield ("ai-settings-get", "GET", "/api/ai/settings", None, None, "keys")
    yield ("ai-settings-put", "PUT", "/api/ai/settings", {"temperature": 0.2}, None, "keys")
    yield ("sources-settings-get", "GET", "/api/sources/settings", None, None, "json")
    yield ("websearch-settings-get", "GET", "/api/websearch/settings", None, None, "keys")
    yield ("create-1", "POST", "/api/papers", {
        "title": "E2E 对照文献一", "authors": ["Zhang San", "Li Si"], "year": 2024,
        "venue": "Test Journal", "type": "journal", "doi": "https://doi.org/10.1000/E2E.1",
        "tags": ["e2e", "测试"], "keywords": ["kw-one", "kw-two"],
        "summary": "This is a test abstract about host-guest chemistry.", "status": "reading",
        "rating": 3, "starred": True, "pages": "1-10", "volume": "12", "issue": "3",
    }, None, "json")
    yield ("create-2", "POST", "/api/papers", {
        "title": "E2E Comparison Paper Two", "authors": ["Wang Wu"], "year": 2023,
        "venue": "Another Journal", "type": "conference", "tags": ["e2e"],
        "summary": "Second abstract.", "keywords": ["cucurbituril"],
    }, None, "json")
    yield ("list", "GET", "/api/papers?pageSize=10", None, None, "json")
    yield ("search-q", "GET", "/api/papers?q=e2e&pageSize=10", None, None, "json")
    yield ("search-tags", "GET", "/api/papers?tags=e2e", None, None, "json")
    yield ("sort-title", "GET", "/api/papers?sort=title", None, None, "json")
    yield ("paged", "GET", "/api/papers?page=1&pageSize=1", None, None, "json")
    yield ("facets", "GET", "/api/facets", None, None, "json")
    yield ("tags", "GET", "/api/tags", None, None, "json")
    yield ("get-1", "GET", "/api/papers/__ID1__", None, None, "json")
    yield ("update-1", "PUT", "/api/papers/__ID1__", {"rating": 5, "tags": ["e2e", "updated"]}, None, "json")
    yield ("bulk", "POST", "/api/papers/bulk", {"ids": ["__ID1__", "__ID2__"], "patch": {"status": "read", "tags": ["bulk"]}}, None, "json")
    yield ("cite-gb", "GET", "/api/papers/__ID1__/cite?format=gb", None, None, "json")
    yield ("cite-apa", "GET", "/api/papers/__ID1__/cite?format=apa", None, None, "json")
    yield ("cite-bibtex", "GET", "/api/papers/__ID1__/cite?format=bibtex", None, None, "json")
    yield ("chat-get", "GET", "/api/papers/__ID1__/chat", None, None, "json")
    yield ("stats", "GET", "/api/stats", None, None, "json")
    yield ("export-json", "GET", "/api/export/json", None, None, "keys")
    yield ("export-bibtex", "GET", "/api/export/bibtex", None, None, "text")
    yield ("export-markdown", "GET", "/api/export/markdown", None, None, "text")
    yield ("import-json", "POST", "/api/import/json", {"papers": [{"title": "Imported E2E Paper", "year": 2022, "authors": ["Import Author"]}]}, None, "json")
    yield ("tags-rename", "POST", "/api/tags/rename", {"from": "bulk", "to": "批量"}, None, "json")
    yield ("tags-delete", "POST", "/api/tags/delete", {"name": "测试"}, None, "json")
    yield ("delete-2", "DELETE", "/api/papers/__ID2__", None, None, "json")
    yield ("get-deleted", "GET", "/api/papers/__ID2__", None, None, "json")
    yield ("list-after-delete", "GET", "/api/papers?pageSize=10", None, None, "json")
    yield ("static-index", "GET", "/", None, None, "status")
    yield ("static-css", "GET", "/styles.css", None, None, "status")
    yield ("missing-route", "GET", "/api/does-not-exist", None, None, "json")
    yield ("method-mismatch", "PATCH", "/api/tags", {"x": 1}, None, "json")


def run_scenario(client, ids):
    results = []
    for step in scenario():
        label, method, path, body, raw, mode = step
        real_path = path
        for token, value in ids.items():
            real_path = real_path.replace("__" + token.upper() + "__", str(value))
        if method in ("POST", "PUT", "PATCH", "DELETE") and body is not None:
            text = json.dumps(body, ensure_ascii=False)
            for token, value in ids.items():
                text = text.replace("__" + token.upper() + "__", str(value))
            body = json.loads(text)
        status, ctype, payload = client.call(method, real_path, body=body, raw=raw)
        kind, data = classify(ctype, payload)
        if label.startswith("create-") and status in (200, 201) and isinstance(data, dict):
            paper = data.get("paper") or {}
            token = "id1" if label == "create-1" else "id2"
            ids[token] = paper.get("id")
        results.append({"label": label, "method": method, "path": real_path, "status": status,
                        "ctype": ctype, "kind": kind, "data": data})
    return results


def compare_steps(node_results, py_results, node_ids, py_ids):
    problems = []
    modes = {step[0]: step[5] for step in scenario()}
    for node, py in zip(node_results, py_results):
        mode = modes.get(node["label"], "json")
        label = node["label"]
        if node["status"] != py["status"]:
            problems.append((label, "status", node["status"], py["status"]))
            continue
        if mode == "status":
            continue
        if mode in ("json", "keys"):
            n = normalize(node["data"], node_ids)
            p = normalize(py["data"], py_ids)
            for path, nv, pv, why in diff(n, p):
                problems.append((label, path, nv, pv))
        elif mode == "text":
            n = normalize(node["data"], node_ids)
            p = normalize(py["data"], py_ids)
            if n != p:
                problems.append((label, "text", str(n)[:200], str(p)[:200]))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--node-base", default="")
    parser.add_argument("--py-base", default="")
    parser.add_argument("--node-port", type=int, default=0)
    parser.add_argument("--py-port", type=int, default=0)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args(argv)

    procs = []
    dirs = []
    node_port = args.node_port or free_port()
    py_port = args.py_port or free_port()
    while py_port == node_port:
        py_port = free_port()
    try:
        node_base = args.node_base.rstrip("/")
        if not node_base:
            node_dir = prepare_data_dir("e2e-node-")
            dirs.append(node_dir)
            procs.append(start_node(node_dir, node_port))
            node_base = "http://127.0.0.1:%d" % node_port
            if not wait_health(node_base):
                print("Node 参考服务启动失败")
                return 1
        py_base = args.py_base.rstrip("/")
        if not py_base:
            py_dir = prepare_data_dir("e2e-py-")
            dirs.append(py_dir)
            procs.append(start_python(py_dir, py_port))
            py_base = "http://127.0.0.1:%d" % py_port
            if not wait_health(py_base):
                print("Python 服务启动失败")
                return 1

        node_ids = {}
        py_ids = {}
        node_results = run_scenario(Client(node_base), node_ids)
        py_results = run_scenario(Client(py_base), py_ids)
        problems = compare_steps(node_results, py_results, node_ids, py_ids)

        failed = {p[0] for p in problems}
        for result in node_results:
            label = result["label"]
            print(("PASS " if label not in failed else "FAIL ") + label)
        if problems:
            print("\n差异（前 100 条）：")
            for label, path, nv, pv in problems[:100]:
                print("  [%s] %s" % (label, path))
                print("      node: %r" % (nv,))
                print("      py  : %r" % (pv,))
        print("\nE2E：%d 步，%d 步有差异，%d 个字段差异" % (len(node_results), len(failed), len(problems)))
        return 1 if problems else 0
    finally:
        for proc in procs:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if not args.keep:
            for path in dirs:
                shutil.rmtree(path, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
