"""共享内核 · 布尔检索查询解析与求值（移植自 Sci-lib shared/pure/boolean-query.js）。

契约：shared/contracts/api.md §检索（布尔语法 / 11 个字段限定 / year 区间）。零 I/O、零全局态。

T2/T5 依赖的稳定入口：
    parse(query) -> AST | None        只返回 AST 根节点，与 JS 完全同构：
        {"type":"term","value":str,"phrase":bool,"field":str}
        {"type":"and"|"or","left":node,"right":node} / {"type":"not","child":node}
    parse_query(query) -> dict        Node parse() 同构 bundle（terms/allTerms/negatedTerms/
                                      fieldTerms/hasBoolean/needsEvaluator/error，键名 camelCase）
    evaluate(ast, haystack, opts=None)  haystack: str | 0参文本提供者 | 1参 matcher(node) |
                                        2参 matcher(field, value)；opts={"field_text": fn(field)}
    filter_items(items, query, text_of, opts=None)  与 JS filterItems 完全一致
    matches(paper, query, opts=None)  便捷入口（paper 是 dict）
    词表：positive_terms / all_positive_terms / negated_terms / field_terms / has_field_terms
    判定：needs_evaluator / has_boolean / has_field_syntax / normalize_field / hit / year_hit
    外部源：plain_query / to_arxiv_query / supports_boolean / BOOLEAN_CAPABLE
"""
from __future__ import annotations

import inspect
import re

# 字段别名 → 规范名（与 JS FIELD_ALIASES 逐字一致）
FIELD_ALIASES = {
    "title": "title", "titles": "title", "author": "author", "authors": "author", "au": "author",
    "abstract": "abstract", "summary": "abstract", "摘要": "abstract",
    "journal": "journal", "venue": "journal", "publication": "journal", "期刊": "journal",
    "year": "year", "years": "year", "年": "year", "年份": "year", "type": "type", "类型": "type",
    "tag": "tag", "tags": "tag", "标签": "tag", "doi": "doi", "keyword": "keyword",
    "keywords": "keyword", "关键词": "keyword", "note": "note", "notes": "note", "笔记": "note",
    "fulltext": "fulltext", "body": "fulltext", "text": "fulltext", "全文": "fulltext",
}

BOOLEAN_CAPABLE = ("openalex", "europepmc", "arxiv")

# 词内终止符：空白、括号、引号、操作符（对应 JS 词终止字符类）
_WORD_STOP_RE = re.compile(r'[\s()"“”&|!]')
_FIELD_SYNTAX_RE = re.compile(r"(^|[\s(（])([A-Za-z\u4e00-\u9fff]{1,12})\s*:")
_HAS_BOOLEAN_WORD_RE = re.compile(r"\b(AND|OR|NOT)\b")
_HAS_BOOLEAN_CHAR_RE = re.compile(r'[&|!]|[()]|["“”]')
_HAS_BOOLEAN_CJK_RE = re.compile("[与或非]")
_WS_SPLIT_RE = re.compile(r"\s+")
_WS_HAS_RE = re.compile(r"\s")
_YEAR_TOKEN_RE = re.compile(r"\b(1[6-9]\d{2}|20\d{2}|21\d{2})\b")
_YEAR_RANGE_RE = re.compile(r"^(\d{4})?\s*[-–—]\s*(\d{4})?$")
_YEAR_SINGLE_RE = re.compile(r"^(\d{4})$")

# 正则转义字符集（等价 JS escapeRegExp 的字符类）
_ESCAPE_CHARS = frozenset("*+?^$.{}()|[]\\")

# paper dict 里各规范字段可能对应的键
_FIELD_KEYS = {
    "title": ("title", "titleZh"), "author": ("authors", "authorsText", "author", "creator"),
    "abstract": ("summary", "abstract", "summaryZh"), "journal": ("journal", "venue", "publisher"),
    "year": ("year", "date", "published", "publishedAt"), "type": ("type", "kind", "entryType"),
    "tag": ("tags", "tag"), "doi": ("doi", "DOI"), "keyword": ("keywords", "keyword"),
    "note": ("note", "notes", "comment"), "fulltext": ("fulltext", "fullText", "body", "text", "content"),
}
_TEXT_ORDER = ("title", "author", "abstract", "journal", "year", "tag", "keyword", "doi", "note", "fulltext")


