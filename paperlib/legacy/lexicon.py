# -*- coding: utf-8 -*-
"""中英术语表：加载 terms.json 并提供检索 / 中英扩展。

移植自 旧 PaperLibrary 工程\\local_rank.py 的 GLOSSARY
（119 条中英术语对照）+ 合并后补充的化学/材料/生物交叉词条，共 155 条。
契约名：terms.search（GET /api/terms/search）

数据文件 paperlib/legacy/terms.json 每条形如：
  {"zh": "亲核取代", "en": "nucleophilic substitution", "aliases": ["SN2", "SN1"], "field": "反应与结构"}

检索口径（契约 §9）：q 中英均可，对 zh / en / aliases 做子串匹配（大小写不敏感），
zh 命中优先于 en、en 优先于 aliases，同分按 zh 字典序稳定排序。
"""
from __future__ import annotations

import json
from pathlib import Path

TERMS_FILE = Path(__file__).with_name("terms.json")
_CACHE = None


def load_terms():
    """读术语表（进程内缓存；文件缺失时返回空表，端点仍然可用）。"""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        with open(TERMS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = []
    terms = []
    for raw in data if isinstance(data, list) else []:
        if not isinstance(raw, dict):
            continue
        zh = str(raw.get("zh") or "").strip()
        en = str(raw.get("en") or "").strip()
        if not zh and not en:
            continue
        aliases = [str(a).strip() for a in (raw.get("aliases") or []) if str(a).strip()]
        terms.append({
            "zh": zh,
            "en": en,
            "aliases": aliases,
            "field": str(raw.get("field") or "通用").strip() or "通用",
        })
    _CACHE = terms
    return terms


def count():
    return len(load_terms())


def _normalize(text):
    return str(text or "").strip().lower()


def _score(term, needle):
    """0 = 不命中；分数越小越靠前（zh 起首 < zh 包含 < en 起首 < en 包含 < 别名）。"""
    zh = _normalize(term["zh"])
    en = _normalize(term["en"])
    if zh == needle:
        return 0
    if zh.startswith(needle):
        return 1
    if needle in zh:
        return 2
    if en == needle:
        return 3
    if en.startswith(needle):
        return 4
    if needle in en:
        return 5
    for alias in term["aliases"]:
        low = _normalize(alias)
        if low == needle:
            return 6
        if needle in low:
            return 7
    return None


def search(query, limit=20):
    """返回 (items, total)；total 是命中总数（limit 之前）。"""
    needle = _normalize(query)
    if not needle:
        return [], 0
    hits = []
    for term in load_terms():
        score = _score(term, needle)
        if score is not None:
            hits.append((score, term["zh"], term))
    hits.sort(key=lambda item: (item[0], item[1]))
    total = len(hits)
    try:
        limit = max(0, int(limit))
    except (TypeError, ValueError):
        limit = 20
    items = [term for _score_value, _zh, term in hits[:limit]] if limit else []
    return items, total


def english_forms(query):
    """把中文查询扩展成英文同义写法（图表检索用：中文搜英文图注）。"""
    needle = _normalize(query)
    if not needle:
        return []
    out = []
    for term in load_terms():
        if needle in _normalize(term["zh"]) or needle in _normalize(term["en"]):
            if term["en"] and term["en"] not in out:
                out.append(term["en"])
            for alias in term["aliases"]:
                if alias not in out:
                    out.append(alias)
    return out[:12]
