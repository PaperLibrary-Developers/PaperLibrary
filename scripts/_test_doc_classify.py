# -*- coding: utf-8 -*-
# doc_classify 的测试：重点验证「不误杀 Word 导出的真 SI」。
import os
import shutil
import sys
import tempfile

import fitz

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import doc_classify as D

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


def make_pdf(path, text, producer="", creator="", pages=1, title=""):
    doc = fitz.open()
    for i in range(pages):
        pg = doc.new_page()
        pg.insert_text((50, 80), (text[:1500] if i == 0 else f"page {i+1}"), fontsize=10)
    doc.set_metadata({"producer": producer, "creator": creator, "title": title})
    doc.save(path)
    doc.close()


tmp = tempfile.mkdtemp(prefix="lvcls_")
try:
    cases = []

    # 1) 自写笔记：Word 导出 + 名字像笔记 + 正文极短 + 无期刊特征
    p = os.path.join(tmp, "文献总结.pdf")
    make_pdf(p, "这是我读了几篇论文后的总结。要点一……", producer="Microsoft Word")
    cases.append(("自写笔记（Word+短+无特征）", p, False))

    # 2) ★ 关键的误杀测试：Word 导出的真 SI（长、有期刊特征）
    p = os.path.join(tmp, "ja5c10757_si_001.pdf")
    make_pdf(p, ("Supporting Information. This journal is (c) 2025 American Chemical Society. "
                 "DOI: 10.1021/jacs.5c10757. Received, accepted, published. "
                 "Supplementary Figures and Tables follow. " * 30),
             producer="Microsoft Word", creator="Microsoft Word", pages=11)
    cases.append(("★ Word 导出的真 SI（不应被砍）", p, True))

    # 3) 真论文：含 DOI、期刊、收稿日期
    p = os.path.join(tmp, "paper.pdf")
    make_pdf(p, ("Nature Catalysis. https://doi.org/10.1038/s41929-025-0001. "
                 "Received 12 January 2025, Accepted 3 March 2025. "
                 "Abstract: We report... Introduction... References " * 20),
             producer="pdfTeX", pages=9)
    cases.append(("真论文（有 DOI）", p, True))

    # 4) Word 导出但内容正常、有期刊特征 → 放行
    p = os.path.join(tmp, "manuscript.pdf")
    make_pdf(p, ("Journal of the American Chemical Society. doi 10.1021/jacs. "
                 "Keywords: catalysis. Abstract ... References ... " * 25),
             producer="WPS Office", pages=8)
    cases.append(("Word 导出但有期刊特征", p, True))

    # 5) 空 PDF / 只有标题几个字
    p = os.path.join(tmp, "空.pdf")
    make_pdf(p, "abc", pages=1)
    cases.append(("几乎空白（1页3字）", p, False))

    # 6) 文件名像笔记，但内容是完整论文 → 应放行（名字不作为单独否决理由）
    p = os.path.join(tmp, "读书笔记.pdf")
    make_pdf(p, ("Chemical Reviews. doi:10.1021/acs.chemrev.5b00001. Received, accepted. "
                 "Abstract. Introduction. References. " * 30),
             producer="pdfTeX", pages=12)
    cases.append(("名字像笔记但内容是论文", p, True))

    # 7) 打不开的文件 → 默认放行（宁可真男人存进来，也不静默丢）
    p = os.path.join(tmp, "broken.pdf")
    with open(p, "wb") as f:
        f.write(b"not a real pdf")
    cases.append(("损坏文件默认放行", p, True))

    print("=== 判定结果 ===")
    for name, path, expect in cases:
        r = D.classify(path, os.path.basename(path))
        check(f"{name} -> {'入库' if expect else '跳过'}", r["is_article"] == expect,
              f"实际 {'入库' if r['is_article'] else '跳过'}，理由={r.get('reasons')}")
        if not r["is_article"]:
            print(f"          理由: {'; '.join(r['reasons'])}")
        print(f"          元数据: {r['meta']}")

    print("\n=== 批量接口 ===")
    infos = [{"full_path": p, "filename": os.path.basename(p), "folder": "x"}
             for _, p, _ in cases]
    ok, skipped = D.classify_many(infos)
    check("批量：放行数正确", len(ok) == sum(1 for _, _, e in cases if e),
          f"ok={len(ok)}")
    check("批量：跳过数正确", len(skipped) == sum(1 for _, _, e in cases if not e),
          f"skipped={len(skipped)}")
    check("批量：跳过的都带理由", all(len(r) > 0 for _, r in skipped))

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
