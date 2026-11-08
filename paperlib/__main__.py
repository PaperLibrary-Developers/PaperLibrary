"""python -m paperlib 入口。

`--stop` 走轻量分支：只 import config + ctl，不碰整个应用。
否则「停服务」也要先把 routes → api → legacy/figures → PyMuPDF 全部加载起来，
机器上 PATH 里的 python 若不是装了 PyMuPDF 的那个，`--stop` 会直接 ImportError —— 看起来就像停止功能坏了。
"""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if "--help" in args or "-h" in args:
        # 帮助同样走轻量分支：--help 只是打印 config.HELP_TEXT，没必要为它加载整个应用
        from paperlib.config import HELP_TEXT

        print(HELP_TEXT)
        return 0
    if "--stop" in args:
        from paperlib.config import parse_args
        from paperlib.ctl import stop_running

        return stop_running(parse_args(args))

    from paperlib.app import main as app_main

    return app_main(args)


if __name__ == "__main__":
    sys.exit(main())
