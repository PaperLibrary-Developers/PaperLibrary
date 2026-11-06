r"""PDF 题录解析规则（移植自 server/adapters/pdf-parse-rules.js，429 行；契约名：pdf.extract_metadata(path) → Parsed）。

这里只有**本库独有的排版经验**，全部是纯函数、可离线单测：
  · DOI 扫描（含被排版断行的 DOI 拼接）；
  · 标题行打分 / 作者行识别 / 期刊名识别 / 标题候选拼接；
  · PDF 内嵌元数据（Info / XMP）里的作者、年份、DOI、标题可用性；
  · 标题相似度（用于导入查重与 Crossref 结果复核）。

零 I/O、零全局态，不 import api/domain/data；编排（读 PDF、查题录、组装草稿、摘要兜底）在
domain/pdf_import.py。

与 JS 版的唯一差异是正则写法：JS 用 \p{L}/\p{Lu} 这类 Unicode property，Python re 不支持，
统一换成等价的字符类 [^\W\d_]（Unicode 字母）与 [^\Wa-z\d_]（大写类字母）。
"""
from __future__ import annotations

import re

from paperlib.util import normalize_doi, sanitize_file_name

# 预检时看前几页（正式文献的 DOI 基本都在首页页眉/页脚或首页摘要旁）
HEAD_PAGES = 3

# DOI 允许的字符集（Crossref/DataCite 的规范字符，去掉常见的中文标点与引号）
DOI_RE = re.compile(r"10\.\d{4,9}/[A-Za-z0-9._;()/:<>+-]+", re.I)
# DOI 末尾常见的粘连标点：'.' ',' ';' ')' ']' '。' 等可能是正文标点而不是 DOI 的一部分
TRAILING_JUNK = re.compile(r"[.,;:)\]}>'\"，。；：）】》]+$")

# Unicode 字母 / 大写类字母（替代 JS 的 \p{L} / \p{Lu}）
_L = r"[^\W\d_]"
_CAP = r"[^\Wa-z\d_]"

# 明显不是标题的行（期刊页眉页脚、数据库标记、下载信息等）
TITLE_NOISE = [re.compile(p, re.I) for p in (
    r"^this journal is\b",
    r"^subscriber access provided by\b",
    r"^downloaded (on|from|via)\b",
    r"^view (article|journal) online\b",
    r"^article in press\b",
    r"^check for updates\b",
    r"^cite this\b",
    r"^published (on|by|as)\b",
    r"^received\b",
    r"^accepted\b",
    r"©|\bcopyright\b",
    r"^https?://",
    r"^doi[:.\s]",
    r"^www\.",
    r"^abstract\b",
    r"^keywords?\b",
    r"^introduction\b",
    r"^contents? lists?\b",
    r"^journal homepage\b",
    r"^volume \d",
    r"^issue \d",
    r"^pp\.\s*\d",
    r"^\d+\s*[-–]\s*\d+$",
    r"^page \d",
    r"research explorer|repository|manuscript version|accepted manuscript",
    r"^university of\b",
    r"^department of\b",
    r"^e-?mail\b",
    r"^\S+@\S+$",
    # 作者行的续行（作者太多时排版会把 "and Someone" 单独放一行）
    r"^(and|&)\s+[A-Z]",
    # 出版社的版权声明/刊物介绍（"Accounts of Chemical Research is published by …"）
    r"\bis published by\b",
    r"\bis a (monthly|weekly|biweekly|peer[- ]reviewed)\b",
    r"^colou?r figures?\b",
    # 数据库导出页/检索结果里的样板
    r"^(communications?|article|research article|review article|original (article|research))\b.{0,12}$",
    r"^search results?\b",
    # 出版社在页眉打的分类标签（"Metal-Organic Cages Very Important Paper"）
    r"\b(very important paper|hot paper|communication|highlight|editor'?s? (choice|pick)|front cover|inside cover)\b",
)]

# 作者行：1~3 个词首大写的词，逗号串起至少两段
_AUTHOR_TOKEN = _CAP + r"(?:" + _L + r"|[.'’\-])+"
_AUTHOR_LINE_RE = re.compile(
    r"^" + _AUTHOR_TOKEN + r"(?:\s+" + _AUTHOR_TOKEN + r"){0,2}"
    r"(?:,\s*" + _AUTHOR_TOKEN + r"(?:\s+" + _AUTHOR_TOKEN + r"){0,2}){1,}$"
)

