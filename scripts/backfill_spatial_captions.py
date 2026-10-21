# -*- coding: utf-8 -*-
"""
backfill_spatial_captions.py
使用基于 Bbox 空间近邻的图注匹配算法，对现有数据库中图注为空或缺失的图片进行精准回填。
"""
import os
import sys
import sqlite3
import fitz

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from caption_matcher import match_page_images_with_captions

DB_PATH = os.path.join(BASE_DIR, 'data', 'papers.db')


def backfill_captions():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # 查出所有有 abs_path 的文献
    c.execute("""
        SELECT DISTINCT p.id, p.abs_path, p.title
        FROM papers p
        JOIN figures f ON f.paper_id = p.id
        WHERE (f.caption IS NULL OR TRIM(f.caption) = '') AND p.abs_path != ''
    """)
    papers_to_fix = c.fetchall()
    print(f"待处理文献篇数（含有未匹配图注）: {len(papers_to_fix)}", flush=True)

    total_updated = 0
    papers_improved = 0

    for idx, (pid, path, title) in enumerate(papers_to_fix, 1):
        if not os.path.exists(path):
            continue

        try:
            doc = fitz.open(path)
        except Exception:
            continue

        # 如果文献页数超过 60 页，通常是专著或教材，只处理前 60 页
        max_pages = min(len(doc), 60)

        # 获取该文献在数据库中的所有 figures
        c.execute("""
            SELECT id, fig_index, image_path, caption
            FROM figures
            WHERE paper_id = ?
            ORDER BY fig_index ASC
        """, (pid,))
        db_figs = c.fetchall()

        fig_map_caption = {}
        fig_idx_counter = 0

        for page_num in range(max_pages):
            page = doc[page_num]
            try:
                geo_caps = match_page_images_with_captions(page)
            except Exception:
                geo_caps = {}

            image_list = page.get_images(full=True)
            for img_info in image_list:
                xref = img_info[0]
                try:
                    base = doc.extract_image(xref)
                    img_bytes = base.get('image', b'')
                    if len(img_bytes) < 3072:
                        continue
                    fig_idx_counter += 1
                    if xref in geo_caps:
                        fig_map_caption[fig_idx_counter] = geo_caps[xref]
                except Exception:
                    continue

        doc.close()

        # 更新数据库
        updated_in_paper = 0
        for fig_id, fig_index, img_path, old_cap in db_figs:
            if (not old_cap or not old_cap.strip()) and fig_index in fig_map_caption:
                new_cap = fig_map_caption[fig_index]
                if new_cap and len(new_cap.strip()) > 3:
                    c.execute("UPDATE figures SET caption = ? WHERE id = ?", (new_cap, fig_id))
                    updated_in_paper += 1

        if updated_in_paper > 0:
            total_updated += updated_in_paper
            papers_improved += 1
            conn.commit()
            print(f"[{idx}/{len(papers_to_fix)}] {title[:35]}... -> 成功回填 {updated_in_paper} 个图注", flush=True)

    conn.commit()
    conn.close()

    print(f"\n==========================================", flush=True)
    print(f"回填完成！共改善 {papers_improved} 篇文献，新增精准几何图注 {total_updated} 个！", flush=True)


if __name__ == '__main__':
    backfill_captions()
