"""在线监听 / 空闲自动停机的单元测试。

覆盖三件事：
  1. 计时只在「没有在线连接」时走，attach 后立刻暂停；
  2. 重新连上来（attach / 任意请求 touch）会把空闲计时归零 → 取消自动停机；
  3. 触发只发生一次（停机不可逆），以及 --idle-exit 0 时整体关闭。
时间用可注入的假时钟，不靠 sleep。
"""
from __future__ import annotations

import socket
import unittest

from paperlib.http.presence import PresenceTracker, peer_closed


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


class PresenceTrackerTest(unittest.TestCase):
    def make(self, idle=30.0, grace=8.0):
        clock = FakeClock()
        fired = []
        tracker = PresenceTracker(idle_seconds=idle, grace_seconds=grace,
                                  on_idle=lambda: fired.append(clock()), now=clock)
        return tracker, clock, fired

    def test_grace_period_blocks_exit(self):
        tracker, clock, fired = self.make(idle=30, grace=8)
        clock.advance(5)            # 启动 5 秒，谁都没连过
        self.assertFalse(tracker.should_exit())
        self.assertEqual(fired, [])

    def test_exits_after_idle_when_nobody_connected(self):
        """从没人连过也要计时：启动后 last_seen = started，空闲满 30s 就停。

        真正的停机时刻是 max(grace, idle)——宽限只负责「至少等这么久」。
        """
        tracker, clock, fired = self.make(idle=30, grace=8)
        clock.advance(29)
        self.assertFalse(tracker.should_exit())
        clock.advance(1)            # 空闲满 30 秒
        self.assertTrue(tracker.should_exit())
        self.assertTrue(tracker.tick())
        self.assertEqual(len(fired), 1)

    def test_grace_dominates_when_longer_than_idle(self):
        tracker, clock, fired = self.make(idle=5, grace=20)
        clock.advance(19.5)
        self.assertFalse(tracker.should_exit())   # 空闲够了，但启动宽限还没过
        clock.advance(1)
        self.assertTrue(tracker.should_exit())

    def test_connected_page_keeps_server_alive(self):
        tracker, clock, fired = self.make(idle=30)
        token = tracker.attach()
        clock.advance(600)          # 页面挂着不动 10 分钟
        self.assertEqual(tracker.clients, 1)
        self.assertFalse(tracker.should_exit())
        self.assertFalse(tracker.tick())
        self.assertEqual(fired, [])
        tracker.detach(token)

    def test_detach_starts_the_idle_countdown(self):
        tracker, clock, fired = self.make(idle=30)
        token = tracker.attach()
        clock.advance(12)
        self.assertFalse(tracker.should_exit())   # 连着的时候不计时
        tracker.detach(token)
        clock.advance(29)
        self.assertFalse(tracker.should_exit())
        clock.advance(1)
        self.assertTrue(tracker.should_exit())

    def test_reconnect_cancels_the_countdown(self):
        tracker, clock, fired = self.make(idle=30)
        token = tracker.attach()
        tracker.detach(token)
        clock.advance(25)                       # 差 5 秒就要停了
        again = tracker.attach()                # 用户又打开了页面
        self.assertFalse(tracker.should_exit())
        self.assertEqual(tracker.idle_for(), 0.0)
        clock.advance(29)
        self.assertFalse(tracker.should_exit())
        tracker.detach(again)
        clock.advance(30)
        self.assertTrue(tracker.should_exit())

    def test_any_request_touches_last_seen(self):
        tracker, clock, fired = self.make(idle=30)
        clock.advance(20)
        tracker.touch()                         # 一次普通请求（比如刷新）
        clock.advance(29)
        self.assertFalse(tracker.should_exit())
        clock.advance(1)
        self.assertTrue(tracker.should_exit())

    def test_trigger_is_one_shot(self):
        tracker, clock, fired = self.make(idle=5)
        clock.advance(20)
        self.assertTrue(tracker.tick())          # 第一次触发
        clock.advance(60)
        self.assertFalse(tracker.tick())         # 不再重复触发
        self.assertFalse(tracker.should_exit())  # triggered 之后 should_exit 也恒 False
        self.assertEqual(len(fired), 1)

    def test_disabled_when_zero(self):
        tracker, clock, fired = self.make(idle=0)
        clock.advance(3600)
        self.assertFalse(tracker.should_exit())
        self.assertFalse(tracker.start())        # 0 = 不启动监控线程
        self.assertFalse(tracker.status()["enabled"])

    def test_detach_unknown_token_is_harmless(self):
        tracker, clock, fired = self.make(idle=30)
        tracker.detach(999)                      # 重复/未知 token 不该抛
        self.assertEqual(tracker.clients, 0)

    def test_status_reports_countdown(self):
        tracker, clock, fired = self.make(idle=30, grace=8)
        self.assertIsNone(tracker.status()["exitInSeconds"])   # 还在宽限期
        clock.advance(8)
        clock.advance(10)                        # 启动 18 秒，空闲也按 18 秒算（没人连过）
        status = tracker.status()
        self.assertEqual(status["clients"], 0)
        self.assertAlmostEqual(status["idleSeconds"], 18, places=1)
        self.assertAlmostEqual(status["exitInSeconds"], 12, places=1)
        token = tracker.attach()
        self.assertEqual(tracker.status()["exitInSeconds"], None)  # 有页面连着 → 不倒数
        tracker.detach(token)


class PeerClosedTest(unittest.TestCase):
    def test_detects_closed_peer(self):
        a, b = socket.socketpair()
        try:
            self.assertFalse(peer_closed(a))
            b.close()
            self.assertTrue(peer_closed(a))
        finally:
            a.close()

    def test_none_connection_is_closed(self):
        self.assertTrue(peer_closed(None))


if __name__ == "__main__":
    unittest.main()
