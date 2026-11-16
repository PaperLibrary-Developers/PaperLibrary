"""P2-19：update-apply.md / update-install.md 的契约测试（结构 + 关键冻结字段）。"""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APPLY = ROOT / "shared" / "contracts" / "update-apply.md"
INSTALL = ROOT / "shared" / "contracts" / "update-install.md"


class UpdateContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.apply = APPLY.read_text(encoding="utf-8")
        cls.install = INSTALL.read_text(encoding="utf-8")

    def test_apply_contract_has_frozen_handshake_and_startup_rules(self):
        for token in (
            "paperlibrary:install.request", "paperlibrary:install.result",
            "requestId", "只接受本应用页面", "127.0.0.1", "4321",
            "helper-start-failed", "service-stop-timeout",
            "启动判定", "不得静默安装", "是否立即安装",
        ):
            self.assertIn(token, self.apply, token)
        for field in ("schema", "version", "stagingPath", "backupPath", "fromVersion"):
            self.assertIn(field, self.apply, field)
        self.assertIn("P2-09R", self.apply)
        self.assertIn("P2-11R", self.apply)

    def test_install_contract_has_frozen_phases_locks_health_and_retry(self):
        for phase in ("prepare", "backup", "replace", "verify", "launch", "success", "failed"):
            self.assertIn(phase, self.install, phase)
        for token in (
            "launchedPid", "oldLaunchedPid", "restored", "recovered",
            "install.lock", "30 分钟", "recover", "health-timeout",
            "health-http", "health-json", "health-not-ok", "health-app", "health-version",
            "300 ms", "21 秒", "file-lock-timeout", "backup-data", "backup-program",
            "replace-program", "300 MB", "Program Files",
        ):
            self.assertIn(token, self.install, token)


if __name__ == "__main__":
    unittest.main()
