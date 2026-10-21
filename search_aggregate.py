# -*- coding: utf-8 -*-
"""search_aggregate.py — 检索结果的折叠、排序与本地碰撞比对

三个概念**语义不同，刻意不合并成一个函数**（指南 3.3）：
  · 跨源去重  merge_results()   同一条记录别显示 5 次（DOI → 归一化标题）
  · 同名折叠  group_by_title()  预印本/正式版收成一组（归一化标题）
  · 本地碰撞  mark_in_library() 告诉用户「这条你已经有了」（DOI → 归一化标题）

全是纯函数，不碰网络，便于离线测试。
"""

import re

# 中文数字字母保留，其余转空格（用于标题归一化）
_NORM_KEEP = re.compile(r"[^a-z0-9\u4e00-\u9fff]+")


def normalize_doi(doi: str) -> str:
    s = str(doi or "").strip().lower()
    s = re.sub(r"^https?://(dx\.)?doi\.org/", "", s)
    s = re.sub(r"^doi:\s*", "", s)
    return s.strip()


def title_key(title: str) -> str:
    """标题归一化：去标点/空白/大小写差异，用于判断「是不是同一篇」"""
    return _NORM_KEEP.sub(" ", str(title or "").lower()).strip()


def dedupe_key(item: dict) -> str:
    """跨页去重用的键：DOI 优先，标题兜底"""
    doi = normalize_doi(item.get("doi"))
    return f"doi:{doi}" if doi else f"title:{title_key(item.get('title'))}"


def merge_results(lists):
    """跨源折叠（指南 3.1）：同一个 DOI（或同名）合并成一条并记住命中过哪些源。

    合并时取更好的值：被引取**最大**（Crossref 可能偏旧、OpenAlex 更新更勤），
    作者取**更全**的那份，缺的字段补上。
    刻意不「先到先得」—— 各源返回顺序会随限流/超时而变，那样结果会时好时坏。

    这里**再归一化一次 DOI**：即使某个适配器漏了（指南坑 #4），折叠也不会失效。
    """
    merged = {}
    order = []
    for lst in lists or []:
        for item in lst or []:
            item = dict(item)
            item["doi"] = normalize_doi(item.get("doi"))
            k = dedupe_key(item)
            if not k or k == "title:":
                continue
            cur = merged.get(k)
            if cur is None:
                copy = dict(item)
                copy["sources"] = [item.get("source")] if item.get("source") else []
                merged[k] = copy
                order.append(k)
                continue
            cur["citations"] = max(int(cur.get("citations") or 0),
                                   int(item.get("citations") or 0))
            if len(item.get("authors") or []) > len(cur.get("authors") or []):
                cur["authors"] = item["authors"]
            for f in ("venue", "year", "doi", "abstract", "oaUrl", "url", "type"):
                if not cur.get(f) and item.get(f):
                    cur[f] = item[f]
            cur["isOpenAccess"] = bool(cur.get("isOpenAccess")) or bool(item.get("isOpenAccess"))
            src = item.get("source")
            if src and src not in cur["sources"]:
                cur["sources"].append(src)
    return [merged[k] for k in order]


def title_tokens(s: str):
    """切词：拉丁词 + 单个中文字（前缀匹配也认）"""
    t = str(s or "").lower()
    return [w for w in re.split(r"[^a-z0-9\u4e00-\u9fff]+", t) if w]


def _bigrams(tokens):
    out = set()
    for i in range(len(tokens) - 1):
        out.add(tokens[i] + " " + tokens[i + 1])
    return out


def hit_ratio(query_tokens, text_tokens):
    """查询词在文本里的覆盖率（前缀也算命中：curcum* 能对上 curcumin）"""
    if not query_tokens:
        return 0.0
    text_joined = " ".join(text_tokens)
    hit = 0
    for q in query_tokens:
        if not q:
            continue
        if q in text_joined or any(t.startswith(q) for t in text_tokens):
            hit += 1
    return hit / len(query_tokens)


def similarity(query: str, item: dict) -> float:
    """自己算相关度（指南 2.5）：各源自带的相关度不可比，必须统一口径。

    0.50 标题覆盖 + 0.20 摘要覆盖 + 0.20 相邻词对 + 0.10 整串包含
    """
    q_tokens = title_tokens(query)
    if not q_tokens:
        return 0.0
    t_tokens = title_tokens(item.get("title"))
    a_tokens = title_tokens(item.get("abstract"))

    title_cov = hit_ratio(q_tokens, t_tokens)
    abstract_cov = hit_ratio(q_tokens, a_tokens)

    qb = _bigrams(q_tokens)
    tb = _bigrams(t_tokens) | _bigrams(a_tokens)
    bigram_hit = (len(qb & tb) / len(qb)) if qb else 0.0

    q_norm = " ".join(q_tokens)
    t_norm = " ".join(t_tokens)
    phrase = 1.0 if len(q_norm) >= 4 and q_norm in t_norm else 0.0

    score = 100 * (0.50 * title_cov + 0.20 * abstract_cov + 0.20 * bigram_hit + 0.10 * phrase)
    return round(score, 1)


