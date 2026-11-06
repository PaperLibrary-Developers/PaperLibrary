"""外部检索的查询降级与结果复核（移植自 Sci-lib shared/pure/boolean-query.js 的调用子集）。

契约名：search.external(query, filters) → ExternalItem[]（布尔下推与本地复核部分）

为什么单独一份：外部源能力不一——OpenAlex / Europe PMC 原生布尔，arXiv 只认
all:"词" + ANDNOT，Crossref / Semantic Scholar 只做相关性检索。适配层要做到三件事：
  1) 认出查询里的布尔与字段语法（has_boolean / has_field_syntax）；
  2) 把查询降级成各源能吃的形态（plain_query / to_arxiv_query / field_terms）；
  3) 结果回来后用同一套语法复核（filter_items）——宁可少召回，也不给不符的结果。

shared/pure/boolean_query.py（T1）是本地检索的权威实现；本文件是它的**语法子集**，
只服务适配层的下推与复核（T1 稳定后可换成复用，test_search_adapters.py 有对照断言）。
"""
from __future__ import annotations

import re

# 字段别名 → 规范名（与 shared/pure/boolean_query.py 的 FIELD_ALIASES 同名同义）
FIELD_ALIASES = {
    "title": "title", "titles": "title",
    "author": "author", "authors": "author", "au": "author",
    "abstract": "abstract", "summary": "abstract", "摘要": "abstract",
    "journal": "journal", "venue": "journal", "publication": "journal", "期刊": "journal",
    "year": "year", "years": "year", "年": "year", "年份": "year",
    "type": "type", "类型": "type",
    "tag": "tag", "tags": "tag", "标签": "tag",
    "doi": "doi",
    "keyword": "keyword", "keywords": "keyword", "关键词": "keyword",
    "note": "note", "notes": "note", "笔记": "note",
    "fulltext": "fulltext", "body": "fulltext", "text": "fulltext", "全文": "fulltext",
}

# 真的懂布尔的外部源（Crossref / Semantic Scholar 只能做相关性检索）
BOOL_CAPABLE = ("openalex", "europepmc", "arxiv")

_BOOL_WORD_RE = re.compile(r"\b(?:AND|OR|NOT)\b")
_BOOL_PUNCT_RE = re.compile('[&|!()]|["\u201c\u201d]')
_BOOL_CJK_RE = re.compile("[\u4e0e\u6216\u975e]")
_SPLIT_CHARS = '()"\u201c\u201d&|!'
_CJK_OPS = {"\u4e0e": "AND", "\u6216": "OR", "\u975e": "NOT"}   # 与 或 非


def normalize_field(name) -> str:
    """规范字段名；未知字段返回空串（＝当普通文本，不做字段限定）。"""
    return FIELD_ALIASES.get(str(name or "").strip().lower(), "")


def has_boolean(text) -> bool:
    """查询里是否真的用了布尔语法（不是普通关键词）。"""
    s = "" if text is None else str(text)
    if _BOOL_WORD_RE.search(s):
        return True
    if _BOOL_PUNCT_RE.search(s):
        return True
    return bool(_BOOL_CJK_RE.search(s))


def has_field_syntax(text) -> bool:
    """查询里是否含「已知字段:值」写法（title:… / author:…）。只认已知字段名。"""
    s = "" if text is None else str(text)
    for match in re.finditer(r"(^|[\s(\uff08])([A-Za-z\u4e00-\u9fff]{1,12})\s*:", s):
        if normalize_field(match.group(2)):
            return True
    return False


def supports_boolean(source) -> bool:
    return str(source or "") in BOOL_CAPABLE


def needs_evaluator(text) -> bool:
    return has_boolean(text) or has_field_syntax(text)


def _field_prefix(word) -> str:
    """一个词是不是「字段:值」的前缀；冒号后必须还有内容或紧跟引号。"""
    at = str(word).find(":")
    if at <= 0:
        return ""
    return normalize_field(word[:at])


