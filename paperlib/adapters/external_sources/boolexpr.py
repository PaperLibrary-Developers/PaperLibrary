"""外部检索的查询降级与复核：**薄适配层**，逻辑全部复用 shared/pure/boolean_query.py。

契约名：search.external(query, filters) → ExternalItem[]（布尔下推与本地复核部分）

为什么还要这一层：外部源能力不一——OpenAlex / Europe PMC 原生布尔，arXiv 只认
all:"词" + ANDNOT，Crossref / Semantic Scholar 只做相关性检索。适配层需要三件事：
  1) 认出查询里的布尔与字段语法（has_boolean / has_field_syntax）；
  2) 把查询降级成各源能吃的形态（plain_query / to_arxiv_query / field_terms）；
  3) 结果回来后用同一套语法复核（filter_items）——宁可少召回，也不给不符的结果。
这三件事的语义与本地检索必须**完全一致**，所以解析/求值一律用 shared.pure.boolean_query
（T1 交付，与 JS 同构）；本文件只把它的接口翻译成适配层顺手的样子（少传 opts 字典）。
"""
from __future__ import annotations

from shared.pure import boolean_query as _bq

# 真的懂布尔的外部源（Crossref / Semantic Scholar 只能做相关性检索）
BOOL_CAPABLE = tuple(_bq.BOOLEAN_CAPABLE)

normalize_field = _bq.normalize_field
has_boolean = _bq.has_boolean
has_field_syntax = _bq.has_field_syntax
needs_evaluator = _bq.needs_evaluator
plain_query = _bq.plain_query
to_arxiv_query = _bq.to_arxiv_query
supports_boolean = _bq.supports_boolean
hit = _bq.hit
year_hit = _bq.year_hit


def field_terms(query) -> list:
    """非取反的字段限定项 [{field, value}]（下推给各源自己的字段语法）。"""
    return [dict(item) for item in _bq.parse_query(query)["fieldTerms"]]


def all_terms(query) -> list:
    """含字段项 value 的全部正项（给不认字段的源降级用）。"""
    return list(_bq.parse_query(query)["allTerms"])


def filter_items(items, query, text_of, field_text=None) -> list:
    """对一批外部结果做布尔/字段复核（Crossref 这类不认布尔的源靠这一步纠偏）。

    text_of(item) → 用于匹配的整段文本；field_text(item, field) → 某个字段的文本。
    """
    opts = {"field_text": field_text} if field_text else None
    return _bq.filter_items(list(items or []), query, text_of, opts)
