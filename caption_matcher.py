# -*- coding: utf-8 -*-
"""
基于空间几何（Bounding Box Proximity）的学术论文图注精准匹配器。
结合 PyMuPDF 的 page.get_image_info(xrefs=True) 与 page.get_text("blocks")，
通过空间拓扑距离与图注特征（Figure/Fig/Scheme/Chart/图）将页面内的图像与图注智能绑定。
"""
import re
import fitz

# 匹配学术图注起首模式：Figure 1, Fig. 2a, Scheme 1, Chart 3, 图 1 等
FIGURE_CAPTION_PATTERN = re.compile(
    r'^(?:(?:Figure|Fig|Scheme|Chart)\.?\s*(\d+[a-zA-Z\-]*)|(?:图|示意图|附图)\s*(\d+[a-zA-Z\-]*))[:.\s]',
    re.IGNORECASE
)

# 粗略兜底匹配（若非起首，但块内明显含有图注标记）
FIGURE_INLINE_PATTERN = re.compile(
    r'(?:(?:Figure|Fig|Scheme|Chart)\.?\s*\d+[a-zA-Z\-]*|(?:图|示意图|附图)\s*\d+[a-zA-Z\-]*)[:.\s]',
    re.IGNORECASE
)


def extract_page_captions_with_boxes(page):
    """
    提取一页内所有可能的图注文本块及其几何边界框 (bbox)。
    返回列表: [ {'bbox': (x0, y0, x1, y1), 'text': str, 'label': str}, ... ]
    """
    caption_candidates = []
    blocks = page.get_text("blocks")
    for b in blocks:
        # b: (x0, y0, x1, y1, text, block_no, block_type)
        if len(b) < 5 or b[6] != 0:  # 0 为文本块，1 为图像块
            continue
        raw_text = b[4].strip()
        if not raw_text or len(raw_text) < 5:
            continue

        clean_text = raw_text.replace('\n', ' ')
        # 尝试匹配起首
        m = FIGURE_CAPTION_PATTERN.match(clean_text)
        if m:
            label = m.group(1) or m.group(2) or ''
            caption_candidates.append({
                'bbox': (b[0], b[1], b[2], b[3]),
                'text': clean_text[:350],
                'label': label,
                'is_head': True
            })
            continue

        # 若未起首但在前 30 个字符内出现 Fig./Figure/Scheme
        inline_m = FIGURE_INLINE_PATTERN.search(clean_text[:50])
        if inline_m:
            caption_candidates.append({
                'bbox': (b[0], b[1], b[2], b[3]),
                'text': clean_text[inline_m.start():][:350],
                'label': '',
                'is_head': False
            })

    return caption_candidates


def compute_bbox_distance(img_bbox, cap_bbox):
    """
    计算图片与图注之间的几何排版契合度打分（分数越高越契合）。
    学术排版规则：
    1. 图注绝大多数位于图片正下方（垂直方向 cap_y0 >= img_y1 - 10 且间距一般在 5~150 pt）。
    2. 少数图注或表格位于图片正上方（cap_y1 <= img_y0 + 10 且间距在 5~80 pt）。
    3. 水平中心尽量重叠或相近。
    """
    ix0, iy0, ix1, iy1 = img_bbox
    cx0, cy0, cx1, cy1 = cap_bbox

    img_cx = (ix0 + ix1) / 2.0
    cap_cx = (cx0 + cx1) / 2.0
    h_overlap = max(0, min(ix1, cx1) - max(ix0, cx0))
    h_dist = abs(img_cx - cap_cx)

    # 1. 图注在图片下方
    v_gap_down = cy0 - iy1
    if -15 <= v_gap_down <= 200:
        # 正下方最优
        score = 1000.0 - abs(v_gap_down) * 2.0 - (0 if h_overlap > 10 else h_dist * 0.8)
        return score

    # 2. 图注在图片上方
    v_gap_up = iy0 - cy1
    if -15 <= v_gap_up <= 100:
        score = 700.0 - abs(v_gap_up) * 3.0 - (0 if h_overlap > 10 else h_dist * 1.0)
        return score

    # 3. 距离太远（>250pt）不视为该图图注
    return -9999.0


def match_page_images_with_captions(page):
    """
    为一页内的所有有效图片匹配最近的图注。
    返回字典: { xref: caption_text }
    """
    img_infos = page.get_image_info(xrefs=True)
    if not img_infos:
        return {}

    captions = extract_page_captions_with_boxes(page)
    if not captions:
        return {}

    # 过滤掉过小尺寸（可能是图标、角标、分隔线）
    valid_imgs = []
    for info in img_infos:
        xref = info.get('xref')
        bbox = info.get('bbox')
        if not bbox:
            continue
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if xref and w >= 40 and h >= 40:
            valid_imgs.append((xref, bbox))

    if not valid_imgs:
        return {}

    # 构建候选打分对
    score_pairs = []
    for xref, ibbox in valid_imgs:
        for c_idx, cap in enumerate(captions):
            sc = compute_bbox_distance(ibbox, cap['bbox'])
            if sc > 0:
                score_pairs.append((sc, xref, c_idx))

    # 按契合度降序贪心分配（每个图注最多匹配一个主图，每张图最多匹配一个图注）
    score_pairs.sort(key=lambda x: x[0], reverse=True)
    assigned_imgs = {}
    used_caps = set()

    for sc, xref, c_idx in score_pairs:
        if xref not in assigned_imgs and c_idx not in used_caps:
            assigned_imgs[xref] = captions[c_idx]['text']
            used_caps.add(c_idx)

    return assigned_imgs
