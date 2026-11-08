"""`python -m paperlib` 入口的轻量化回归测试。

为什么值得单独测：`--stop` / `--help` 以前都要先 import 整个应用
（routes → api → legacy/figures → PyMuPDF），只要 PATH 里的 python 不是装了 PyMuPDF 的那个，
「停服务」「看帮助」就直接 ImportError，用户会以为功能坏了。
这里用子进程确认：这两个分支不会把 paperlib.app 拉进来。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
