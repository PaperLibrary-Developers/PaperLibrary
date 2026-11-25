"""Regression coverage for physical text frames and PDF translation fitting."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pymupdf as fitz

from paperlib.adapters.pdf_translate import (
    _font_dir, apply_text_budgets, blocks_from_content_list_v2, blocks_from_layout,
    render_translated, translate_blocks,
)
from paperlib.domain.translate import Translator


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


class TranslationRenderTest(unittest.TestCase):
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
