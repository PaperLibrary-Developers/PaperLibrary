"""原文 / 译文切换式翻译任务：状态、产物与编排。

任务目录 data/translations/<paper_id>/
    state.json        状态（idle / running / done / failed / cancelled + 阶段与进度）
    report.json       块级报告（替换数、XObject 兜底数、未译块数）
    region.json       阅读器中尚未关闭或应用的框选重译草稿
    review/           与当前译文 PDF 指纹配对的分块与回填数据
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
from threading import Condition, RLock, Thread
from uuid import uuid4

from paperlib.adapters.llm import MAX_TRANSLATION_CONCURRENCY, valid_translation_concurrency
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
    render_geometry,
    render_translated,
    repair_sources,
    review_frames,
    selected_source_blocks,
    supplement_mineru_frames,
    translate_blocks,
)
from paperlib.assets import asset_path, sha256_file
from paperlib.util import read_json, sanitize_file_name, write_json_atomic

LAYOUT_ONLY_ISSUES = {"overlapping_graphic", "missing_native_text_frame", "overlapping_frames", "missing_source"}


class TranslateError(RuntimeError):
    pass


def translated_pdf_name(paper):
    stem = Path(str(paper.get("pdfFile") or "")).stem
    if not stem:
        stem = sanitize_file_name(paper.get("title") or "", 80) or str(paper.get("id"))
    stem = re.sub(r"^\[Translated\]\s*", "", stem)
    return "[Translated]" + stem + ".pdf"


class TranslationSlots:
    """Limit model requests across translation jobs, including when settings change."""

    def __init__(self, limit):
        self.limit = limit
        self.active = 0
        self.condition = Condition()

    def acquire(self, *, timeout):
        with self.condition:
            if not self.condition.wait_for(lambda: self.active < self.limit(), timeout):
                return False
            self.active += 1
            return True

    def release(self):
        with self.condition:
            self.active -= 1
            self.condition.notify()


class Translator:
    def __init__(self, services):
        self.services = services
        self.root = Path(services.runtime.data_dir) / "translations"
        self.article_dir = Path(services.runtime.data_dir) / "article" / "TranslatedPDF"
        self._lock = RLock()
        self._running = set()
        self._cancel = set()
        self._translation_slots = TranslationSlots(self._translation_limit)
        self._region_previews = set()

    def _translation_limit(self):
        settings = getattr(self.services.llm, "settings", None)
        value = settings.get("translationConcurrency") if isinstance(settings, dict) else None
        return value if valid_translation_concurrency(value) else MAX_TRANSLATION_CONCURRENCY

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
            with self._lock:
                return self.existing_pdf(paper, self._read_state(paper.get("id")))
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
                              warning="", seconds=0, outputSource=None, outputRevision="", retryBlock=None)
            self._clear_review(paper_id)
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
            return self._launch(paper, self._run)

    def _launch(self, paper, run, *args, block_id=None, region_token=None):
        paper_id = str(paper["id"])
        self._write_state(paper_id, state="running", stage="queued", progress=0, total=int(block_id is not None),
                          pages=0, pagesDone=0, error="", retryBlock=block_id, regionToken=region_token)
        self._running.add(paper_id)
        try:
            Thread(target=run, args=(paper, *args), daemon=True).start()
        except RuntimeError as exc:
            self._running.discard(paper_id)
            self._write_state(paper_id, state="failed", stage="failed", error=str(exc))
            raise
        return {"started": True, "state": self.state(paper)}

    def _review_dir(self, paper_id):
        return self.root / str(paper_id) / "review"

    def _clear_review(self, paper_id, keep=None):
        for old in self._review_dir(paper_id).glob("*.json"):
            if old != keep:
                try:
                    old.unlink(missing_ok=True)
                except OSError:
                    pass

    def _review_snapshot(self, paper):
        target = self.existing_pdf(paper)
        if target is None:
            return None, "", None
        revision = sha256_file(target)
        snapshot = read_json(self._review_dir(paper["id"]) / f"{revision}.json") if revision else None
        return target, revision, snapshot if isinstance(snapshot, dict) and snapshot.get("schema") == 1 else None

    def review(self, paper):
        with self._lock:
            target, revision, snapshot = self._review_snapshot(paper)
            if snapshot:
                return {"revision": revision, "pages": snapshot["frames"]}
            # Older translations may have problem locations but lack replayable translations.
            report = read_json(self.root / str(paper["id"]) / "report.json", {}) or {}
            pages = review_frames(target, report.get("pages", []), set()) if target and report.get("pages") else {}
            return {"revision": revision, "pages": pages, "legacy": bool(target)}

    def retry(self, paper, block_id, revision):
        paper_id = str(paper["id"])
        with self._lock:
            if paper_id in self._running:
                raise TranslateError("这篇文献正在翻译，请稍后再试")
            _, current, snapshot = self._review_snapshot(paper)
            if current != revision:
                raise TranslateError("译文已更新，请重新打开译文后再试")
            if not snapshot:
                raise TranslateError("这份旧译文没有保存分块数据，请先重新生成译文")
            pages = snapshot["frames"]
            if not any(frame["id"] == block_id and frame["retryable"]
                       for frames in pages.values() for frame in frames):
                raise TranslateError("当前文本块无法单独重译")
            pdf_path = self.services.db.path_for_paper_file(paper.get("pdfFile"))
            if not pdf_path or sha256_file(pdf_path) != snapshot["sourceHash"]:
                raise TranslateError("原文已更换，请重新生成译文")
            return self._launch(paper, self._run_retry, pdf_path, snapshot, block_id, block_id=block_id)

    def inspect_region(self, paper, revision, page, rect):
        paper_id = str(paper["id"])
        with self._lock:
            if self.region_status(paper):
                raise TranslateError("请先应用或关闭已有的框选重译")
            _, current, snapshot = self._review_snapshot(paper)
            if not current or revision != current:
                raise TranslateError("译文已更新，请重新打开译文后框选")
            pdf_path = self.services.db.path_for_paper_file(paper.get("pdfFile"))
            source_hash = sha256_file(pdf_path) if pdf_path else ""
            if not source_hash or (snapshot and source_hash != snapshot["sourceHash"]):
                raise TranslateError("原文已变更或无法读取，请重新生成译文")
            known = [block for block in snapshot["blocks"] if block["page"] == page] if snapshot else []
            placed = snapshot["report"]["pages"][page - 1]["placed"] if snapshot and 1 <= page <= len(
                snapshot["report"]["pages"]) else {}
            candidates = selected_source_blocks(pdf_path, page, rect, known, placed)
            if not candidates:
                raise TranslateError("框选位置没有可提取的原文文字，请重新框选")
            if len(candidates) > 8 or sum(len(block["source"]) for block in candidates) > 12000:
                raise TranslateError("框选内容较长，请按段落或栏拆开重译")
            context = {block["id"]: block for block in (*known, *candidates)}
            relink(sorted(context.values(), key=lambda block: (block["bbox"][1], block["bbox"][0])))
            apply_text_budgets(pdf_path, candidates, snapshot["layout"] if snapshot else None)
            draft = {
                "token": uuid4().hex, "sourceHash": source_hash, "sourceFile": paper["pdfFile"],
                "revision": revision, "blocks": candidates, "canApply": bool(snapshot), "phase": "selected",
            }
            write_json_atomic(self._region_path(paper_id), draft)
            return self._region_public(draft)

    def _region_path(self, paper_id):
        return self.root / str(paper_id) / "region.json"

    def _read_region(self, paper_id):
        draft = read_json(self._region_path(paper_id))
        return draft if isinstance(draft, dict) else None

    def _region_draft(self, paper_id, token):
        draft = self._read_region(paper_id)
        if not draft or draft["token"] != token:
            raise TranslateError("框选预览已失效，请重新框选")
        return draft

    def _region_public(self, draft, *, phase=None, message=None):
        phase = phase or draft["phase"]
        translations = draft.get("translations", [])
        return {
            "token": draft["token"], "source": [block["source"] for block in draft["blocks"]],
            "translations": translations, "phase": phase,
            "canApply": phase == "ready" and draft["canApply"] and bool(translations) and all(translations),
            "message": draft.get("error", "") if message is None else message,
        }

    def region_status(self, paper):
        paper_id = str(paper["id"])
        with self._lock:
            draft = self._read_region(paper_id)
            if not draft:
                return None
            token = draft["token"]
            state = self._read_state(paper_id)
            owns_writeback = state.get("regionToken") == token
            if owns_writeback and state.get("state") == "done":
                target = self.existing_pdf(paper, state)
                published = sha256_file(target) if target else ""
                if published and state.get("outputRevision") == published:
                    return None
                if published == draft["revision"]:
                    return self._region_public(draft, message="上次写回已中断，可重新应用。")
                return self._region_public(draft, phase="stale", message="译文已更新。可复制已有译文，关闭后可重新框选。")
            if owns_writeback and state.get("state") == "running" and paper_id in self._running:
                return self._region_public(draft, phase="applying")
            revision = state.get("outputRevision")
            if (paper.get("pdfFile") != draft["sourceFile"]
                    or not self.existing_pdf(paper, state)
                    or (revision and revision != draft["revision"] and not owns_writeback)):
                return self._region_public(draft, phase="stale", message="原文或译文已更新。可复制已有译文，关闭后可重新框选。")
            if owns_writeback:
                message = {
                    "failed": state.get("error") or "写回失败，可重新应用。",
                    "cancelled": "已取消写回，可重新应用。",
                    "running": "上次写回已中断，可重新应用。",
                }.get(state["state"], "")
                return self._region_public(draft, message=message)
            if draft["phase"] == "previewing" and token not in self._region_previews:
                return self._region_public(draft, phase="selected", message="上次重译已中断，可重新重译选区。")
            return self._region_public(draft)

    def close_region(self, paper, token):
        paper_id = str(paper["id"])
        with self._lock:
            draft = self._read_region(paper_id)
            if not draft:
                return
            if draft["token"] != token:
                raise TranslateError("当前框选已更新，请重新打开详情页")
            self._region_path(paper_id).unlink(missing_ok=True)
            self._region_previews.discard(token)
            if self._read_state(paper_id).get("regionToken") == token:
                self.cancel(paper)

    def _preview_active(self, token):
        with self._lock:
            return token in self._region_previews

    def preview_region(self, paper, token):
        paper_id = str(paper["id"])
        with self._lock:
            draft = self._region_draft(paper_id, token)
            if token in self._region_previews:
                return self._region_public(draft)
            if draft["phase"] == "ready":
                return self.region_status(paper)
            target = self.existing_pdf(paper)
            pdf_path = self.services.db.path_for_paper_file(paper.get("pdfFile"))
            if (not target or sha256_file(target) != draft["revision"]
                    or not pdf_path or sha256_file(pdf_path) != draft["sourceHash"]):
                raise TranslateError("原文或译文已更新，请重新框选")
            draft.update(phase="previewing", error="")
            write_json_atomic(self._region_path(paper_id), draft)
            self._region_previews.add(token)
            try:
                Thread(target=self._run_region_preview, args=(paper_id, draft), daemon=True).start()
            except RuntimeError as exc:
                self._region_previews.discard(token)
                draft.update(phase="selected", error="无法启动重译，请重试。")
                write_json_atomic(self._region_path(paper_id), draft)
                raise TranslateError(draft["error"]) from exc
            return self._region_public(draft)

    def _run_region_preview(self, paper_id, draft):
        token = draft["token"]
        try:
            candidates = [dict(block, translation="") for block in draft["blocks"]]
            # Protected content can still be read in a preview. The renderer retains that protection.
            for block in candidates:
                block.pop("protected_layout", None)
            translate_blocks(self.services.llm, candidates, workers=self._translation_limit(),
                             slots=self._translation_slots, should_cancel=lambda: not self._preview_active(token))
            translations = []
            for block in candidates:
                text = block.get("translation") or ""
                translations.append("" if text == block["source"] else text)
            if not any(translations):
                raise TranslateError("本次没有获得可用的译文，可重新框选后再试")
            draft.update(phase="ready", translations=translations)
        except Exception as exc:  # noqa: BLE001 - return preview errors through the saved draft
            draft.update(phase="selected", error=str(exc)[:400])
        finally:
            with self._lock:
                if token in self._region_previews:
                    self._region_previews.discard(token)
                    write_json_atomic(self._region_path(paper_id), draft)

    def apply_region(self, paper, token):
        paper_id = str(paper["id"])
        with self._lock:
            draft = self._region_draft(paper_id, token)
            if paper_id in self._running or token in self._region_previews:
                raise TranslateError("这篇文献正在翻译，请稍后再试")
            _, revision, snapshot = self._review_snapshot(paper)
            pdf_path = self.services.db.path_for_paper_file(paper.get("pdfFile"))
            if not snapshot or not draft.get("translations") or not all(draft["translations"]):
                raise TranslateError("当前预览无法写回，可在预览中阅读或复制译文")
            if revision != draft["revision"] or not pdf_path or sha256_file(pdf_path) != draft["sourceHash"]:
                raise TranslateError("原文或译文已更新，请重新打开译文后框选")
            return self._launch(paper, self._run_region_apply, pdf_path, snapshot, draft,
                                block_id="region", region_token=token)

    def _run_region_apply(self, paper, pdf_path, snapshot, draft):
        paper_id = str(paper["id"])
        started = time.time()
        error = None
        try:
            blocks = {block["id"]: block for block in snapshot["blocks"]}
            selected = []
            for block, translation in zip(draft["blocks"], draft["translations"]):
                replacement = dict(block, translation=translation)
                replacement.pop("translation_issue", None)
                blocks[block["id"]] = replacement
                selected.append(replacement)
            self._render(paper, pdf_path, draft["sourceHash"], snapshot["layout"], list(blocks.values()),
                         snapshot["report"], started, 0, retry_blocks=selected)
            with self._lock:
                saved = self._read_region(paper_id)
                if saved and saved["token"] == draft["token"] and self._read_state(paper_id).get("state") == "done":
                    try:
                        self._region_path(paper_id).unlink(missing_ok=True)
                    except OSError:
                        # A completed writeback is also recognized by its published PDF revision.
                        pass
        except Exception as exc:  # noqa: BLE001 - report back without publishing an unsafe rewrite
            error = exc
        finally:
            self._finish_job(paper_id, error)

    def _finish_job(self, paper_id, error=None):
        with self._lock:
            try:
                if paper_id in self._cancel and self._read_state(paper_id).get("state") == "running":
                    self._write_state(paper_id, state="cancelled", stage="cancelled")
                elif error:
                    self._write_state(paper_id, state="failed", stage="failed", error=str(error)[:400])
            finally:
                self._cancel.discard(paper_id)
                self._running.discard(paper_id)

    def _run_retry(self, paper, pdf_path, snapshot, block_id):
        paper_id = str(paper["id"])
        started = time.time()
        error = None
        try:
            blocks = snapshot["blocks"]
            block = next(item for item in blocks if item["id"] == block_id)
            self._write_state(paper_id, stage="translate")
            previous = snapshot["report"]
            page = previous["pages"][block["page"] - 1]
            reason = next((issue["reason"] for issue in page["issues"] if issue["id"] == block_id), "")
            # Keep the shorter budget local to this attempt. Subsequent attempts start from the original budget.
            candidate = dict(block)
            if reason == "no_safe_fit" and candidate.get("text_budget"):
                candidate["text_budget"] = max(6, round(candidate["text_budget"] * 0.80))
            translate_blocks(self.services.llm, [candidate], slots=self._translation_slots,
                             should_cancel=lambda: self._should_cancel(paper_id))
            if self._should_cancel(paper_id):
                return
            translated = candidate.get("translation")
            if not translated or translated == block["source"]:
                raise TranslateError("这次没有获得可用的译文，已保留当前内容")
            block["translation"] = translated
            block.pop("translation_issue", None)
            self._write_state(paper_id, progress=1)
            self._render(paper, pdf_path, snapshot["sourceHash"], snapshot["layout"], blocks,
                         previous, started, round(time.time() - started, 2), retry_blocks=[block])
        except Exception as exc:  # noqa: BLE001 - surface retry failure through the existing job state
            error = exc
        finally:
            self._finish_job(paper_id, error)

    # ---------------- 管线 ----------------

    def _run(self, paper):
        paper_id = str(paper.get("id"))
        started = time.time()
        mineru_warning = ""
        error = None
        try:
            pdf_path = self.services.db.path_for_paper_file(paper.get("pdfFile"))
            source_hash = sha256_file(pdf_path) if pdf_path else ""
            if not source_hash:
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
                source_frames = {block["id"]: {"id": block["id"], "page": block["page"], "bbox": block["bbox"]}
                                 for block in blocks}
                blocks = repair_sources(blocks, pdf_path)
                missing = sorted(source_frames.keys() - {block["id"] for block in blocks})
                normalization["emptySourceIds"] = missing
                normalization["emptySourceFrames"] = [source_frames[key] for key in missing]
                if normalization["format"] == "middle" and not content_v2 and not recovery.get("cross_page_recovered_block_count"):
                    blocks = relocate_by_text(blocks, pdf_path)
                blocks = relink(dedupe_hierarchy(blocks, pdf_path))
            if not blocks:
                blocks = blocks_from_pdf(pdf_path)
                normalization["format"] = "native_pdf"
            if not blocks:
                raise TranslateError("没有可翻译的文本块（可能是扫描件或图片版 PDF）")

            parse_seconds = round(time.monotonic() - phase_started, 2)
            # 2) 并发翻译文本块，所有文献共享 LLM 请求上限
            phase_started = time.monotonic()
            self._write_state(paper_id, stage="translate", total=len(blocks), progress=0)
            apply_text_budgets(pdf_path, blocks, layout)
            translate_blocks(
                self.services.llm, blocks,
                should_cancel=lambda: self._should_cancel(paper_id),
                on_block=lambda done, total: self._write_state(
                    paper_id, stage="translate", progress=done, total=total),
                workers=self._translation_limit(), slots=self._translation_slots,
            )
            if self._should_cancel(paper_id):
                return

            translate_seconds = round(time.monotonic() - phase_started, 2)
            self._render(paper, pdf_path, source_hash, layout, blocks, {
                "normalization": normalization, "warning": mineru_warning,
                "stageSeconds": {"parse": parse_seconds},
            }, started, translate_seconds)
            if self._should_cancel(paper_id):
                return
            legacy_pdf = self.root / paper_id / "translated.pdf"
            if legacy_pdf.is_file():     # 旧版本把产物放在 translations/<id>/，成功后清掉
                try:
                    legacy_pdf.unlink()
                except OSError:
                    pass
        except Exception as exc:  # noqa: BLE001 - 失败写进状态，前端可读
            print("[translate] 任务失败：%s" % exc)
            error = exc
        finally:
            self._finish_job(paper_id, error)

    def _render(self, paper, pdf_path, source_hash, layout, blocks, previous, started, translate_seconds,
                retry_blocks=()):
        paper_id = str(paper["id"])
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
            self._write_state(paper_id, pagesDone=done, pages=total)

        with TemporaryDirectory(prefix=".translate-", dir=out_pdf.parent) as work_dir:
            staged_pdf = Path(work_dir) / "translated.pdf"
            retry = ({"ids": {block["id"] for block in retry_blocks}, "page": retry_blocks[0]["page"],
                      "pages": previous["pages"]} if retry_blocks else None)
            pages = render_translated(pdf_path=pdf_path, layout=layout, translations=blocks,
                                      out_pdf=staged_pdf, font_dir=font_dir, font_name=font_name,
                                      on_page=on_page, retry=retry)
            if retry:
                page = pages[retry["page"] - 1]
                if retry["ids"] - page["placed"].keys():
                    raise TranslateError("新译文无法安全放入原版式，已保留当前内容")
                if any(old["placed"].keys() - new["placed"].keys() for old, new in zip(previous["pages"], pages)):
                    raise TranslateError("新译文会影响其他段落，已保留当前内容")

            normalization = previous["normalization"]
            for frame in normalization.get("emptySourceFrames", ()):
                if 1 <= frame["page"] <= len(pages):
                    pages[frame["page"] - 1]["issues"].append({**frame, "reason": "missing_source"})
            preserved = (sum(page["preserved_original"] for page in pages)
                         + len(normalization.get("emptySourceIds", ())))
            report = {
                "blocks": len(blocks),
                "translated": sum(1 for block in blocks if block.get("translation")),
                "pages": pages,
                "mineru": bool(layout),
                "preservedOriginal": preserved,
                "normalization": normalization,
                "stageSeconds": {"parse": 0 if retry else previous["stageSeconds"]["parse"],
                                 "translate": translate_seconds, "render": round(time.monotonic() - phase_started, 2)},
                "captions": sum(block["type"] == "caption" for block in blocks),
                "renderedCaptions": sum(page["caption_replaced"] for page in pages),
                "warning": previous["warning"],
                "seconds": round(time.time() - started, 1),
            }
            retryable = {block["id"] for block in blocks if not block.get("protected_layout")}
            retryable.difference_update(issue["id"] for page in pages for issue in page["issues"]
                                        if issue["reason"] in LAYOUT_ONLY_ISSUES)
            frames = review_frames(staged_pdf, pages, retryable)
            revision = sha256_file(staged_pdf)
            if not revision:
                raise TranslateError("译文无法读取，已停止保存")
            review_path = self._review_dir(paper_id) / f"{revision}.json"
            with self._lock:
                if self._should_cancel(paper_id):
                    return
                library = getattr(self.services, "library", None)
                current = library.get(paper_id) if library is not None else paper
                if (not current or current.get("pdfFile") != paper["pdfFile"]
                        or sha256_file(pdf_path) != source_hash):
                    raise TranslateError("关联原文已变更，已停止保存")
                # Publish the review data before replacing the file. Only a matching PDF can activate it.
                write_json_atomic(review_path, {"schema": 1, "sourceHash": source_hash, "blocks": blocks,
                                                "layout": render_geometry(layout), "frames": frames, "report": report})
                old_state = self._read_state(paper_id)
                total = len(retry_blocks) if retry else len(blocks)
                self._write_state(
                    paper_id, state="done", stage="done", error="", progress=total, total=total,
                    pages=len(pages), pagesDone=len(pages), translated=report["translated"], preservedOriginal=preserved,
                    renderedCaptions=report["renderedCaptions"], warning=report["warning"],
                    seconds=report["seconds"], outputSource=paper["pdfFile"], outputRevision=revision,
                )
                try:
                    staged_pdf.replace(out_pdf)
                except OSError:
                    write_json_atomic(self._state_path(paper_id), old_state)
                    raise
                try:
                    write_json_atomic(self.dir_for(paper_id) / "report.json", report)
                except OSError as exc:
                    print("[translate] 诊断报告副本未能写入：%s" % exc)
                self._clear_review(paper_id, keep=review_path)
