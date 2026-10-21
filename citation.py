# -*- coding: utf-8 -*-
"""citation.py — 学术引文生成器
支持格式：
  1. BibTeX
  2. GB/T 7714-2015 (中国国标科技论文引用格式)
  3. APA (7th Edition)
  4. Markdown (带超链接与 DOI)
"""
import re
import json
from typing import Dict, Any, List


def _clean_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def parse_authors(raw_authors: Any) -> List[str]:
    """解析论文作者字段（可能是 JSON 字符串、逗号分隔、或列表）"""
    if not raw_authors:
        return []
    if isinstance(raw_authors, list):
        return [str(a).strip() for a in raw_authors if str(a).strip()]
    if isinstance(raw_authors, str):
        raw = raw_authors.strip()
        if raw.startswith("[") and raw.endswith("]"):
            try:
                arr = json.loads(raw)
                if isinstance(arr, list):
                    return [str(a).strip() for a in arr if str(a).strip()]
            except Exception:
                pass
        # 逗号或分号分隔
        parts = re.split(r"[,;、&]|\band\b", raw)
        return [p.strip() for p in parts if p.strip()]
    return []


def to_bibtex(paper: Dict[str, Any]) -> str:
    """生成标准 BibTeX 条目"""
    authors = parse_authors(paper.get("authors"))
    title = _clean_str(paper.get("title") or paper.get("filename", "")).replace(".pdf", "")
    journal = _clean_str(paper.get("journal"))
    year = _clean_str(paper.get("year"))
    doi = _clean_str(paper.get("doi"))

    # 生成引用 citekey: FirstAuthorYear 或 TitleYear
    if authors:
        # 取第一作者姓氏
        first_last = authors[0].split()[-1]
        citekey = re.sub(r"[^a-zA-Z0-9]", "", first_last) + (year or "")
    else:
        # 取标题前两个单词
        words = re.findall(r"[a-zA-Z0-9]+", title)
        citekey = "".join(words[:2]).capitalize() + (year or "")

    if not citekey:
        citekey = f"paper_{paper.get('id', 'item')[:8]}"

    author_str = " and ".join(authors) if authors else "Unknown"

    lines = [f"@article{{{citekey},"]
    lines.append(f"  title = {{{{{title}}}}},")
    lines.append(f"  author = {{{author_str}}},")
    if journal:
        lines.append(f"  journal = {{{journal}}},")
    if year:
        lines.append(f"  year = {{{year}}},")
    if doi:
        lines.append(f"  doi = {{{doi}}},")
        lines.append(f"  url = {{https://doi.org/{doi}}},")
    lines.append("}")
    return "\n".join(lines)


def to_gbt7714(paper: Dict[str, Any]) -> str:
    """生成 GB/T 7714-2015 格式引用：
    张三, 李四, 王五, 等. 论文题目[J]. 期刊名, 年, 卷(期): 页码.
    """
    authors = parse_authors(paper.get("authors"))
    title = _clean_str(paper.get("title") or paper.get("filename", "")).replace(".pdf", "")
    journal = _clean_str(paper.get("journal"))
    year = _clean_str(paper.get("year"))
    doi = _clean_str(paper.get("doi"))

    if authors:
        if len(authors) > 3:
            au_part = ", ".join(authors[:3]) + ", 等"
        else:
            au_part = ", ".join(authors)
    else:
        au_part = "佚名"

    parts = [f"{au_part}. {title}[J]."]
    if journal:
        parts.append(f" {journal},")
    if year:
        parts.append(f" {year}.")
    if doi:
        parts.append(f" DOI: {doi}.")

    return "".join(parts)


def to_apa(paper: Dict[str, Any]) -> str:
    """生成 APA (7th) 格式引用"""
    authors = parse_authors(paper.get("authors"))
    title = _clean_str(paper.get("title") or paper.get("filename", "")).replace(".pdf", "")
    journal = _clean_str(paper.get("journal"))
    year = _clean_str(paper.get("year")) or "n.d."
    doi = _clean_str(paper.get("doi"))

    if authors:
        if len(authors) == 1:
            au_part = authors[0]
        elif len(authors) == 2:
            au_part = f"{authors[0]}, & {authors[1]}"
        elif len(authors) <= 20:
            au_part = ", ".join(authors[:-1]) + f", & {authors[-1]}"
        else:
            au_part = ", ".join(authors[:19]) + f", ... {authors[-1]}"
    else:
        au_part = "Anonymous"

    citation = f"{au_part} ({year}). {title}."
    if journal:
        citation += f" {journal}."
    if doi:
        citation += f" https://doi.org/{doi}"
    return citation


def to_markdown(paper: Dict[str, Any]) -> str:
    """生成 Markdown 超链接格式"""
    title = _clean_str(paper.get("title") or paper.get("filename", "")).replace(".pdf", "")
    year = _clean_str(paper.get("year"))
    doi = _clean_str(paper.get("doi"))
    authors = parse_authors(paper.get("authors"))
    au_str = f" ({authors[0]} et al.)" if authors else ""
    year_str = f" [{year}]" if year else ""

    if doi:
        return f"[{title}](https://doi.org/{doi}){au_str}{year_str}"
    return f"**{title}**{au_str}{year_str}"


def format_all(paper: Dict[str, Any]) -> Dict[str, str]:
    """返回该论文的各种格式引文字典"""
    return {
        "BibTeX": to_bibtex(paper),
        "GB/T 7714": to_gbt7714(paper),
        "APA": to_apa(paper),
        "Markdown": to_markdown(paper),
    }


# 常用格式导出别名
format_bibtex = to_bibtex
format_gbt7714 = to_gbt7714
format_apa = to_apa
format_markdown_ref = to_markdown
