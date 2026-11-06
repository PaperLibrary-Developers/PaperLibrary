"""占位处理器：模块骨架期用，实现后替换掉。

保留这个工具是为了让「还没实现的端点」给出明确 501，而不是把 500 堆栈丢给前端。
"""
from __future__ import annotations


def todo(name):
    def handler(ctx):
        raise NotImplementedError("待实现：" + name)

    handler.__name__ = str(name).replace(".", "_")
    return handler
