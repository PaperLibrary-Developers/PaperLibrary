"""零依赖 Word 97–2003（.doc）纯文本抽取单元测试。"""
from __future__ import annotations

import shutil
import struct
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from paperlib.adapters.attachment_text import extract_attachment_text
from paperlib.adapters.doc_text import _clean_word_text, _strip_fields, extract_doc_text

WORD97_FIXTURE = Path(__file__).with_name("fixtures") / "si-word97.doc"


class DocTextTests(unittest.TestCase):
    def test_word97_fixture_extracts_chinese_and_table(self):
        text = extract_doc_text(WORD97_FIXTURE.read_bytes())
        self.assertIn("Supplementary methods", text)
        self.assertIn("补充材料：样品编号 S17。\nSample\tValue\nS17\t7.25", text)

    def test_word_fields_and_control_chars_cleaned(self):
        self.assertEqual(
            _strip_fields('See \x13 HYPERLINK "https://example.org" \x14Fig. 1\x15 now'),
            "See Fig. 1 now",
        )
        self.assertEqual(
            _strip_fields("A\x13 PAGE \x15B"),
            "AB",
        )
        cleaned = _clean_word_text("Line 1\rColA\x07ColB\x07\x07V1\x07V2\x07\x07\x01\x02")
        self.assertEqual(cleaned, "Line 1\nColA\tColB\nV1\tV2")

    def test_rejects_invalid_or_encrypted_doc(self):
        with self.assertRaises(ValueError):
            extract_doc_text(b"not an ole file")
        with self.assertRaises(ValueError):
            extract_doc_text(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 504)

        # 修改真实 fixture 的 fEncrypted 标志位（WordDocument 流偏移 0x0A 的 bit 0x0100）
        raw = bytearray(WORD97_FIXTURE.read_bytes())
        # WordDocument 扇区位于固定签名 0xA5EC 处
        sig_pos = raw.find(struct.pack("<H", 0xA5EC))
        self.assertGreater(sig_pos, 511)
        flags = struct.unpack_from("<H", raw, sig_pos + 0x0A)[0]
        struct.pack_into("<H", raw, sig_pos + 0x0A, flags | 0x0100)
        with self.assertRaisesRegex(ValueError, "加密"):
            extract_doc_text(bytes(raw))

    def test_attachment_extractor_supports_doc_and_disguised_docx(self):
        tmp = Path(tempfile.mkdtemp(prefix="helix-doc-test-"))
        try:
            real_doc = tmp / "sample.doc"
            real_doc.write_bytes(WORD97_FIXTURE.read_bytes())
            res = extract_attachment_text(real_doc, ".doc", lambda _: {"text": ""})
            self.assertTrue(res["ok"], res)
            self.assertEqual(res["kind"], "doc")
            self.assertIn("补充材料：样品编号 S17。", res["text"])

            # 扩展名为 .doc 但内容是 .docx（常见于用户手动改后缀）
            fake_doc = tmp / "renamed.doc"
            with ZipFile(fake_doc, "w") as zf:
                zf.writestr(
                    "word/document.xml",
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                    "<w:body><w:p><w:r><w:t>伪装成doc的docx内容</w:t></w:r></w:p></w:body></w:document>",
                )
            res2 = extract_attachment_text(fake_doc, ".doc", lambda _: {"text": ""})
            self.assertTrue(res2["ok"], res2)
            self.assertEqual(res2["text"], "伪装成doc的docx内容")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
