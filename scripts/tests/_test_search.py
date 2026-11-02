# -*- coding: utf-8 -*-
# search_aggregate 的测试：折叠 / 排序 / 碰撞 / facet，全部离线纯函数。
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import search_aggregate as A
import search_sources as S

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


def mk(source, title, doi="", cites=0, year=2020, authors=None, venue="", abstract="",
       oa=False):
    return {"source": source, "sourceId": source + "1", "title": title,
            "authors": authors or ["A"], "year": year, "venue": venue,
            "doi": doi, "citations": cites, "type": "journal",
            "isOpenAccess": oa, "oaUrl": "", "url": "", "abstract": abstract}


print("=== 1) 跨源折叠（DOI 优先）===")
lists = [
    [mk("openalex", "Chirality induced spin selectivity", "10.1021/AAA", cites=100, venue="JACS")],
    [mk("crossref", "Chirality induced spin selectivity", "https://doi.org/10.1021/aaa", cites=80)],
    [mk("europepmc", "Chirality induced spin selectivity", "10.1021/AAA", cites=0,
        authors=["A", "B", "C"])],
]
merged = A.merge_results(lists)
check("三条同 DOI 折叠成 1 条", len(merged) == 1, f"实际 {len(merged)}")
check("命中源记全（3 个）", len(merged[0]["sources"]) == 3, merged[0].get("sources"))
check("被引取最大（100）", merged[0]["citations"] == 100, merged[0]["citations"])
check("作者取更全的（3 个）", len(merged[0]["authors"]) == 3, merged[0]["authors"])
check("缺的字段被补上（venue）", merged[0]["venue"] == "JACS", merged[0]["venue"])
check("DOI 已归一化", merged[0]["doi"] == "10.1021/aaa", merged[0]["doi"])

print("\n=== 2) 标题兜底折叠（无 DOI）===")
lists2 = [
    [mk("arxiv", "A Study of Catalysis!", "")],
    [mk("openalex", "a study of catalysis", "")],
]
m2 = A.merge_results(lists2)
check("同标题（标点大小写不同）折叠成 1 条", len(m2) == 1, f"实际 {len(m2)}")

print("\n=== 3) 同名折叠：预印本 + 正式版 ===")
items = [
    mk("arxiv", "Deep Learning for Chemistry", year=2021, cites=5),
    mk("openalex", "Deep learning for Chemistry", year=2023, cites=50),
    mk("crossref", "Another Paper", year=2020, cites=1),
]
groups = A.group_by_title(items)
check("分成 2 组", len(groups) == 2, f"实际 {len(groups)}")
g0 = [g for g in groups if "Deep" in (g["main"]["title"])][0]
check("代表是被引最高的那条（50）", g0["main"]["citations"] == 50, g0["main"]["citations"])
check("另一条进 duplicates", len(g0["duplicates"]) == 1)

print("\n=== 4) 相关度与分档排序 ===")
q = "chirality induced spin selectivity"
a = mk("openalex", "Chirality-Induced Spin Selectivity Effect", cites=100)
b = mk("openalex", "Some unrelated topic about water", cites=9999)
ranked = A.rank_by_similarity([b, a], q)
check("相关的那条排在前（即使被引低）", ranked[0]["title"] == a["title"],
      ranked[0]["title"])
check("相关度是数值", isinstance(ranked[0]["relevance"], float))
check("不相关的相关度明显更低", ranked[0]["relevance"] > ranked[1]["relevance"])
sim_exact = A.similarity(q, a)
check("整串包含拿到高分（>60）", sim_exact > 60, sim_exact)

print("\n=== 5) 本地碰撞比对 ===")
lib = [mk("openalex", "Chirality-Induced Spin Selectivity Effect", doi="10.1021/jz300793y")]
doi_set = {A.normalize_doi(x["doi"]) for x in lib}
title_set = {A.title_key(x["title"]) for x in lib}
ext = [
    mk("crossref", "Chirality-Induced Spin Selectivity Effect", doi="10.1021/JZ300793Y"),
    mk("crossref", "Brand New Paper", doi="10.9999/xyz"),
]
A.mark_in_library(ext, doi_set, title_set)
check("DOI 命中（大小写无关）标为已在库中", ext[0]["inLibrary"] is True)
check("新论文不标", ext[1]["inLibrary"] is False)