# 页眉式的「期刊名」整行（不能当标题；但可以作为期刊候选）
JOURNAL_LINE = [re.compile(p, re.I) for p in (
    r"^(chemical society reviews|chemical reviews|accounts of chemical research|"
    r"journal of the american chemical society|angewandte chemie.*|new journal of chemistry|"
    r"communications chemistry|chem soc rev|chem\. soc\. rev\.|j\. am\. chem\. soc\.|"
    r"nature|science|cell|acs nano|acs omega|jacs au)$",
    r"^(chem|j|angew|accounts|org|inorg|anal|phys|tetrahedron|european|dalton|langmuir|"
    r"macromolecules)\b\.?[^.]{0,30}$",
)]

_NAME_PART_RE = re.compile(r"(?:" + _L + r"|[.'’\-])+$")


def looks_like_author_line(s):
    """作者行特征：多个逗号分隔的短片段 / 结尾角标 / "and" 串联（标题极少长这样）。"""
    line = str(s or "")
    if re.search(r"[*†‡§¶]\s*$", line):
        return True
    commas = line.count(",")
    if commas >= 2 and len(line) < 160:
        return True
    if re.search(r"\band\b", line, re.I) and commas >= 1 and len(line) < 110:
        return True
    return bool(_AUTHOR_LINE_RE.match(line))


def extract_dois(lines):
    """取出候选 DOI，做归一化 + 去重，保留首次出现的顺序。"""
    out = []
    seen = set()
    for line in lines or []:
        for raw in DOI_RE.findall(str(line)):
            cleaned = re.sub(r"\s+", "", TRAILING_JUNK.sub("", raw))
            doi = normalize_doi(cleaned)
            if not doi:
                continue
            key = doi.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(doi)
    return out


def extract_dois_across_lines(lines):
    """正文里的 DOI 被排版断行的兜底（"10.1002/anie.2023" + "15020" → 拼完整）。

    判定「可能被切断」的信号：这一行**只有**一个 DOI（去掉 doi:/doi.org/ 前缀后整行就是它），
    而且下一行是纯数字。拼接结果要排在单行扫描之前，否则截断版会先被采用。
    """
    out = []
    items = list(lines or [])
    for index, raw_line in enumerate(items):
        line = str(raw_line or "").strip()
        if not line:
            continue
        stripped = re.sub(r"^(?:https?://)?(?:dx\.)?doi\.org/|^doi[:.\s]+", "", line, flags=re.I).strip()
        found = extract_dois([stripped])
        head = found[0] if found else ""
        if not head or head != stripped:
            continue
        nxt = str(items[index + 1] if index + 1 < len(items) else "").strip()
        if not re.fullmatch(r"\d+", nxt):
            continue
        joined = head
        for j in range(index + 1, min(index + 5, len(items))):
            piece = str(items[j] or "").strip()
            if not re.fullmatch(r"\d+", piece):
                break
            joined += piece
            merged = extract_dois([joined])
            if merged and len(merged[0]) > len(head):
                out.append(merged[0])
                break
    return out


def score_title_line(line, index):
    """单行像个标题吗？给个分数（越大越像，<=-100 表示直接淘汰）。"""
    s = str(line or "").strip()
    if len(s) < 12 or len(s) > 300:
        return -100
    if any(rx.search(s) for rx in TITLE_NOISE):
        return -100
    score = max(0, 12 - int(index or 0))
    letters = len(re.findall(r"[A-Za-z]", s))
    digits = len(re.findall(r"\d", s))
    if letters / max(1, len(s)) < 0.5:
        return -100
    if digits / max(1, len(s)) > 0.25:
        score -= 8
    if re.search(r"[A-Z]", s):
        score += 3
    if re.search(r"\b(of|for|and|the|in|on|via|with|by|using|from|to)\b", s, re.I):
        score += 3
    if re.search(r"[:：]", s):
        score += 1
    if re.search(r"[.!?]\s*$", s):
        score -= 2
    if looks_like_author_line(s):
        return -100
    if re.match(r"^\d+\s", s):
        score -= 6
    if re.search(r"[\u4e00-\u9fa5]", s) and len(s) >= 6:
        score += 4
    return score


def looks_like_journal_name(line):
    s = str(line or "").strip()
    if not s or len(s) > 60:
        return False
    if re.search(r"[.!?]$", s) and not re.search(r"\b(rev|soc|chem|am|j|int|ed|lett|commun)\.$", s, re.I):
        return False
    return any(rx.search(s) for rx in JOURNAL_LINE)


def looks_like_name_part(part):
    """一个片段是不是「人名」：1~3 个词，词首大写（允许 van/von/del 之类前缀）。"""
    s = re.sub(r"[\s,;]+", " ", str(part or "")).strip()
    if not s or len(s) > 40:
        return False
    if re.search(r"\b(of|for|the|with|from|to|via|using|by|in|on|and)\b", s, re.I):
        return False
    if not re.match(_CAP, s[0]):
        return False
    words = [w for w in s.split(" ") if w]
    if not 1 <= len(words) <= 3:
        return False
    return all(_NAME_PART_RE.match(w) for w in words)


