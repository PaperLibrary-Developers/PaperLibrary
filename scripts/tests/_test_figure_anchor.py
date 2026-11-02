# -*- coding: utf-8 -*-
"""
scripts/_test_figure_anchor.py
测试图文双向锚定引擎 (figure_anchor.py)
1. 校验 extract_figure_identity 图号解析算法（Fig, Figure, Scheme, Table 等）
2. 在真实数据库与本地 PDF 上测试正文定位与页码提取
3. 校验离线学术结论规则抽取
"""

import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import figure_anchor


def test_figure_identity_extraction():
    print("=== 1. 测试图表身份指纹提取 ===")
    cases = [
        ("Figure 1. SEM images of MoS2 nanosheets.", "Figure", ("Fig", "1")),
        ("Fig. 2a (a) XRD patterns and (b) Raman spectra.", "Figure", ("Fig", "2a")),
        ("Scheme 1: Synthetic route of the hybrid catalyst.", "Scheme", ("Scheme", "1")),
        ("Table 2. Summary of electrochemical parameters.", "Table", ("Table", "2")),
        ("FIG. 3 (a) Cyclic voltammetry curves.", "Figure", ("Fig", "3")),
        ("Fig 4 | Cross-sectional TEM image.", "Figure", ("Fig", "4")),
    ]
    for cap, ft, (expected_pfx, expected_num) in cases:
        ident = figure_anchor.extract_figure_identity(cap, ft)
        assert ident, f"Failed to extract ident for: {cap}"
        assert ident["prefix"] == expected_pfx, f"Prefix mismatch for {cap}: got {ident['prefix']}, expected {expected_pfx}"
        assert ident["num"] == expected_num, f"Num mismatch for {cap}: got {ident['num']}, expected {expected_num}"
        print(f"  [OK] {cap[:30]}... -> {ident['label']}")
    print("图表身份指纹测试全部通过!\n")


def test_real_paper_mentions():
    print("=== 2. 测试真实文献中的正文引用提取 ===")
    conn = database.get_conn()
    # 找一篇有本地 PDF 且有图注带 Fig/Figure 的文献
    row = conn.execute("""
        SELECT p.id, p.title, p.abs_path, f.id as fig_id, f.caption, f.figure_type
        FROM figures f
        JOIN papers p ON f.paper_id = p.id
        WHERE (f.caption LIKE '%Fig%' OR f.caption LIKE '%Scheme%') AND p.abs_path != ''
        LIMIT 20
    """).fetchall()
    conn.close()

    if not row:
        print("  [WARN] 数据库中未找到符合条件的图表，跳过真实文件测试")
        return

    tested = 0
    for r in row:
        p_path = r["abs_path"]
        if not os.path.isfile(p_path):
            continue
        mentions = figure_anchor.find_figure_mentions_in_paper(p_path, r["caption"], r["figure_type"])
        print(f"  文献: {r['title'][:35]}...")
        print(f"  图注: {r['caption'][:40]}...")
        print(f"  找到正文讨论处数: {len(mentions)}")
        if mentions:
            for m in mentions[:2]:
                print(f"    - [第 {m['page']} 页]: {m['snippet'][:80]}...")
            
            # 测试离线结论提炼
            offline_conc = figure_anchor.extract_offline_conclusion(mentions, r["caption"])
            assert "正文核心论断" in offline_conc
            print(f"  离线结论生成成功: {offline_conc[:70]}...\n")
            tested += 1
            break

    print(f"真实文献锚定测试完成（测试成功 {tested} 篇）!\n")


if __name__ == "__main__":
    test_figure_identity_extraction()
    test_real_paper_mentions()
    print("ALL TESTS PASSED!")
