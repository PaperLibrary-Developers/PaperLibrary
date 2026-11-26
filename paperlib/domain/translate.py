"""原文 / 译文切换式翻译任务：状态、产物与编排。

任务目录 data/translations/<paper_id>/
    state.json        状态（idle / running / done / failed / cancelled + 阶段与进度）
    report.json       块级报告（替换数、XObject 兜底数、未译块数）
    mineru/           MinerU 原始解析 ZIP，便于复查

产物按文献存于 data/article/TranslatedPDF/<paper_id>/。
同一篇文档同时只跑一个任务；取消通过内存标记 + 每页/每块轮询实现。
MinerU 不可用（未配 token、超时、业务错误）时自动降级到 PyMuPDF 自带块。
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock, Thread

from paperlib.adapters.pdf_translate import (
    _font_dir,
    apply_text_budgets,
    blocks_from_content_list_v2,
    blocks_from_layout,
    blocks_from_pdf,
    dedupe_hierarchy,
    normalize_mineru_layout,
    read_mineru_result,
    relink,
    relocate_by_text,
    render_translated,
    repair_sources,
    supplement_mineru_frames,
    translate_blocks,
)
from paperlib.assets import asset_path
from paperlib.util import read_json, sanitize_file_name, write_json_atomic


class TranslateError(RuntimeError):
    pass


def translated_pdf_name(paper):
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
        self._lock = RLock()
        self._running = set()
        self._cancel = set()

    # ---------------- 路径与状态 ----------------

    def dir_for(self, paper_id):
        path = self.root / str(paper_id)
        path.mkdir(parents=True, exist_ok=True)
        return path

    def pdf_path(self, paper):
        return asset_path(self.article_dir, f"{paper['id']}/{translated_pdf_name(paper)}")

    def _state_path(self, paper_id):
        return self.root / str(paper_id) / "state.json"

    def _read_state(self, paper_id):
        raw = read_json(self._state_path(paper_id), {})
        return raw if isinstance(raw, dict) else {}

    def existing_pdf(self, paper, data=None):
        if not paper.get("pdfFile"):
            return None
        if data is None:
            data = self._read_state(paper.get("id"))
        target = self.pdf_path(paper)
        if target is None:
            return None
        if "outputSource" in data:
            return target if data["outputSource"] == paper["pdfFile"] and target.is_file() else None
        # 旧版把各目录的译文放在同一目录，只有归属唯一的旧产物才可复用。
        legacy = asset_path(self.article_dir, target.name)
        if legacy is None or not legacy.is_file() or "translated" not in data:
            return None
        if any(other.get("id") != paper.get("id") and other.get("pdfFile")
               and translated_pdf_name(other).casefold() == target.name.casefold()
               for other in self.services.db.list_papers()):
            return None
        return legacy

    def state(self, paper):
        paper_id = str(paper.get("id"))
        with self._lock:
            data = self._read_state(paper_id)
            if data.get("state") == "running" and paper_id not in self._running:
                data.update(state="failed", stage="failed", error="翻译任务已中断，请重新翻译")
            has_pdf = self.existing_pdf(paper, data) is not None
        data.pop("outputSource", None)
        data.setdefault("state", "idle")
        data.setdefault("stage", "")
        data.setdefault("progress", 0)
        data.setdefault("total", 0)
        data.setdefault("pagesDone", 0)
        data.setdefault("pages", 0)
        data.setdefault("error", "")
        data.setdefault("updatedAt", "")
        data["exists"] = has_pdf
        data["url"] = ("/api/papers/%s/translation.pdf" % paper_id) if has_pdf else ""
        return data

    def _write_state(self, paper_id, **patch):
        with self._lock:
            current = self._read_state(paper_id)
            current.pop("exists", None)
            current.pop("url", None)
            current.update(patch)
            if paper_id in self._cancel and current.get("state") == "running":
                current["stage"] = "cancelling"
            current["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            write_json_atomic(self._state_path(paper_id), current)

    def cancel(self, paper):
        paper_id = str(paper.get("id"))
        with self._lock:
            if self._read_state(paper_id).get("state") == "running" and paper_id in self._running:
                self._cancel.add(paper_id)
                self._write_state(paper_id, stage="cancelling")
            return {"cancelled": True, "state": self.state(paper)}

    def remove(self, paper):
        paper_id = str(paper.get("id"))
        with self._lock:
            if paper_id in self._running:
                raise TranslateError("翻译正在运行，请先取消或等待完成")
            target = self.existing_pdf(paper)
            if target is not None:
                target.unlink(missing_ok=True)
            self._write_state(paper_id, state="idle", stage="", progress=0, total=0,
                              pages=0, pagesDone=0, error="", translated=0, preservedOriginal=0, renderedCaptions=0,
                              warning="", seconds=0, outputSource=None)
            return self.state(paper)

    def _should_cancel(self, paper_id):
        with self._lock:
            return paper_id in self._cancel

    # ---------------- 启动 ----------------

    def start(self, paper, *, force=False):
        paper_id = str(paper.get("id"))
        with self._lock:
            state = self.state(paper)
            if paper_id in self._running:
                return {"started": False, "reason": "running", "state": state}
            if state.get("exists") and not force:
                return {"started": False, "reason": "cached", "state": state}
            self._write_state(paper_id, state="running", stage="queued", progress=0,
                              total=0, pages=0, pagesDone=0, error="")
            self._running.add(paper_id)
            try:
                Thread(target=self._run, args=(paper,), daemon=True).start()
            except RuntimeError as exc:
                self._running.discard(paper_id)
                self._write_state(paper_id, state="failed", stage="failed", error=str(exc))
                raise
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
            phase_started = time.monotonic()
            self._write_state(paper_id, stage="parse")
            layout = None
            content_v2 = None
            mineru = getattr(self.services, "mineru", None)
            if mineru and mineru.ready() and mineru.settings.get("enabled"):
                try:
                    work_dir = self.dir_for(paper_id) / "mineru"
                    batch_id, upload_url = mineru.submit(str(pdf_path), data_id=paper_id)
                    mineru.upload(upload_url, str(pdf_path))
                    item = mineru.wait(batch_id, should_cancel=lambda: self._should_cancel(paper_id))
                    zip_path = mineru.download(item.get("full_zip_url"),
                                               work_dir / "result.zip")
                    layout, content_v2 = read_mineru_result(zip_path)
                except Exception as exc:  # noqa: BLE001 - 降级而不是整单失败
                    if not self._should_cancel(paper_id):
                        mineru_warning = "MinerU 不可用，已改用本地版面：" + str(exc)
                        print("[translate] " + mineru_warning)

            if self._should_cancel(paper_id):
                self._write_state(paper_id, state="cancelled", stage="cancelled")
                return

            blocks = []
            normalization = {"format": "native_pdf"}
            if layout:
                try:
                    layout, recovery = normalize_mineru_layout(layout)
                except ValueError as exc:
                    mineru_warning = "MinerU 跨页归位失败，已使用备用分块：" + str(exc)
                else:
                    blocks = blocks_from_layout(layout, physical_pages=True)
                    normalization = {"format": "middle", **recovery}
            if content_v2:
                page_sizes = [page.get("page_size") or [595.0, 841.0]
                              for page in (layout or {}).get("pdf_info") or []]
                physical_frames = blocks_from_content_list_v2(content_v2, page_sizes, layout)
                if blocks:
                    blocks, supplemented = supplement_mineru_frames(blocks, physical_frames)
                    normalization["supplementedFrameIds"] = supplemented
                else:
                    blocks = physical_frames
                    normalization = {"format": "content_list_v2"}
            if blocks:
                source_ids = {block["id"] for block in blocks}
                blocks = repair_sources(blocks, pdf_path)
                normalization["emptySourceIds"] = sorted(source_ids - {block["id"] for block in blocks})
                if normalization["format"] == "middle" and not content_v2 and not recovery:
                    blocks = relocate_by_text(blocks, pdf_path)
                blocks = relink(dedupe_hierarchy(blocks, pdf_path))
            if not blocks:
                blocks = blocks_from_pdf(pdf_path)
                normalization["format"] = "native_pdf"
            if not blocks:
                raise TranslateError("没有可翻译的文本块（可能是扫描件或图片版 PDF）")

            parse_seconds = round(time.monotonic() - phase_started, 2)
            # 2) 逐块翻译
            phase_started = time.monotonic()
            self._write_state(paper_id, stage="translate", total=len(blocks), progress=0)
            apply_text_budgets(pdf_path, blocks, layout)
            translate_blocks(
                self.services.llm, blocks,
                should_cancel=lambda: self._should_cancel(paper_id),
                on_block=lambda done, total: self._write_state(
                    paper_id, stage="translate", progress=done, total=total),
            )
            if self._should_cancel(paper_id):
                self._write_state(paper_id, state="cancelled", stage="cancelled")
                return

            translate_seconds = round(time.monotonic() - phase_started, 2)
            # 3) 回填
            phase_started = time.monotonic()
            self._write_state(paper_id, stage="render")
            out_pdf = self.pdf_path(paper)
            if out_pdf is None:
                raise TranslateError("译文保存路径无效")
            out_pdf.parent.mkdir(parents=True, exist_ok=True)
            font_dir, font_name = _font_dir()

            def on_page(done, total):
                if self._should_cancel(paper_id):
                    raise TranslateError("任务已取消")
                self._write_state(paper_id, stage="render", pagesDone=done, pages=total)

            with TemporaryDirectory(prefix=".translate-", dir=out_pdf.parent) as work_dir:
                staged_pdf = Path(work_dir) / "translated.pdf"
                pages_report = render_translated(
                    pdf_path=pdf_path, layout=layout, translations=blocks,
                    out_pdf=staged_pdf, font_dir=font_dir, font_name=font_name, on_page=on_page,
                )
                preserved = (sum(page["preserved_original"] for page in pages_report)
                             + len(normalization.get("emptySourceIds", ())))
                report = {
                    "blocks": len(blocks),
                    "translated": sum(1 for b in blocks if b.get("translation")),
                    "pages": pages_report,
                    "mineru": bool(layout),
                    "preservedOriginal": preserved,
                    "normalization": normalization,
                    "stageSeconds": {"parse": parse_seconds, "translate": translate_seconds,
                                     "render": round(time.monotonic() - phase_started, 2)},
                    "captions": sum(block["type"] == "caption" for block in blocks),
                    "renderedCaptions": sum(page["caption_replaced"] for page in pages_report),
                    "warning": mineru_warning,
                    "seconds": round(time.time() - started, 1),
                }
                with self._lock:
                    if self._should_cancel(paper_id):
                        self._write_state(paper_id, state="cancelled", stage="cancelled")
                        return
                    staged_pdf.replace(out_pdf)
                    write_json_atomic(self.dir_for(paper_id) / "report.json", report)
                    self._write_state(
                        paper_id, state="done", stage="done", error="",
                        progress=len(blocks), total=len(blocks), pages=len(pages_report), pagesDone=len(pages_report),
                        translated=report["translated"], preservedOriginal=preserved,
                        renderedCaptions=report["renderedCaptions"], warning=mineru_warning,
                        seconds=report["seconds"], outputSource=paper["pdfFile"],
                    )
            legacy_pdf = self.root / paper_id / "translated.pdf"
            if legacy_pdf.is_file():     # 旧版本把产物放在 translations/<id>/，成功后清掉
                try:
                    legacy_pdf.unlink()
                except OSError:
                    pass
        except Exception as exc:  # noqa: BLE001 - 失败写进状态，前端可读
            print("[translate] 任务失败：%s" % exc)
            try:
                with self._lock:
                    if self._should_cancel(paper_id):
                        self._write_state(paper_id, state="cancelled", stage="cancelled")
                    else:
                        self._write_state(paper_id, state="failed", stage="failed", error=str(exc)[:400])
            except Exception:  # noqa: BLE001
                pass
        finally:
            with self._lock:
                self._cancel.discard(paper_id)
                self._running.discard(paper_id)
