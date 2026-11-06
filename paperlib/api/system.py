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
        "version": services.db.state["version"],
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


_release_running = False


def release_build(ctx):
    """打一个发布包（POST /api/release/build）：调 tools/release.py，返回产物信息。

    契约：200 {ok,name,path,relPath,size,mtime,papers,files,seconds}；409 已在打包；400 环境不满足。
    """
    global _release_running
    if _release_running:
        return ctx.fail(409, "已经有一个打包任务在跑，请稍后再试")
    import subprocess
    import sys
    import time

    root = ctx.services.runtime.root
    script = root / "tools" / "release.py"
    if not script.is_file():
        return ctx.fail(400, "缺少打包脚本 tools/release.py")
    started = time.time()
    _release_running = True
    try:
        proc = subprocess.run([sys.executable, str(script)], cwd=str(root),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=900)
    finally:
        _release_running = False
    if proc.returncode != 0:
        return ctx.fail(400, "打包失败：" + ((proc.stderr or proc.stdout or "").strip()[-300:] or "未知原因"))
    dist = root / "dist"
    targets = sorted([p for p in dist.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime) if dist.is_dir() else []
    if not targets:
        return ctx.fail(500, "打包完成但找不到产物目录")
    target = targets[-1]
    stat = target.stat()
    files = 0
    size = 0
    for base, _dirs, names in __import__("os").walk(target):
        for name in names:
            files += 1
            try:
                size += __import__("os").path.getsize(__import__("os").path.join(base, name))
            except OSError:
                pass
    try:
        rel = target.relative_to(root)
    except ValueError:
        rel = target.name
    return ctx.json(200, {
        "ok": True,
        "name": target.name,
        "path": str(target),
        "relPath": str(rel).replace("\\", "/"),
        "size": size,
        "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stat.st_mtime)),
        "papers": ctx.services.db.count(),
        "files": files,
        "seconds": round(time.time() - started, 1),
    })
