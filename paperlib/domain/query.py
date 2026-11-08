"""领域层 · 查询（移植自 Sci-lib server/domain/query.js，436 行）：只读。

职责：组合过滤 + 排序 + 分页（query）、结果集内的 facet 计数（facets）、
各字段的联想清单与统计（tags / authors / venues / stats）。

语义与拆分前完全一致，三处最容易改坏的地方保留了原注释：
  · 两个查询串（boolExpr / rawQ）不能混用；
  · facets 里**每类计数都要先剥掉本类自己的条件**（同类是 OR，跨类才是 AND）；
  · 含 OR 的查询必须按「并集」粗筛，否则只满足一支的文献在进求值器前就被砍掉。

契约名：GET /api/papers 的筛选/排序/分页 + GET /api/facets 的计数语义
        （shared/contracts/api.md §3.3 参数表、§4 两段式粗筛、§5 不变量）。
布尔语法与字段限定由 shared.pure.boolean_query 负责（T1 交付，接口见 docs 内部接口 §3.5）。
"""
from __future__ import annotations

import math
import os
from functools import cmp_to_key
from pathlib import Path

from paperlib.domain.naming import display_file_name
from paperlib.domain.repository import SORTABLE
from paperlib.util import clamp_int, safe_path_join, to_string_array, truncate
from shared.pure.text import zh_collation_key

try:  # T1 交付；暂缺时布尔/字段查询降级（测试用 skip 标记）
    from shared.pure import boolean_query as bool_query
except Exception:  # noqa: BLE001
    bool_query = None


# ---------------------------------------------------------------- 布尔查询胶水
def needs_evaluator(text):
    """查询串是否必须走求值器（布尔语法或字段限定）。对应 JS bool.needsEvaluator。"""
    raw = str(text or "")
    if not raw.strip() or bool_query is None:
        return False
    return bool(bool_query.needs_evaluator(raw))


def parse_query(text):
    """parse_query(q) -> Node 同构 bundle（keys: ast/terms/allTerms/fieldTerms/needsEvaluator…）。"""
    if bool_query is None:
        return None
    return bool_query.parse_query(text)


def has_or_node(node):
    """AST 里是否含 OR 节点（含嵌套）。含 OR 时必须按并集粗筛。"""
    if not isinstance(node, dict):
        return False
    if node.get("type") == "or":
        return True
    return (has_or_node(node.get("left")) or has_or_node(node.get("right"))
            or has_or_node(node.get("child")))


def search_union(index, terms):
    """多词「并集」粗筛（含 OR 的查询专用）：每个词单独查索引，同一条取最高分。

    返回形状与 index.search 一致（[{id, score}]，按分降序）；一个词都查不到时返回 None，
    让调用方按既有语义给出空结果（不要把 None 当成「不过滤」）。
    """
    scores = {}
    any_hit = False
    for term in terms or []:
        hits = index.search(term)
        if not hits:
            continue
        any_hit = True
        for hit in hits:
            prev = scores.get(hit["id"])
            if prev is None or hit["score"] > prev:
                scores[hit["id"]] = hit["score"]
    if not any_hit:
        return None
    return [{"id": pid, "score": score} for pid, score in
            sorted(scores.items(), key=lambda kv: -kv[1])]


def _cmp(a, b):
    return -1 if a < b else (1 if a > b else 0)


# 中文/拉丁混排的近似排序键由 shared/pure/text.py 提供（Node 那边是 localeCompare(..., 'zh-Hans-CN')）


def _name_key(name):
    """并列项的次序键：与 Node 的 localeCompare 一致（汉字在前、拉丁不分大小写）。"""
    return zh_collation_key(name)


def _number(value):
    """对应 JS 的 Number()：解析失败给 NaN（比较恒为 False，与 JS 同）。"""
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return float("nan")


def _round1(value):
    """对应 JS 的 Math.round(x*10)/10（四舍五入，不是银行家舍入）。"""
    return math.floor(float(value) * 10 + 0.5) / 10


