# -*- coding: utf-8 -*-
"""预热缩略图缓存：把列表页首屏要用到的缩略图先生成好。

首次生成一张约 28 ms，整库首屏 537 张要 15 秒 —— 不该让用户在打开页面时等。
预生成之后，页面加载直接命中缓存（每张 <1 ms）。

用法：
  python scripts/warm_thumbs.py          # 只生成列表页首屏需要的（每篇前 N 张）
  python scripts/warm_thumbs.py --all    # 全库 4566 张都生成（较慢）
"""
import os
import sys
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

import database            # noqa: E402
import thumbs              # noqa: E402

PER_PAPER = 4              # 列表页每篇显示的图片数（与 app.py 的 limit 一致）


def main():
    want_all = "--all" in sys.argv
    thumbs.clear_cache(APP)
    papers = database.get_all_papers()
    fm = database.get_figures_map([p["id"] for p in papers])

    todo = []
    for p in papers:
        figs = fm.get(p["id"]) or []
        picked = figs if want_all else figs[:PER_PAPER]
        for f in picked:
            ip = f.get("image_path", "")
            fp = ip if os.path.isabs(ip) else os.path.join(APP, ip)
            if os.path.isfile(fp):
                todo.append(fp)

    print(f"待生成缩略图: {len(todo)} 张（{'全库' if want_all else f'每篇前 {PER_PAPER} 张'}）")
    t0 = time.perf_counter()
    ok = fail = 0
    before = after = 0
    for i, fp in enumerate(todo, 1):
        try:
            before += os.path.getsize(fp)
        except Exception:
            pass
        t = thumbs.make_thumb(fp, APP)
        if t:
            ok += 1
            try:
                after += os.path.getsize(t)
            except Exception:
                pass
        else:
            fail += 1
        if i % 50 == 0 or i == len(todo):
            el = time.perf_counter() - t0
            print(f"  {i}/{len(todo)}  已用 {el:.1f}s  "
                  f"（{el/max(i,1)*1000:.0f} ms/张）", flush=True)

    st = thumbs.cache_stats(APP)
    print()
    print(f"完成: 成功 {ok}，失败 {fail}")
    if before:
        print(f"原图合计 {before/1024/1024:.1f} MB → 缩略图缓存 {after/1024/1024:.1f} MB"
              f"（磁盘实际 {st['bytes']/1024/1024:.1f} MB）")
    print(f"耗时 {time.perf_counter()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
