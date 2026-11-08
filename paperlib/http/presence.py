"""在线页面监听 + 空闲自动停机（关掉网页 30 秒后自己退出，不用再手动点「停止服务」）。

判定「页面还在」的唯一依据是 SSE 长连接（GET /api/presence），不靠轮询：
    · 连接建立        → attach()：在线连接 +1，同时把 last_seen 打成「现在」
    · 连接断开        → detach()：在线连接 -1，last_seen 再打一次（空闲从这里开始算）
    · 服务端每秒用 select + MSG_PEEK 探一次对端是否已关闭，不等写失败才发现（关标签页 1 秒内就知道）
    · EventSource 断线会自动重连，所以「刷新」「切走再回来」「短断网」只是极短的离线，不会误停

计时的触发条件（三个同时满足才停）：
    1. 在线连接数为 0（没有页面挂着）
    2. 距离最后一次「有人用」（attach / detach / 任意 HTTP 请求 touch）已过 idle_seconds（默认 30s）
    3. 进程启动已过 grace_seconds（默认 8s，留给浏览器打开）

重新连上怎么取消：
    · 任何一次 attach() 或 touch() 都把 last_seen 归零 → 下一拍 tick() 自然不再满足空闲条件
    · triggered 之后不再撤销：那一刻已经是「决定停机」，紧接着就是存盘 + 退出，属于不可逆动作；
      因此不做「取消」而做「提前不满足条件」，语义更简单
"""
from __future__ import annotations

import select
import socket
import threading
import time


def peer_closed(conn, timeout: float = 0.0) -> bool:
    """对端是否已经关闭连接：只 peek 不消费；连接不可用也当作已关闭。"""
    if conn is None:
        return True
    try:
        readable, _, _ = select.select([conn], [], [], timeout)
        if not readable:
            return False
        return conn.recv(1, socket.MSG_PEEK) == b""
    except (OSError, ValueError):
        return True


class PresenceTracker:
    """在线连接计数 + 空闲计时。线程安全；now 可注入，便于测试不靠 sleep。"""

    def __init__(self, idle_seconds: float = 30.0, grace_seconds: float = 8.0,
                 on_idle=None, now=time.monotonic, tick_seconds: float = 0.5):
        self.idle_seconds = max(0.0, float(idle_seconds))
        self.grace_seconds = max(0.0, float(grace_seconds))
        self.tick_seconds = max(0.1, float(tick_seconds))
        self.on_idle = on_idle
        self._now = now
        self._lock = threading.RLock()
        self.started_at = now()
        self.last_seen = self.started_at
        self.triggered = False
        self.exit_reason = ""
        self._tokens = set()
        self._seq = 0
        self.total_attaches = 0
        self._stop = threading.Event()
        self._thread = None

    # ---------------- 连接生命周期 ----------------
    def attach(self) -> int:
        with self._lock:
            self._seq += 1
            token = self._seq
            self._tokens.add(token)
            self.total_attaches += 1
            self.last_seen = self._now()
            return token

    def detach(self, token) -> None:
        with self._lock:
            self._tokens.discard(token)
            # 断开也算一次「刚刚还有人在」：空闲从最后一个页面离开的那一刻开始计
            self.last_seen = self._now()

    def touch(self) -> None:
        """任何一次 HTTP 请求都算「有人在用」，把空闲计时归零。"""
        with self._lock:
            self.last_seen = self._now()

    # ---------------- 状态 ----------------
    @property
    def clients(self) -> int:
        with self._lock:
            return len(self._tokens)

    def idle_for(self) -> float:
        with self._lock:
            if self._tokens:
                return 0.0
            return max(0.0, self._now() - self.last_seen)

    def status(self) -> dict:
        with self._lock:
            now = self._now()
            clients = len(self._tokens)
            idle = 0.0 if clients else max(0.0, now - self.last_seen)
            grace_left = max(0.0, self.grace_seconds - (now - self.started_at))
            enabled = self.idle_seconds > 0
            will_exit = None
            if enabled and not clients and not self.triggered and grace_left <= 0:
                will_exit = round(max(0.0, self.idle_seconds - idle), 1)
            return {
                "enabled": enabled,
                "clients": clients,
                "idleSeconds": round(idle, 1),
                "idleLimitSeconds": self.idle_seconds,
                "graceSeconds": self.grace_seconds,
                "graceRemainingSeconds": round(grace_left, 1),
                "exitInSeconds": will_exit,
                "triggered": self.triggered,
                "totalConnects": self.total_attaches,
            }

    # ---------------- 判定 ----------------
    def should_exit(self) -> bool:
        with self._lock:
            if self.idle_seconds <= 0 or self.triggered:
                return False
            if self._tokens:
                return False
            now = self._now()
            if now - self.started_at < self.grace_seconds:
                return False
            return (now - self.last_seen) >= self.idle_seconds

    def tick(self) -> bool:
        """一拍检查：满足空闲条件就触发一次 on_idle（只触发一次）。"""
        if not self.should_exit():
            return False
        with self._lock:
            if self.triggered:
                return False
            self.triggered = True
            self.exit_reason = "idle"
        callback = self.on_idle
        if callback:
            try:
                callback()
            except Exception as exc:  # noqa: BLE001 - 停机回调失败不该带走监控线程
                print("[presence] 自动停机回调异常：%s" % exc, flush=True)
        return True

    # ---------------- 监控线程 ----------------
    def _loop(self):
        while not self._stop.wait(self.tick_seconds):
            try:
                if self.tick():
                    return
            except Exception as exc:  # noqa: BLE001
                print("[presence] 监控线程异常：%s" % exc, flush=True)

    def start(self) -> bool:
        if self.idle_seconds <= 0:
            return False
        if self._thread and self._thread.is_alive():
            return False
        self._thread = threading.Thread(target=self._loop, name="presence", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
