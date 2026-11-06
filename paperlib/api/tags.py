"""标签端点：列表 / 重命名 / 删除。"""
from __future__ import annotations


def list_tags(ctx):
    ctx.json(200, {"ok": True, "tags": ctx.services.library.tags()})


def rename(ctx):
    body = ctx.read_json() or {}
    source = body.get("from")
    target = body.get("to")
    if not str(source or "").strip() or not str(target or "").strip():
        return ctx.fail(400, "标签名不能为空")
    try:
        result = ctx.services.library.rename_tag(source, target)
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    ctx.json(200, {"ok": True, "result": result})


def remove(ctx):
    body = ctx.read_json() or {}
    name = body.get("name")
    if not str(name or "").strip():
        return ctx.fail(400, "标签名不能为空")
    try:
        result = ctx.services.library.remove_tag(name)
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    ctx.json(200, {"ok": True, "result": result})