def looks_like_name_list(line):
    """整行看起来是不是「一串人名」（作者行），而不是标题。"""
    raw = str(line or "").strip()
    if not raw or len(raw) > 140:
        return False
    if re.search(r"\b(of|for|the|with|from|to|via|using|by)\b", raw, re.I):
        return False
    if re.search(r"[.!?]$", raw):
        return False
    pre = re.sub(r"^(?:and|&)\s+", "", raw, flags=re.I)
    merged = re.sub(r",\s*(?=" + _CAP + r")", " ", pre)
    parts = [p.strip() for p in re.split(r"\s*,\s*|\s+and\s+", merged, flags=re.I) if p.strip()]
    if len(parts) < 2:
        return False
    return all(looks_like_name_part(p) for p in parts)


def strip_author_run(line):
    """把「作者行」从候选里剥出去（都刻意保守，宁可少剥也不要动到真标题）。"""
    s = re.sub(r"^(?:and|&)\s+", "", str(line or "").strip(), flags=re.I).strip()
    if looks_like_name_list(s):
        return ""
    tail = re.match(r"^(.*?),\s*(?:and\s+)?(" + _CAP + r"[^,]{0,40})$", s)
    if tail:
        head = re.sub(r"[\s,]+$", "", tail.group(1))
        name_tail = tail.group(2).strip()
        tail_words = [w for w in name_tail.split() if w]
        if (len(head) >= 25 and len(tail_words) <= 3
                and not re.search(r"\b(of|for|the|with|from|to|via|using|by|and|in|on)\b", name_tail, re.I)
                and looks_like_name_part(name_tail)):
            s = head
    return s


def title_candidates(lines, limit=6):
    """从首页行里挑标题候选。

    返回 {title, candidates, journalCandidates}：
      · candidates 只保留看起来真像标题的（作者行、刊名、页眉都不进来）；
      · journalCandidates 是页眉里的期刊名（Crossref 查不到时给用户当候选）。
    """
    slice_lines = [str(x or "").strip() for x in list(lines or [])[:40]]
    journals = []
    rows = []
    for index, line in enumerate(slice_lines):
        if looks_like_journal_name(line):
            if line not in journals:
                journals.append(line)
            continue
        cleaned = strip_author_run(line)
        if not cleaned:
            continue  # 整行就是作者名单
        if cleaned != line:
            if is_plausible_title(cleaned):
                rows.append({"line": cleaned, "index": index, "score": score_title_line(cleaned, index) + 1})
            continue
        rows.append({"line": line, "index": index, "score": score_title_line(line, index)})

    # 标题在排版里常折成两三行；只在「这一行自己不像完整标题、而下一行接着它读得通」时才拼
    joined = []
    for i, cur in enumerate(slice_lines):
        nxt = slice_lines[i + 1] if i + 1 < len(slice_lines) else ""
        if not cur or not nxt:
            continue
        if len(cur) < 18:
            continue
        if looks_like_author_line(cur) or looks_like_name_list(cur) or looks_like_journal_name(cur):
            continue
        if re.search(r"[.!?,;]$", cur):
            continue  # 已经写完了
        if re.search(r"\b(and|of|the|for|with|in|on|to|by|using|from|via|or|a|an)$", cur, re.I):
            continue
        if re.match(r"^[a-z]", nxt):
            continue  # 下一行是正文/摘要的续句
        if looks_like_journal_name(nxt) or any(rx.search(nxt) for rx in TITLE_NOISE):
            continue
        if looks_like_author_line(nxt) or looks_like_name_list(nxt):
            continue
        merged = cur + " " + nxt
        if len(merged) > 200 or not is_plausible_title(merged):
            continue
        if score_title_line(merged, i) < 0:
            continue
        joined.append({"line": merged, "index": i, "completes": i, "score": score_title_line(merged, i) + 2})

    all_rows = sorted(rows + joined, key=lambda row: -row["score"])
    banned = {str(slice_lines[j["completes"]] or "").strip().lower() for j in joined}
    seen = set()
    out = []
    for row in all_rows:
        if row["score"] <= -100:
            break
        is_joined = "completes" in row
        if not is_plausible_title(row["line"]):
            continue
        key = re.sub(r"\s+", " ", row["line"].lower())
        if key in seen:
            continue
        if key in banned and not is_joined:
            continue
        if not is_joined and any(b for b in banned if len(b) > 25 and key.startswith(b)):
            continue
        seen.add(key)
        out.append(row["line"])
        if len(out) >= int(limit or 6):
            break
    return {"title": out[0] if out else "", "candidates": out, "journalCandidates": journals[:3]}


