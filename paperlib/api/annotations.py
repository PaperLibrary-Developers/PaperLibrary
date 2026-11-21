"""原文与补充材料 PDF 的荧光笔和便签批注端点。

  GET /api/papers/:id/annotations   读这篇文献的全部批注
  PUT /api/papers/:id/annotations   { items: [...] } 整表覆盖（一次请求写完，最原子）

为什么不新开一张表：
  批注只是「一篇文章的一坨 JSON」，量很小（几十条），也没有跨文献查询的需求。
  analysis 表（paper_id, kind, payload）本来就是干这个的，已有
  get_analysis / save_analysis 访问器，删文献时还会跟着一起删
  （repository.delete_paper 会清掉该 paper 的所有 analysis 行）。
  所以这里只用一个 kind = "pdf-annotations"，不动 schema、不改卡片形状、
  不让批注混进 GET /api/papers/:id 的响应里。

坐标一律存「相对该页的 0~1 归一化值」：
  阅读器缩放、适宽/适页、换屏幕、窗口拉伸都会改变页面像素尺寸，
  存归一化坐标就只需要百分比定位，重排时不用重算，也不会越用越歪。
  旋转页同样成立（矩形是在屏幕上量出来的，量和画用的是同一套页面盒子）。

颜色存 #rrggbb：前端带调色盘，用户可以自己加颜色；老的键名（yellow/green/blue/pink）
在规整时折算成色值，所以库里只有一种形状。

本层只做「校验入参 → 读写」；几何计算全在前端（public/js/pdfviewer.mjs）。
"""
from __future__ import annotations

import math
import re

from paperlib.api.paper_scope import require_paper
from paperlib.util import now_iso

KIND = "pdf-annotations"

BODY_LIMIT = 512 * 1024        # 批注 JSON 上限（正常几十 KB，给足余量）
MAX_ITEMS = 800                # 单篇最多存这么多条，防前端死循环把库撑爆
MAX_RECTS = 120                # 一条荧光笔最多这么多矩形（每行一个，正常远小于此）
ID_MAX = 64
QUOTE_MAX = 600                # 引文：只用于列表里辨认，不存全文
NOTE_MAX = 4000                # 便签正文
TYPES = ("highlight", "note")  # 荧光笔 / 便签
# 颜色：老数据存的是键名（yellow/green/blue/pink），现在也允许任意 #rrggbb
# （前端有调色盘，用户能自己加颜色）。键名一律折算成色值再存，落库只有一种形状。
COLOR_KEYS = {
    "yellow": "#f2c433",
    "green": "#3f9e77",
    "blue": "#4f8fd6",
    "pink": "#dd7a9d",
}
DEFAULT_COLOR = COLOR_KEYS["yellow"]
_HEX_RE = re.compile(r"^#([0-9a-f]{6}|[0-9a-f]{3})$")
ISO_MAX = 40

_NOT_FOUND = "找不到这条文献"


