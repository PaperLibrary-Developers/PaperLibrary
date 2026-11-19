"""更新验证窗口的 launchedPid 回填窗口（A1）。

更新助手的 install 顺序是「先写 phase=launch + launchedPid=0 → 冷启动新版进程 → 回填真实 PID」。
新版进程可能在回填之前就读到 journal：此时 launchedPid=0 只代表「尚未回填」，直接退出会让新版进程
自杀、启动器健康检查失败、触发一次无意义的整包回滚。这里锁住三种收口：

1. 回填会在等待窗口内到达 → 窗口打开（2 秒预算，50ms 轮询）；
2. 始终没回填 → 窗口关闭（失败要干净、不无限等待）；
3. 判定仍然严格：PID 不匹配、phase 不是 launch、安装锁不在 → 一律关闭。

同时锁住时序约束：等待预算必须小于启动器 WaitForUpdateProbe 的 5 秒（`launcher/PaperLibraryLauncher.cs`）。
"""
from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from paperlib.util import write_json_atomic


def load_probe():
    from paperlib.app import probe_window_open
    return probe_window_open


def make_updates(root: Path, journal: dict, *, lock: bool = True) -> tuple[Path, Path]:
    updates = root / "updates"
    updates.mkdir(parents=True, exist_ok=True)
    journal_path = updates / "journal.json"
    write_json_atomic(journal_path, journal)
    if lock:
        write_json_atomic(updates / "install.lock", {"pid": 4321, "schema": 1})
    return journal_path, updates


class ProbeWindowTest(unittest.TestCase):
    def test_backfill_within_wait_opens_window(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal_path, updates = make_updates(root, {"phase": "launch", "launchedPid": 0})

            def backfill():
                time.sleep(0.3)
                write_json_atomic(journal_path, {"phase": "launch", "launchedPid": 4242})

            worker = threading.Thread(target=backfill)
            worker.start()
            try:
                started = time.monotonic()
                self.assertTrue(probe(journal_path, updates, 4242, wait_seconds=2.0))
                elapsed = time.monotonic() - started
            finally:
                worker.join()
            self.assertGreaterEqual(elapsed, 0.2, "应当真的等过回填窗口，不能读到 0 就直接放行")
            self.assertLess(elapsed, 1.8)

    def test_never_backfilled_closes_window(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal_path, updates = make_updates(root, {"phase": "launch", "launchedPid": 0})
            started = time.monotonic()
            self.assertFalse(probe(journal_path, updates, 4242, wait_seconds=0.3))
            self.assertGreaterEqual(time.monotonic() - started, 0.25)

    def test_missing_pid_field_is_treated_as_pending_then_rejected(self):
        probe = load_probe()
        for journal in ({"phase": "launch"}, {"phase": "launch", "launchedPid": None},
                        {"phase": "launch", "launchedPid": ""}):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                journal_path, updates = make_updates(root, journal)
                self.assertFalse(probe(journal_path, updates, 4242, wait_seconds=0.15))

    def test_matching_pid_opens_immediately(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal_path, updates = make_updates(root, {"phase": "launch", "launchedPid": 4242})
            started = time.monotonic()
            self.assertTrue(probe(journal_path, updates, 4242, wait_seconds=2.0))
            self.assertLess(time.monotonic() - started, 0.2)

    def test_other_pid_closes_window(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal_path, updates = make_updates(root, {"phase": "launch", "launchedPid": 9999})
            self.assertFalse(probe(journal_path, updates, 4242, wait_seconds=0.2))

    def test_missing_install_lock_closes_window(self):
        probe = load_probe()
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            journal_path, updates = make_updates(root, {"phase": "launch", "launchedPid": 4242},
                                                  lock=False)
            self.assertFalse(probe(journal_path, updates, 4242, wait_seconds=0.2))

    def test_non_launch_phase_closes_window(self):
        probe = load_probe()
        for phase in ("prepare", "backup", "replace", "verify", "success", "failed"):
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                journal_path, updates = make_updates(root, {"phase": phase, "launchedPid": 4242})
                self.assertFalse(probe(journal_path, updates, 4242, wait_seconds=0.15), phase)

    def test_wait_budget_stays_under_launcher_probe_timeout(self):
        """启动器 WaitForUpdateProbe 只容错 5 秒（starting），等待预算必须明显更短。"""
        import paperlib.app as app_module

        self.assertLess(app_module.PROBE_PID_BACKFILL_SECONDS, 5)
        self.assertGreater(app_module.PROBE_PID_BACKFILL_POLL_SECONDS, 0)


if __name__ == "__main__":
    unittest.main()