def rank_by_similarity(items, query):
    """打分并按「相似度分档 → 被引 → 相关度」排序（指南 2.5）。

    先分档再比被引，避免出现「50 分排在 30 分后面」这种肉眼可见的错误。
    """
    BUCKET = 4
    out = []
    for it in items or []:
        copy = dict(it)
        copy["relevance"] = similarity(query, copy)
        out.append(copy)
    def sort_key(x):
        return (-round(x.get("relevance", 0) / BUCKET),
                -int(x.get("citations") or 0),
                -x.get("relevance", 0))
    return sorted(out, key=sort_key)


def group_by_title(items, key_func=title_key):
    """同名折叠（指南 3.2）：同一标题只把**被引最高**的那条摆在明面上。

    返回 [{key, main, duplicates}]，顺序按每组首次出现的位置。
    """
    members = {}
    for it in items or []:
        k = key_func(it.get("title"))
        if not k:
            continue
        members.setdefault(k, []).append(it)
    for k in members:
        members[k].sort(key=lambda x: (-int(x.get("citations") or 0),
                                       -int(x.get("year") or 0)))
    out = []
    done = set()
    for it in items or []:
        k = key_func(it.get("title"))
        if not k:
            out.append({"key": "", "main": it, "duplicates": []})
            continue
        if k in done:
            continue
        done.add(k)
        group = members.get(k) or [it]
        out.append({"key": k, "main": group[0], "duplicates": group[1:]})
    return out


def mark_in_library(items, doi_set, title_set):
    """本地碰撞比对（指南 3.3）：实时标注「已在库中」。

    doi_set / title_set 由调用方从 papers.db 准备好（标题用 title_key 归一化）。
    """
    for it in items or []:
        doi = normalize_doi(it.get("doi"))
        title = title_key(it.get("title"))
        hit = (doi and doi in doi_set) or (title and title in title_set)
        it["inLibrary"] = bool(hit)
    return items


def apply_filters(items, filters):
    """本地兜底过滤（指南 2.4 第 4 层）：把源不支持的条件下推到本地。"""
    f = filters or {}
    out = []
    for it in items or []:
        y = int(it.get("year") or 0)
        if f.get("yearFrom") and (not y or y < int(f["yearFrom"])):
            continue
        if f.get("yearTo") and (not y or y > int(f["yearTo"])):
            continue
        if f.get("onlyOA") and not it.get("isOpenAccess"):
            continue
        if f.get("minCitations") and int(it.get("citations") or 0) < int(f["minCitations"]):
            continue
        if f.get("venue") and str(f["venue"]).lower() not in str(it.get("venue") or "").lower():
            continue
        if f.get("author") and str(f["author"]).lower() not in \
                " ".join(it.get("authors") or []).lower():
            continue
        out.append(it)
    return out


def compute_facets(items):
    """对「已取回的这一批」做内存聚合（指南 4.2）。

    外部 API 不提供聚合，只能现场算 —— 界面必须如实标注「计数只统计这批」，
    不能假装是全网分布。
    """
    years, venues, authors = {}, {}, {}
    oa = 0
    for it in items or []:
        y = it.get("year")
        if y:
            years[str(y)] = years.get(str(y), 0) + 1
        v = (it.get("venue") or "").strip()
        if v:
            venues[v] = venues.get(v, 0) + 1
        for a in it.get("authors") or []:
            a = (a or "").strip()
            if a:
                authors[a] = authors.get(a, 0) + 1
        if it.get("isOpenAccess"):
            oa += 1

    def to_group(d, by_num=False):
        arr = [{"value": k, "count": v} for k, v in d.items()]
        if by_num:
            arr.sort(key=lambda x: (-int(x["value"]) if str(x["value"]).isdigit() else 0))
        else:
            arr.sort(key=lambda x: (-x["count"], str(x["value"]).lower()))
        return {"items": arr, "distinct": len(arr)}

    return {
        "total": len(items or []),
        "years": to_group(years, by_num=True),
        "venues": to_group(venues),
        "authors": to_group(authors),
        "oa": {"items": [{"value": "oa", "count": oa}], "distinct": 1},
    }


def sort_items(items, mode):
    """网络结果的排序（只重排**已取回的这一批**，不等于重新检索）"""
    arr = list(items or [])
    if mode == "citations":
        arr.sort(key=lambda x: (-int(x.get("citations") or 0), -int(x.get("year") or 0)))
    elif mode == "timeDesc":
        arr.sort(key=lambda x: (-int(x.get("year") or 0), -int(x.get("citations") or 0)))
    # relevance：保持传入顺序
    return arr
