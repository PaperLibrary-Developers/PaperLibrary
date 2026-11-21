"""领域层 · 卡片仓库（移植自 Sci-lib server/domain/repository.js）。

职责：把任意输入规整成合法卡片（normalize_paper / normalize_chat / normalize_attachments），
增删改、批量打标、标签重命名与删除。写一律走 store 访问器（db），并同步检索索引。

**契约名：repository(Paper / Attachment / Tag) —— 唯一写入口是 store 访问器**
"""
from __future__ import annotations

import os
from pathlib import Path

from paperlib.util import (
    clean_title,
    now_iso,
    normalize_doi,
    to_string_array,
    truncate,
    uid,
)
from shared.pure.citation import bibtex_key

PAPER_TYPES = ["journal", "conference", "preprint", "book", "thesis", "report", "web", "other"]
STATUSES = ["unread", "reading", "read", "archived"]


def normalize_attachments(value):
    if not isinstance(value, list):
        return []
    out = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        file = str(raw.get("file") or "").strip()
        if not file:
            continue
        out.append({
            "file": file,
            "originalName": str(raw.get("originalName") or file)[:300],
            "size": int(raw.get("size") or 0),
            "kind": str(raw.get("kind") or "other")[:20],
            "hasText": bool(raw.get("hasText")),
            "textChars": int(raw.get("textChars") or 0),
            "addedAt": str(raw.get("addedAt") or ""),
        })
    return out


def normalize_refs(value):
    """参考文献：入库只认 {num, text, url} 三个键，最多 200 条。"""
    if not isinstance(value, list):
        return []
    out = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        try:
            num = int(str(raw.get("num")).strip())
        except (TypeError, ValueError):
            num = None
        text = str(raw.get("text") if raw.get("text") is not None else "")[:2000]
        url = str(raw.get("url") if raw.get("url") is not None else "")[:1000]
        if num is None and not text.strip() and not url.strip():
            continue
        out.append({"num": 0 if num is None else num, "text": text, "url": url})
    return out[:200]


def normalize_image_count(value):
    if isinstance(value, list):
        n = len(value)
    else:
        try:
            n = int(value or 0)
        except (TypeError, ValueError):
            n = 0
    if n <= 0:
        return 0
    return min(n, 20)


USAGE_KEYS = [
    "prompt_tokens", "completion_tokens", "total_tokens",
    "promptTokens", "completionTokens", "totalTokens",
]


def normalize_usage(value):
    if not isinstance(value, dict):
        return None
    picked = {}
    for key in USAGE_KEYS:
        try:
            n = float(value.get(key))
        except (TypeError, ValueError):
            continue
        picked[key] = n
    source = picked if picked else value
    import json

    try:
        raw = json.dumps(source, ensure_ascii=False)
    except (TypeError, ValueError):
        return None
    if not raw or raw == "{}":
        return None
    if len(raw) > 2048:
        return picked if picked else None
    return source


def normalize_chat(value):
    if not isinstance(value, list):
        return []
    out = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        role = "assistant" if raw.get("role") == "assistant" else "user"
        content = str(raw.get("content") if raw.get("content") is not None else "")[:40000]
        if not content.strip():
            continue
        sources = []
        if isinstance(raw.get("sources"), list):
            sources = [str(s)[:40] for s in raw.get("sources")][:8]
        out.append({
            "role": role,
            "content": content,
            "at": str(raw.get("at") or ""),
            "model": str(raw.get("model"))[:100] if raw.get("model") else "",
            "sources": sources,
            "images": normalize_image_count(raw.get("images")),
            "usage": normalize_usage(raw.get("usage")),
        })
    return out[-200:]


def _clean(value, maximum=0):
    s = "" if value is None else str(value)
    return s[:maximum] if maximum else s