def _s(value) -> str:
    return "" if value is None else str(value)


def _dedupe(items):
    return list(dict.fromkeys(items))


def normalize_field(name) -> str:
    """字段别名 → 规范名；未知字段返回空串（当作普通文本，不做字段限定）。"""
    return FIELD_ALIASES.get(_s(name).strip().lower(), "")


def has_field_syntax(query) -> bool:
    """查询里是否含「已知字段名 + 冒号」写法（http:// 这类不算）。"""
    for m in _FIELD_SYNTAX_RE.finditer(_s(query)):
        if normalize_field(m.group(2)):
            return True
    return False


def has_boolean(query) -> bool:
    """是否真的用了布尔语法（普通关键词不算）。AND/OR/NOT 大写才算，与 JS 一致。"""
    text = _s(query)
    if _HAS_BOOLEAN_WORD_RE.search(text):
        return True
    if _HAS_BOOLEAN_CHAR_RE.search(text):
        return True
    return bool(_HAS_BOOLEAN_CJK_RE.search(text))


def needs_evaluator(query) -> bool:
    """是否必须走求值器（布尔语法或字段限定，二者之一）。"""
    return has_boolean(query) or has_field_syntax(query)


def _field_prefix(word: str) -> str:
    at = word.find(":")
    if at <= 0:
        return ""
    return normalize_field(word[:at])


def _read_phrase(text: str, i: int):
    """从引号处读到收尾引号，返回 (短语, 新下标)。"""
    ch = text[i]
    close = "”" if ch == "“" else ch
    j = i + 1
    buf = []
    n = len(text)
    while j < n and text[j] != close:
        buf.append(text[j])
        j += 1
    if j < n:
        j += 1  # 吃掉收尾引号
    return "".join(buf).strip(), j


def tokenize(query):
    """把查询串切成 token 序列（与 JS tokenize 同构）。"""
    text = _s(query)
    tokens = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
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
        if ch in ('"', "“", "”"):
            phrase, i = _read_phrase(text, i)
            if phrase:
                tokens.append({"type": "term", "value": phrase, "phrase": True})
            continue
        if ch == "&" and i + 1 < n and text[i + 1] == "&":
            tokens.append({"type": "AND"})
            i += 2
            continue
        if ch == "|" and i + 1 < n and text[i + 1] == "|":
            tokens.append({"type": "OR"})
            i += 2
            continue
        if ch == "!":
            tokens.append({"type": "NOT"})
            i += 1
            continue
        if ch == "与":
            tokens.append({"type": "AND"})
            i += 1
            continue
        if ch == "或":
            tokens.append({"type": "OR"})
            i += 1
            continue
        if ch == "非":
            tokens.append({"type": "NOT"})
            i += 1
            continue
        j = i
        word = []
        while j < n and not _WORD_STOP_RE.match(text[j]) and text[j] not in "与或非":
            word.append(text[j])
            j += 1
        i = j
        word = "".join(word)
        if not word:
            i += 1
            continue
        upper = word.upper()
        if upper == "AND":
            tokens.append({"type": "AND"})
            continue
        if upper == "OR":
            tokens.append({"type": "OR"})
            continue
        if upper == "NOT" or word == "-":
            tokens.append({"type": "NOT"})
            continue
        field = _field_prefix(word)
        if field:
            rest = word[word.index(":") + 1:].strip()
            if rest:
                tokens.append({"type": "term", "value": rest, "phrase": False, "field": field})
                continue
            # 冒号后直接跟引号短语
            if i < n and text[i] in ('"', "“", "”"):
                phrase, i = _read_phrase(text, i)
                if phrase:
                    tokens.append({"type": "term", "value": phrase, "phrase": True, "field": field})
                continue
            # 冒号后什么都没有：忽略这一项，别让它把结果全筛没
            continue
        tokens.append({"type": "term", "value": word, "phrase": False, "field": ""})
    return tokens


