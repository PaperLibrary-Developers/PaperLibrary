"""原文 / 译文切换式翻译任务：状态、产物与编排。

产物目录 data/translations/<paper_id>/
    state.json        状态（idle / running / done / failed / cancelled + 阶段与进度）
    translated.pdf    保留原版式的译文 PDF
    report.json       块级报告（替换数、XObject 兜底数、未译块数）
    mineru/           MinerU 原始解析结果（zip 与解压目录，便于复查）

同一篇文档同时只跑一个任务；取消通过内存标记 + 每页/每块轮询实现。
MinerU 不可用（未配 token、超时、业务错误）时自动降级到 PyMuPDF 自带块。
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from threading import Lock, Thread

from paperlib.adapters.pdf_translate import (
    blocks_from_layout,
    blocks_from_pdf,
    dedupe_hierarchy,
    extract_layout_from_zip,
    fill_empty_sources,
    relink,
    render_translated,
    translate_blocks,
)
from paperlib.util import read_json, sanitize_file_name, write_json_atomic


class TranslateError(RuntimeError):
    pass


def translated_pdf_name(paper):
    """译文文件名：与 papers/ 同级存放，命名 [Translated]<原文件名>.pdf。"""
    stem = Path(str(paper.get("pdfFile") or "")).stem
    if not stem:
        stem = sanitize_file_name(paper.get("title") or "", 80) or str(paper.get("id"))
    stem = re.sub(r"^\[Translated\]\s*", "", stem)
    return "[Translated]" + stem + ".pdf"


class Translator:
    def __init__(self, services):
        self.services = services
        self.root = Path(services.runtime.data_dir) / "translations"
        self.article_dir = Path(services.runtime.data_dir) / "article" / "TranslatedPDF"
        self._lock = Lock()
        self._cancel = set()

    # ---------------- 路径与状态 ----------------

    def dir_for(self, paper_id):
        path = self.root / str(paper_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def pdf_path(self, paper):
        self.article_dir.mkdir(parents=True, exist_ok=True)
        return self.article_dir / translated_pdf_name(paper)

    def state(self, paper):
        paper_id = str(paper.get("id"))
        path = self.root / paper_id / "state.json"
        data = {}
        try:
            raw = read_json(path, {})
            if isinstance(raw, dict):
                data = dict(raw)
        except Exception:  # noqa: BLE001 - 状态文件损坏当作没跑过
            data = {}
        data.setdefault("state", "idle")
        data.setdefault("stage", "")
        data.setdefault("progress", 0)
        data.setdefault("total", 0)
        data.setdefault("pagesDone", 0)
        data.setdefault("pages", 0)
        data.setdefault("error", "")
        data.setdefault("updatedAt", "")
        has_pdf = self.pdf_path(paper).is_file()
        data["exists"] = has_pdf
        data["url"] = ("/api/papers/%s/translation.pdf" % paper_id) if has_pdf else ""
        return data

    def _write_state(self, paper_id, **patch):
        path = self.root / str(paper_id) / "state.json"
        current = {}
        try:
            raw = read_json(path, {})
            if isinstance(raw, dict):
                current = dict(raw)
        except Exception:  # noqa: BLE001 - 损坏当作空
            current = {}
        current.pop("exists", None)
        current.pop("url", None)
        current.update(patch)
        current["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        write_json_atomic(self.root / str(paper_id) / "state.json", current)
        return current

    def is_running(self, paper):
        return self.state(paper).get("state") == "running"

    def cancel(self, paper):
        paper_id = str(paper.get("id"))
        with self._lock:
            self._cancel.add(paper_id)
        state = self.state(paper)
        if state.get("state") == "running":
            self._write_state(paper_id, stage="cancelling")
        return {"cancelled": True, "state": self.state(paper)}

    def _should_cancel(self, paper_id):
        return str(paper_id) in self._cancel

    # ---------------- 启动 ----------------

    def start(self, paper, *, force=False):
        paper_id = str(paper.get("id"))
        with self._lock:
            state = self.state(paper)
            if state.get("state") == "running":
                return {"started": False, "reason": "running", "state": state}
            if state.get("exists") and not force:
                return {"started": False, "reason": "cached", "state": state}
            self._cancel.discard(paper_id)
            self.dir_for(paper_id)
            self._write_state(paper_id, state="running", stage="queued", progress=0,
                              total=0, pages=0, pagesDone=0, error="")
            Thread(target=self._run, args=(paper,), daemon=True).start()
            return {"started": True, "state": self.state(paper)}

    # ---------------- 管线 ----------------

    def _run(self, paper):
        paper_id = str(paper.get("id"))
        started = time.time()
        mineru_warning = ""
        try:
            pdf_path = self.services.db.path_for_paper_file(paper.get("pdfFile"))
            if not pdf_path or not Path(pdf_path).is_file():
                raise TranslateError("这条文献没有关联 PDF，先上传原文")

            # 1) 版面解析
            self._write_state(paper_id, stage="parse")
            layout = None
            mineru = getattr(self.services, "mineru", None)
            if mineru and mineru.ready() and mineru.settings.get("enabled"):
                try:
                    work_dir = self.dir_for(paper_id) / "mineru"
                    batch_id, upload_url = mineru.submit(str(pdf_path), data_id=paper_id)
                    mineru.upload(upload_url, str(pdf_path))
                    item = mineru.wait(batch_id, should_cancel=lambda: self._should_cancel(paper_id))
                    zip_path = mineru.download(item.get("full_zip_url"),
                                               work_dir / "result.zip")
                    layout = extract_layout_from_zip(zip_path, work_dir)
                except Exception as exc:  # noqa: BLE001 - 降级而不是整单失败
                    mineru_warning = "MinerU 不可用，已改用本地版面：" + str(exc)
                    print("[translate] " + mineru_warning)

            blocks = blocks_from_layout(layout) if layout else blocks_from_pdf(pdf_path)
            if layout:
                # MinerU 的空文本块用 PyMuPDF 补；父块（罩住多段、文本只是其中一段）丢掉
                blocks = fill_empty_sources(blocks, pdf_path)
                blocks = dedupe_hierarchy(blocks, pdf_path)
                blocks = relink(blocks)
            if not blocks:
                raise TranslateError("没有可翻译的文本块（可能是扫描件或图片版 PDF）")

            # 2) 逐块翻译
            self._write_state(paper_id, stage="translate", total=len(blocks), progress=0)
            translate_blocks(
                self.services.llm, blocks,
                should_cancel=lambda: self._should_cancel(paper_id),
                on_block=lambda done, total: self._write_state(
                    paper_id, stage="translate", progress=done, total=total),
            )
            if self._should_cancel(paper_id):
                self._write_state(paper_id, state="cancelled", stage="cancelled")
                return

            # 3) 回填
            self._write_state(paper_id, stage="render")
            out_pdf = self.pdf_path(paper)
            font_dir, font_name = _pick_font()
            pages_report = render_translated(
                pdf_path=pdf_path, layout=layout, translations=blocks,
                out_pdf=out_pdf, font_dir=font_dir, font_name=font_name,
                on_page=lambda done, total: self._write_state(
                    paper_id, stage="render", pagesDone=done, pages=total),
            )
            if self._should_cancel(paper_id):
                self._write_state(paper_id, state="cancelled", stage="cancelled")
                return

            report = {
                "blocks": len(blocks),
                "translated": sum(1 for b in blocks if b.get("translation")),
                "pages": pages_report,
                "mineru": bool(layout),
                "warning": mineru_warning,
                "seconds": round(time.time() - started, 1),
            }
            write_json_atomic(self.dir_for(paper_id) / "report.json", report)
            legacy_pdf = self.root / paper_id / "translated.pdf"
            if legacy_pdf.is_file():     # 旧版本把产物放在 translations/<id>/，成功后清掉
                try:
                    legacy_pdf.unlink()
                except OSError:
                    pass
            self._write_state(
                paper_id, state="done", stage="done", error="",
                progress=len(blocks), total=len(blocks),
                pages=len(pages_report), pagesDone=len(pages_report),
                translated=report["translated"], warning=mineru_warning,
                seconds=report["seconds"],
            )
        except Exception as exc:  # noqa: BLE001 - 失败写进状态，前端可读
            print("[translate] 任务失败：%s" % exc)
            try:
                self._write_state(paper_id, state="failed", stage="failed",
                                  error=str(exc)[:400])
            except Exception:  # noqa: BLE001
                pass
        finally:
            with self._lock:
                self._cancel.discard(paper_id)


def _pick_font():
    from paperlib.adapters.pdf_translate import _font_dir
    return _font_dir()