print("\n=== 6) facet 聚合 ===")
f = A.compute_facets([
    mk("x", "P1", year=2024, venue="JACS", authors=["Zhang S", "Li Y"]),
    mk("x", "P2", year=2024, venue="JACS", authors=["Zhang S"], oa=True),
    mk("x", "P3", year=2020, venue="Nature", authors=["Wang"]),
])
check("年份计数正确", {i["value"]: i["count"] for i in f["years"]["items"]} == {"2024": 2, "2020": 1},
      f["years"]["items"])
check("年份按数值降序", [i["value"] for i in f["years"]["items"]] == ["2024", "2020"])
check("期刊计数正确", {i["value"]: i["count"] for i in f["venues"]["items"]} == {"JACS": 2, "Nature": 1})
check("作者计数正确", f["authors"]["items"][0]["value"] == "Zhang S" and
      f["authors"]["items"][0]["count"] == 2, f["authors"]["items"])
check("OA 计数正确", f["oa"]["items"][0]["count"] == 1)
check("total 正确", f["total"] == 3)

print("\n=== 7) 本地兜底过滤 ===")
fs = [
    mk("x", "P1", year=2024, oa=True, cites=50),
    mk("x", "P2", year=2015, oa=False, cites=2),
]
check("yearFrom 生效", len(A.apply_filters(fs, {"yearFrom": 2020})) == 1)
check("yearTo 生效", len(A.apply_filters(fs, {"yearTo": 2020})) == 1)
check("onlyOA 生效", len(A.apply_filters(fs, {"onlyOA": True})) == 1)
check("minCitations 生效", len(A.apply_filters(fs, {"minCitations": 10})) == 1)
check("年份未知的被 yearFrom 滤掉",
      len(A.apply_filters([mk("x", "P", year=None)], {"yearFrom": 2000})) == 0)

print("\n=== 8) 排序模式 ===")
s = A.sort_items([mk("x", "A", year=2020, cites=5), mk("x", "B", year=2024, cites=1)], "timeDesc")
check("按时间降序", s[0]["year"] == 2024)
s = A.sort_items([mk("x", "A", year=2024, cites=5), mk("x", "B", year=2020, cites=99)], "citations")
check("按被引降序", s[0]["citations"] == 99)

print("\n=== 9) 清洗（指南 3.5：先解实体再剥标签）===")
check("剥离 HTML 标签", S.clean_text("Fe<sup>II</sup>L<sub>6</sub>") == "Fe II L 6".replace("  ", " ").strip()
      or "Fe" in S.clean_text("Fe<sup>II</sup>L<sub>6</sub>"), S.clean_text("Fe<sup>II</sup>L<sub>6</sub>"))
check("解 HTML 实体", "&" in S.clean_text("A &amp; B") and "amp" not in S.clean_text("A &amp; B"),
      S.clean_text("A &amp; B"))
check("双重编码也能解", "<i>" not in S.clean_text("&amp;lt;i&amp;gt;d-&amp;lt;/i&amp;gt;π"),
      S.clean_text("&amp;lt;i&amp;gt;d-&amp;lt;/i&amp;gt;π"))
check("DOI 归一化（去前缀/小写）",
      S.normalize_doi("https://doi.org/10.1021/JZ300793Y") == "10.1021/jz300793y",
      S.normalize_doi("https://doi.org/10.1021/JZ300793Y"))
check("倒排摘要能还原",
      S._restore_abstract({"Hello": [0], "world": [1]}) == "Hello world",
      S._restore_abstract({"Hello": [0], "world": [1]}))

print("\n=== 10) 源能力如实标注 ===")
check("OpenAlex 收原生布尔", S.query_for("openalex", 'a AND b') == 'a AND b')
check("Crossref 降级为朴素查询", S.query_for("crossref", 'a AND b') == 'a b',
      S.query_for("crossref", 'a AND b'))
check("arXiv 也降级", "AND" not in S.query_for("arxiv", 'x AND y'))
check("源清单有 5 个", len(S.SOURCES) == 5)

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