def parse_query(query):
    """解析成 Node parse() 同构的 bundle（键名保持 camelCase，可整字典对照）。"""
    tokens = tokenize(query)
    terms, all_terms, negated, field_terms = [], [], [], []
    pos = 0
    has_bool = False
    neg_depth = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def eat(kind):
        nonlocal pos
        tok = peek()
        if tok and tok["type"] == kind:
            pos += 1
            return True
        return False

    def add_term(value, field):
        if not value:
            return
        (negated if neg_depth > 0 else all_terms).append(value)
        if field:
            if neg_depth == 0:
                field_terms.append({"field": field, "value": value})
        else:
            (negated if neg_depth > 0 else terms).append(value)

    def parse_or():
        nonlocal pos, has_bool
        node = parse_and()
        while peek() and peek()["type"] == "OR":
            has_bool = True
            pos += 1
            node = {"type": "or", "left": node, "right": parse_and()}
        return node

    def parse_and():
        nonlocal pos, has_bool
        node = parse_not()
        while True:
            tok = peek()
            if not tok:
                break
            if tok["type"] == "AND":
                has_bool = True
                pos += 1
                node = {"type": "and", "left": node, "right": parse_not()}
                continue
            if tok["type"] in ("OR", "rparen"):
                break
            # 相邻词默认 AND：a b == a AND b
            node = {"type": "and", "left": node, "right": parse_not()}
        return node

    def parse_not():
        nonlocal pos, has_bool, neg_depth
        tok = peek()
        flipped = bool(tok and tok["type"] == "NOT")
        if flipped:
            has_bool = True
            pos += 1
        before = neg_depth
        if flipped:
            neg_depth += 1
        node = parse_primary()
        neg_depth = before
        return {"type": "not", "child": node} if flipped else node

    def parse_primary():
        nonlocal pos, has_bool, neg_depth
        tok = peek()
        if not tok:
            return None
        if tok["type"] == "lparen":
            has_bool = True
            pos += 1
            before = neg_depth
            node = parse_or()      # 括号内保持当前的否定作用域
            neg_depth = before
            eat("rparen")
            return node
        if tok["type"] == "term":
            pos += 1
            value = tok["value"]
            field = tok.get("field") or ""
            add_term(value, field)
            return {"type": "term", "value": value, "phrase": bool(tok.get("phrase")), "field": field}
        # 意外 token（例如多余的右括号）：当作空
        pos += 1
        return None

    ast = parse_or()
    return {
        "ast": ast,
        "terms": _dedupe(terms),
        "allTerms": _dedupe(all_terms),
        "negatedTerms": _dedupe(negated),
        "fieldTerms": field_terms,
        "hasBoolean": has_bool,
        "needsEvaluator": has_bool or len(field_terms) > 0,
        "error": "",
    }


def parse(query):
    """只返回 AST 根节点（空查询返回 None）；bundle 用 parse_query()。"""
    return parse_query(query)["ast"]


def _escape_re(value: str) -> str:
    return "".join(("\\" + ch) if ch in _ESCAPE_CHARS else ch for ch in value)


def hit(haystack, term, phrase=False) -> bool:
    """文本是否命中某个词：短语允许中间空白差异（PDF 断行），普通词按原子串（忽略大小写）。"""
    text = _s(haystack)
    if not text:
        return False
    needle = _s(term)
    if not needle:
        return False
    if phrase or _WS_HAS_RE.search(needle):
        parts = [_escape_re(p) for p in _WS_SPLIT_RE.split(needle)]
        return re.search(r"[\s\u00a0]*".join(parts), text, re.IGNORECASE) is not None
    return needle.lower() in text.lower()


def year_hit(field_value, spec) -> bool:
    """年份匹配：year:2013 / 2013-2018 / 2013- / -2018（区间按数值比较）。"""
    raw = _s(spec).strip()
    years = _YEAR_TOKEN_RE.findall(_s(field_value))
    if not years:
        return False
    nums = [int(y) for y in years]
    m = _YEAR_RANGE_RE.match(raw)
    if m:
        start = int(m.group(1)) if m.group(1) else float("-inf")
        end = int(m.group(2)) if m.group(2) else float("inf")
        return any(start <= y <= end for y in nums)
    single = _YEAR_SINGLE_RE.match(raw)
    if single:
        return int(single.group(1)) in nums
    return hit(field_value, raw, False)


