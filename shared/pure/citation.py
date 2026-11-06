"""引用格式生成（移植自 shared/pure/citation.js）：GB/T 7714-2015、APA 7、MLA 9、BibTeX、Markdown。

只依赖文献卡片自身的字段，不联网。契约名：citation.format(paper, style) -> str（零 I/O 纯函数）。
"""
from __future__ import annotations

TYPE_LABEL_GB = {
    "journal": "J", "conference": "C", "preprint": "EB/OL", "book": "M",
    "thesis": "D", "report": "R", "web": "EB/OL", "other": "Z",
}
TYPE_LABEL_BIB = {
    "journal": "article", "conference": "inproceedings", "preprint": "misc",
    "book": "book", "thesis": "phdthesis", "report": "techreport",
    "web": "misc", "other": "misc",
}

WESTERN_STRIP = r"[^\w\u4e00-\u9fff]"


def _first_author_surname(authors):
    first = (authors or [None])[0]
    return str(first).strip() if first else ""


def _apa_authors(authors):
    items = [str(a).strip() for a in (authors or []) if str(a).strip()]
    if not items:
        return ""
    formatted = []
    for author in items:
        parts = author.split()
        if len(parts) == 1:
            formatted.append(author)
            continue
        surname = parts[-1]
        initials = " ".join((p[0].upper() + ".") for p in parts[:-1] if p)
        formatted.append(surname + ", " + initials)
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) <= 20:
        return ", ".join(formatted[:-1]) + ", & " + formatted[-1]
    return ", ".join(formatted[:19]) + ", ... " + formatted[-1]


def _gb_name(name, western):
    raw = str(name or "").strip()
    if not raw or not western:
        return raw
    parts = [p for p in raw.split() if p]
    if len(parts) < 2:
        return raw
    surname = parts[-1]
    initials = " ".join(p[0].upper() for p in " ".join(parts[:-1]).replace(".", " ").replace("-", " ").split() if p)
    return (surname + " " + initials) if initials else surname


def _gb_authors(authors, western):
    items = [str(a).strip() for a in (authors or []) if str(a).strip()]
    if not items:
        return "Anon." if western else "佚名"
    shown = ", ".join(_gb_name(a, western) for a in items[:3])
    if len(items) <= 3:
        return shown
    return shown + (", et al" if western else ", 等")


def _is_western(paper):
    import re

    text = " ".join(str(paper.get(key) or "") for key in ("title", "venue", "publisher"))
    return not re.search("[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]", text)


def _gb(paper):
    ptype = paper.get("type")
    label = TYPE_LABEL_GB.get(ptype, "Z")
    western = _is_western(paper)
    authors = _gb_authors(paper.get("authors"), western)
    year = paper.get("year") or "n.d."
    title = paper.get("title") or ("Untitled" if western else "无题")
    venue = str(paper.get("venue") or "").strip()
    pages = paper.get("pages") or ""
    doi = (" DOI: " + str(paper.get("doi")) + ".") if paper.get("doi") else ""
    url = (" " + str(paper.get("url"))) if paper.get("url") else ""
    volume = str(paper.get("volume") or "").strip()
    issue = str(paper.get("issue") or "").strip()
    if volume:
        vol_issue = volume + ("(" + issue + ")" if issue else "")
    else:
        vol_issue = ("(" + issue + ")") if issue else ""
    if ptype == "journal":
        head = ", ".join(x for x in (venue, str(year)) if x)
        tail = ", ".join(x for x in (head, vol_issue) if x)
        return authors + ". " + title + "[J]. " + tail + (": " + str(pages) if pages else "") + "." + doi
    tail = (venue + ", " + str(year)) if venue else str(year)
    if ptype == "conference":
        if venue:
            return authors + ". " + title + "[C]//" + venue + ". " + str(year) + (": " + str(pages) if pages else "") + "." + doi
        return authors + ". " + title + "[C]. " + str(year) + (": " + str(pages) if pages else "") + "." + doi
    if ptype == "book":
        place = ": ".join(x for x in (venue, str(paper.get("publisher") or "")) if x)
        return authors + ". " + title + "[M]. " + (place + ", " if place else "") + str(year) + "." + doi
    if ptype == "thesis":
        return authors + ". " + title + "[D]. " + tail + "." + doi
    if ptype == "report":
        return authors + ". " + title + "[R]. " + tail + "." + doi
    return authors + ". " + title + "[" + label + "]. " + tail + url + "."


