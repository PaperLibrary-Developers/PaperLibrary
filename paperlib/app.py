"""进程入口（对应 Node server/server.js）：解析参数 → 建 store/服务 → 挂路由 + 静态 → 监听。"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from paperlib.config import HELP_TEXT, parse_args
from paperlib.data.store import Store
from paperlib.http.router import Router
from paperlib.http.server import HttpServer, probe_port
from paperlib.http.static import StaticHandler
from paperlib.routes import ROUTES
from paperlib.services import create_services
from paperlib.util import ensure_dir, write_json_atomic


def open_browser(url, log_dir=None):
    """打开默认浏览器；os.startfile → webbrowser → cmd start 三级兜底（结果写日志）。"""
    def _log(message):
        if not log_dir:
            return
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(log_dir) / "launcher.log", "a", encoding="utf-8") as fh:
                fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + message + "\n")
        except OSError:
            pass

    if os.name == "nt":
        try:
            os.startfile(url)  # noqa: S606 - 本地固定地址（ShellExecute）
            _log("已打开浏览器（os.startfile）：" + url)
            return True
        except OSError as exc:
            _log("os.startfile 失败：" + str(exc))
    try:
        if webbrowser.open(url, new=2):
            _log("已打开浏览器（webbrowser）：" + url)
            return True
    except Exception as exc:  # noqa: BLE001
        _log("webbrowser 失败：" + str(exc))
    try:
        subprocess.Popen(["cmd", "/c", "start", "", url])
        _log("已打开浏览器（cmd start）：" + url)
        return True
    except Exception as exc:  # noqa: BLE001
        _log("cmd start 失败：" + str(exc))
    _log("浏览器打开失败，请手动访问：" + url)
    return False


def stop_running(config):
    import urllib.request

    url = "http://127.0.0.1:%d/api/shutdown" % config.port
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        with urllib.request.urlopen(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
        if payload.get("ok"):
            print("  已通知 %d 端口上的文献库停止运行（数据已保存）。" % config.port)
            return 0
        print("  停止失败：服务返回 %s" % payload, file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print("  %d 端口上没有正在运行的文献库，无需停止。（%s）" % (config.port, exc))
        return 0


def seed_once_if_needed(config, store):
    """示例数据只在「这个库从来没被初始化过」时写入一次（data/.seeded 标记）。"""
    marker = config.data_dir / ".seeded"
    if marker.exists():
        return
    seed_file = config.root / "shared" / "fixtures" / "seed-library.json"
    if not seed_file.is_file():
        seed_file = config.root / "server-seed-library.json"
    if store.count() == 0 and seed_file.is_file():
        try:
            from paperlib.domain.repository import normalize_paper
            from paperlib.util import read_json

            payload = read_json(seed_file, {}) or {}
            papers = payload.get("papers") if isinstance(payload, dict) else None
            for raw in papers or []:
                paper = normalize_paper(raw, None)
                paper["addedAt"] = raw.get("addedAt") or paper["addedAt"]
                store.insert_paper(paper)
            if papers:
                print("[init] 首次运行，已载入 %d 条示例文献（可以直接删掉）" % len(papers))
        except Exception as exc:  # noqa: BLE001
            print("[init] 示例数据载入失败：%s" % exc)
    try:
        write_json_atomic(marker, {
            "seededAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": "这个文件表示示例数据已经播种过；删掉它不会让示例数据回来（故意的）。",
        })
    except OSError as exc:
        print("[init] 无法写入播种标记：%s" % exc)


def build(config):
    """装配（可被测试直接调用）：返回 (services, router, static, http_server)。"""
    store = Store(config.data_dir)
    store.load()
    # 旧库迁移（Sci-lib data/library.json、PaperLibrary data/papers.db）——B8 实现，缺失时跳过
    # 只在「默认 data/ 目录」或显式 --migrate 时执行，避免测试临时目录被真实旧库灌入 600MB
    auto_migrate = config.migrate
    if auto_migrate is None:
        auto_migrate = config.data_dir.resolve() == (config.root / "data").resolve()
    if auto_migrate and os.environ.get("PAPERLIB_SKIP_MIGRATE") != "1":
        try:
            try:
                from paperlib.migrate import migrate_from_legacy
            except ImportError:
                from paperlib.data.migrate import migrate_from_legacy

            migration = migrate_from_legacy(store, config)
            if migration:
                print("[migrate] " + json.dumps(migration, ensure_ascii=False))
        except ImportError:
            pass
        except Exception as exc:  # noqa: BLE001
            print("[migrate] 迁移失败（继续启动）：" + str(exc))
    seed_once_if_needed(config, store)
    services = create_services(config, store)
    static_handler = StaticHandler(config.root, config.public_dir, config.shared_dir, config.vendor_dir)
    router = Router(ROUTES, services)
    return services, router, static_handler, store


def main(argv=None, root=None):
    config = parse_args(argv, root)
    if config.help:
        print(HELP_TEXT)
        return 0
    if config.stop:
        return stop_running(config)

    config.started_at = time.time()
    services, router, static_handler, store = build(config)
    url = "http://%s:%d/" % ("127.0.0.1" if config.host == "0.0.0.0" else config.host, config.port)
    state = probe_port(config.host, config.port)
    if state == "ours":
        print("")
        print("  这个端口上已经有一个文献库在运行了：%s" % url)
        print("  直接为你打开浏览器，不需要重复启动。")
        if not config.no_open:
            open_browser(url, config.data_dir / "logs")
        return 0
    if state == "busy":
        print("  [x] 端口 %d 被其它程序占用了，服务无法启动。" % config.port, file=sys.stderr)
        return 1

    server = HttpServer(router, static_handler, config.host, config.port, verbose=True)
    services.runtime.on_shutdown = server.shutdown
    ensure_dir(config.data_dir)
    write_json_atomic(config.runtime_file, {
        "app": config.app, "pid": os.getpid(), "port": config.port, "host": config.host,
        "dataDir": str(config.data_dir), "startedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    })

    def shutdown(reason="signal"):
        try:
            store.save_now()
        finally:
            server.shutdown()

    def on_signal(signum, frame):
        print("\n正在保存数据并退出…")
        shutdown("signal")
        raise SystemExit(0)

    signal.signal(signal.SIGINT, on_signal)
    try:
        signal.signal(signal.SIGTERM, on_signal)
    except (AttributeError, ValueError):
        pass

    server.start_background()
    config.boot_ms = int((time.time() - config.started_at) * 1000)
    services.runtime.timing["bootMs"] = config.boot_ms
    print("")
    print("  PaperLibrary v1.0 Nexus 已启动 →  %s" % url)
    print("  数据文件：%s" % store.data_file())
    print("  原文目录：%s" % store.papers_dir)
    print("  补充材料：%s" % store.attachments_dir)
    print("  共 %d 条文献。" % store.count())
    print("  进程号（PID）：%d，按 Ctrl+C 停止。" % os.getpid())
    print("  也可以在网页「设置 → 服务与数据」里点「停止服务」。")
    print("")
    if not config.no_open:
        open_browser(url, config.data_dir / "logs")
    try:
        while server.thread.is_alive():
            time.sleep(0.3)
    except KeyboardInterrupt:
        on_signal(None, None)
    return 0