def _arity(fn) -> int:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return 0
    count = 0
    for param in sig.parameters.values():
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD):
            count += 1
        elif param.kind is param.VAR_POSITIONAL:
            return 2
    return count


def _walk(node, text, field_text, matcher) -> bool:
    if not node:
        return True
    kind = node.get("type")
    if kind == "term":
        if matcher is not None:
            return bool(matcher(node))
        target = text
        if node.get("field") and field_text:
            try:
                target = _s(field_text(node["field"]))
            except Exception:  # noqa: BLE001  与 JS 的 try/catch 一致
                target = ""
        if node.get("field") == "year":
            return year_hit(target, node.get("value"))
        return hit(target, node.get("value"), bool(node.get("phrase")))
    if kind == "not":
        return not _walk(node.get("child"), text, field_text, matcher)
    if kind in ("and", "or"):
        left = _walk(node.get("left"), text, field_text, matcher)
        if kind == "or" and left:
            return True
        if kind == "and" and not left:
            return False
        return _walk(node.get("right"), text, field_text, matcher)
    return True


def evaluate(ast, haystack, opts=None) -> bool:
    """求值。haystack 按可调用对象的位置参数个数判定：字符串=文本；0 参=文本提供者（JS 的
    () => text）；1 参=matcher(node)；2 参=matcher(field, value)（field None 表示非字段项）。
    opts: {"field_text": fn(field) -> str}（也接受 "fieldText"）；不传时字段项退化为整段文本匹配。
    """
    o = opts or {}
    field_text = o.get("field_text", o.get("fieldText"))
    if callable(haystack):
        arity = _arity(haystack)
        if arity >= 2:
            def matcher(node, _fn=haystack):
                return _fn(node.get("field") or None, node.get("value"))
            return _walk(ast, "", field_text, matcher)
        if arity == 1:
            return _walk(ast, "", field_text, haystack)
        return _walk(ast, _s(haystack()), field_text, None)
    return _walk(ast, _s(haystack), field_text, None)


def _ast_of(value):
    """接受 AST 根节点或 parse_query() 的 bundle。"""
    if isinstance(value, dict) and "ast" in value:
        return value["ast"]
    return value


def _collect(node, plain, all_positive, negative, fields, negated=False):
    if not node:
        return
    kind = node.get("type")
    if kind == "term":
        value = node.get("value")
        if not value:
            return
        field = node.get("field") or ""
        (negative if negated else all_positive).append(value)
        if field:
            if not negated:
                fields.append({"field": field, "value": value})
        else:
            (negative if negated else plain).append(value)
        return
    if kind == "not":
        _collect(node.get("child"), plain, all_positive, negative, fields, True)
        return
    if kind in ("and", "or"):
        _collect(node.get("left"), plain, all_positive, negative, fields, negated)
        _collect(node.get("right"), plain, all_positive, negative, fields, negated)


def _terms(ast):
    plain, all_positive, negative, fields = [], [], [], []
    _collect(_ast_of(ast), plain, all_positive, negative, fields)
    return plain, all_positive, negative, fields


def positive_terms(ast):
    """正项词表（粗筛用，等价 JS parsed.terms）：不含字段限定项、不含 NOT 作用域内的词，去重保序。"""
    return _dedupe(_terms(ast)[0])


def all_positive_terms(ast):
    """含字段项 value 的全部正项（等价 JS allTerms，给不认字段的外部源降级）。"""
    return _dedupe(_terms(ast)[1])


def negated_terms(ast):
    """NOT 作用域内的全部词（等价 JS negatedTerms）。"""
    return _dedupe(_terms(ast)[2])


def field_terms(ast):
    """非取反的字段限定项 [{field, value}]（等价 JS fieldTerms）。"""
    return _terms(ast)[3]


def _has_field(node) -> bool:
    if not node:
        return False
    if node.get("type") == "term":
        return bool(node.get("field"))
    if node.get("type") == "not":
        return _has_field(node.get("child"))
    return _has_field(node.get("left")) or _has_field(node.get("right"))


def has_field_terms(ast) -> bool:
    """AST 里任意位置是否出现字段限定项（含 NOT 子树）。"""
    return _has_field(_ast_of(ast))