def tokenize(text) -> list:
    """切词：AND/OR/NOT（含 && || ! 与「与或非」）、括号、引号短语、字段:值。"""
    s = "" if text is None else str(text)
    tokens = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append({"type": "lparen"})
            i += 1
            continue
        if ch == ")":
            tokens.append({"type": "rparen"})
            i += 1
            continue
        if ch in ('"', "\u201c", "\u201d"):
            close = "\u201d" if ch == "\u201c" else ch
            j = i + 1
            buf = []
            while j < n and s[j] != close:
                buf.append(s[j])
                j += 1
            if j < n:
                j += 1
            phrase = "".join(buf).strip()
            if phrase:
                tokens.append({"type": "term", "value": phrase, "phrase": True, "field": ""})
            i = j
            continue
        if s.startswith("&&", i):
            tokens.append({"type": "AND"})
            i += 2
            continue
        if s.startswith("||", i):
            tokens.append({"type": "OR"})
            i += 2
            continue
        if ch == "!":
            tokens.append({"type": "NOT"})
            i += 1
            continue
        if ch in _CJK_OPS:
            tokens.append({"type": _CJK_OPS[ch]})
            i += 1
            continue
        j = i
        buf = []
        while j < n and not s[j].isspace() and s[j] not in _SPLIT_CHARS and s[j] not in _CJK_OPS:
            buf.append(s[j])
            j += 1
        word = "".join(buf)
        i = j
        if not word:
            i += 1
            continue
        upper = word.upper()
        if upper in ("AND", "OR", "NOT") or word == "-":
            tokens.append({"type": "NOT" if upper == "NOT" else upper})
            continue
        field = _field_prefix(word)
        if field:
            rest = word[word.find(":") + 1:].strip()
            if rest:
                tokens.append({"type": "term", "value": rest, "phrase": False, "field": field})
                continue
            # 冒号后面直接跟引号短语：title:"molecular capsule"
            if i < n and s[i] in ('"', "\u201c", "\u201d"):
                close = "\u201d" if s[i] == "\u201c" else s[i]
                k = i + 1
                pbuf = []
                while k < n and s[k] != close:
                    pbuf.append(s[k])
                    k += 1
                if k < n:
                    k += 1
                i = k
                phrase = "".join(pbuf).strip()
                if phrase:
                    tokens.append({"type": "term", "value": phrase, "phrase": True, "field": field})
                continue
            continue  # 「title:」后面什么都没有：忽略这一项，别把结果全筛没
        tokens.append({"type": "term", "value": word, "phrase": False, "field": ""})
    return tokens


def parse(text) -> dict:
    """解析成 AST；字段项、正项词、排除项按 JS 版语义一并收集。"""
    tokens = tokenize(text)
    terms, all_terms, negated, field_list = [], [], [], []
    state = {"has_boolean": False, "neg_depth": 0, "pos": 0}

    def peek():
        pos = state["pos"]
        return tokens[pos] if pos < len(tokens) else None

    def eat(kind):
        token = peek()
        if token and token["type"] == kind:
            state["pos"] += 1
            return True
        return False

    def parse_or():
        node = parse_and()
        while peek() and peek()["type"] == "OR":
            state["has_boolean"] = True
            state["pos"] += 1
            node = {"type": "or", "left": node, "right": parse_and()}
        return node

    def parse_and():
        node = parse_not()
        while True:
            token = peek()
            if not token:
                break
            if token["type"] == "AND":
                state["has_boolean"] = True
                state["pos"] += 1
                node = {"type": "and", "left": node, "right": parse_not()}
                continue
            if token["type"] in ("OR", "rparen"):
                break
            node = {"type": "and", "left": node, "right": parse_not()}  # 相邻词默认 AND
        return node

    def parse_not():
        flipped = bool(peek() and peek()["type"] == "NOT")
        if flipped:
            state["has_boolean"] = True
            state["pos"] += 1
        before = state["neg_depth"]
        if flipped:
            state["neg_depth"] += 1
        node = parse_primary()
        state["neg_depth"] = before
        return {"type": "not", "child": node} if flipped else node

    def parse_primary():
        token = peek()
        if not token:
            return None
        if token["type"] == "lparen":
            state["has_boolean"] = True
            state["pos"] += 1
            before = state["neg_depth"]
            node = parse_or()
            state["neg_depth"] = before
            eat("rparen")
            return node
        if token["type"] == "term":
            state["pos"] += 1
            value = token["value"]
            field = token.get("field") or ""
            if value:
                (negated if state["neg_depth"] > 0 else all_terms).append(value)
                if field:
                    if state["neg_depth"] == 0:
                        field_list.append({"field": field, "value": value})
                else:
                    (negated if state["neg_depth"] > 0 else terms).append(value)
            return {"type": "term", "value": value, "phrase": bool(token.get("phrase")), "field": field}
        state["pos"] += 1  # 意外 token（多余的右括号等）：跳过
        return None

    ast = parse_or()
    return {
        "ast": ast,
        "terms": _unique(terms),
        "allTerms": _unique(all_terms),
        "negatedTerms": _unique(negated),
        "fieldTerms": field_list,
        "hasBoolean": state["has_boolean"],
        "needsEvaluator": bool(state["has_boolean"] or field_list),
    }