def usable_embedded_title(title):
    """内嵌元数据里的标题能不能用（很多 PDF 写的是 untitled、印刷厂流水号或页码区间）。"""
    s = str(title or "").strip()
    if len(s) < 8:
        return False
    if re.match(r"^(untitled|no title|document|microsoft word|pdf|slide|manuscript)\b", s, re.I):
        return False
    if re.search(r"\.(indd|docx?|tex|qxd|pdf)$", s, re.I):
        return False
    if re.fullmatch(r"\S+\s+\d+\.\.\d+", s):
        return False
    if re.match(r"^[A-Za-z]+_\w+_\d+_AIP\b", s, re.I):
        return False
    letters = len(re.findall(r"[A-Za-z\u4e00-\u9fa5]", s))
    return letters / max(1, len(s)) >= 0.5


def authors_from_meta(info, xmp):
    """从内嵌元数据里取作者（Info.Author / XMP dc:creator），切成数组。"""
    raw = []
    info = info or {}
    xmp = xmp or {}
    if info.get("Author"):
        raw.append(info.get("Author"))
    creator = xmp.get("dc:creator")
    if creator:
        if isinstance(creator, (list, tuple)):
            raw.extend(creator)
        else:
            raw.append(str(creator))
    out = []
    for chunk in raw:
        for part in re.split(r"\s*[;；]\s*", str(chunk)):
            if not part:
                continue
            for piece in re.split(r"\s+and\s+", part, flags=re.I):
                cleaned = re.sub(r"[.,;]+$", "", piece).strip()
                if 2 <= len(cleaned) <= 80:
                    out.append(cleaned)
    seen = set()
    unique = []
    for name in out:
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(name)
    return unique[:30]


def year_from_meta(info, xmp):
    """内嵌元数据里的年份：优先 XMP 的 prism:publicationDate，其次 Info.CreationDate 的 D:YYYY。"""
    info = info or {}
    xmp = xmp or {}
    candidates = [
        xmp.get("prism:publicationDate") or xmp.get("xmp:CreateDate") or xmp.get("dc:date"),
        info.get("CreationDate"),
    ]
    for candidate in candidates:
        match = re.search(r"(19|20)\d{2}", str(candidate or ""))
        if match:
            return int(match.group(0))
    return None


def doi_from_meta(xmp):
    """内嵌 DOI（XMP prism:doi / dc:identifier），没有就空串。"""
    if not xmp:
        return ""
    for key in ("prism:doi", "prism:DOI", "dc:identifier", "xmp:Identifier"):
        value = xmp.get(key)
        items = value if isinstance(value, (list, tuple)) else ([value] if value else [])
        for item in items:
            match = re.search(r"10\.\d{4,9}/\S+", str(item))
            if match:
                return normalize_doi(TRAILING_JUNK.sub("", match.group(0)))
    return ""


def title_similarity(a, b):
    """标题相似度（token 交集 / 归一化长度），用来复核检索结果与导入查重。"""
    def norm(value):
        s = re.sub(r"[^\w\s]", " ", str(value or "").lower())
        return re.sub(r"\s+", " ", s).strip()

    ta = {w for w in norm(a).split(" ") if len(w) > 2}
    tb = {w for w in norm(b).split(" ") if len(w) > 2}
    if not ta or not tb:
        return 0.0
    hit = sum(1 for w in ta if w in tb)
    return hit / max(len(ta), len(tb))


def title_from_file_name(file):
    """没有标题时的兜底：用文件名（去掉 id 前缀与扩展名）。"""
    base = re.sub(r"^[a-f0-9]{8,}-", "", re.sub(r"\.pdf$", "", str(file or ""), flags=re.I), flags=re.I)
    cleaned = re.sub(r"\s+", " ", re.sub(r"[_-]+", " ", base)).strip()
    return sanitize_file_name(cleaned, 120) or base


def is_plausible_title(line):
    """「像个标题」的最低门槛：别比文件名还差就行（避免 3 个词的短标题被误判成不可信）。"""
    s = str(line or "").strip()
    if len(s) < 18:
        return False
    if looks_like_journal_name(s):
        return False
    if any(rx.search(s) for rx in TITLE_NOISE):
        return False
    if looks_like_author_line(s) or looks_like_name_list(s):
        return False
    words = len(s.split())
    return words >= 3 or bool(re.search(r"[\u4e00-\u9fa5]", s))


__all__ = [
    "HEAD_PAGES", "extract_dois", "extract_dois_across_lines", "score_title_line",
    "title_candidates", "strip_author_run", "looks_like_name_list", "looks_like_name_part",
    "looks_like_author_line", "looks_like_journal_name", "usable_embedded_title",
    "authors_from_meta", "year_from_meta", "doi_from_meta", "title_similarity",
    "title_from_file_name", "is_plausible_title",
]
