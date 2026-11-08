"""停止正在运行的服务（`python -m paperlib --stop` 用）。

单独放一个轻量模块，是为了让「停服务」不必 import 整个应用：
  paperlib.app → routes → api → legacy/figures → PyMuPDF
只要其中一个环节缺失（例如 PATH 里的 python 属于别的软件、没装 PyMuPDF），
`--stop` 就会在 import 阶段直接挂掉，用户会以为「停止按钮坏了」。
这里只依赖标准库 + config，任何 Python 都能跑。
"""
from __future__ import annotations

import json
import sys
import urllib.request


def stop_running(config) -> int:
    """给正在监听的实例发一个 /api/shutdown；没有实例在跑也算成功（幂等）。"""
    url = "http://127.0.0.1:%d/api/shutdown" % config.port
    try:
        req = urllib.request.Request(url, method="POST", data=b"")
        # 本机回环不要走系统代理（有些环境把 HTTP_PROXY 指向公司代理，会连到代理上而不是本机服务）
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=3) as resp:
            payload = json.loads(resp.read().decode("utf-8") or "{}")
        if payload.get("ok"):
            print("  已通知 %d 端口上的文献库停止运行（数据已保存）。" % config.port)
            return 0
        print("  停止失败：服务返回 %s" % payload, file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - 连不上就是没在跑，不算错误
        print("  %d 端口上没有正在运行的文献库，无需停止。（%s）" % (config.port, exc))
        return 0