# ------------------------------------------------------------------ 规整
def _finite(value, fallback=0.0):
    """任意输入 → 有限浮点；NaN / Inf / 非数字一律走兜底。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return fallback
    return number if math.isfinite(number) else fallback


def _unit(value, fallback=0.0):
    """夹到 [0, 1]：坐标是页面内的百分比，越界只会让标记飞到页面外。"""
    return min(1.0, max(0.0, _finite(value, fallback)))


def _one_line(value, limit):
    """引文：折叠所有空白成单行，列表里才好显示。"""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _color(value):
    """颜色规整成小写 #rrggbb。

    接受三种输入：老的键名（yellow…）、6 位 / 3 位十六进制。认不出来退回默认黄，
    保证一条脏数据不会让整页批注白掉。
    """
    raw = str(value or "").strip().lower()
    if raw in COLOR_KEYS:
        return COLOR_KEYS[raw]
    if _HEX_RE.match(raw):
        if len(raw) == 4:            # #abc → #aabbcc
            return "#" + raw[1] * 2 + raw[2] * 2 + raw[3] * 2
        return raw
    return DEFAULT_COLOR


def _block(value, limit):
    """便签正文：保留换行（用户可能分行写），只裁长度。"""
    text = str(value or "").replace("\r\n", "\n").strip()
    return text[:limit]


def _rects(raw):
    """矩形数组 → 只留宽高为正、坐标夹在页面内的那些。"""
    out = []
    if not isinstance(raw, (list, tuple)):
        return out
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        width = _unit(entry.get("w"))
        height = _unit(entry.get("h"))
        if width <= 0 or height <= 0:
            continue
        out.append({"x": _unit(entry.get("x")), "y": _unit(entry.get("y")), "w": width, "h": height})
        if len(out) >= MAX_RECTS:
            break
    return out


def _point(raw, rects):
    """便签锚点：没有 point 就退回第一个矩形的左上角。"""
    if isinstance(raw, dict):
        return {"x": _unit(raw.get("x")), "y": _unit(raw.get("y"))}
    if rects:
        return {"x": rects[0]["x"], "y": rects[0]["y"]}
    return None


def _norm_item(raw, stamp=False):
    """一条批注 → 合法形状；认不出来的返回 None（宁可丢一条，也不要坏一整页）。"""
    if not isinstance(raw, dict):
        return None
    kind = str(raw.get("type") or "").strip()
    if kind not in TYPES:
        return None
    ident = str(raw.get("id") or "").strip()[:ID_MAX]
    if not ident:
        return None

    rects = _rects(raw.get("rects"))
    if kind == "highlight" and not rects:
        return None                     # 没有矩形的高亮画不出来，等于空记录
    # 只有便签需要落点；高亮存 rects 就够了，多存一个 point 只是白占体积
    point = None
    if kind == "note":
        point = _point(raw.get("point"), rects)
        if not point:
            return None                 # 便签必须有个落点

    page = int(_finite(raw.get("page"), 1))
    if page < 1:
        page = 1
    color = _color(raw.get("color"))

    created_at = str(raw.get("createdAt") or "")[:ISO_MAX]
    updated_at = now_iso() if stamp else (str(raw.get("updatedAt") or "")[:ISO_MAX] or created_at)
    return {
        "id": ident,
        "type": kind,
        "page": page,
        "color": color,
        "rects": rects,
        "point": point,
        "quote": _one_line(raw.get("quote"), QUOTE_MAX),
        "note": _block(raw.get("note"), NOTE_MAX),
        "createdAt": created_at,
        "updatedAt": updated_at,
    }


# ------------------------------------------------------------------ 端点
def attachment_annotation_kind(file):
    return KIND + ":attachment:" + file


def _document_kind(ctx, paper):
    file = (ctx.params or {}).get("file")
    if file is None:
        return KIND
    if not any(item.get("file") == file for item in paper.get("attachments") or []):
        ctx.fail(404, "找不到这个附件")
        return None
    if not file.lower().endswith(".pdf"):
        ctx.fail(400, "只有 PDF 附件支持批注")
        return None
    return attachment_annotation_kind(file)


def get_annotations(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    kind = _document_kind(ctx, paper)
    if kind is None:
        return None
    payload = ctx.services.db.repo.get_analysis(paper.get("id"), kind) or {}
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    items = []
    for raw in raw_items if isinstance(raw_items, list) else []:
        item = _norm_item(raw)
        if item:
            items.append(item)
    updated_at = str(payload.get("updatedAt") or "") if isinstance(payload, dict) else ""
    return ctx.json(200, {"ok": True, "items": items, "updatedAt": updated_at})


def put_annotations(ctx):
    paper = require_paper(ctx)
    if not paper:
        return None
    try:
        body = ctx.read_json(BODY_LIMIT)
    except ValueError as exc:
        return ctx.fail(400, str(exc))
    if not isinstance(body, dict):
        return ctx.fail(400, "请求体必须是 JSON 对象")

    raw_items = body.get("items")
    if raw_items is None:
        raw_items = []
    if not isinstance(raw_items, list):
        return ctx.fail(400, "items 必须是数组")
    if len(raw_items) > MAX_ITEMS:
        return ctx.fail(400, "批注太多了（单篇上限 %d 条）" % MAX_ITEMS)

    items = []
    seen = set()
    for raw in raw_items:
        item = _norm_item(raw, stamp=True)
        if not item or item["id"] in seen:
            continue                    # 坏数据 / 重复 id：跳过，不整单失败
        seen.add(item["id"])
        items.append(item)

    payload = {"items": items, "updatedAt": now_iso()}
    store = ctx.services.db
    with store.paper_writes():
        current = require_paper(ctx)
        if not current:
            return None
        kind = _document_kind(ctx, current)
        if kind is None:
            return None
        store.repo.save_analysis(current.get("id"), kind, payload)
    return ctx.json(200, {"ok": True, "items": items, "updatedAt": payload["updatedAt"]})
