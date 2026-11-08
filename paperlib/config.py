"""运行配置：命令行/环境变量 → Config。

命令行参数优先于环境变量（系统里的 PORT/HOST 常常是给别的程序用的），与 Node 版一致：
  --port / -p, --host, --data-dir, --no-open, --stop, --help, --idle-exit
环境变量：LV_PORT / PORT、LV_HOST / HOST、LV_DATA_DIR / DATA_DIR、OPEN=0、LV_IDLE_EXIT
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 4321
DEFAULT_HOST = "127.0.0.1"
APP_ID = "literature-vault"   # 前端探测「是不是自己的服务」用这个值，不要改


@dataclass
class Config:
    root: Path
    data_dir: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    no_open: bool = False
    stop: bool = False
    help: bool = False
    # 关掉网页后多久自动停机（秒）：0 = 不自动停。见 http/presence.py
    idle_exit: int = 30
    # 启动宽限期（秒）：进程起来后这段时间内不判定空闲，留给浏览器打开
    idle_grace: int = 8
    app: str = APP_ID
    version: str = "2.0.0"
    # 启动打点（app.py 在 listen 就绪后写入）
    boot_ms: int | None = None
    started_at: float = 0.0
    # None=自动（仅默认 data/ 目录才迁移旧库）；True/False 由 --migrate/--no-migrate 显式控制
    migrate: bool | None = None
    extra: dict = field(default_factory=dict)

    @property
    def public_dir(self) -> Path:
        return self.root / "public"

    @property
    def shared_dir(self) -> Path:
        return self.root / "shared"

    @property
    def vendor_dir(self) -> Path:
        return self.root / "vendor"

    @property
    def runtime_file(self) -> Path:
        return self.data_dir / "server.runtime.json"

    def to_public(self) -> dict:
        return {
            "app": self.app,
            "version": self.version,
            "port": self.port,
            "host": self.host,
            "dataDir": str(self.data_dir),
        }


def _pick_seconds(raw, default: int) -> int:
    """秒数参数：非法值回默认值；负数按 0（= 关闭）处理。"""
    try:
        n = int(float(raw))
    except (TypeError, ValueError):
        return default
    return n if n > 0 else 0


def _pick_port(raw) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return n if 0 < n < 65536 else DEFAULT_PORT


def parse_args(argv=None, root=None) -> Config:
    argv = list(argv if argv is not None else [])
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    out = {"flags": set()}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in ("--help", "-h"):
            out["flags"].add("help")
        elif arg == "--no-open":
            out["flags"].add("no-open")
        elif arg == "--no-migrate":
            out["migrate"] = False
        elif arg == "--migrate":
            out["migrate"] = True
        elif arg == "--stop":
            out["flags"].add("stop")
        elif arg in ("--port", "-p") and i + 1 < len(argv):
            i += 1
            out["port"] = argv[i]
        elif arg.startswith("--port="):
            out["port"] = arg[7:]
        elif arg == "--host" and i + 1 < len(argv):
            i += 1
            out["host"] = argv[i]
        elif arg.startswith("--host="):
            out["host"] = arg[7:]
        elif arg == "--data-dir" and i + 1 < len(argv):
            i += 1
            out["data_dir"] = argv[i]
        elif arg.startswith("--data-dir="):
            out["data_dir"] = arg[11:]
        elif arg == "--idle-exit" and i + 1 < len(argv):
            i += 1
            out["idle_exit"] = argv[i]
        elif arg.startswith("--idle-exit="):
            out["idle_exit"] = arg[12:]
        elif arg == "--idle-grace" and i + 1 < len(argv):
            i += 1
            out["idle_grace"] = argv[i]
        elif arg.startswith("--idle-grace="):
            out["idle_grace"] = arg[13:]
        i += 1

    env = os.environ
    port = _pick_port(out.get("port") or env.get("LV_PORT") or env.get("PORT") or DEFAULT_PORT)
    host = out.get("host") or env.get("LV_HOST") or env.get("HOST") or DEFAULT_HOST
    raw_dir = out.get("data_dir") or env.get("LV_DATA_DIR") or env.get("DATA_DIR") or "data"
    data_dir = Path(raw_dir)
    if not data_dir.is_absolute():
        data_dir = (root / data_dir).resolve()
    no_open = ("no-open" in out["flags"]) or env.get("OPEN") == "0"
    idle_raw = out.get("idle_exit")
    if idle_raw is None:
        idle_raw = env.get("LV_IDLE_EXIT")
    return Config(
        root=root,
        data_dir=data_dir,
        host=host,
        port=port,
        no_open=no_open,
        stop=("stop" in out["flags"]),
        help=("help" in out["flags"]),
        migrate=out.get("migrate"),
        idle_exit=_pick_seconds(idle_raw, 30),
        idle_grace=_pick_seconds(out.get("idle_grace"), 8),
    )


HELP_TEXT = """PaperLibrary 文献库服务（Python 版）

用法：python -m paperlib [选项]

选项：
  -p, --port <端口>       监听端口（默认 4321）
      --host <地址>       监听地址（默认 127.0.0.1，仅本机可访问）
      --data-dir <目录>   数据目录（默认 ./data）
      --no-open           启动后不自动打开浏览器
      --idle-exit <秒>    关掉网页多久后自动停机（默认 30，0 = 不自动停）
      --idle-grace <秒>   启动后多久内不判定空闲（默认 8，留给浏览器打开）
      --stop              停止正在运行的服务（数据会先存盘）
  -h, --help              显示这份帮助

环境变量（命令行参数优先）：
  LV_PORT / PORT、LV_HOST / HOST、LV_DATA_DIR / DATA_DIR、OPEN=0、LV_IDLE_EXIT
"""