def normalize_paper(input_data, existing=None):
    """把任意输入规整成一张合法卡片；existing 用于保留不该被覆盖的字段。

    注意：必须区分「字段没传」与「传了空值」——局部更新只覆盖显式传入的键。
    """
    base = existing or {}
    src = input_data or {}

    def given(key):
        return key in src and src.get(key) is not None

    def resolve(key):
        return src.get(key) if given(key) else base.get(key)

    year_value = resolve("year")
    try:
        year_raw = int(str(year_value).strip()) if year_value not in (None, "") else None
    except (TypeError, ValueError):
        year_raw = None
    year = year_raw if year_raw is not None and 1000 < year_raw < 3000 else None

    rating_value = resolve("rating")
    try:
        rating_raw = int(str(rating_value).strip()) if rating_value not in (None, "") else None
    except (TypeError, ValueError):
        rating_raw = None
    rating = min(5, max(0, rating_raw)) if rating_raw is not None else int(base.get("rating") or 0)

    starred = bool(src.get("starred")) if src.get("starred") is not None else bool(base.get("starred"))
    paper = {
        "id": base.get("id") or src.get("id") or uid(),
        "title": clean_title(src.get("title") if src.get("title") is not None else base.get("title"))[:500],
        "authors": to_string_array(src.get("authors") if src.get("authors") is not None else base.get("authors")),
        "year": year,
        "type": src.get("type") if src.get("type") in PAPER_TYPES else (base.get("type") or "journal"),
        "venue": _clean(src.get("venue") if src.get("venue") is not None else base.get("venue"), 300).strip(),
        "volume": _clean(src.get("volume") if src.get("volume") is not None else base.get("volume"), 50).strip(),
        "issue": _clean(src.get("issue") if src.get("issue") is not None else base.get("issue"), 50).strip(),
        "pages": _clean(src.get("pages") if src.get("pages") is not None else base.get("pages"), 50).strip(),
        "publisher": _clean(src.get("publisher") if src.get("publisher") is not None else base.get("publisher"), 200).strip(),
        "doi": normalize_doi(src.get("doi") if src.get("doi") is not None else base.get("doi")),
        "url": _clean(src.get("url") if src.get("url") is not None else base.get("url"), 1000).strip(),
        "tags": to_string_array(src.get("tags") if src.get("tags") is not None else base.get("tags")),
        "keywords": to_string_array(src.get("keywords") if src.get("keywords") is not None else base.get("keywords")),
        "status": src.get("status") if src.get("status") in STATUSES else (base.get("status") or "unread"),
        "rating": rating,
        "starred": starred,
        "starredAt": (base.get("starredAt") or _clean(src.get("starredAt"), 40) or now_iso()) if starred else "",
        "summary": _clean(src.get("summary") if src.get("summary") is not None else base.get("summary"), 4000),
        "summarySource": _clean(src.get("summarySource") if src.get("summarySource") is not None else base.get("summarySource"), 20).strip(),
        "summaryZh": _clean(src.get("summaryZh") if src.get("summaryZh") is not None else base.get("summaryZh"), 4000),
        "summaryZhAt": _clean(src.get("summaryZhAt") if src.get("summaryZhAt") is not None else base.get("summaryZhAt"), 40),
        "summaryZhModel": _clean(src.get("summaryZhModel") if src.get("summaryZhModel") is not None else base.get("summaryZhModel"), 100),
        "notes": _clean(src.get("notes") if src.get("notes") is not None else base.get("notes"), 200000),
        "aiSummary": _clean(src.get("aiSummary") if src.get("aiSummary") is not None else base.get("aiSummary"), 200000),
        "aiSummaryAt": _clean(src.get("aiSummaryAt") if src.get("aiSummaryAt") is not None else base.get("aiSummaryAt"), 40),
        "aiSummaryModel": _clean(src.get("aiSummaryModel") if src.get("aiSummaryModel") is not None else base.get("aiSummaryModel"), 100),
        "attachments": normalize_attachments(src.get("attachments") if src.get("attachments") is not None else base.get("attachments")),
        "chat": normalize_chat(src.get("chat") if src.get("chat") is not None else base.get("chat")),
        "refs": normalize_refs(src.get("refs") if src.get("refs") is not None else base.get("refs")),
        "pdfFile": str(src.get("pdfFile") if src.get("pdfFile") is not None else base.get("pdfFile") or "").replace("\\", "/"),
        "bibtexKey": _clean(src.get("bibtexKey") if src.get("bibtexKey") is not None else base.get("bibtexKey"), 100).strip(),
        "addedAt": base.get("addedAt") or src.get("addedAt") or now_iso(),
        "updatedAt": now_iso(),
    }
    # union 字段（合并后新增）：只在非空时出现，保持与 Node 卡片形状一致
    folder = str(src.get("folder") if src.get("folder") is not None else base.get("folder") or "")
    if folder:
        paper["folder"] = folder
    digest = src.get("pdfSha256")
    if digest is None and paper["pdfFile"] == base.get("pdfFile"):
        digest = base.get("pdfSha256")
    if paper["pdfFile"] and isinstance(digest, str) and len(digest) == 64:
        paper["pdfSha256"] = digest
    arxiv_id = _clean(src.get("arxivId") if src.get("arxivId") is not None else base.get("arxivId"), 100).strip()
    if arxiv_id:
        paper["arxivId"] = arxiv_id
    if not paper["title"]:
        paper["title"] = "（未命名文献）"
    if not paper["bibtexKey"]:
        paper["bibtexKey"] = bibtex_key(paper)
    return paper


