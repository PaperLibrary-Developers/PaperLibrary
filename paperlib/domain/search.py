"""内存倒排索引（移植自 Sci-lib server/search.js）。

字段权重：标题 > 作者/标签 > 摘要 > 期刊/笔记 > 其他；短语整体命中额外加分。
中文没有空格，靠 bigram 定位；多词查询默认 AND（交集为空退化为并集）。
"""
from __future__ import annotations

from shared.pure.text import query_terms, tokenize

FIELD_WEIGHTS = {
    "title": 10,
    "authors": 6,
    "tags": 7,
    "summary": 4,
    "keywords": 5,
    "notes": 2.5,
    "venue": 3,
    "doi": 2,
    "extra": 1,
}
INDEX_FIELDS = list(FIELD_WEIGHTS.keys())


def _doc_from_paper(paper):
    return {
        "title": paper.get("title") or "",
        "authors": " ".join(paper.get("authors") or []),
        "tags": " ".join(paper.get("tags") or []),
        "summary": paper.get("summary") or "",
        "keywords": " ".join(paper.get("keywords") or []),
        "notes": paper.get("notes") or "",
        "venue": paper.get("venue") or "",
        "doi": paper.get("doi") or "",
        "extra": " ".join(str(x) for x in [
            paper.get("year"), paper.get("type"), paper.get("url"),
            paper.get("bibtexKey"), paper.get("status"),
        ] if x),
    }


def _token_key(field, token):
    return field + "\u0000" + token


class SearchIndex:
    def __init__(self):
        self.docs = {}
        self.postings = {}

    def set(self, paper):
        self.remove(paper.get("id"))
        fields = _doc_from_paper(paper)
        sets = {}
        length = 0
        for field in INDEX_FIELDS:
            tokens = set(tokenize(fields.get(field)))
            sets[field] = tokens
            length += len(tokens)
            for token in tokens:
                key = _token_key(field, token)
                bucket = self.postings.setdefault(key, {})
                bucket[paper.get("id")] = FIELD_WEIGHTS[field]
        self.docs[paper.get("id")] = {
            "fields": sets,
            "haystack": " \u0001 ".join(fields[f] for f in INDEX_FIELDS).lower(),
            "length": length,
        }

    def remove(self, paper_id):
        doc = self.docs.get(paper_id)
        if not doc:
            return
        for field in INDEX_FIELDS:
            for token in doc["fields"].get(field, ()):
                key = _token_key(field, token)
                bucket = self.postings.get(key)
                if not bucket:
                    continue
                bucket.pop(paper_id, None)
                if not bucket:
                    self.postings.pop(key, None)
        self.docs.pop(paper_id, None)

    def _match_token(self, token):
        """查一个查询词命中的 (paper_id, 字段权重, 折扣系数)。

        先按整词精确查；**整词一个都没命中**时才放宽成「前缀 > 子串」，
        这样 "cucur" 也能命中索引里的 "cucurbiturils"，而精确命中的排序权重不受影响。
        放宽只在精确零命中时发生，常用词的查询路径不会变慢。
        """
        hits = []
        exact = False
        for field in INDEX_FIELDS:
            bucket = self.postings.get(_token_key(field, token))
            if not bucket:
                continue
            exact = True
            factor = 1.0 if len(token) > 1 else 0.35
            for paper_id, weight in bucket.items():
                hits.append((paper_id, weight, factor))
        # 单字不做模糊：会把整库都捞出来，反而更糟
        if exact or len(token) < 2:
            return hits
        for key, bucket in self.postings.items():
            _, _, word = key.partition("\u0000")
            if word.startswith(token):
                factor = 0.7          # 前缀命中：接近整词，但要排在精确命中后面
            elif token in word:
                factor = 0.45         # 词内子串：更弱
            else:
                continue
            for paper_id in bucket:
                hits.append((paper_id, bucket[paper_id], factor))
        return hits

    def search(self, query):
        """返回 [{id, score}] 按分数降序；空查询返回 None（表示不过滤）。"""
        terms = query_terms(query)
        if not terms:
            return None
        per_term = []
        for term in terms:
            tokens = tokenize(term)
            if not tokens:
                continue
            unique = list(dict.fromkeys(tokens))
            bigrams = [t for t in unique if len(t) > 1]
            hit = {}
            for token in unique:
                for paper_id, weight, factor in self._match_token(token):
                    entry = hit.setdefault(paper_id, {"total": 0.0, "matched": set()})
                    entry["total"] += weight * factor
                    entry["matched"].add(token)
            accepted = {}
            for paper_id, entry in hit.items():
                if len(bigrams) >= 2:
                    if not all(b in entry["matched"] for b in bigrams):
                        continue
                elif len(bigrams) == 1:
                    if bigrams[0] not in entry["matched"]:
                        continue
                coverage = len(entry["matched"]) / float(len(unique))
                accepted[paper_id] = entry["total"] * (0.6 + coverage * coverage * 0.9)
            if not accepted and len(bigrams) >= 2:
                # 中文长词（无空格、靠 bigram 定位）严格「全 bigram 命中」常常落空：
                # 查「单原子催化」匹配不到「单原子电催化剂」——中间多了一个「电」字就断链。
                # 这里按同一套「交集为空退化为并集」的思路放宽：任一 bigram 命中就算，
                # 分数打个 8 折，保证排在严格命中之后。
                for paper_id, entry in hit.items():
                    coverage = len(entry["matched"]) / float(len(unique))
                    accepted[paper_id] = entry["total"] * (0.6 + coverage * coverage * 0.9) * 0.8
            per_term.append(accepted)
        if not per_term:
            return None

        candidates = None
        for accepted in per_term:
            if candidates is None:
                candidates = set(accepted.keys())
            else:
                candidates = {pid for pid in candidates if pid in accepted}
            if not candidates:
                break
        if not candidates:
            union = {}
            for accepted in per_term:
                for paper_id, score in accepted.items():
                    union[paper_id] = union.get(paper_id, 0.0) + score
            return sorted(({"id": k, "score": v} for k, v in union.items()), key=lambda x: -x["score"])

        scores = {}
        for paper_id in candidates:
            scores[paper_id] = sum(accepted.get(paper_id, 0.0) for accepted in per_term)
        phrase = str(query or "").strip().lower()
        if len(phrase) >= 2:
            for paper_id, doc in self.docs.items():
                if paper_id not in scores:
                    continue
                if phrase in doc["haystack"]:
                    scores[paper_id] += 25
        return sorted(({"id": k, "score": v} for k, v in scores.items()), key=lambda x: -x["score"])
