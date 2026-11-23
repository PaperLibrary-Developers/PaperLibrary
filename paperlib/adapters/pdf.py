"""PDF 全文抽取与缓存（移植自 server/adapters/pdf.js，248 行；契约名：pdf.extract_text(path) → Text）。

权威实现是 **PyMuPDF**（不是 pdfjs）：pdfjs 只在前端展示，抽取一律走这里。

与 Node 版的落点差异（形状不变）：
  · 正文的**唯一真源是 SQLite 的 paper_text 表**（store.get_text / set_text），不再以
    data/fulltext/<id>.txt 为准；store.set_text 会顺手在 data/fulltext/ 镜像一份老布局的 .txt，
    旧备份/外部工具有需要时还能读。
  · **全文倒排索引在本类里（内存、按需建）**：Node 启动时 fulltext.loadAll 就把正文索引进内存；
    这里在首次检索时按批读取正文，之后根据 SQLite 读取版本和应用提交的变更增量维护。
    检索语义：一个 term 的全部 token 都要命中
    （中文 bigram 全命中 = 整词），term 之间取并集——与 Node 的全文粗筛一致。
  · stats() 的 available 沿用 Node 语义：没探测过是 None，探测过后才是 True/False。
  · 附件文本的键是 att-<附件文件名>（摊平后的文件名本身已带 <文献id>- 前缀，不再叠 paperId）。
"""
from __future__ import annotations

import math
import os
import re
import threading
from collections import Counter
from pathlib import Path

from paperlib.adapters import pdf_text
from paperlib.util import query_terms, tokenize

# 每篇文献参与检索的最大字符数（与 Node 一致）
INDEX_CHARS = 200000
INDEX_BATCH_SIZE = 64
# 送给大模型的正文上限
PROMPT_CHARS = 12000


