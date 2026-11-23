"""原文目录分类端点。

  GET    /api/folders              → 扁平列表 + 计数（前端拼树）
  POST   /api/folders              → 新建（{path} 或 {parent,name}；已存在则幂等返回）
  PATCH  /api/folders              → 重命名（{path, name}），整棵子树一起改
  DELETE /api/folders?path=A/B     → 删除（含子文件夹；文献上移到父级，不删文献）
  POST   /api/folders/move         → 把文献移到某个文件夹（{ids, path}）
  PUT    /api/folders/order        → 保存同级顺序（{paths}）
"""
from __future__ import annotations

from paperlib.domain.folders import FolderError


def _folders(ctx):
    return ctx.services.folders


def _fail(ctx, exc):
    return ctx.fail(getattr(exc, "status", 400), str(exc))


def list_folders(ctx):
    data = _folders(ctx).list()
    ctx.json(200, dict({"ok": True}, **data))


def create_folder(ctx):
    body = ctx.read_json() or {}
    try:
        created = _folders(ctx).create(
            path=body.get("path"), parent=body.get("parent"), name=body.get("name"))
    except FolderError as exc:
        return _fail(ctx, exc)
    return ctx.json(201 if created.get("created") else 200, {"ok": True, "folder": created})


def rename_folder(ctx):
    body = ctx.read_json() or {}
    try:
        result = _folders(ctx).rename(body.get("path"), body.get("name"))
    except FolderError as exc:
        return _fail(ctx, exc)
    return ctx.json(200, dict({"ok": True}, **result))


def delete_folder(ctx):
    path = ctx.query("path")
    try:
        result = _folders(ctx).delete(path)
    except FolderError as exc:
        return _fail(ctx, exc)
    return ctx.json(200, result)


def move_papers(ctx):
    body = ctx.read_json() or {}
    ids = body.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    if not ids:
        return ctx.fail(400, "先选中要移动的文献")
    try:
        result = _folders(ctx).move_papers(ids, body.get("path"))
    except FolderError as exc:
        return _fail(ctx, exc)
    return ctx.json(200, result)


def reorder_folders(ctx):
    try:
        body = ctx.read_json()
        _folders(ctx).reorder(body.get("paths") if isinstance(body, dict) else None)
    except ValueError as exc:
        return _fail(ctx, exc)
    return ctx.json(200, {"ok": True})
