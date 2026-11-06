"""基础 / 运维端点：health / stats / shutdown / release。"""
from __future__ import annotations

import os
import threading
import time


def _rss_mb():
    try:
        import ctypes
        from ctypes import wintypes

        class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        fn = kernel32.K32GetProcessMemoryInfo
        fn.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD]
        fn.restype = wintypes.BOOL
        if fn(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            return round(counters.WorkingSetSize / 1024 / 1024)
    except Exception:
        pass
    return 0


def _uptime(services):
    return int(time.time() - getattr(services.runtime, "started_at", time.time()))


def health(ctx):
    services = ctx.services
    store = services.db
    ctx.json(200, {
        "ok": True,
        "app": services.runtime.app,
        "version": services.runtime.version,
        "dataDir": str(services.runtime.data_dir),
        "pid": os.getpid(),
        "port": services.runtime.port,
        "papers": store.count(),
        "uptimeSeconds": _uptime(services),
        "updatedAt": store.meta.get("updatedAt") or "",
        "bootMs": services.runtime.timing.get("bootMs"),
        "rssMB": _rss_mb(),
    })


def stats(ctx):
    data = ctx.services.library.stats()
    ctx.json(200, {"ok": True, "stats": data})


def shutdown_status(ctx):
    services = ctx.services
    ctx.json(200, {
        "ok": True,
        "app": services.runtime.app,
        "papers": services.db.count(),
        "dataDir": str(services.runtime.data_dir),
        "pid": os.getpid(),
        "port": services.runtime.port,
        "uptimeSeconds": _uptime(services),
    })


def shutdown_now(ctx):
    peer = ctx.handler.client_address[0] if getattr(ctx.handler, "client_address", None) else ""
    if peer not in ("127.0.0.1", "::1", "localhost"):
        return ctx.fail(403, "只接受本机回环地址的停机请求")
    ctx.json(200, {"ok": True, "message": "服务正在停止（数据已保存）"})
    try:
        ctx.services.runtime.on_shutdown()
    except Exception:
        pass


def release_build(ctx):
    # 发布打包：由 tools/release.py 承担；API 触发属于运维端点（T-settings 实现）
    ctx.fail(501, "待实现：POST /api/release/build")