class FullText:
    """全文缓存：正文落 paper_text 表（store），附件文本用 att-<文件名> 键。"""

    def __init__(self, store):
        self.store = store
        base = Path(getattr(store, "data_dir", Path(".")))
        self.dir = Path(getattr(store, "fulltext_dir", base / "fulltext"))
        # 内存倒排索引：token -> {paperId: 词频}；按需建（第一次检索时扫一遍 paper_text）。
        # 附件文本（att- 前缀）不进索引：那是给大模型读的 SI 正文，不参与文献检索。
        self._postings = {}
        self._lengths = {}
        self._index_cursor = None
        self._index_lock = threading.Lock()

    # ---------------------------------------------------------------- 依赖状态
    def available(self):
        return pdf_text.available()

    def availability_known(self):
        return pdf_text.availability_known()

    def install_hint(self):
        return pdf_text.install_hint()

    # ---------------------------------------------------------------- 正文缓存
    def text_file(self, paper_id):
        return str(self.dir / (str(paper_id) + ".txt"))

    def has(self, paper_id):
        return self.store.has_text(str(paper_id))

    def read(self, paper_id, limit=None):
        """读已提取的正文；没有返回 None（端点据此回 404「还没有提取过全文」）。"""
        text = self.store.get_text(str(paper_id))
        if not text:
            return None
        if limit and len(text) > int(limit):
            return text[: int(limit)]
        return text

    def save(self, paper_id, text):
        value = str(text or "")
        self.store.set_text(str(paper_id), value)
        return {"chars": len(value)}

    def remove(self, paper_id):
        key = str(paper_id)
        try:
            self.store.repo.delete_text(key)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - 老 store 没有 delete_text 时退化成清空
            self.store.set_text(key, "")
        try:
            target = Path(self.text_file(key))
            if target.is_file():
                target.unlink()
        except OSError:
            pass

    def stats(self):
        return {
            "available": self.availability_known(),
            "indexed": self.store.repo.count_body_texts(),
            "dir": str(self.dir),
            "tokens": len(self._postings),
        }

    # ---------------------------------------------------------------- 全文检索
    def _refresh_index(self):
        cursor, rebuild, keys = self.store.repo.body_index_changes(self._index_cursor)
        if rebuild:
            self._postings.clear()
            self._lengths.clear()
        for start in range(0, len(keys), INDEX_BATCH_SIZE):
            batch = keys[start:start + INDEX_BATCH_SIZE]
            texts = self.store.repo.read_body_batch(batch, INDEX_CHARS)
            for key in batch:
                self._index_doc(key, texts.get(key, ""))
        self._index_cursor = cursor

    def _index_doc(self, key, text):
        if self._lengths.pop(key, None) is not None:
            for token in [token for token, bucket in self._postings.items() if key in bucket]:
                bucket = self._postings[token]
                del bucket[key]
                if not bucket:
                    del self._postings[token]
        tokens = tokenize(text)
        if not tokens:
            return
        counts = Counter(tokens)
        for token, freq in counts.items():
            self._postings.setdefault(token, {})[key] = freq
        self._lengths[key] = len(tokens)

    def search(self, terms, limit=200):
        """全文检索：返回 [{id, score}] 按分数降序（无命中给空列表，不抛异常）。

        limit=None 返回完整命中集，供分页和分面筛选使用。
        语义：terms 用空白/逗号分隔；**每个词的全部 token 都要命中**
        才算这个词命中——中文 bigram 全命中就等于整词（「超分子」不会命中只有「超分」的正文）。
        打分沿用 Node：词频饱和 + 文档长度归一，避免长文档一味占优。
        """
        text = str(terms or "").strip()
        if not text:
            return []
        try:
            wanted = None if limit is None else max(0, int(limit or 200))
        except (TypeError, ValueError):
            wanted = 200
        with self._index_lock:
            self._refresh_index()
            scores = self._score_terms(text)
        ordered = sorted(scores.items(), key=lambda kv: -kv[1])[:wanted]
        return [{"id": paper_id, "score": score} for paper_id, score in ordered]

    def _score_terms(self, text):
        scores = {}
        for term in query_terms(text):
            tokens = []
            for token in tokenize(term):
                if token not in tokens:
                    tokens.append(token)
            if not tokens:
                continue
            buckets = [self._postings.get(token) for token in tokens]
            if any(bucket is None for bucket in buckets):
                continue
            hit_ids = set(buckets[0])
            for bucket in buckets[1:]:
                hit_ids &= set(bucket)
                if not hit_ids:
                    break
            if not hit_ids:
                continue
            for paper_id in hit_ids:
                norm = math.sqrt((self._lengths.get(paper_id) or 1) / 1000 + 1)
                total = 0.0
                for bucket in buckets:
                    freq = bucket.get(paper_id) or 0
                    total += (1 + math.log(1 + freq)) / norm
                scores[paper_id] = scores.get(paper_id, 0.0) + total
        return scores

    def excerpts(self, paper_id, query, limit=1):
        """正文里包含查询词的一段摘录（前后各约 80 字，空白压平）；无命中返回 []。"""
        text = self.read(paper_id)
        if not text:
            return []
        words = []
        for raw in re.split(r"[\s,，;；]+", str(query or "")):
            word = raw.strip()
            if len(word) >= 2 and word not in words:
                words.append(word)
        if not words:
            return []
        try:
            wanted = max(1, int(limit or 1))
        except (TypeError, ValueError):
            wanted = 1
        lower = text.lower()
        out = []
        for word in words:
            at = lower.find(word.lower())
            if at < 0:
                continue
            start = max(0, at - 80)
            end = min(len(text), at + len(word) + 80)
            snippet = re.sub(r"\s+", " ", text[start:end]).strip()
            if not snippet:
                continue
            out.append(("…" if start > 0 else "") + snippet + ("…" if end < len(text) else ""))
            if len(out) >= wanted:
                break
        return out

    # ---------------------------------------------------------------- 抽取
    def extract_from_file(self, abs_path):
        """整篇抽取：{pages, text}（单页坏掉只丢那一页）。"""
        if not self.available():
            raise RuntimeError(self.install_hint())
        result = pdf_text.extract_text(abs_path)
        unreadable = result["unreadablePages"]
        if unreadable:
            pages = "、".join(map(str, unreadable[:12])) + (" 等" if len(unreadable) > 12 else "")
            raise ValueError("PDF 第 " + pages + " 页的字体字符映射异常，请使用「识图识别全文」。")
        return {"pages": int(result.get("pages") or 0), "text": str(result.get("text") or "")}

    def extract_with_meta(self, abs_path):
        """整篇抽取 + 分页与字体信息：{pages, text, perPage, fonts, info, xmp}。

        perPage[i] = {page, lines, chars, fonts:[{font,size,chars}]}
        （图注 bbox / 标题字体级别判断这类活要的是字体与分页，不是纯文本）。
        """
        if not self.available():
            raise RuntimeError(self.install_hint())
        doc = pdf_text.open_document(abs_path)
        try:
            total = int(doc.page_count)
            chunks = []
            per_page = []
            fonts_total = {}
            for number in range(1, total + 1):
                try:
                    page = doc.load_page(number - 1)
                    lines = pdf_text.rich_lines_from_page(page, with_fonts=True)
                except Exception:  # noqa: BLE001
                    lines = []
                texts = [line["text"] for line in lines]
                text = " ".join(texts).strip()
                if text:
                    chunks.append(text)
                page_fonts = {}
                for line in lines:
                    for item in line.get("fonts") or []:
                        key = (item["font"], item["size"])
                        page_fonts[key] = page_fonts.get(key, 0) + int(item["chars"])
                        fonts_total[key] = fonts_total.get(key, 0) + int(item["chars"])
                per_page.append({
                    "page": number,
                    "lines": len(lines),
                    "chars": len(text),
                    "fonts": [{"font": f, "size": s, "chars": n}
                              for (f, s), n in sorted(page_fonts.items(), key=lambda kv: -kv[1])],
                })
            info, xmp = pdf_text.document_info(doc)
            return {
                "pages": total,
                "text": "\n\n".join(chunks),
                "perPage": per_page,
                "fonts": [{"font": f, "size": s, "chars": n}
                          for (f, s), n in sorted(fonts_total.items(), key=lambda kv: -kv[1])],
                "info": info,
                "xmp": xmp,
            }
        finally:
            try:
                doc.close()
            except Exception:  # noqa: BLE001
                pass

    def build_prompt_excerpt(self, paper_id, limit=None):
        """给大模型用的节选：优先开头（摘要/引言），再补结尾（结论）。"""
        text = self.read(paper_id)
        if not text:
            return ""
        maximum = int(limit or PROMPT_CHARS)
        if len(text) <= maximum:
            return text
        head = text[: int(maximum * 0.75)]
        tail = text[-int(maximum * 0.25):] if int(maximum * 0.25) else ""
        return head + "\n\n……（中间略）……\n\n" + tail

    # ---------------------------------------------------------------- 附件文本
    def attachment_key(self, file):
        """附件文本在 store 里的键：att-<摊平后的附件文件名>。

        附件文件名本身已以 <文献id>- 开头（摊平存放后靠它标明归属），所以**不再**叠 paperId，
        否则会拼成 att-<id>-<id>-xxx.txt（Node 版踩过一次）；paperId 形参只为兼容旧调用。
        """
        return "att-" + os.path.basename(str(file or ""))

    def attachment_text_file(self, paper_id, file):
        return str(self.dir / (self.attachment_key(file) + ".txt"))

    def has_attachment_text(self, paper_id, file):
        return self.store.has_text(self.attachment_key(file))

    def read_attachment(self, paper_id, file):
        key = self.attachment_key(file)
        if not self.store.has_text(key):
            return None
        return self.store.get_text(key) or ""

    def save_attachment_text(self, paper_id, file, text):
        key = self.attachment_key(file)
        self.store.set_text(key, str(text or ""))
        return self.attachment_text_file(paper_id, file)

    def rename_attachment_text(self, paper_id, old_file, new_file):
        """附件改名时把抽取文本一起改名（否则改名后读不到 SI 内容）。"""
        if str(old_file) == str(new_file):
            return False
        old_key = self.attachment_key(old_file)
        new_key = self.attachment_key(new_file)
        if not self.store.has_text(old_key):
            return False
        text = self.store.get_text(old_key) or ""
        self.store.set_text(new_key, text)
        self.remove_attachment_text(paper_id, old_file)
        return True

    def remove_attachment_text(self, paper_id, file):
        key = self.attachment_key(file)
        try:
            self.store.repo.delete_text(key)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            try:
                self.store.set_text(key, "")
            except Exception:  # noqa: BLE001
                pass
        try:
            target = Path(self.attachment_text_file(paper_id, file))
            if target.is_file():
                target.unlink()
        except OSError:
            pass

    def copy_attachment_text(self, paper_id, old_file, new_file):
        """旧文件名 → 新文件名的文本副本（tidy 重命名用；失败返回 False）。"""
        try:
            text = self.read_attachment(paper_id, old_file)
            if text is None:
                return False
            self.save_attachment_text(paper_id, new_file, text)
            return True
        except Exception:  # noqa: BLE001
            return False


__all__ = ["FullText", "INDEX_CHARS", "PROMPT_CHARS"]