def _apa(paper):
    authors = _apa_authors(paper.get("authors")) or "佚名"
    year = ("(" + str(paper.get("year")) + ").") if paper.get("year") else "(n.d.)."
    title = (paper.get("title") or "无题") + "."
    venue = str(paper.get("venue") or "")
    if venue:
        venue = ("*" + venue + "*") if paper.get("type") == "journal" else venue
    vol = (", " + str(paper.get("volume"))) if paper.get("volume") else ""
    issue = ("(" + str(paper.get("issue")) + ")") if paper.get("issue") else ""
    pages = (", " + str(paper.get("pages"))) if paper.get("pages") else ""
    if paper.get("doi"):
        tail = " https://doi.org/" + str(paper.get("doi"))  # Node 原实现带前导空格，逐字节对齐
    else:
        tail = str(paper.get("url") or "")
    mid = ". ".join(x for x in ((venue + vol + issue + pages) if venue else "", tail) if x)
    return " ".join(x for x in (authors, year, title, mid) if x)


def _mla(paper):
    authors = _first_author_surname(paper.get("authors"))
    title = '"' + (paper.get("title") or "无题") + '."'
    venue = ("*" + str(paper.get("venue")) + "*,") if paper.get("venue") else ""
    year = (str(paper.get("year")) + ",") if paper.get("year") else ""
    pages = ("pp. " + str(paper.get("pages")) + ",") if paper.get("pages") else ""
    if paper.get("doi"):
        doi = "doi:" + str(paper.get("doi")) + "."
    else:
        doi = str(paper.get("url") or "")
    return " ".join(x for x in ((authors + ".") if authors else "", title, venue, year, pages, doi) if x)


def bibtex_key(paper):
    if paper.get("bibtexKey"):
        return paper.get("bibtexKey")
    import re

    first = (paper.get("authors") or ["anon"])[0] or "anon"
    raw = str(first).strip()
    surname = re.sub(WESTERN_STRIP, "", raw.split()[-1] if raw else "") or "anon"
    return surname + str(paper.get("year") or "nd")


def _bibtex(paper):
    kind = TYPE_LABEL_BIB.get(paper.get("type"), "misc")
    lines = ["  title = {" + str(paper.get("title") or "") + "},"]
    if paper.get("authors"):
        lines.append("  author = {" + " and ".join(paper.get("authors")) + "},")
    if paper.get("year"):
        lines.append("  year = {" + str(paper.get("year")) + "},")
    if paper.get("venue"):
        lines.append("  " + ("journal" if kind == "article" else "booktitle") + " = {" + str(paper.get("venue")) + "},")
    if paper.get("volume"):
        lines.append("  volume = {" + str(paper.get("volume")) + "},")
    if paper.get("issue"):
        lines.append("  number = {" + str(paper.get("issue")) + "},")
    if paper.get("pages"):
        lines.append("  pages = {" + str(paper.get("pages")) + "},")
    if paper.get("publisher"):
        lines.append("  publisher = {" + str(paper.get("publisher")) + "},")
    if paper.get("doi"):
        lines.append("  doi = {" + str(paper.get("doi")) + "},")
    if paper.get("url"):
        lines.append("  url = {" + str(paper.get("url")) + "},")
    return "@" + kind + "{" + bibtex_key(paper) + ",\n" + "\n".join(lines) + "\n}"


def _markdown(paper):
    fence = chr(96) * 3
    bits = ["# " + (paper.get("title") or "无题")]
    meta_line = " · ".join(str(x) for x in [
        ", ".join(paper.get("authors") or []),
        paper.get("year") or "",
        paper.get("venue") or "",
        ("DOI: " + str(paper.get("doi"))) if paper.get("doi") else "",
        paper.get("url") or "",
    ] if x)
    if meta_line:
        bits.extend(["", meta_line])
    if paper.get("tags"):
        bits.extend(["", "标签：" + " ".join("#" + str(t) for t in paper.get("tags"))])
    if paper.get("summary"):
        bits.extend(["", "## 一句话总结", "", str(paper.get("summary"))])
    if paper.get("notes"):
        bits.extend(["", "## 我的笔记", "", str(paper.get("notes"))])
    bits.extend(["", "## 引用", "", fence, _gb(paper), fence])
    return "\n".join(bits)


FORMATTERS = {"gb": _gb, "apa": _apa, "mla": _mla, "bibtex": _bibtex, "markdown": _markdown}


def format(paper, kind):
    return FORMATTERS.get(kind, _gb)(paper)


def available():
    return list(FORMATTERS.keys())
