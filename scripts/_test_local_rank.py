# -*- coding: utf-8 -*-
# local_rank 的测试：分词、字段加权、排序、片段。
# 核心验收点：标题里没有搜索词、但正文相关的文献**必须展示**，
#            同时排在「标题命中」的文献之后。
import os
import sys

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import local_rank as LR

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


def mk(pid, title="", summary="", full_text="", year=2020):
    return {"id": pid, "title": title, "summary": summary,
            "full_text": full_text, "year": year}


print("=== 1) 分词：中文 bigram ===")
t, p = LR.tokenize("循环伏安")
check("中文切成 bigram", t == ["循环", "环伏", "伏安"], t)
check("短语保留原串", p == "循环伏安", p)
check("2 字词只产生 1 个词", LR.tokenize("羰基")[0] == ["羰基"], LR.tokenize("羰基"))
check("单字词保留", LR.tokenize("电")[0] == ["电"], LR.tokenize("电"))

print("\n=== 2) 分词：拉丁与混排 ===")
check("拉丁词按空格切", LR.tokenize("spin selectivity")[0] == ["spin", "selectivity"])
check("混排同时给出两类词",
      set(LR.tokenize("CISS 手性")[0]) == {"ciss", "手性"}, LR.tokenize("CISS 手性")[0])
check("停用词被丢掉", "of" not in LR.tokenize("reduction of CO2")[0],
      LR.tokenize("reduction of CO2")[0])
check("单字母拉丁词被丢掉（g 会匹配一切）",
      LR.tokenize("g 因子")[0] == ["因子"], LR.tokenize("g 因子")[0])
check("空查询返回空", LR.tokenize("")[0] == [])
check("纯标点返回空", LR.tokenize("!!! ???")[0] == [], LR.tokenize("!!! ???"))

print("\n=== 3) 归一化 ===")
check("大小写统一", LR.normalize("Chirality-Induced SELECTIVITY") == "chirality induced selectivity",
      LR.normalize("Chirality-Induced SELECTIVITY"))
check("连字符变空格（让短语能对上）",
      "chirality induced" in LR.normalize("Chirality-Induced Spin"))
check("全角空格处理", LR.normalize("a　b") == "a b", repr(LR.normalize("a　b")))

print("\n=== 4) 字段加权：标题 > 摘要 > 正文 ===")
C = LR.parse_concepts("spin selectivity")
ph = "spin selectivity"
s_title, m1, _ = LR.score_fields(C, ph, {"title": "Spin Selectivity in Chiral Molecules"})
s_sum, m2, _ = LR.score_fields(C, ph, {"summary": "Spin Selectivity in Chiral Molecules"})
s_full, m3, _ = LR.score_fields(C, ph, {"full_text": "Spin Selectivity in Chiral Molecules"})
check("标题得分 > 摘要得分", s_title > s_sum, (s_title, s_sum))
check("摘要得分 > 正文得分", s_sum > s_full, (s_sum, s_full))
check("命中字段被记录（title）", m1 == ["title"], m1)
check("命中字段被记录（full_text）", m3 == ["full_text"], m3)

print("\n=== 5) 短语加分（连续出现优于散落）===")
C2 = LR.parse_concepts("spin selectivity")
s_phrase, _, _ = LR.score_fields(C2, "spin selectivity",
                                 {"title": "spin selectivity effects"})
s_scatter, _, _ = LR.score_fields(C2, "spin selectivity",
                                  {"title": "selectivity of spin transport"})
check("连续短语得分更高", s_phrase > s_scatter, (s_phrase, s_scatter))

print("\n=== 6) 覆盖率：命中词越多分越高 ===")
s_all, _, _ = LR.score_fields(C2, "spin selectivity", {"title": "spin selectivity"})
s_half, _, _ = LR.score_fields(C2, "spin selectivity", {"title": "spin transport only"})
check("全覆盖 > 半覆盖", s_all > s_half, (s_all, s_half))
s_none, _, _ = LR.score_fields(LR.parse_concepts("spin"), "spin", {"title": "unrelated words"})
check("不命中得 0", s_none == 0, s_none)

print("\n=== 7) ★ 核心需求：标题无、正文有 → 仍要展示但排在后面 ===")
items = [
    mk("body_only", title="Unrelated Title About Water Splitting",
       full_text="We used cyclic voltammetry to study spin selectivity carefully."),
    mk("title_hit", title="Spin Selectivity in Chiral Molecules", full_text="text"),
    mk("none", title="Nothing Relevant Here", full_text="also nothing"),
]
ranked = LR.rank_items(items, "spin selectivity")
ids = [r["id"] for r in ranked]
check("正文相关的**出现在结果里**（不是被丢掉）", "body_only" in ids, ids)
check("标题命中的排在正文命中的前面", ids[0] == "title_hit", ids)
check("完全不相关的被排除", "none" not in ids, ids)
check("每条都带 relevance", all("relevance" in r for r in ranked))
check("每条都带 match_reason", all(r.get("match_reason") for r in ranked))
check("标题命中分更高", ranked[0]["relevance"] > ranked[-1]["relevance"],
      [r["relevance"] for r in ranked])

