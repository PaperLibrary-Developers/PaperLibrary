"""`python -m paperlib` 入口的轻量化回归测试。

为什么值得单独测：`--stop` / `--help` 以前都要先 import 整个应用
（routes → api → legacy/figures → PyMuPDF），只要 PATH 里的 python 不是装了 PyMuPDF 的那个，
「停服务」「看帮助」就直接 ImportError，用户会以为功能坏了。
这里用子进程确认：这两个分支不会把 paperlib.app 拉进来。
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import closing
from pathlib import Path

from paperlib.data.store import Store
from paperlib.util import write_json_atomic

ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def run_py(code: str) -> subprocess.CompletedProcess:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    return subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


class CliEntryTest(unittest.TestCase):
    def test_help_does_not_import_app(self):
        proc = run_py(
            "import sys\n"
            "from paperlib.__main__ import main\n"
            "rc = main(['--help'])\n"
            "print('rc=', rc)\n"
            "print('app_loaded=', 'paperlib.app' in sys.modules)\n"
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rc= 0", proc.stdout)
        self.assertIn("app_loaded= False", proc.stdout)
        self.assertIn("--idle-exit", proc.stdout)

    def test_stop_does_not_import_app(self):
        port = free_port()
        proc = run_py(
            "import sys\n"
            "from paperlib.__main__ import main\n"
            "rc = main(['--stop', '--port', '%d'])\n"
            "print('rc=', rc)\n"
            "print('app_loaded=', 'paperlib.app' in sys.modules)\n" % port
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("rc= 0", proc.stdout)          # 没在跑也算成功（幂等）
        self.assertIn("app_loaded= False", proc.stdout)

    def test_update_probe_blocks_ui_and_writes_until_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            store = Store(data)
            store.load()
            store.insert_paper({"id": "probe-fixture", "title": "Existing paper",
                                "pdfFile": "root.pdf", "folder": "Legacy virtual folder"})
            store.repo.close()
            update_dir = data / "updates"
            journal = update_dir / "journal.json"
            launcher_pid = os.getpid()
            write_json_atomic(journal, {"phase": "launch", "launchedPid": launcher_pid})
            write_json_atomic(update_dir / "install.lock", {"pid": launcher_pid})
            port = free_port()
            command = [sys.executable, "-B", "-m", "paperlib", "--no-open", "--idle-exit", "0",
                       "--host", "127.0.0.1", "--port", str(port), "--data-dir", str(data)]
            env = dict(os.environ, PAPERLIB_UPDATE_PROBE_PID=str(launcher_pid))

            def request(method, path, body=None):
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                try:
                    headers = {"Content-Type": "application/json"} if body is not None else {}
                    conn.request(method, path, body=body, headers=headers)
                    response = conn.getresponse()
                    return response.status, response.read()
                finally:
                    conn.close()

            with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) as proc:
                try:
                    deadline = time.monotonic() + 10
                    while time.monotonic() < deadline and proc.poll() is None:
                        try:
                            self.assertEqual(request("GET", "/api/health")[0], 200)
                            break
                        except OSError:
                            time.sleep(0.05)
                    else:
                        self.fail("update probe service did not start")

                    self.assertEqual(request("GET", "/api/version")[0], 200)
                    self.assertEqual(request("GET", "/")[0], 503)
                    body = json.dumps({"title": "Update gate fixture"})
                    self.assertEqual(request("POST", "/api/papers", body)[0], 503)
                    with closing(sqlite3.connect(data / "library.db")) as db:
                        stored_folder, = db.execute("SELECT folder FROM papers WHERE id='probe-fixture'").fetchone()
                    self.assertEqual(stored_folder, "Legacy virtual folder")
                    write_json_atomic(journal, {"phase": "success", "launchedPid": launcher_pid + 1})
                    self.assertEqual(request("POST", "/api/papers", body)[0], 503)
                    write_json_atomic(journal, {"phase": "success", "launchedPid": launcher_pid})
                    self.assertEqual(request("GET", "/")[0], 200)
                    status, payload = request("GET", "/api/papers/probe-fixture")
                    self.assertEqual(status, 200)
                    self.assertFalse(json.loads(payload)["paper"].get("folder"))
                    self.assertEqual(request("POST", "/api/papers", body)[0], 201)
                    self.assertEqual(request("POST", "/api/shutdown", "{}")[0], 200)
                    self.assertEqual(proc.wait(timeout=5), 0)
                finally:
                    if proc.poll() is None:
                        proc.terminate()

    def test_update_probe_survives_unbackfilled_launch_pid(self):
        """A1 回归：journal 先写 launchedPid=0、稍后才回填，新版进程不能因此自杀。

        更新助手的真实顺序是「写 launch+0 → 冷启动新进程 → 回填 PID」（
        tools/updater/PaperLibraryUpdater.cs install 的 launch 分支）。新版进程若在回填前读到 0
        就返回 1，启动器健康检查必然超时，触发一次无意义的整包回滚。这里把回填放在 1.2 秒后，
        在 2 秒等待预算之内：修复后的进程会等回填并正常服务；修复前会在回填前退出，测试失败。
        """
        with tempfile.TemporaryDirectory() as temp:
            data = Path(temp)
            update_dir = data / "updates"
            journal = update_dir / "journal.json"
            launcher_pid = os.getpid()
            write_json_atomic(journal, {"phase": "launch", "launchedPid": 0})
            write_json_atomic(update_dir / "install.lock", {"pid": launcher_pid})
            port = free_port()
            command = [sys.executable, "-B", "-m", "paperlib", "--no-open", "--idle-exit", "0",
                       "--host", "127.0.0.1", "--port", str(port), "--data-dir", str(data)]
            env = dict(os.environ, PAPERLIB_UPDATE_PROBE_PID=str(launcher_pid))

            def backfill():
                time.sleep(1.2)
                write_json_atomic(journal, {"phase": "launch", "launchedPid": launcher_pid})

            with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL) as proc:
                worker = threading.Thread(target=backfill)
                worker.start()
                try:
                    deadline = time.monotonic() + 20
                    status = None
                    while time.monotonic() < deadline and proc.poll() is None:
                        try:
                            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                            try:
                                conn.request("GET", "/api/health")
                                status = conn.getresponse().status
                            finally:
                                conn.close()
                            if status == 200:
                                break
                        except OSError:
                            time.sleep(0.05)
                    self.assertIsNone(proc.poll(),
                                      "新版进程在 launchedPid 回填前退出了（A1 回归）")
                    self.assertEqual(status, 200, "更新验证服务未启动")
                    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                    try:
                        conn.request("POST", "/api/shutdown", "{}")
                        self.assertEqual(conn.getresponse().status, 200)
                    finally:
                        conn.close()
                    self.assertEqual(proc.wait(timeout=5), 0)
                finally:
                    worker.join()
                    if proc.poll() is None:
                        proc.terminate()


if __name__ == "__main__":
    unittest.main()
