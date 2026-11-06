"""PDF 全文抽取与缓存（移植自 server/adapters/pdf.js，248 行；契约名：pdf.extract_text(path) → Text）。

权威实现是 **PyMuPDF**（不是 pdfjs）：pdfjs 只在前端展示，抽取一律走这里。

与 Node 版的落点差异（形状不变）：
  · 正文的**唯一真源是 SQLite 的 paper_text 表**（store.get_text / set_text），不再以
    data/fulltext/<id>.txt 为准；store.set_text 会顺手在 data/fulltext/ 镜像一份老布局的 .txt，
    旧备份/外部工具有需要时还能读。
  · 倒排索引不在这里：检索由 domain/search_index（T2）负责，启动时按 store 里的正文重建，
    所以本类只做「抽取 + 缓存 + 附件文本」。
  · 附件文本的键是 att-<附件文件名>（摊平后的文件名本身已带 <文献id>- 前缀，不再叠 paperId）。
"""
from __future__ import annotations

import os
from pathlib import Path

from paperlib.adapters import pdf_text

# 每篇文献参与检索的最大字符数（与 Node 一致）
INDEX_CHARS = 200000
# 送给大模型的正文上限
PROMPT_CHARS = 12000


class FullText:
    """全文缓存：正文落 paper_text 表（store），附件文本用 att-<文件名> 键。"""

    def __init__(self, store):
        self.store = store
        base = Path(getattr(store, "data_dir", Path(".")))
        self.dir = Path(getattr(store, "fulltext_dir", base / "fulltext"))

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
        key = str(paper_id)
        if not self.store.has_text(key):
            return None
        text = self.store.get_text(key) or ""
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

    def load_all(self, papers):
        """启动时统计已提取正文的篇数（索引由 domain/search_index 重建，这里不再建倒排）。"""
        count = 0
        for paper in papers or []:
            if paper and paper.get("id") and self.has(paper.get("id")):
                count += 1
        return count

    def stats(self):
        keys = []
        try:
            keys = [k for k in self.store.list_text_keys() if not str(k).startswith("att-")]
        except Exception:  # noqa: BLE001
            keys = []
        return {
            "available": self.availability_known(),
            "indexed": len(keys),
            "dir": str(self.dir),
            "tokens": 0,  # 倒排已交给 domain/search_index
        }

    # ---------------------------------------------------------------- 抽取
    def extract_from_file(self, abs_path):
        """整篇抽取：{pages, text}（单页坏掉只丢那一页）。"""
        if not self.available():
            raise RuntimeError(self.install_hint())
        result = pdf_text.extract_text(abs_path)
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
