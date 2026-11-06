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


_build_lock = __import__("threading").Lock()


def release_build(ctx):
    """打一个发布包（POST /api/release/build）：调 tools/release.py，返回产物信息。

    契约：200 {ok,name,path,relPath,size,mtime,papers,files,seconds}；409 已在打包；400 环境不满足。
    """
    if not _build_lock.acquire(blocking=False):
        return ctx.fail(409, "已经有一个打包任务在跑，请稍后再试")
    import subprocess
    import sys
    import time

    root = ctx.services.runtime.root
    script = root / "tools" / "release.py"
    if not script.is_file():
        _build_lock.release()
        return ctx.fail(400, "缺少打包脚本 tools/release.py")
    started = time.time()
    try:
        proc = subprocess.run([sys.executable, str(script)], cwd=str(root),
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=900)
    finally:
        _build_lock.release()
    if proc.returncode != 0:
        # 契约不变量：error 只回中文可读消息，不泄漏堆栈/本机路径；细节写日志
        log_dir = ctx.services.runtime.data_dir / "logs"
        log_path = ""
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = str(log_dir / ("release-" + time.strftime("%Y%m%d-%H%M%S") + ".log"))
            with open(log_path, "w", encoding="utf-8") as fh:
                fh.write((proc.stdout or "") + "\n" + (proc.stderr or ""))
        except OSError:
            pass
        return ctx.fail(400, "打包失败，请查看日志" + ("：" + log_path if log_path else ""))
    dist = root / "dist"
    targets = sorted([p for p in dist.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime) if dist.is_dir() else []
    if not targets:
        return ctx.fail(500, "打包完成但找不到产物目录")
    target = targets[-1]
    stat = target.stat()
    files = 0
    size = 0
    # 优先用 release-manifest.json 的口径（与 tools/release.py 一致，排除 __pycache__ 等）
    manifest_path = target / "release-manifest.json"
    try:
        import json as _json

        manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
        size = int(manifest.get("bytes") or 0)
        files = int(manifest.get("files") or 0)
    except (OSError, ValueError, TypeError):
        size = 0
    if not size:
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
        "papers": 0,  # 发布包按设计不含 data/（用户数据），所以包内文献数为 0
        "files": files,
        "seconds": round(time.time() - started, 1),
    })