SORTABLE = {
    "updatedAt": lambda a, b: _cmp(str(b.get("updatedAt")), str(a.get("updatedAt"))),
    "addedAt": lambda a, b: _cmp(str(b.get("addedAt") or b.get("createdAt") or ""),
                                 str(a.get("addedAt") or a.get("createdAt") or "")),
    "yearDesc": lambda a, b: _cmp_num(b.get("year"), a.get("year")) or _cmp(str(b.get("updatedAt")), str(a.get("updatedAt"))),
    "yearAsc": lambda a, b: _cmp_num(a.get("year"), b.get("year")) or _cmp(str(b.get("updatedAt")), str(a.get("updatedAt"))),
    "title": lambda a, b: _cmp(str(a.get("title") or ""), str(b.get("title") or "")),
    "rating": lambda a, b: _cmp_num(b.get("rating"), a.get("rating")) or _cmp(str(b.get("updatedAt")), str(a.get("updatedAt"))),
    "starredAt": lambda a, b: _cmp(str(b.get("starredAt") or ""), str(a.get("starredAt") or ""))
    or _cmp(str(b.get("updatedAt")), str(a.get("updatedAt"))),
}


def _cmp(a, b):
    return -1 if a < b else (1 if a > b else 0)


def _cmp_num(a, b):
    return _cmp(int(a or 0), int(b or 0))


class Repository:
    """卡片的所有写操作（对应 Node 的 domain/repository.js）。"""

    def __init__(self, db, index):
        self.db = db
        self.index = index

    def create(self, input_data):
        return self.insert_normalized(normalize_paper(input_data, None))

    def update(self, paper_id, input_data):
        """Apply fields, or calculate a patch from the latest card under the write lock."""
        with self.db.paper_writes():
            existing = self.db.find_paper(paper_id)
            if not existing:
                return None
            if callable(input_data):
                input_data = input_data(existing)
                if input_data is None:
                    return None
            merged = normalize_paper(input_data, existing)
            self.db.replace_paper(paper_id, merged)
            self.index.set(merged)
            return merged

    def remove(self, paper_id):
        with self.db.paper_writes():
            paper = self.db.remove_paper(paper_id)
            if not paper:
                return False
            self.index.remove(paper.get("id"))
            pdf_file = paper.get("pdfFile")
            if pdf_file and not any(p.get("pdfFile") == pdf_file for p in self.db.list_papers()):
                abs_path = self.db.path_for_paper_file(pdf_file)
                if abs_path and os.path.isfile(abs_path):
                    try:
                        os.unlink(abs_path)
                    except OSError:
                        pass
            return True

    def bulk(self, ids, patch):
        add = to_string_array(patch.get("tags"))
        drop = {t.lower() for t in to_string_array(patch.get("removeTags") or patch.get("untags"))}

        def merge(existing):
            tags = to_string_array(list(existing.get("tags") or []) + add)
            return {
                "tags": [t for t in tags if t.lower() not in drop],
                "status": patch.get("status"),
                "rating": patch.get("rating"),
                "starred": patch.get("starred"),
                "type": patch.get("type"),
            }

        changed = []
        for paper_id in to_string_array(ids):
            saved = self.update(paper_id, merge)
            if saved is not None:
                changed.append(saved)
        return changed

    def rename_tag(self, source, target):
        old_tag = str(source or "").strip()
        new_tag = str(target or "").strip()
        if not old_tag or not new_tag:
            raise ValueError("标签名不能为空")
        def rename(paper):
            tags = paper.get("tags") or []
            if not any(t.lower() == old_tag.lower() for t in tags):
                return None
            return {"tags": [new_tag if t.lower() == old_tag.lower() else t for t in tags]}

        changed = sum(self.update(paper["id"], rename) is not None for paper in self.db.list_papers())
        return {"changed": changed, "from": old_tag, "to": new_tag}

    def remove_tag(self, name):
        target = str(name or "").strip().lower()
        if not target:
            raise ValueError("标签名不能为空")
        def remove(paper):
            tags = paper.get("tags") or []
            remaining = [t for t in tags if t.lower() != target]
            return {"tags": remaining} if len(remaining) != len(tags) else None

        changed = sum(self.update(paper["id"], remove) is not None for paper in self.db.list_papers())
        return {"changed": changed, "name": str(name).strip()}

    def get(self, paper_id):
        return self.db.find_paper(paper_id)

    def all(self):
        return self.db.list_papers()

    def find_duplicate(self, norm):
        doi = str(norm.get("doi") or "").lower()
        title = str(norm.get("title") or "").strip().lower()
        for paper in self.db.list_papers():
            if doi and paper.get("doi") and str(paper.get("doi")).lower() == doi:
                return paper
            if str(paper.get("title") or "").strip().lower() == title:
                return paper
        return None

    def insert_normalized(self, paper, options=None):
        with self.db.paper_writes():
            self.db.insert_paper(paper, options)
            self.index.set(paper)
            return paper
