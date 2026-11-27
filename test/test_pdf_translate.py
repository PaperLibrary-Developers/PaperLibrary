"""Regression coverage for physical text frames and PDF translation fitting."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from threading import Event, Lock, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pymupdf as fitz

from paperlib.adapters.mineru import MinerU, MinerUError
from paperlib.adapters.pdf_translate import (
    _font_dir, _protected_boxes, apply_text_budgets, blocks_from_content_list_v2, blocks_from_layout, build_html,
    normalize_mineru_layout, read_mineru_result, render_translated, repair_sources,
    supplement_mineru_frames, translate_blocks,
)
from paperlib.assets import sha256_file
from paperlib.domain.translate import TranslateError, Translator


class MinerUTranslationTest(unittest.TestCase):
    def test_methods_prose_mislabelled_as_references_is_recovered(self):
        def block(kind, text, y):
            box = [40, y, 220, y + 22]
            return {"type": kind, "bbox": box, "lines": [
                {"bbox": [40, y + 11 * row, 220, y + 11 * (row + 1)],
                 "spans": [{"type": "text", "content": line}]}
                for row, line in enumerate(text.splitlines())
            ]}

        prose = ("Sample preparation. The solution was prepared and left to equilibrate.\n"
                 "The measurement was then recorded and compared with the control sample.")
        blocks = [block("title", "Methods", 20), block("ref_text", prose, 42),
                  block("ref_text", "1. " + prose, 64), block("title", "References", 108),
                  block("ref_text", prose, 130)]
        layout = {"pdf_info": [{"page_idx": 0, "para_blocks": blocks}]}

        normalized, report = normalize_mineru_layout(layout)
        self.assertEqual(report["methods_prose_recovered"], 1)
        self.assertEqual(blocks[1]["type"], "ref_text")
        self.assertEqual([item["type"] for item in normalized["pdf_info"][0]["para_blocks"]],
                         ["title", "text", "ref_text", "title", "ref_text"])
        self.assertIn("Sample preparation.", blocks_from_layout(normalized)[1]["source"])

    def test_only_current_archive_can_supply_layout_and_v2(self):
        with tempfile.TemporaryDirectory() as folder:
            work = Path(folder)
            (work / "old_content_list_v2.json").write_text('[[]]', encoding="utf-8")
            archive = work / "result.zip"
            layout = {"pdf_info": [{"page_idx": 0}]}
            with ZipFile(archive, "w") as stream:
                stream.writestr("new/layout.json", json.dumps(layout))
                stream.writestr("new/broken_content_list_v2.json", "invalid JSON")
            self.assertEqual(read_mineru_result(archive), (layout, None))
            content = [[{"type": "paragraph"}]]
            with ZipFile(archive, "a") as stream:
                stream.writestr("new/content_list_v2.json", json.dumps(content))
            self.assertEqual(read_mineru_result(archive), (layout, content))

    def test_accepts_current_middle_archive_name(self):
        with tempfile.TemporaryDirectory() as folder:
            archive = Path(folder) / "result.zip"
            layout = {"pdf_info": [{"page_idx": 0}]}
            with ZipFile(archive, "w") as stream:
                stream.writestr("new/paper_middle.json", json.dumps(layout))
            self.assertEqual(read_mineru_result(archive), (layout, None))

    def test_dns_upload_failure_retries_but_http_failure_is_final(self):
        client = MinerU.__new__(MinerU)
        with patch("paperlib.adapters.mineru.subprocess.run") as run, patch("paperlib.adapters.mineru.time.sleep"):
            run.side_effect = [SimpleNamespace(returncode=6), SimpleNamespace(returncode=0, stdout=b"200")]
            self.assertTrue(client.upload("https://example.com/upload", "input.pdf"))
            self.assertEqual(run.call_count, 2)
            run.reset_mock(side_effect=True)
            run.return_value = SimpleNamespace(returncode=0, stdout=b"403")
            with self.assertRaisesRegex(MinerUError, "HTTP 403"):
                client.upload("https://example.com/upload", "input.pdf")
            self.assertEqual(run.call_count, 1)


class TranslationFramesTest(unittest.TestCase):
    def test_mineru_merged_paragraph_recovers_each_physical_frame(self):
        first_text = {"type": "text", "content": "The experiment produced "}
        formula = {"type": "inline_equation", "content": r"K^{2}"}
        second_text = {"type": "text", "content": "under the conditions shown."}
        first_line = {"bbox": [21, 72, 108, 82], "spans": [first_text, formula]}
        second_line = {"bbox": [122, 22, 204, 32], "spans": [second_text]}
        left = {"type": "text", "bbox": [20, 70, 110, 85], "lines": [first_line]}
        right = {"type": "text", "bbox": [120, 20, 205, 35], "lines": [second_line]}
        layout = {"pdf_info": [{
            "page_idx": 0,
            "preproc_blocks": [left, right],
            "para_blocks": [dict(left, lines=[first_line, second_line]), dict(right, lines=[], lines_deleted=True)],
        }]}
        pages = [[
            {"type": "paragraph", "bbox": [80, 280, 440, 340], "content": {
                "paragraph_content": [first_text, formula, second_text],
            }},
            {"type": "paragraph", "bbox": [480, 80, 820, 140], "content": {"paragraph_content": []}},
        ]]

        for blocks in (blocks_from_content_list_v2(pages, [(250, 250)], layout), blocks_from_layout(layout)):
            self.assertEqual([b["source"] for b in blocks], [
                "The experiment produced ⟦K^{2}⟧", "under the conditions shown.",
            ])
            self.assertLess(fitz.Rect(blocks[0]["bbox"]).x1, fitz.Rect(blocks[1]["bbox"]).x0)
            self.assertEqual(blocks[0]["next"], blocks[1]["source"])

    def test_transition_moves_with_verified_continuation_without_losing_content(self):
        left_source = r"Values replace ⟦\gamma ;⟧ however, these"
        right_source = "constants range from 200 to 500."
        left_line = {"bbox": [21, 72, 108, 82], "spans": [
            {"type": "text", "content": left_source},
        ]}
        right_line = {"bbox": [122, 22, 204, 32], "spans": [
            {"type": "text", "content": right_source},
        ]}
        left = {"type": "text", "bbox": [20, 70, 110, 85], "lines": [left_line]}
        right = {"type": "text", "bbox": [120, 20, 205, 35], "lines": [right_line]}
        layout = {"pdf_info": [{
            "page_idx": 0,
            "preproc_blocks": [left, right],
            "para_blocks": [dict(left, lines=[left_line, right_line]), dict(right, lines=[], lines_deleted=True)],
        }]}
        pages = [[
            {"type": "paragraph", "bbox": [80, 280, 440, 340], "content": {
                "paragraph_content": [{"type": "text", "content": left_source + " " + right_source}],
            }},
            {"type": "paragraph", "bbox": [480, 80, 820, 140], "content": {"paragraph_content": []}},
        ]]
        for frames in (blocks_from_content_list_v2(pages, [(250, 250)], layout), blocks_from_layout(layout)):
            a, b = frames
            self.assertEqual(a["source"], r"Values replace ⟦\gamma ;⟧")
            self.assertEqual(b["source"], "however, these " + right_source)
            self.assertEqual(a["source"] + " " + b["source"], left_source + " " + right_source)
            self.assertEqual(a["next"], b["source"])

    def test_cross_page_spans_keep_physical_page(self):
        def text_block(index, bbox, text):
            return {"index": index, "type": "text", "bbox": bbox, "lines": [
                {"bbox": bbox, "spans": [{"type": "text", "bbox": bbox, "content": text}]}
            ]}

        head = text_block(3, [40, 180, 190, 194], "A long paragraph begins")
        tail = text_block(1, [40, 20, 190, 34], "and continues on another page.")
        moved = {"bbox": tail["bbox"], "spans": [dict(tail["lines"][0]["spans"][0], cross_page=True)]}
        layout = {"pdf_info": [
            {"page_idx": 0, "preproc_blocks": [head],
             "para_blocks": [dict(head, lines=[*head["lines"], moved])]},
            {"page_idx": 1, "preproc_blocks": [tail],
             "para_blocks": [dict(tail, lines=[], lines_deleted=True)]},
        ]}
        normalized, report = normalize_mineru_layout(layout)
        blocks = blocks_from_layout(normalized, physical_pages=True)
        self.assertEqual([(b["page"], b["source"]) for b in blocks], [
            (1, "A long paragraph begins"), (2, "and continues on another page."),
        ])
        self.assertEqual(report["cross_page_recovered_span_count"], 1)
        self.assertTrue(layout["pdf_info"][1]["para_blocks"][0]["lines_deleted"])

    def test_supplement_adds_unclaimed_frame_without_duplication(self):
        left = {"id": "p1b0", "page": 1, "bbox": [20, 60, 110, 92], "source": "A complete left frame."}
        right = {"id": "p1v1", "page": 1, "bbox": [130, 60, 221, 92], "source": "A valid continuation."}
        footer = {"id": "p1b2", "page": 1, "bbox": [21, 120, 110, 150], "source": "Then the next section."}
        blocks, added = supplement_mineru_frames(
            [left, footer], [dict(left, id="duplicate"), right, dict(footer, id="duplicate-2")],
        )
        self.assertEqual([b["id"] for b in blocks], [left["id"], right["id"], footer["id"]])
        self.assertEqual(added, [right["id"]])

    def test_formal_caption_is_translatable_but_panel_label_is_not(self):
        def caption(text, bbox):
            return {"type": "image_caption", "bbox": bbox, "lines": [
                {"bbox": bbox, "spans": [{"type": "text", "content": text}]}
            ]}

        layout = {"pdf_info": [{"page_idx": 0, "para_blocks": [
            {"type": "image", "blocks": [
                caption("a)", [20, 90, 44, 105]),
                caption("Figure 1. Structure of the complex.", [20, 200, 190, 216]),
            ]},
        ]}]}
        self.assertEqual([(b["type"], b["source"]) for b in blocks_from_layout(layout)], [
            ("caption", "Figure 1. Structure of the complex."),
        ])

    def test_translation_retries_length_and_restores_protected_tokens(self):
        llm = Mock()
        llm.chat.side_effect = [
            {"text": "这是本来可以写得更加简练的一段科学译文⟦T1⟧，参见⟦T2⟧。"},
            {"text": "测得⟦T1⟧，见⟦T2⟧。"},
        ]
        block = {"id": "1", "source": r"We measured ⟦K^{2}⟧, see ref.<sup>31</sup>", "text_budget": 12}
        translate_blocks(llm, [block])

        self.assertEqual(block["translation"], r"测得⟦K^{2}⟧，见<sup>31</sup>。")
        calls = llm.chat.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertIn("上次译文", calls[1].args[0][1]["content"])
        prompt = calls[0].args[0][1]["content"]
        self.assertIn("We measured ⟦T1⟧, see ref.⟦T2⟧", prompt)
        self.assertNotIn(r"K^{2}", prompt)

    def test_corrupt_tokens_are_rejected(self):
        llm = Mock()
        llm.chat.return_value = {"text": "结果为⟦T2⟧。"}
        block = {"id": "1", "source": "The result is ⟦A⟧."}
        translate_blocks(llm, [block])
        self.assertEqual(block["translation"], "")

    def test_severe_truncation_is_retried_then_reported(self):
        llm = Mock()
        llm.chat.return_value = {"text": "实验结果。"}
        block = {"id": "1", "source": "The experiment produced several distinct assemblies. " * 7}
        translate_blocks(llm, [block])
        self.assertEqual(llm.chat.call_count, 3)
        self.assertEqual(block["translation"], "")
        self.assertEqual(block["translation_issue"], "suspected_truncation")

    def test_length_finish_reason_never_replaces_source_with_partial_result(self):
        llm = Mock()
        llm.chat.side_effect = [
            {"text": "模型返回的未完句", "finish_reason": "length"},
            {"text": "模型返回的完整译文。", "finish_reason": "stop"},
        ]
        block = {"id": "1", "source": "This paragraph is kept until a complete replacement."}
        translate_blocks(llm, [block])
        self.assertEqual(block["translation"], "模型返回的完整译文。")
        self.assertGreater(llm.chat.call_args_list[1].args[1]["maxTokens"],
                           llm.chat.call_args_list[0].args[1]["maxTokens"])

    def test_out_of_order_responses_keep_block_order_and_monotonic_progress(self):
        later_done = Event()
        progress = []
        blocks = [{"id": "first", "source": "First source."}, {"id": "second", "source": "Second source."},
                  {"id": "diagram", "source": "Diagram label.", "protected_layout": True}]

        def chat(messages, _):
            if "First source." in messages[1]["content"]:
                if not later_done.wait(3):
                    raise RuntimeError("second block did not finish")
                return {"text": "第一段。"}
            return {"text": "第二段。"}

        def on_block(done, total):
            progress.append((done, total))
            if blocks[1].get("translation"):
                later_done.set()

        llm = Mock()
        llm.chat.side_effect = chat
        self.assertIs(translate_blocks(llm, blocks, workers=2, on_block=on_block), blocks)
        self.assertEqual([block.get("translation") for block in blocks], ["第一段。", "第二段。", None])
        self.assertEqual(progress, [(1, 3), (2, 3), (3, 3)])
        self.assertEqual(llm.chat.call_count, 2)

    def test_explicit_tex_spacing_in_ocr_formula_is_displayed_as_space(self):
        html = build_html(r"范围为⟦(n = 1, \ 2, \ 3)⟧")
        self.assertNotIn("\\", html)
        self.assertIn("1, 2, 3", html)

    def test_corrupt_ocr_scripts_use_native_prose_and_keep_real_subscripts(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "source.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_htmlbox(
                    fitz.Rect(30, 30, 290, 82),
                    "<div>The observed structure retained formula Fe<sup>II</sup><sub>4</sub>.</div>",
                )
                doc.save(pdf)
            block = {
                "page": 1, "bbox": [30, 30, 290, 82],
                "source": "Th<sub>e</sub> o<sub>b</sub>se<sub>rv</sub>ed str<sub>uc</sub>ture "
                          "r<sup>e</sup>tained formula ⟦Fe^{II}_{4}⟧.",
            }
            repair_sources([block], pdf)
        self.assertEqual(block["source"], "The observed structure retained formula ⟦Fe<sup>II</sup><sub>4</sub>⟧.")
        llm = Mock()
        llm.chat.return_value = {"text": "测得结构保留了化学式⟦T1⟧。"}
        block["id"] = "1"
        translate_blocks(llm, [block])
        self.assertEqual(block["translation"], "测得结构保留了化学式⟦Fe<sup>II</sup><sub>4</sub>⟧。")

    def test_known_null_symbols_are_recovered_before_protecting_values(self):
        block = {"id": "1", "source": "The energy was (\x00" "15.80 kcal mol<sup>\x00</sup> <sup>1</sup>)."}
        llm = Mock()
        llm.chat.return_value = {"text": "能量为⟦T1⟧ kcal mol⟦T2⟧。"}
        translate_blocks(llm, [block])
        self.assertEqual(block["translation"], "能量为−15.80 kcal mol<sup>−1</sup>。")
        self.assertNotIn("\0", llm.chat.call_args.args[0][1]["content"])


class TranslationRenderTest(unittest.TestCase):
    def test_region_previews_and_applies_native_prose_missing_from_the_block_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / "source.pdf"
            first = "Methods"
            missed = "This native paragraph was skipped by the recognizer."
            neighbor = "The separate paragraph must remain visible."
            with fitz.open() as doc:
                page = doc.new_page(width=350, height=280)
                page.insert_text((48, 60), first, fontsize=10)
                page.insert_text((48, 82), missed, fontsize=10)
                page.insert_text((48, 200), neighbor, fontsize=10)
                heading_box = page.search_for(first)[0]
                missed_box = page.search_for(missed)[0]
                original_neighbor = page.search_for(neighbor)
                doc.save(source)

            services = SimpleNamespace(runtime=SimpleNamespace(data_dir=directory),
                                       db=SimpleNamespace(path_for_paper_file=lambda _: source), llm=Mock())
            translator = Translator(services)
            paper = {"id": "paper", "pdfFile": source.name}
            layout = {"pdf_info": [{"para_blocks": [{"type": "ref_text", "bbox": list(missed_box)}]}]}
            baseline = [{"id": "heading", "page": 1, "type": "title", "bbox": list(heading_box),
                         "source": first, "translation": "方法标题"}]
            translator._render(paper, source, sha256_file(source), layout, baseline, {
                "normalization": {}, "warning": "", "stageSeconds": {"parse": 0},
            }, time.time(), 0)
            output = translator.pdf_path(paper)
            previous = output.read_bytes()
            revision = translator.review(paper)["revision"]
            draft = translator.inspect_region(paper, revision, 1, [.11, .15, .96, .37])
            self.assertEqual(draft["source"], [first, missed])
            services.llm.chat.side_effect = lambda messages, _: {
                "text": "方法" if "【本块（请翻译）】\nMethods\n\n" in messages[1]["content"] else "这是一段恢复的漏译正文。"
            }
            preview = translator.preview_region(paper, draft["token"])
            self.assertEqual(preview["translations"], ["方法", "这是一段恢复的漏译正文。"])
            self.assertTrue(preview["canApply"])
            self.assertEqual(output.read_bytes(), previous)

            worker = Thread()

            def launch(*args, **kwargs):
                nonlocal worker
                worker = Thread(*args, **kwargs)
                return worker

            with patch("paperlib.domain.translate.Thread", side_effect=launch):
                self.assertTrue(translator.apply_region(paper, draft["token"])["started"])
            worker.join(12)
            self.assertFalse(worker.is_alive())
            self.assertEqual(translator.state(paper)["state"], "done")
            self.assertEqual(services.llm.chat.call_count, 2)
            with fitz.open(output) as doc:
                self.assertIn("这是一段恢复的漏译正文。", doc[0].get_text())
                self.assertEqual(doc[0].search_for(neighbor), original_neighbor)

            previous = output.read_bytes()
            oversized = translator.inspect_region(paper, translator.review(paper)["revision"], 1, [.11, .15, .96, .37])
            services.llm.chat.side_effect = lambda *_: {"text": "用于验证排版容量的完整译文。" * 180}
            preview = translator.preview_region(paper, oversized["token"])
            self.assertTrue(preview["canApply"])
            self.assertGreater(len(preview["translations"][0]), 1000)
            with patch("paperlib.domain.translate.Thread", side_effect=launch):
                self.assertTrue(translator.apply_region(paper, oversized["token"])["started"])
            worker.join(12)
            self.assertFalse(worker.is_alive())
            self.assertIn("无法安全放入", translator.state(paper)["error"])
            self.assertEqual(output.read_bytes(), previous)

    def test_reader_retries_only_the_selected_block_and_keeps_other_layout(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / "source.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((45, 90), "Alpha paragraph must remain visible after the first attempt.", fontsize=10)
                page.insert_text((45, 225), "Beta paragraph is already translated and must not move.", fontsize=10)
                page = doc.new_page()
                page.insert_text((45, 100), "Gamma paragraph is on a separate page.", fontsize=10)
                doc.save(source)

            services = SimpleNamespace(runtime=SimpleNamespace(data_dir=directory),
                                       db=SimpleNamespace(path_for_paper_file=lambda _: source), llm=Mock())
            paper = {"id": "paper", "pdfFile": source.name}
            translator = Translator(services)

            def requested_block(messages):
                return messages[1]["content"].split("【本块（请翻译）】\n")[1].split("\n\n")[0]

            def first_pass(messages, _):
                text = requested_block(messages)
                if text.startswith("Alpha"):
                    return {"text": ""}
                return {"text": "已译旁边的段落。" if text.startswith("Beta") else "已译第二页。"}

            services.llm.chat.side_effect = first_pass
            translator._run(paper)
            output = translator.pdf_path(paper)
            first = translator.review(paper)
            issue, = [frame for frame in first["pages"]["1"] if frame["reason"]]
            self.assertEqual(issue["reason"], "missing_translation")
            self.assertTrue(issue["retryable"])
            self.assertTrue(all(0 <= edge <= 1 for edge in issue["rect"]))
            with fitz.open(output) as doc:
                self.assertIn("Alpha paragraph", doc[0].get_text())
                other_block = doc[0].search_for("已译旁边的段落。")
                other_page = doc[1].get_pixmap().samples

            # A new app instance must be able to replay the saved blocks without any OCR.
            translator = Translator(services)
            services.llm.chat.reset_mock(side_effect=True)
            runners = []

            def launch(*args, **kwargs):
                runner = Thread(*args, **kwargs)
                runners.append(runner)
                return runner

            def retry(review, block_id, answer):
                services.llm.chat.return_value = {"text": answer}
                services.llm.chat.reset_mock()
                with patch("paperlib.domain.translate.Thread", side_effect=launch):
                    self.assertTrue(translator.retry(paper, block_id, review["revision"])["started"])
                runners[-1].join(12)
                self.assertFalse(runners[-1].is_alive())
                self.assertTrue(all(requested_block(call.args[0]).startswith("Alpha")
                                    for call in services.llm.chat.call_args_list))
                return translator.state(paper)

            self.assertEqual(retry(first, issue["id"], "现在已重译首段。")["state"], "done")
            services.llm.chat.assert_called_once()
            repaired = translator.review(paper)
            self.assertNotEqual(repaired["revision"], first["revision"])
            self.assertFalse(any(frame["reason"] for frame in repaired["pages"]["1"]))
            with fitz.open(output) as doc:
                self.assertIn("现在已重译首段。", doc[0].get_text())
                self.assertEqual(doc[0].search_for("已译旁边的段落。"), other_block)
                self.assertEqual(doc[1].get_pixmap().samples, other_page)
            with self.assertRaisesRegex(TranslateError, "译文已更新"):
                translator.retry(paper, issue["id"], first["revision"])

            before = output.read_bytes()
            self.assertEqual(retry(repaired, issue["id"], "")["state"], "failed")
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(translator.review(paper), repaired)

            with patch.object(Path, "replace", side_effect=PermissionError("PDF is open elsewhere")):
                failed = retry(repaired, issue["id"], "文件暂时被占用。")
            self.assertEqual(failed["state"], "failed")
            self.assertEqual(failed["outputRevision"], repaired["revision"])
            self.assertEqual(output.read_bytes(), before)

            self.assertEqual(retry(repaired, issue["id"], "手动修正首段译文。")["state"], "done")
            with fitz.open(output) as doc:
                self.assertIn("手动修正首段译文。", doc[0].get_text())
                self.assertEqual(doc[0].search_for("已译旁边的段落。"), other_block)
                self.assertEqual(doc[1].get_pixmap().samples, other_page)
            with source.open("ab") as changed:
                changed.write(b"\n% source changed\n")
            with self.assertRaisesRegex(TranslateError, "原文已更换"):
                translator.retry(paper, issue["id"], translator.review(paper)["revision"])

    def test_legacy_review_can_locate_issues_without_enabling_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            paper = {"id": "paper", "pdfFile": "source.pdf"}
            translator = Translator(SimpleNamespace(runtime=SimpleNamespace(data_dir=directory)))
            output = translator.pdf_path(paper)
            output.parent.mkdir(parents=True)
            with fitz.open() as doc:
                page = doc.new_page(width=200, height=300)
                page.set_rotation(90)
                doc.save(output)
            translator._write_state("paper", state="done", outputSource=paper["pdfFile"])
            translator.dir_for("paper").joinpath("report.json").write_text(json.dumps({"pages": [{
                "page": 1, "issues": [{"id": "a", "reason": "missing_translation", "bbox": [20, 30, 60, 90]}],
            }]}), encoding="utf-8")

            review = translator.review(paper)
            self.assertTrue(review["legacy"])
            self.assertEqual(review["pages"][1][0]["rect"], [0.7, 0.1, 0.9, 0.3])
            self.assertFalse(review["pages"][1][0]["retryable"])
            with self.assertRaisesRegex(TranslateError, "旧译文"):
                translator.retry(paper, "a", review["revision"])

    def test_production_jobs_share_eight_slots_and_cancel_without_more_calls(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for filename, marker, pages in (("first.pdf", "Alpha", 12), ("second.pdf", "Beta", 2)):
                with fitz.open() as doc:
                    for _ in range(pages):
                        page = doc.new_page()
                        page.insert_text((48, 80), f"{marker} paragraph with enough text for translation.", fontsize=10)
                    doc.save(directory / filename)

            release = Event()
            saturated = Event()
            second_called = Event()
            lock = Lock()
            active = peak = first_calls = second_calls = 0

            def chat(messages, _):
                nonlocal active, peak, first_calls, second_calls
                is_first = "Alpha paragraph" in messages[1]["content"]
                with lock:
                    active += 1
                    peak = max(peak, active)
                    if is_first:
                        first_calls += 1
                    else:
                        second_calls += 1
                        second_called.set()
                    if active == 8:
                        saturated.set()
                try:
                    if is_first:
                        release.wait(8)
                        return {"text": ""}
                    return {"text": "这是第二份的译文。"}
                finally:
                    with lock:
                        active -= 1

            def wait_for(predicate):
                deadline = time.monotonic() + 6
                while not predicate():
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(0.02)
                return True

            services = SimpleNamespace(
                runtime=SimpleNamespace(data_dir=directory),
                db=SimpleNamespace(path_for_paper_file=lambda name: directory / name),
                llm=SimpleNamespace(chat=chat),
            )
            translator = Translator(services)
            first = {"id": "first", "pdfFile": "first.pdf"}
            second = {"id": "second", "pdfFile": "second.pdf"}
            runners = []

            def start_job(*args, **kwargs):
                thread = Thread(*args, **kwargs)
                runners.append(thread)
                return thread

            with patch("paperlib.domain.translate.Thread", side_effect=start_job):
                try:
                    self.assertTrue(translator.start(first)["started"])
                    self.assertTrue(saturated.wait(5))
                    self.assertTrue(translator.start(second)["started"])
                    self.assertTrue(wait_for(lambda: translator.state(second)["stage"] == "translate"))
                    self.assertFalse(second_called.wait(0.2))
                    self.assertEqual(translator.cancel(first)["state"]["stage"], "cancelling")
                finally:
                    release.set()
                    for runner in runners:
                        runner.join(10)
            self.assertFalse(any(runner.is_alive() for runner in runners))
            self.assertEqual((peak, first_calls, second_calls), (8, 8, 2))
            self.assertEqual(translator.state(first)["state"], "cancelled")
            self.assertFalse(translator.state(first)["exists"])
            state = translator.state(second)
            self.assertEqual((state["state"], state["progress"], state["total"]), ("done", 2, 2))
            self.assertTrue(state["exists"])

    def test_matching_pdf_names_are_isolated_and_unique_legacy_still_loads(self):
        with tempfile.TemporaryDirectory() as folder:
            first = {"id": "first", "pdfFile": "left/same.pdf"}
            second = {"id": "second", "pdfFile": "right/same.pdf"}
            papers = [first]
            translator = Translator(SimpleNamespace(
                runtime=SimpleNamespace(data_dir=folder), db=SimpleNamespace(list_papers=lambda: papers),
            ))
            translator.article_dir.mkdir(parents=True)
            legacy = translator.article_dir / "[Translated]same.pdf"
            legacy.write_bytes(b"legacy")
            translator._write_state("first", state="done", translated=1)
            self.assertEqual(translator.existing_pdf(first), legacy)

            papers.append(second)
            self.assertIsNone(translator.existing_pdf(first))
            self.assertIsNone(translator.existing_pdf(second))
            for paper in papers:
                output = translator.pdf_path(paper)
                output.parent.mkdir()
                output.write_bytes(paper["id"].encode())
                translator._write_state(paper["id"], state="done", outputSource=paper["pdfFile"])
            self.assertEqual(translator.existing_pdf(first).read_bytes(), b"first")
            self.assertEqual(translator.existing_pdf(second).read_bytes(), b"second")
            self.assertFalse(translator.remove(first)["exists"])
            self.assertEqual(translator.existing_pdf(second).read_bytes(), b"second")
            self.assertEqual(legacy.read_bytes(), b"legacy")

    def test_application_publishes_a_complete_pdf(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            source = directory / "source.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((48, 80), "This original scientific paragraph has enough words for a local translation.",
                                 fontsize=10)
                doc.save(source)
            services = SimpleNamespace(
                runtime=SimpleNamespace(data_dir=directory),
                db=SimpleNamespace(path_for_paper_file=lambda _: source),
                llm=Mock(),
            )
            services.llm.chat.return_value = {"text": "这是用于验收的科学段落。"}
            paper = {"id": "paper", "pdfFile": source.name}
            translator = Translator(services)
            translator.dir_for(paper["id"])
            translator._run(paper)

            state = translator.state(paper)
            self.assertEqual(state["state"], "done")
            self.assertEqual(state["preservedOriginal"], 0)
            with fitz.open(translator.pdf_path(paper)) as doc:
                self.assertIn("这是用于验收的科学段落。", doc[0].get_text())
            self.assertFalse(list(translator.article_dir.glob(".translate-*")))

    def test_protected_heading_does_not_inherit_oversized_marker_metrics(self):
        heading = fitz.Rect(25, 80, 125, 93)
        extracted_line = fitz.Rect(26, 65, 124, 103)
        blocked = _protected_boxes({}, [], [(extracted_line, [])], [], [heading])
        self.assertEqual(blocked, [heading])

    def test_wider_reflowed_paragraph_stays_below_adjacent_figure_caption(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "translation.pdf"
            caption = fitz.Rect(185, 116, 347, 146)
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=450)
                page.draw_rect(fitz.Rect(185, 79, 345, 112))
                page.insert_text((190, 127), "Figure caption, kept in place.", fontsize=9)
                page.insert_text((42, 92), "Narrow original paragraph.", fontsize=9)
                page.insert_text((42, 170), "Wider original paragraph begins below the figure.", fontsize=9)
                doc.save(source)
                before = page.get_pixmap(clip=caption, dpi=144).samples
            layout = {"pdf_info": [{"page_idx": 0, "para_blocks": [{
                "type": "image",
                "bbox": [185, 79, 345, 112],
                "blocks": [
                    {"type": "image_body", "bbox": [185, 79, 345, 112]},
                    {"type": "image_caption", "bbox": list(caption)},
                ],
            }]}]}
            blocks = [
                {"id": "narrow", "page": 1, "bbox": [40, 80, 180, 154], "source": "Narrow original paragraph.",
                 "translation": "这是窄段。"},
                {"id": "wide", "page": 1, "bbox": [40, 154, 230, 207], "source": "Wider original paragraph.",
                 "translation": "这是原图注下方的正文，宽段仍须完整回填。"},
            ]
            font_dir, font_name = _font_dir()
            report = render_translated(pdf_path=source, layout=layout, translations=blocks,
                                       out_pdf=output, font_dir=font_dir, font_name=font_name)
            self.assertEqual(report[0]["replaced"], 2)
            with fitz.open(output) as doc:
                page = doc[0]
                self.assertEqual(page.get_pixmap(clip=caption, dpi=144).samples, before)
                self.assertGreaterEqual(page.search_for("这是原图注下方的正文")[0].y0, caption.y1)

    def test_spare_room_increases_spacing_at_original_size(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "translation.pdf"
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=450)
                for y in (99, 111, 123, 135, 147, 168, 180, 192, 204):
                    page.insert_text((42, y), "Original paragraph on a 12-point line grid.", fontsize=10)
                doc.save(source)
            blocks = [
                {"id": "1", "page": 1, "bbox": [40, 89, 242, 155], "source": "First.",
                 "translation": "第一段" + "这里验证有余量时行距可以适当放宽" * 4},
                {"id": "2", "page": 1, "bbox": [40, 155, 242, 216], "source": "Second.",
                 "translation": "第二段" + "段前距离应该均匀" * 5},
            ]
            font_dir, font_name = _font_dir()
            layout = {"pdf_info": [{"page_idx": 0, "para_blocks": [
                {"type": "abstract", "bbox": [40, 90, 242, 155]},
            ]}]}
            report = render_translated(pdf_path=source, layout=layout, translations=blocks,
                                       out_pdf=output, font_dir=font_dir, font_name=font_name)
            self.assertEqual(report[0]["replaced"], 2)
            with fitz.open(output) as doc:
                lines = [span for block in doc[0].get_text("dict")["blocks"] for line in block.get("lines", [])
                         for span in line["spans"]]
                self.assertTrue(all(abs(span["size"] - 10) < 0.05 for span in lines))
                positions = sorted({round(span["origin"][1], 2) for span in lines})
                self.assertGreater(positions[1] - positions[0], 12.6)
                second = next(span["origin"][1] for span in lines if span["text"].startswith("第二段"))
                prev = max(pos for pos in positions if pos < second - 0.05)
                self.assertGreater(second - prev, 16.5)
                self.assertLess(positions[-1], blocks[-1]["bbox"][3])

    def test_roomy_column_spreads_short_paragraphs_with_uniform_gaps(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "translation.pdf"
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=450)
                for y in range(64, 197, 12):
                    page.insert_text((42, y), "Original paragraph on a 12-point grid.", fontsize=10)
                doc.save(source)
            blocks = [
                {"id": name, "page": 1, "bbox": [40, y, 220, y + 48], "source": "Original.",
                 "translation": name + "这段译文比原文短，段落需要均匀铺开。" * 2}
                for name, y in zip(("第一段", "第二段", "第三段"), (53, 101, 149))
            ]
            font_dir, font_name = _font_dir()
            report = render_translated(pdf_path=source, layout=None, translations=blocks,
                                       out_pdf=output, font_dir=font_dir, font_name=font_name)
            self.assertEqual(report[0]["replaced"], 3)
            with fitz.open(output) as doc:
                spans = [s for item in doc[0].get_text("dict")["blocks"] for line in item.get("lines", [])
                         for s in line["spans"]]
                self.assertTrue(all(abs(s["size"] - 10) < 0.05 for s in spans))
                starts = [s["origin"][1] for s in spans if s["text"].startswith(("第一", "第二", "第三"))]
                self.assertEqual(len(starts), 3)
                self.assertGreater(starts[1] - starts[0], 39)
                self.assertAlmostEqual(starts[1] - starts[0], starts[2] - starts[1], places=1)
                self.assertLess(spans[-1]["bbox"][3], blocks[-1]["bbox"][3])

    def test_bundled_serif_keeps_weight_and_searchable_han_characters(self):
        from paperlib.adapters.pdf_translate import SERIF_FONT

        font_dir, font_name = _font_dir()
        if font_name != SERIF_FONT:
            self.skipTest("bundled translation font or fontTools unavailable")
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "translation.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((48, 80), "A section heading.", fontsize=11)
                page.insert_text((48, 115), "The original paragraph.", fontsize=10)
                doc.save(source)

            blocks = [
                {"id": "title", "page": 1, "type": "title", "bbox": [46, 64, 280, 91],
                 "source": "A section heading.", "translation": "研究标题"},
                {"id": "body", "page": 1, "type": "text", "bbox": [46, 101, 280, 130],
                 "source": "The original paragraph.", "translation": "译文段落"},
            ]
            report = render_translated(pdf_path=source, layout=None, translations=blocks,
                                       out_pdf=output, font_dir=font_dir, font_name=font_name)
            self.assertEqual(report[0]["replaced"], 2)
            with fitz.open(output) as doc:
                fonts = {span["text"]: span["font"] for block in doc[0].get_text("dict")["blocks"]
                         for line in block.get("lines", []) for span in line["spans"]}
            self.assertIn("SourceHanSerifCNVF-Bold", fonts["研究标题"])
            self.assertIn("SourceHanSerifCNVF-Regular", fonts["译文段落"])

    def test_light_text_remains_visible_on_a_dark_label(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "translation.pdf"
            box = fitz.Rect(44, 60, 153, 85)
            with fitz.open() as doc:
                page = doc.new_page()
                page.draw_rect(box, color=None, fill=(0.13, 0.13, 0.13))
                page.insert_text((48, 77), "Subject heading", fontsize=11, color=(1, 1, 1))
                doc.save(source)

            block = {"id": "label", "page": 1, "type": "text", "bbox": [47, 65, 141, 82],
                     "source": "Subject heading", "translation": "主题栏目"}
            font_dir, font_name = _font_dir()
            report = render_translated(pdf_path=source, layout=None, translations=[block],
                                       out_pdf=output, font_dir=font_dir, font_name=font_name)
            self.assertEqual(report[0]["replaced"], 1)
            with fitz.open(output) as doc:
                page = doc[0]
                span = page.get_text("dict")["blocks"][0]["lines"][0]["spans"][0]
                self.assertEqual((span["text"], span["color"]), ("主题栏目", 0xFFFFFF))
                self.assertTrue(any(draw["rect"] == box for draw in page.get_drawings()))

    def test_render_preserves_text_after_null_even_with_pretranslated_blocks(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "translation.pdf"
            with fitz.open() as doc:
                page = doc.new_page()
                page.insert_text((42, 78), "Original paragraph with interaction energy.", fontsize=10)
                doc.save(source)
            block = {"id": "1", "page": 1, "bbox": [40, 62, 310, 100], "source": "Original.",
                     "translation": "相互作用能(\x00" "15.80 kcal mol<sup>\x00</sup><sup>1</sup>)，余文必须完整。"}
            font_dir, font_name = _font_dir()
            report = render_translated(pdf_path=source, layout=None, translations=[block],
                                       out_pdf=output, font_dir=font_dir, font_name=font_name)
            self.assertEqual(report[0]["replaced"], 1)
            with fitz.open(output) as doc:
                text = doc[0].get_text()
                self.assertIn("−15.80", text)
                self.assertIn("余文必须完整", text)
                self.assertNotIn("Original paragraph", text)

    def test_same_column_compacts_and_overflow_keeps_original(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            output = Path(folder) / "translation.pdf"
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=450)
                page.draw_line((39, 50), (210, 50), color=(1, 0, 0))
                for x, y, text in [
                    (40, 60, "First paragraph line one."), (40, 72, "First paragraph line two."),
                    (40, 84, "First paragraph line three."), (40, 99, "Second paragraph line one."),
                    (40, 111, "Second paragraph line two."), (220, 60, "Other column."),
                    (40, 140, "Figure caption."), (40, 385, "Keep this original text."),
                    (40, 421, "Original page footer."),
                ]:
                    page.insert_text((x, y), text, fontsize=9)
                doc.save(source)

            blocks = [
                {"id": "1", "page": 1, "bbox": [39, 49, 205, 87], "source": "First paragraph.",
                 "translation": "这是第一段译文，测试中文回填的字号。"},
                {"id": "2", "page": 1, "bbox": [39, 89, 205, 114], "source": "Second paragraph.",
                 "translation": "这是第二段译文，测试段落之间的距离。"},
                {"id": "3", "page": 1, "bbox": [39, 374, 205, 389], "source": "Keep this original text.",
                 "translation": "非常长的译文" * 500},
            ]
            apply_text_budgets(source, blocks)
            self.assertGreater(blocks[0]["text_budget"], blocks[1]["text_budget"])
            font_dir, font_name = _font_dir()
            report = render_translated(pdf_path=source, layout=None, translations=blocks,
                                       out_pdf=output, font_dir=font_dir, font_name=font_name)

            self.assertEqual((report[0]["replaced"], report[0]["preserved_original"]), (2, 1))
            with fitz.open(output) as doc:
                page = doc[0]
                self.assertEqual(page.rect, fitz.Rect(0, 0, 400, 450))
                self.assertEqual(len(page.get_drawings()), 1)
                text = page.get_text()
                for phrase in ("Other column.", "Figure caption.", "Keep this original text.", "Original page footer."):
                    self.assertIn(phrase, text)
                self.assertNotIn("paragraph line", text)
                translated = [
                    span for block in page.get_text("dict")["blocks"] for line in block.get("lines", [])
                    for span in line["spans"] if span["text"].startswith("这是")
                ]
                self.assertEqual(len(translated), 2)
                self.assertTrue(all(abs(span["size"] - 9) < 0.05 for span in translated))
                gap = translated[1]["origin"][1] - translated[0]["origin"][1]
                self.assertGreater(gap, 11)
                self.assertLess(gap, 20)


if __name__ == "__main__":
    unittest.main()
