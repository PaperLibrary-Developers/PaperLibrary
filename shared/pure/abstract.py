"""共享内核 · 摘要来源编排（移植自 Sci-lib shared/pure/abstract.js）。

契约：摘要三级降级 Crossref → PDF → OpenAlex（docs 工程计划 §4 B4 摘要回填）。
纯函数部分：清洗（去 JATS 标签 / 去出版社网页残渣）、按优先级挑一条、来源说明。
零 I/O（网络取摘要由 adapter 负责）。

另外提供 docs §3.5 提到的别名：build_fallback = choose_abstract（extract 同义）。
"""
from __future__ import annotations

import re

from shared.pure.text_repair import repair_text

# 短于这个长度的「摘要」基本没有价值（目录宣传语一般 300+，真正的摘要 600+）
MIN_USEFUL = 200

# 出版社网页的导航/元信息样板——出现这些就说明这压根不是摘要
CHROME_MARKERS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"ADVERTISEMENT",
    r"RETURN TO ISSUE",
    r"View Author Information",
    r"Cite this\s*:",
    r"Publication Date\b",
    r"Publication History\b",
    r"Downloaded (?:via|on|from)\b",
    r"Request reuse permissions",
    r"Article Views\b",
    r"Altmetric",
    r"Get e-?Alerts",
    r"Add to (?:View|Favorites)",
    r"Export\s*RIS",
    r"Share on\s*(?:Facebook|Twitter)",
    r"Skip to (?:main content|content)",
    r"https?://pubs\.acs\.org",
    r"Copyright ©",
    r"LEARN ABOUT THESE METRICS",
))

# 摘要开头常见的标签，剥掉它
_LEAD_LABEL_RE = re.compile(
    r"^\s*(?:A\s?B\s?S\s?T\s?R\s?A\s?C\s?T|Abstract|ABSTRACT|Conspectus|CONSPECTUS|Summary|"
    r"Graphical abstract|摘\s*要)\s*[:.．。\-—–]?\s*",
    re.IGNORECASE,
)

_CHROME_HEAD_RE = re.compile(
    r"^\s*(?:ADVERTISEMENT|RETURN TO ISSUE|Skip to|View Author Information|Cite this)\b",
    re.IGNORECASE,
)

_TAG_RE = re.compile(r"<[^>]*>")
_ENTITY_NUM_RE = re.compile(r"&#x?[0-9a-f]+;", re.IGNORECASE)


def _s(value) -> str:
    return "" if value is None else str(value)


def strip_tags(value) -> str:
    """去掉 JATS 标签与 HTML 实体（Crossref 的 abstract 常带这些）。"""
    s = _TAG_RE.sub(" ", _s(value))
    s = s.replace("&lt;", "<").replace("&gt;", ">")
    s = s.replace("&amp;", "&").replace("&quot;", '"')
    return _ENTITY_NUM_RE.sub(" ", s)


def looks_like_chrome(text) -> bool:
    """这条文本像不像「出版社网页残渣」而不是摘要。"""
    s = _s(text)
    if not s.strip():
        return False
    hits = sum(1 for marker in CHROME_MARKERS if marker.search(s))
    if hits >= 2:
        return True
    # 开头就是导航词，也判为残渣
    return _CHROME_HEAD_RE.search(s) is not None


def clean_source_abstract(text) -> str:
    """清洗来自接口（Crossref / OpenAlex）的摘要；返回空字符串表示「这段不能用」。"""
    s = strip_tags(text)
    if not s.strip():
        return ""
    if looks_like_chrome(s):
        return ""
    s = _LEAD_LABEL_RE.sub("", s, count=1)
    return repair_text(s)


def choose_abstract(cands):
    """按优先级挑一条摘要：Crossref ≥200 → PDF ≥200 → OpenAlex ≥200 → 短但有。

    cands: {"crossref": str, "pdf": {"text","method"} | None, "openalex": str}
    return: {"text", "source": "crossref"|"pdf"|"openalex", "method"?} | None
    """
    c = cands or {}
    crossref = clean_source_abstract(c.get("crossref"))
    if len(crossref) >= MIN_USEFUL:
        return {"text": crossref, "source": "crossref"}

    pdf_meta = c.get("pdf")
    pdf_text = pdf_meta.get("text") if isinstance(pdf_meta, dict) else None
    pdf = repair_text(pdf_text) if pdf_text else ""
    if len(pdf) >= MIN_USEFUL:
        return {"text": pdf, "source": "pdf", "method": pdf_meta.get("method")}

    openalex = clean_source_abstract(c.get("openalex"))
    if len(openalex) >= MIN_USEFUL:
        return {"text": openalex, "source": "openalex"}

    # 都不够好：有 Crossref 的就先用着（哪怕短），否则留空
    if crossref:
        return {"text": crossref, "source": "crossref"}
    if pdf:
        return {"text": pdf, "source": "pdf", "method": pdf_meta.get("method")}
    return None


def describe_source(source, method=None) -> str:
    """给界面看的中文来源说明。"""
    if source == "crossref":
        return "Crossref"
    if source == "openalex":
        return "OpenAlex"
    if source == "pdf":
        return "PDF（带摘要标题的那一段）" if method == "heading" else "PDF（首页摘要段）"
    return "手工填写"


# docs §3.5 的兼容别名
build_fallback = choose_abstract
extract = choose_abstract
