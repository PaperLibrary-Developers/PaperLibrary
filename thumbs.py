# -*- coding: utf-8 -*-
"""thumbs.py — 图表缩略图缓存

为什么需要：库里 4566 张图共 **882 MB**（最大单张 7.6 MB）。卡片预览直接把原图交给
浏览器，首屏 537 张就是 **114 MB** —— 这才是「每次打开网页要等很久」的主因。

做法：按需生成固定尺寸的 JPEG 缩略图缓存到 data/thumb_cache/，用原图路径的哈希命名，
并用「原图 mtime」判断是否过期。缩略图用居中裁剪填满，尺寸参差的原图在网格里也整齐。
"""

import hashlib
import os

try:
    from PIL import Image
except Exception:                                    # pragma: no cover
    Image = None

THUMB_SIZE = (480, 360)
QUALITY = 82


def _cache_dir(app_dir: str) -> str:
    return os.path.join(app_dir, "data", "thumb_cache")


def _cache_path(app_dir: str, src: str, mtime: float, size=None) -> str:
    key = f"{os.path.abspath(src)}|{round(float(mtime), 3)}|{size[0]}x{size[1]}"
    name = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32] + ".jpg"
    return os.path.join(_cache_dir(app_dir), name)


def make_thumb(src: str, app_dir: str, size=THUMB_SIZE, quality=QUALITY):
    """返回缩略图路径；生成失败或 Pillow 不可用时返回 None（调用方回退原图）。"""
    if Image is None:
        return None
    if not src or not os.path.isfile(src):
        return None
    try:
        st = os.stat(src)
    except Exception:
        return None

    dest = _cache_path(app_dir, src, st.st_mtime, size)
    if os.path.isfile(dest) and os.path.getsize(dest) > 0:
        return dest                                  # 命中缓存
    try:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with Image.open(src) as im:
            im = im.convert("RGB")                   # 统一色彩模式（含 PNG 透明底）
            tw, th = size
            # 居中裁剪到目标比例，再缩放 —— 尺寸再参差，网格里也整齐
            sw, sh = im.size
            if sw <= 0 or sh <= 0:
                return None
            target_ratio = tw / th
            cur_ratio = sw / sh
            if cur_ratio > target_ratio:
                new_w = int(sh * target_ratio)
                left = (sw - new_w) // 2
                im = im.crop((left, 0, left + new_w, sh))
            else:
                new_h = int(sw / target_ratio)
                top = (sh - new_h) // 2
                im = im.crop((0, top, sw, top + new_h))
            im = im.resize(size, Image.LANCZOS)
            tmp = dest + ".tmp"
            im.save(tmp, "JPEG", quality=quality, optimize=True)
            os.replace(tmp, dest)
        return dest
    except Exception:
        return None


def make_many(paths, app_dir: str, size=THUMB_SIZE):
    """批量生成，返回 {原图路径: 缩略图路径}；失败的条目不在结果里。"""
    out = {}
    for p in paths or []:
        t = make_thumb(p, app_dir, size=size)
        if t:
            out[p] = t
    return out


def cache_stats(app_dir: str):
    d = _cache_dir(app_dir)
    n = 0
    total = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            fp = os.path.join(d, f)
            if os.path.isfile(fp):
                n += 1
                total += os.path.getsize(fp)
    return {"files": n, "bytes": total}


def clear_cache(app_dir: str) -> int:
    d = _cache_dir(app_dir)
    n = 0
    if os.path.isdir(d):
        for f in os.listdir(d):
            try:
                os.remove(os.path.join(d, f))
                n += 1
            except Exception:
                pass
    return n