def paper_field(paper, field) -> str:
    """从 paper dict 取某个规范字段的文本（matches() 的字段限定用）。"""
    if not isinstance(paper, dict):
        return "" if paper is None else _s(paper)
    keys = _FIELD_KEYS.get(_s(field))
    if not keys:
        return ""
    out = []
    for key in keys:
        value = paper.get(key)
        if value is None or value == "" or isinstance(value, dict):
            continue
        if isinstance(value, (list, tuple, set)):
            out.append(" ".join(_s(v) for v in value if v is not None))
        else:
            out.append(_s(value))
    return " ".join(part for part in out if part)


def paper_text(paper) -> str:
    """把一篇 paper 的检索文本拼起来（标题/作者/摘要/期刊/年份/标签/关键词/DOI/笔记/全文）。"""
    if not isinstance(paper, dict):
        return "" if paper is None else _s(paper)
    parts = [paper_field(paper, field) for field in _TEXT_ORDER]
    return "\n".join(part for part in parts if part)


def matches(paper, query, opts=None) -> bool:
    """便捷入口：这篇 paper 命中这个查询吗（空查询 = 全通过）。"""
    o = opts or {}
    parsed = parse_query(query)
    if not parsed["ast"]:
        return True
    field_text = o.get("field_text", o.get("fieldText"))
    text = o.get("text")
    if text is None:
        text = paper_text(paper) if isinstance(paper, dict) else _s(paper)
    if field_text is None and isinstance(paper, dict):
        field_text = lambda field, _paper=paper: paper_field(_paper, field)
    return evaluate(parsed["ast"], lambda: _s(text), {"field_text": field_text})


def filter_items(items, query, text_of, opts=None):
    """对一批结果做布尔复核（与 JS filterItems 一致：无布尔语法/字段限定时原样返回）。"""
    seq = list(items or [])
    parsed = parse_query(query)
    if not parsed["ast"] or not parsed["needsEvaluator"]:
        return seq
    o = opts or {}
    field_text = o.get("field_text", o.get("fieldText"))
    out = []
    for item in seq:
        text = _s(text_of(item))      # 注意：这里必须是 0 参闭包，别把可调用对象当成 matcher
        if field_text is not None:
            def _field_of(field, _item=item, _fn=field_text):
                return _fn(_item, field)
            ok = evaluate(parsed["ast"], lambda: text, {"field_text": _field_of})
        else:
            ok = evaluate(parsed["ast"], lambda: text)
        if ok:
            out.append(item)
    return out


def supports_boolean(source) -> bool:
    """这条外部源是否原生支持布尔语法（只有 openalex/europepmc/arxiv）。"""
    return _s(source) in BOOLEAN_CAPABLE


def plain_query(query) -> str:
    """给不认布尔的源用一个「去掉操作符的朴素查询」（用 allTerms：字段项的词本身要保留）。"""
    parsed = parse_query(query)
    if not parsed["allTerms"]:
        stripped = re.sub(r"\b(AND|OR|NOT)\b", " ", _s(query), flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", stripped).strip()
    return " ".join(parsed["allTerms"])


def to_arxiv_query(query) -> str:
    """arXiv 专用：把 AST 摊成 all:"词"+AND+all:"词"+ANDNOT+all:"词" 的形式。"""
    parsed = parse_query(query)
    positive, negative = [], []

    def visit(node, negated):
        if not node:
            return
        kind = node.get("type")
        if kind == "term":
            cleaned = _s(node.get("value")).replace('"', "").strip()
            if cleaned:
                (negative if negated else positive).append('all:"' + cleaned + '"')
            return
        if kind == "not":
            visit(node.get("child"), not negated)
        elif kind == "and":
            visit(node.get("left"), negated)
            visit(node.get("right"), negated)
        elif kind == "or":
            # arXiv 不支持带括号的 OR：退化成「都作为正项」，结果更多但不会漏
            visit(node.get("left"), negated)
            visit(node.get("right"), negated)

    visit(parsed["ast"], False)
    bits = []
    if positive:
        bits.append("+AND+".join(_dedupe(positive)))
    neg = _dedupe(negative)
    if neg:
        bits.append("".join("+ANDNOT+" + item for item in neg))
    return "".join(bits) or plain_query(query)
