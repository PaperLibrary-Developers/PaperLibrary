"""表驱动路由（对应 Node 的 server/http/router.js）。

一条路由：{ name, methods, pattern, handler }。
  methods: tuple/list，含 '*' 表示任何方法（设置类端点沿用 405 语义）。
  pattern: '/api/papers/:id/pdf'，':name' 匹配一段路径（不做 URL 解码）。
  handler(ctx)。

匹配顺序 = 列表顺序：静态段必须排在 :id 前面（routes.py 已排好）。
"""
from __future__ import annotations

import re


def compile_pattern(pattern: str):
    keys = []
    parts = []
    for segment in pattern.split("/"):
        if segment.startswith(":"):
            keys.append(segment[1:])
            parts.append("([^/]+)")
        else:
            parts.append(re.escape(segment))
    return re.compile("^" + "/".join(parts).rstrip("/") + "/?$"), keys


class Router:
    def __init__(self, routes, services):
        self.services = services
        self.compiled = []
        for route in routes:
            methods = route.get("methods") or route.get("method") or ("GET",)
            if isinstance(methods, str):
                methods = (methods,)
            rex, keys = compile_pattern(route["pattern"])
            self.compiled.append({
                "name": route.get("name") or route["pattern"],
                "methods": tuple(methods),
                "pattern": route["pattern"],
                "handler": route["handler"],
                "re": rex,
                "keys": keys,
            })

    def match(self, method: str, pathname: str):
        for route in self.compiled:
            m = route["re"].match(pathname)
            if not m:
                continue
            if "*" not in route["methods"] and method not in route["methods"]:
                continue
            params = {key: m.group(i + 1) for i, key in enumerate(route["keys"])}
            return route, params
        return None, None

    def handle(self, ctx):
        route, params = self.match(ctx.method, ctx.pathname)
        if route is None:
            return ctx.fail(404, "接口不存在：" + ctx.pathname)
        ctx.params = params
        ctx.route_name = route["name"]
        ctx.services = self.services
        if ctx.method == "HEAD" and "HEAD" not in route["methods"] and "*" not in route["methods"]:
            # 与 Node 一致：未声明 HEAD 的方法不匹配 → 落到「接口不存在」
            return ctx.fail(404, "接口不存在：" + ctx.pathname)
        try:
            route["handler"](ctx)
        except (BrokenPipeError, ConnectionResetError):
            raise
        except Exception as exc:  # noqa: BLE001 - HTTP 层兜底，不泄漏堆栈
            import traceback

            traceback.print_exc()
            if not ctx.started:
                try:
                    ctx.fail(500, str(exc) or "服务器内部错误")
                except Exception:
                    pass
            else:
                try:
                    ctx.end_stream()
                except Exception:
                    pass