def _unique(values) -> list:
    seen = set()
    out = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def field_terms(text) -> list:
    return parse(text)["fieldTerms"]


def plain_query(text) -> str:
    """给不支持布尔的源（Crossref / Semantic Scholar）的朴素查询：只留正项词。"""
    parsed = parse(text)
    if not parsed["allTerms"]:
        stripped = re.sub(r"\b(?:AND|OR|NOT)\b", " ", str(text or ""), flags=re.I)
        return re.sub(r"\s+", " ", stripped).strip()
    return " ".join(parsed["allTerms"])


def _escape(term) -> str:
    return re.escape(str(term))


def hit(haystack, term, phrase=False) -> bool:
    """文本是否命中某个词：短语按整段包含（允许中间空白差异），普通词按子串。"""
    text = "" if haystack is None else str(haystack)
    if not text:
        return False
    needle = "" if term is None else str(term)
    if not needle:
        return False
    if phrase or re.search(r"\s", needle):
        pattern = r"[\s\u00a0]*".join(_escape(part) for part in needle.split())
        return re.search(pattern, text, flags=re.I) is not None
    return needle.lower() in text.lower()


def year_hit(field_value, spec) -> bool:
    """年份匹配：year:2013 / year:2013-2018 / year:2013- / year:-2018（按数值比较）。"""
    raw = "" if spec is None else str(spec).strip()
    years = re.findall(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b", "" if field_value is None else str(field_value))
    if not years:
        return False
    nums = [int(year) for year in years]
    match = re.match(r"^(\d{4})?\s*[-\u2013\u2014]\s*(\d{4})?$", raw)
    if match:
        low = int(match.group(1)) if match.group(1) else float("-inf")
        high = int(match.group(2)) if match.group(2) else float("inf")
        return any(low <= year <= high for year in nums)
    single = re.match(r"^(\d{4})$", raw)
    if single:
        return int(single.group(1)) in nums
    return hit(field_value, raw)


def evaluate(ast, text, field_text=None) -> bool:
    """求值；text 可以是字符串或返回字符串的函数（外部源的题录要拼多字段）。"""
    resolved = text() if callable(text) else ("" if text is None else str(text))

    def walk(node, current):
        if not node:
            return True
        kind = node.get("type")
        if kind == "term":
            target = current
            field = node.get("field") or ""
            if field and field_text:
                try:
                    target = str(field_text(field) or "")
                except Exception:  # noqa: BLE001 - 字段取文本失败不应让整条查询报错
                    target = ""
            if field == "year":
                return year_hit(target, node.get("value"))
            return hit(target, node.get("value"), bool(node.get("phrase")))
        if kind == "not":
            return not walk(node.get("child"), current)
        if kind == "and":
            return walk(node.get("left"), current) and walk(node.get("right"), current)
        if kind == "or":
            return walk(node.get("left"), current) or walk(node.get("right"), current)
        return True

    return walk(ast, resolved)


def filter_items(items, query, text_of, field_text=None) -> list:
    """对一批外部结果做布尔/字段复核（Crossref 这类不认布尔的源靠这一步纠偏）。"""
    rows = list(items or [])
    parsed = parse(query)
    if not parsed["ast"] or not parsed["needsEvaluator"]:
        return rows
    out = []
    for item in rows:
        checker = (lambda field, _item=item: field_text(_item, field)) if field_text else None
        if evaluate(parsed["ast"], lambda _item=item: text_of(_item), checker):
            out.append(item)
    return out


def to_arxiv_query(text) -> str:
    """arXiv 专用：摊成 all:"词" +AND+ … +ANDNOT+ …（arXiv 不支持括号与嵌套 OR）。"""
    parsed = parse(text)
    positive, negative = [], []

    def visit(node, negated):
        if not node:
            return
        kind = node.get("type")
        if kind == "term":
            cleaned = str(node.get("value") or "").replace('"', "").strip()
            if not cleaned:
                return
            (negative if negated else positive).append('all:"' + cleaned + '"')
            return
        if kind == "not":
            visit(node.get("child"), not negated)
            return
        if kind == "and":
            visit(node.get("left"), negated)
            visit(node.get("right"), negated)
            return
        if kind == "or":  # OR 退化成「都算正项」：宁多勿漏
            visit(node.get("left"), negated)
            visit(node.get("right"), negated)

    visit(parsed["ast"], False)
    bits = []
    if positive:
        bits.append("+AND+".join(_unique(positive)))
    negs = _unique(negative)
    if negs:
        bits.append("".join("+ANDNOT+" + item for item in negs))
    return "".join(bits) or plain_query(text)
