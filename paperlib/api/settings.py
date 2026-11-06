"""设置类端点：prefs / ai / sources / websearch（方法不匹配回 405）。"""
from __future__ import annotations


def _method_guard(ctx, allowed):
    if ctx.method not in allowed:
        ctx.fail(405, "不支持的请求方法")
        return False
    return True


def prefs(ctx):
    if not _method_guard(ctx, ("GET", "PUT", "POST")):
        return
    if ctx.method == "GET":
        return ctx.json(200, {"ok": True, "prefs": ctx.services.prefs.read()})
    patch = ctx.read_json() or {}
    if not isinstance(patch, dict):
        return ctx.fail(400, "请求体必须是对象")
    return ctx.json(200, {"ok": True, "prefs": ctx.services.prefs.merge(patch)})


def ai_settings(ctx):
    if not _method_guard(ctx, ("GET", "PUT", "POST")):
        return
    llm = ctx.services.llm
    if ctx.method == "GET":
        return ctx.json(200, {"ok": True, "settings": llm.public_settings(), "ready": llm.ready()})
    patch = ctx.read_json() or {}
    if not isinstance(patch, dict):
        return ctx.fail(400, "请求体必须是对象")
    llm.update(patch)
    return ctx.json(200, {"ok": True, "settings": llm.public_settings(), "ready": llm.ready()})


def sources_settings(ctx):
    if not _method_guard(ctx, ("GET", "PUT", "POST")):
        return
    sources = ctx.services.sources
    if ctx.method == "GET":
        return ctx.json(200, {"ok": True, "settings": sources.public_settings()})
    patch = ctx.read_json() or {}
    if not isinstance(patch, dict):
        return ctx.fail(400, "请求体必须是对象")
    sources.update(patch)
    return ctx.json(200, {"ok": True, "settings": sources.public_settings()})


def websearch_settings(ctx):
    if not _method_guard(ctx, ("GET", "PUT", "POST")):
        return
    websearch = ctx.services.websearch
    if ctx.method == "GET":
        return ctx.json(200, {"ok": True, "settings": websearch.public_settings(),
                              "ready": websearch.ready()})
    patch = ctx.read_json() or {}
    if not isinstance(patch, dict):
        return ctx.fail(400, "请求体必须是对象")
    websearch.update(patch)
    return ctx.json(200, {"ok": True, "settings": websearch.public_settings(),
                          "ready": websearch.ready()})
