# -*- coding: utf-8 -*-
"""基于空间几何（Bounding Box Proximity）的图注精准匹配器。

移植自 旧 PaperLibrary 工程\\caption_matcher.py
契约名：caption_matcher（match_boxes_with_captions / extract_page_captions_with_boxes）

算法与 PaperLibrary 完全一致：
  1. 用 page.get_text("blocks") 收集一页内所有图注候选块（Figure/Fig/Scheme/Chart/图 + 编号）；
  2. 用 page.get_image_info(xrefs=True) 拿每张图片的 bbox；
  3. 图注绝大多数在图片正下方（5~200pt），少数在正上方（5~100pt），水平中心尽量重叠；
  4. 按契合度降序贪心分配：一个图注最多配一张图，一张图最多配一个图注。

与旧版的差异：把「打分 + 贪心分配」抽成 match_boxes_with_captions，供矢量图裁切复用。
"""
from __future__ import annotations

import re

# 匹配学术图注起首模式：Figure 1, Fig. 2a, Scheme 1, Chart 3, 图 1 等
FIGURE_CAPTION_PATTERN = re.compile(
    r"^(?:(?:Figure|Fig|Scheme|Chart|Table)\.?\s*(S?\d+[a-zA-Z\-]*)|(?:图|示意图|附图|表)\s*(S?\d+[a-zA-Z\-]*))[:.：。\s]",
    re.IGNORECASE,
)

# 粗略兜底匹配（若非起首，但块内明显含有图注标记）
FIGURE_INLINE_PATTERN = re.compile(
    r"(?:(?:Figure|Fig|Scheme|Chart)\.?\s*\d+[a-zA-Z\-]*|(?:图|示意图|附图)\s*\d+[a-zA-Z\-]*)[:.\s]",
    re.IGNORECASE,
)

CAPTION_MAX_CHARS = 350
MIN_BOX = 40  # 小于这个边长的块基本是图标 / 角标 / 分隔线


def extract_page_captions_with_boxes(page):
    """提取一页内所有可能的图注文本块及其几何边界框。

    返回 [{'bbox': (x0, y0, x1, y1), 'text': str, 'label': str, 'is_head': bool}, ...]
    """
    captions = []
    try:
        blocks = page.get_text("blocks")
    except Exception:  # noqa: BLE001 - 页面异常不应中断整篇提取
        return captions
    for block in blocks:
        if len(block) < 7 or block[6] != 0:  # 0 = 文本块，1 = 图像块
            continue
        raw = str(block[4] or "").strip()
        if len(raw) < 5:
            continue
        text = re.sub(r"\s+", " ", raw.replace("\n", " "))
        bbox = (float(block[0]), float(block[1]), float(block[2]), float(block[3]))
        head = FIGURE_CAPTION_PATTERN.match(text)
        if head:
            captions.append({
                "bbox": bbox,
                "text": text[:CAPTION_MAX_CHARS],
                "label": head.group(1) or head.group(2) or "",
                "is_head": True,
            })
            continue
        inline = FIGURE_INLINE_PATTERN.search(text[:50])
        if inline:
            captions.append({
                "bbox": bbox,
                "text": text[inline.start():][:CAPTION_MAX_CHARS],
                "label": "",
                "is_head": False,
            })
    return captions


def compute_bbox_distance(img_bbox, cap_bbox):
    """图片与图注的排版契合度打分（越高越契合；-9999 表示不视为该图图注）。"""
    ix0, iy0, ix1, iy1 = [float(v) for v in img_bbox]
    cx0, cy0, cx1, cy1 = [float(v) for v in cap_bbox]

    img_cx = (ix0 + ix1) / 2.0
    cap_cx = (cx0 + cx1) / 2.0
    h_overlap = max(0.0, min(ix1, cx1) - max(ix0, cx0))
    h_dist = abs(img_cx - cap_cx)

    # 1. 图注在图片下方
    gap_down = cy0 - iy1
    if -15 <= gap_down <= 200:
        return 1000.0 - abs(gap_down) * 2.0 - (0.0 if h_overlap > 10 else h_dist * 0.8)

    # 2. 图注在图片上方
    gap_up = iy0 - cy1
    if -15 <= gap_up <= 100:
        return 700.0 - abs(gap_up) * 3.0 - (0.0 if h_overlap > 10 else h_dist * 1.0)

    # 3. 距离太远，不视为该图图注
    return -9999.0


def match_boxes_with_captions(boxes, captions):
    """贪心分配：boxes = [(key, bbox)]，captions 为 extract_page_captions_with_boxes 的返回。

    返回 {key: caption_text}。
    """
    if not boxes or not captions:
        return {}
    pairs = []
    for key, bbox in boxes:
        for idx, cap in enumerate(captions):
            if min(bbox[2], cap["bbox"][2]) <= max(bbox[0], cap["bbox"][0]):
                continue
            score = compute_bbox_distance(bbox, cap["bbox"])
            if score > 0:
                pairs.append((score, key, idx))
    pairs.sort(key=lambda item: item[0], reverse=True)

    assigned = {}
    used = set()
    for _score, key, idx in pairs:
        if key in assigned or idx in used:
            continue
        assigned[key] = captions[idx]["text"]
        used.add(idx)
    return assigned


def match_page_images_with_captions(page, captions=None):
    """为一页内的所有有效图片匹配最近的图注，返回 {xref: caption_text}。"""
    try:
        infos = page.get_image_info(xrefs=True) or []
    except Exception:  # noqa: BLE001
        return {}
    if captions is None:
        captions = extract_page_captions_with_boxes(page)
    if not infos or not captions:
        return {}

    boxes = []
    for info in infos:
        xref = info.get("xref")
        bbox = info.get("bbox")
        if not xref or not bbox:
            continue
        if (bbox[2] - bbox[0]) < MIN_BOX or (bbox[3] - bbox[1]) < MIN_BOX:
            continue
        if (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) >= 0.85 * page.rect.get_area():
            continue
        boxes.append((int(xref), tuple(float(v) for v in bbox)))
    return match_boxes_with_captions(boxes, captions)