print("\n=== 8) 中文检索：能命中「循环伏安法」（原 FTS 短语匹配命中不了）===")
items_cn = [
    mk("a", title="某篇论文", summary="通过循环伏安法（CV）验证反应机理。"),
    mk("b", title="另一篇", summary="本文讨论其他内容。"),
]
r_cn = LR.rank_items(items_cn, "循环伏安")
check("bigram 让「循环伏安」命中「循环伏安法」",
      [x["id"] for x in r_cn] == ["a"], [x["id"] for x in r_cn])

print("\n=== 9) relevance 在 0–100 之间 ===")
extreme = [mk("x", title="spin selectivity", summary="spin selectivity",
              full_text="spin selectivity " * 100)]
rx = LR.rank_items(extreme, "spin selectivity")
check("最高不超过 100", 0 < rx[0]["relevance"] <= 100, rx[0]["relevance"])

print("\n=== 10) 片段 ===")
sn = LR.make_snippet("前缀内容 " * 20 + "这里有循环伏安法的描述" + "后缀" * 20, ["循环"])
check("片段包含命中词", "循环" in sn, sn)
check("长文本加省略号", sn.startswith("…") or sn.endswith("…"), sn)
sn2 = LR.make_snippet("short text", ["notfound"])
check("找不到词时给开头且不报错", isinstance(sn2, str) and len(sn2) > 0, sn2)
check("空文本返回空串", LR.make_snippet("", ["a"]) == "")

print("\n=== 11) explain ===")
e = LR.explain("CISS 手性")
check("explain 给出词与短语", e["terms"] and e["phrase"] == "ciss 手性", e)

print("\n=== 11b) ★ 中英对照：中文查询必须能搜到英文内容 ===")
# 这是本库的真实痛点：图片字段全是英文（caption/ai_description 都是英文），
# 而用户用中文搜，所以「循环伏安」「机理」原本命中 0 张。
cn = LR.parse_concepts("循环伏安")
check("「循环伏安」是一个概念（不是一个词一个概念）", len(cn) == 1, cn)
check("概念里带上了英文写法",
      any("cyclic voltammetry" in g or "cv" in g for g in cn), cn)
check("「循环伏安」能命中纯英文文本",
      LR.score_fields(cn, "", {"caption": "Cyclic voltammetry (CV) curves"})[0] > 0)
check("「机理」能命中 mechanism",
      LR.score_fields(LR.parse_concepts("机理"), "",
                      {"caption": "proposed reaction mechanism"})[0] > 0)
check("「XRD 图谱」能命中 PXRD pattern",
      LR.score_fields(LR.parse_concepts("XRD 图谱"), "",
                      {"caption": "PXRD patterns of the MOF"})[0] > 0)
check("「曲线」能命中 curves",
      LR.score_fields(LR.parse_concepts("曲线"), "", {"caption": "CV curves"})[0] > 0)

print("\n=== 11c) 反向：英文查询也能搜到中文内容 ===")
en = LR.parse_concepts("spin selectivity")
check("英文概念里带上了中文写法",
      any("自旋" in f or "选择性" in f for g in en for f in g), en)
check("「spin selectivity」能命中中文摘要",
      LR.score_fields(en, "", {"summary": "手性诱导自旋选择性效应"})[0] > 0)

print("\n=== 11d) all_forms 供 SQL 预筛用 ===")
forms = LR.all_forms("循环伏安")
check("展开后的写法里含英文", "cyclic voltammetry" in forms or "cv" in forms, forms)
check("写法去重", len(forms) == len(set(forms)), forms)
check("空查询展开为空", LR.all_forms("") == [])

print("\n=== 11e) 泛化词不应被当成唯一命中依据 ===")
# 「数据」「时间」这类词刻意没进对照表，避免任何含 data 的图凭泛词抬高覆盖率
check("没有把「数据」收进对照表", "数据" not in LR.GLOSSARY)
check("有收「曲线」这类有区分度的图形词", "曲线" in LR.GLOSSARY)

print("\n=== 12) 排序稳定性 ===")
same = [mk("p1", title="spin selectivity", year=2020),
        mk("p2", title="spin selectivity", year=2023)]
r_same = LR.rank_items(same, "spin selectivity")
check("同分时年份新的靠前", r_same[0]["id"] == "p2", [(x["id"], x["year"]) for x in r_same])

print("\n=== 13) 不修改原对象 ===")
orig = mk("keep", title="spin selectivity")
LR.rank_items([orig], "spin selectivity")
check("原 dict 未被写入 relevance", "relevance" not in orig, list(orig.keys()))

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