def _js_number(value):
    """JS 的 JSON 表示：整数不带小数点（Math.round(...)/10 给 5 而不是 5.0）。"""
    result = _round1(value)
    return int(result) if float(result).is_integer() else result


def _limit_of(value, fallback):
    """对应 JS 的 Number(limit) || fallback（0/NaN 都回落）。"""
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return fallback
    return number if number else fallback


class QueryEngine:
    def __init__(self, db, index):
        self.db = db
        self.index = index
        # 「按 id 取正文」的钩子：scope=fulltext / fulltext: 时由 services 注入（带读取预算）。
        # 默认不读正文，零 I/O。
        self.fulltextTextFor = None

    # ------------------------------------------------------------------ 联想
    def tags(self):
        counter = {}
        for paper in self.db.list_papers():
            for tag in paper.get("tags") or []:
                counter[tag] = counter.get(tag, 0) + 1
        items = [{"name": name, "count": count} for name, count in counter.items()]
        items.sort(key=lambda row: (-row["count"], _name_key(row["name"])))
        return items

    def authors(self, limit=None):
        """汇总所有作者（按出现次数），供高级搜索联想。"""
        counter = {}
        for paper in self.db.list_papers():
            for author in paper.get("authors") or []:
                name = str(author).strip()
                if not name:
                    continue
                counter[name] = counter.get(name, 0) + 1
        items = [{"name": name, "count": count} for name, count in counter.items()]
        items.sort(key=lambda row: (-row["count"], _name_key(row["name"])))
        return items[: _limit_of(limit, 300)]

    def venues(self, limit=None):
        """汇总所有期刊 / 会议名，供高级搜索联想。"""
        counter = {}
        for paper in self.db.list_papers():
            venue = str(paper.get("venue") or "").strip()
            if not venue:
                continue
            counter[venue] = counter.get(venue, 0) + 1
        items = [{"name": name, "count": count} for name, count in counter.items()]
        items.sort(key=lambda row: (-row["count"], _name_key(row["name"])))
        return items[: _limit_of(limit, 300)]

    # ------------------------------------------------------------------ 统计
    def stats(self):
        papers = self.db.list_papers()
        by_year = {}
        by_type = {}
        by_status = {}
        with_pdf = 0
        starred = 0
        rating_sum = 0
        rating_count = 0
        words = 0
        for paper in papers:
            if paper.get("year"):
                by_year[paper["year"]] = by_year.get(paper["year"], 0) + 1
            by_type[paper.get("type")] = by_type.get(paper.get("type"), 0) + 1
            by_status[paper.get("status")] = by_status.get(paper.get("status"), 0) + 1
            if paper.get("pdfFile"):
                with_pdf += 1
            if paper.get("starred"):
                starred += 1
            if paper.get("rating"):
                rating_sum += paper["rating"]
                rating_count += 1
            words += len(str(paper.get("notes") or "")) + len(str(paper.get("summary") or ""))
        years = [{"year": year, "count": count} for year, count in by_year.items()]
        years.sort(key=cmp_to_key(lambda a, b: _cmp(_number(b["year"]), _number(a["year"]))))
        types = [{"type": key, "count": count} for key, count in by_type.items()]
        types.sort(key=lambda row: -row["count"])
        statuses = [{"status": key, "count": count} for key, count in by_status.items()]
        return {
            "total": len(papers),
            "withPdf": with_pdf,
            "starred": starred,
            "pdfMissing": len([p for p in papers
                               if p.get("pdfFile") and not self.pdf_exists(p.get("pdfFile"))]),
            "avgRating": _js_number(rating_sum / rating_count) if rating_count else 0,
            "noteChars": words,
            "byYear": years,
            "byType": types,
            "byStatus": statuses,
            "tags": len(self.tags()),
            "updatedAt": self._updated_at(),
        }

    def _updated_at(self):
        fn = getattr(self.db, "updated_at", None)
        if callable(fn):
            return fn()
        meta = getattr(self.db, "meta", None) or {}
        return meta.get("updatedAt") or ""

    # ------------------------------------------------------------------ 文本
    def searchable_text(self, paper, scope="metadata"):
        """用于布尔/关键词匹配的文本；scope=fulltext 时把已提取的正文也算进来。"""
        parts = [
            paper.get("title"),
            " ".join(paper.get("authors") or []),
            paper.get("venue"),
            paper.get("publisher"),
            " ".join(paper.get("keywords") or []),
            " ".join(paper.get("tags") or []),
            paper.get("summary"),
            paper.get("summaryZh"),
            paper.get("aiSummary"),
            paper.get("notes"),
            paper.get("doi"),
            paper.get("year"),
        ]
        if scope in ("fulltext", "all"):
            parts.append(self._fulltext_of(paper.get("id")))
        return " \n ".join(str(part) for part in parts if part)

    def field_text(self, paper, field):
        """单个字段的文本，供 `title:…` `author:…` `year:…` 这类字段限定检索使用。"""
        name = str(field or "")
        if name == "title":
            return str(paper.get("title") or "")
        if name == "author":
            return " ".join(paper.get("authors") or [])
        if name == "abstract":
            chunks = [paper.get("summary"), paper.get("summaryZh"), paper.get("aiSummary")]
            return " \n ".join(str(c) for c in chunks if c)
        if name == "journal":
            chunks = [paper.get("venue"), paper.get("publisher"), paper.get("volume")]
            return " ".join(str(c) for c in chunks if c)
        if name == "year":
            return str(paper.get("year") or "")
        if name == "type":
            return str(paper.get("type") or "")
        if name == "tag":
            return " ".join(paper.get("tags") or [])
        if name == "keyword":
            return " ".join(paper.get("keywords") or [])
        if name == "note":
            return str(paper.get("notes") or "")
        if name == "doi":
            return str(paper.get("doi") or "")
        # 显式点名 fulltext: 时才读正文（预算由 services 设置的 fulltextTextFor 控制）
        if name == "fulltext":
            return self._fulltext_of(paper.get("id"))
        return ""

    def _fulltext_of(self, paper_id):
        if not callable(self.fulltextTextFor):
            return ""
        try:
            return str(self.fulltextTextFor(paper_id) or "")
        except Exception:  # noqa: BLE001
            return ""

    # ------------------------------------------------------------------ 查询
    def query(self, params):
        """列表查询：筛选 + 检索 + 排序 + 分页。"""
        opts = params or {}
        # 这里有两个**不同**的查询串，别混用：
        #   bool_expr —— 布尔表达式，交给 boolean_query 求值；
        #   raw_q     —— 用来筛「题录与摘要」倒排索引的查询。
        # scope=fulltext 时服务端会把 q 清空（命中的 id 已经由全文索引给出），
        # 只把原查询留在 booleanQuery 里。这时若还拿 booleanQuery 去筛题录索引，
        # 全文命中的文献会被全部误杀——搜正文永远 0 条（踩过）。
        bool_expr = str(opts.get("booleanQuery") or "").strip()
        raw_q = str(opts.get("q") or "").strip()
        # 布尔语法**或字段限定**都必须走求值器，否则 `title:cucurbituril` 会被
        # 当成字面词去索引里找，永远 0 条。
        use_boolean = needs_evaluator(raw_q) or needs_evaluator(bool_expr)
        # 粗筛只用「正项词」：parse().terms 已排除字段限定项（year:2013-2018 这类值
        # 在索引里搜不到，混进去会把候选集清空），NOT 后面的词也不进 terms。
        parsed_raw_q = parse_query(raw_q) if (raw_q and needs_evaluator(raw_q)) else None
        index_query = " ".join(parsed_raw_q.get("terms") or []) if parsed_raw_q else raw_q
        # 含 OR 时必须按「并集」粗筛：交集只留同时命中所有词的几篇，
        # 另一支单独命中的文献会在求值器之前就被丢掉（`a OR b` 只回 1 篇的那种情况）。
        or_query = has_or_node(parsed_raw_q.get("ast")) if parsed_raw_q else False
        rows = self.db.list_papers()

        scored = None
        if index_query:
            # search() 在查询里没有可检索词元时返回 None（例如整串都是标点）：
            # 这种查询本来就没内容可匹配，明确给出空结果——不要把 None 当成「不过滤」。
            hits = search_union(self.index, parsed_raw_q.get("terms") or []) if or_query \
                else self.index.search(index_query)
            if not hits:
                rows = []
            else:
                scored = {hit["id"]: hit["score"] for hit in hits}
                rows = [paper for paper in rows if paper.get("id") in scored]
        # scope=all：正文里命中的文献也要算进结果（题录没命中、只有正文命中的那几篇）。
        fulltext_ids = opts.get("fulltextIds")
        if fulltext_ids is not None and str(fulltext_ids) != "":
            extra = set(to_string_array(fulltext_ids))
            if extra:
                seen = {paper.get("id") for paper in rows}
                for paper in self.db.list_papers():
                    if paper.get("id") in extra and paper.get("id") not in seen:
                        seen.add(paper.get("id"))
                        rows.append(paper)
        if use_boolean:
            scope = opts.get("scope")
            rows = bool_query.filter_items(
                rows, raw_q or bool_expr,
                lambda paper: self.searchable_text(paper, scope),
                {"field_text": lambda paper, field: self.field_text(paper, field)})

        rows = self._filter_rows(rows, opts)

        # 排序：给了合法 sort 就按它；没给时——有关键词查询按相关度，否则按 updatedAt。
        sort_key = opts.get("sort") if opts.get("sort") in SORTABLE else None
        if sort_key is None and not index_query:
            sort_key = "updatedAt"
        if sort_key == "title":
            # Node 这里用 localeCompare(..., 'zh-Hans-CN')：汉字在拉丁之前、拉丁不分大小写
            rows = sorted(rows, key=lambda paper: _name_key(paper.get("title") or ""))
        elif sort_key:
            rows = sorted(rows, key=cmp_to_key(SORTABLE[sort_key]))
        elif scored:
            rows = sorted(rows, key=lambda paper: -(scored.get(paper.get("id")) or 0))

        total = len(rows)
        page = clamp_int(opts.get("page"), 1, 100000, 1)
        page_size = clamp_int(opts.get("pageSize"), 1, 500, 50)
        start = (page - 1) * page_size
        all_flag = opts.get("all") is True or opts.get("all") == "true"
        window = rows if all_flag else rows[start:start + page_size]
        return {
            "items": [self.to_list_item(paper, scored.get(paper.get("id")) if scored else None)
                      for paper in window],
            "total": total,
            "page": page,
            "pageSize": page_size,
            "pageCount": max(1, int(math.ceil(total / float(page_size)))),
            "scope": str(opts.get("scope") or "metadata"),
            # 告知前端「这次用的是布尔查询」，界面据此说明语法生效了
            "boolean": use_boolean,
        }

    def _filter_rows(self, rows, opts):
        """除关键词检索以外的全部筛选条件（顺序与 Node 一致，便于逐行对照）。"""
        tag_filter = to_string_array(opts.get("tags"))
        if tag_filter:
            wanted = [tag.lower() for tag in tag_filter]
            rows = [p for p in rows
                    if any(str(tag).lower() in wanted for tag in (p.get("tags") or []))]
        if opts.get("type") and opts.get("type") != "all":
            wanted = [str(t).lower() for t in to_string_array(opts.get("type"))]
            rows = [p for p in rows if str(p.get("type")).lower() in wanted]
        if opts.get("status") and opts.get("status") != "all":
            wanted = [str(s).lower() for s in to_string_array(opts.get("status"))]
            rows = [p for p in rows if str(p.get("status")).lower() in wanted]
        if opts.get("author"):
            needle = str(opts.get("author")).lower()
            rows = [p for p in rows
                    if needle in " ".join(str(a) for a in (p.get("authors") or [])).lower()]
        if opts.get("venue"):
            needle = str(opts.get("venue")).lower()
            rows = [p for p in rows if needle in str(p.get("venue") or "").lower()]
        if opts.get("keyword"):
            needle = str(opts.get("keyword")).lower()
            rows = [p for p in rows
                    if needle in " ".join(str(k) for k in (p.get("keywords") or [])).lower()]
        if opts.get("hasNotes") is True or opts.get("hasNotes") == "true":
            rows = [p for p in rows if str(p.get("notes") or "").strip()]
        if opts.get("hasPdf") is True or opts.get("hasPdf") == "true":
            rows = [p for p in rows if p.get("pdfFile")]
        # 文件夹分类：默认连子文件夹一起算（资源管理器里点开父目录就该看到全部后代），
        # includeSubfolders=false 时只看直属。folder="" 表示根目录（只含未分类的文献）。
        # 注意用「键存在」判断：?folder=（空值）表示「只看未分类」，不能和「不筛」混为一谈
        if "folder" in opts:
            path = str(opts.get("folder") or "").strip("/")
            deep = str(opts.get("includeSubfolders") or "true").lower() != "false"
            if path:
                prefix = path + "/"
                if deep:
                    rows = [p for p in rows
                            if str(p.get("folder") or "") == path
                            or str(p.get("folder") or "").startswith(prefix)]
                else:
                    rows = [p for p in rows if str(p.get("folder") or "") == path]
            elif deep:
                # 根目录：未分类的文献（空 folder）
                rows = [p for p in rows if not str(p.get("folder") or "")]
        if opts.get("starred") is True or opts.get("starred") == "true":
            rows = [p for p in rows if p.get("starred")]
        if opts.get("yearFrom"):
            floor = _number(opts.get("yearFrom"))
            rows = [p for p in rows if (p.get("year") or 0) >= floor]
        if opts.get("yearTo"):
            ceiling = _number(opts.get("yearTo"))
            rows = [p for p in rows if (p.get("year") or 9999) <= ceiling]
        # 多选 facet（同类 OR）：年份 / 作者 / 期刊各可以勾多个；
        # 与单选参数（author=、venue=）并存：facets 走多选，高级搜索表单走单选。
        if opts.get("years"):
            wanted = set()
            for item in to_string_array(opts.get("years")):
                number = _number(item)
                if not math.isnan(number):
                    wanted.add(number)
            if wanted:
                rows = [p for p in rows if _number(p.get("year")) in wanted]
        if opts.get("authors"):
            wanted = [str(a).lower() for a in to_string_array(opts.get("authors"))]
            if wanted:
                rows = [p for p in rows
                        if any(str(a).lower() in wanted for a in (p.get("authors") or []))]
        if opts.get("venues"):
            wanted = [str(v).lower() for v in to_string_array(opts.get("venues"))]
            if wanted:
                rows = [p for p in rows if str(p.get("venue") or "").lower() in wanted]
        if opts.get("minRating"):
            floor = _number(opts.get("minRating"))
            rows = [p for p in rows if (p.get("rating") or 0) >= floor]
        if opts.get("excludeId"):
            rows = [p for p in rows if p.get("id") != opts.get("excludeId")]
        # 按指定 id 集合过滤（全文检索先用 id 列表缩小范围）。
        # 注意：ids 存在但为空集合时结果必须是空的——否则「没命中」会退化成「返回全部」。
        ids = opts.get("ids")
        if isinstance(ids, (list, tuple)):
            ids_present = len(ids) > 0
        else:
            ids_present = ids is not None and str(ids) != ""
        if ids_present:
            wanted = set(to_string_array(ids))
            rows = [p for p in rows if p.get("id") in wanted]
        elif opts.get("idsEmpty"):
            rows = []
        return rows

    # ------------------------------------------------------------------ facets
    def facets(self, opts):
        """结果集内的 facet 计数（二次筛选用）。

        关键点：**每个类别的计数都要先去掉本类自己的条件**再算。
        因为同类勾选是 OR——如果年份里勾了 2024，却用「已含 2024」的条件去数其它年份，
        其它年份就会全变成 0，用户再也加不了项。跨类别之间是 AND，所以别的类别条件要保留。

        返回 {total, years, authors, venues, types, tags}，每组是 {items:[{value,count}], distinct}；
        顶层联想清单（authors/venues/tags/years 全库）由 api 层组装（与 Node api/papers.js 一致）。
        """
        base = dict(opts or {})

        def strip_year(target):
            target["yearFrom"] = ""
            target["yearTo"] = ""
            target["years"] = ""

        def strip_author(target):
            target["author"] = ""
            target["authors"] = ""

        def strip_venue(target):
            target["venue"] = ""
            target["venues"] = ""

        def strip_type(target):
            target["type"] = "all"

        def strip_tag(target):
            target["tags"] = []

        strip_class = {"year": strip_year, "author": strip_author,
                       "venue": strip_venue, "type": strip_type, "tag": strip_tag}

        def list_for(cls):
            """取「去掉某类条件」之后的结果集（按列表项形状，计数用）。"""
            o = dict(base)
            o["all"] = True
            o["pageSize"] = 1
            stripper = strip_class.get(cls)
            if stripper:
                stripper(o)
            return self.query(o)["items"]

        def count_by(items, pick, compare=None):
            counter = {}
            for item in items:
                for raw in pick(item) or []:
                    value = ("" if raw is None else str(raw)).strip()
                    if not value:
                        continue
                    counter[value] = counter.get(value, 0) + 1
            rows = [{"value": value, "count": count} for value, count in counter.items()]
            if compare:
                rows.sort(key=cmp_to_key(compare))
            else:
                rows.sort(key=lambda row: (-row["count"], _name_key(row["value"])))
            return {"items": rows[: _limit_of(base.get("facetLimit"), 6)],
                    "distinct": len(rows)}

        all_items = list_for("none")
        return {
            "total": len(all_items),
            "years": count_by(list_for("year"), lambda it: [it["year"]] if it.get("year") else [],
                              lambda a, b: _cmp(_number(b["value"]), _number(a["value"]))),
            "authors": count_by(list_for("author"), lambda it: it.get("authors") or []),
            "venues": count_by(list_for("venue"), lambda it: [it["venue"]] if it.get("venue") else []),
            "types": count_by(list_for("type"), lambda it: [it["type"]] if it.get("type") else []),
            "tags": count_by(list_for("tag"), lambda it: it.get("tags") or []),
        }

    # ------------------------------------------------------------------ 列表项
    def to_list_item(self, paper, score=None):
        item = {
            "id": paper.get("id"),
            "title": paper.get("title"),
            "authors": paper.get("authors"),
            "year": paper.get("year"),
            "type": paper.get("type"),
            "venue": paper.get("venue"),
            "tags": paper.get("tags"),
            "status": paper.get("status"),
            "rating": paper.get("rating"),
            # 收藏必须带上：列表页/收藏页的星标要靠它点亮（漏掉过一次，星标永远是空心的）
            "starred": bool(paper.get("starred")),
            # 文件夹分类：卡片上显示所在目录、批量移动后不必重新拉整表也能看出变化
            "folder": str(paper.get("folder") or ""),
            "starredAt": paper.get("starredAt") or "",
            "doi": paper.get("doi"),
            "url": paper.get("url"),
            "pdfFile": paper.get("pdfFile"),
            "pdfDisplayName": display_file_name(paper.get("pdfFile")),
            "hasPdf": bool(paper.get("pdfFile")) and self.pdf_exists(paper.get("pdfFile")),
            "summary": truncate(paper.get("summary"), 200),
            "hasAiSummary": bool(paper.get("aiSummary")),
            "aiSummaryAt": paper.get("aiSummaryAt") or "",
            "attachments": len(paper.get("attachments") or []),
            "chatCount": len(paper.get("chat") or []),
            "notesLength": len(str(paper.get("notes") or "")),
            "addedAt": paper.get("addedAt"),
            "updatedAt": paper.get("updatedAt"),
        }
        if score is not None:
            item["score"] = _js_number(score)
        return item

    def pdf_exists(self, pdf_file):
        if not pdf_file:
            return False
        abs_path = safe_path_join(self.db.papers_dir, pdf_file)
        if not abs_path:
            return False
        try:
            return os.path.isfile(abs_path)
        except OSError:
            return False
