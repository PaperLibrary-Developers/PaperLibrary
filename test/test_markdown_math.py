# -*- coding: utf-8 -*-
"""markdown.mjs 公式渲染回归：深度分析字段（纯文本里的 LaTeX）与既有 Markdown 表面。

背景：深度分析页曾把 fields 直接当纯文本渲染，「关键公式」里的 $$…$$ 原样显示；
修复后新增 renderMathInText()：字段仍按纯文本处理（不解析 Markdown、HTML 一律转义），
只把公式交给 KaTeX。
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN = ROOT / "public" / "js" / "markdown.mjs"
NODE = shutil.which("node")

HARNESS = r"""
import fs from 'node:fs'
import { pathToFileURL } from 'node:url'

const [target, fixturesPath] = process.argv.slice(2)
const fx = JSON.parse(fs.readFileSync(fixturesPath, 'utf8'))
const calls = []
globalThis.window = globalThis
globalThis.katex = {
  renderToString (tex, opts) {
    calls.push({ tex: String(tex), display: !!(opts && opts.displayMode) })
    return '<span class="katex-stub"></span>'
  }
}
const md = await import(pathToFileURL(target).href)
const out = {
  mathText: md.renderMathInText(fx.mathText),
  escape: md.renderMathInText(fx.escape),
  multiline: md.renderMathInText(fx.multiline),
  plainMarkdown: md.renderMathInText(fx.plainMarkdown),
  mathLt: md.renderMathInText(fx.mathLt),
  parenMath: md.renderMathInText(fx.parenMath),
  bracketMath: md.renderMathInText(fx.bracketMath),
  escapedDollar: md.renderMathInText(fx.escapedDollar),
  noKatex: (() => {
    const saved = globalThis.katex
    delete globalThis.katex
    const html = md.renderMathInText(fx.noKatex)
    globalThis.katex = saved
    return html
  })(),
  inlineRender: md.render(fx.inlineRender),
  blockRender: md.render(fx.blockRender),
  ce: md.render(fx.ce)
}
process.stdout.write(JSON.stringify({ out, calls }))
"""

FIXTURES = {
    "mathText": "$$\\Delta G_{hp} = \\mathrm{const}\\cdot \\gamma\\, \\Delta A$$ — 说明；$E=mc^2$",
    "escape": "普通文本 <img src=x onerror=alert(1)>",
    "multiline": "第一行 $a+b$\n第二行",
    "plainMarkdown": "#标签 [1] **粗体** 与 [[某文献]]",
    "mathLt": "$a<b$ 与 $x>y$",
    "parenMath": "括号形式 \\(E=mc^2\\) 结束",
    "bracketMath": "\\[\\Delta G\\]",
    "escapedDollar": "价格 \\$100 与 $x$",
    "noKatex": "$x$",
    "inlineRender": "公式 $E=mc^2$ 与 $100 元",
    "blockRender": "$$\n\\Delta G\n$$",
    "ce": "$\\ce{Fe^{II}4L6}$",
}


@unittest.skipUnless(NODE, "需要 node 才能验证前端公式渲染")
class MarkdownMathTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="helix-md-")
        harness = Path(cls.tmp) / "harness.mjs"
        harness.write_text(HARNESS, encoding="utf-8")
        fixtures = Path(cls.tmp) / "fixtures.json"
        fixtures.write_text(json.dumps(FIXTURES, ensure_ascii=False), encoding="utf-8")
        cls.proc = subprocess.run(
            [NODE, str(harness), str(MARKDOWN), str(fixtures)],
            cwd=str(ROOT), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
        cls.data = None
        if cls.proc.returncode == 0 and (cls.proc.stdout or "").strip():
            cls.data = json.loads(cls.proc.stdout)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        if self.data is None:
            self.fail("node harness 失败（exit=%s）：\n%s" % (
                self.proc.returncode, (self.proc.stderr or self.proc.stdout or "")[-2000:]))

    def test_deep_field_math_renders_as_katex(self):
        html = self.data["out"]["mathText"]
        self.assertIn('class="math math-display"', html)
        self.assertIn('class="math"', html)
        self.assertNotIn("$$", html, "深度字段不应再输出原始 $$ 定界符")
        self.assertNotIn("$E=mc^2$", html)

    def test_plain_text_is_escaped_not_html(self):
        html = self.data["out"]["escape"]
        self.assertIn("&lt;img", html)
        self.assertNotIn("<img", html)

    def test_plain_markdown_syntax_is_not_interpreted(self):
        html = self.data["out"]["plainMarkdown"]
        self.assertIn("#标签", html)
        self.assertIn("[1]", html)
        self.assertIn("**粗体**", html)
        self.assertNotIn('class="ref"', html)
        self.assertNotIn("<strong>", html)

    def test_newlines_preserved_and_inline_math(self):
        html = self.data["out"]["multiline"]
        self.assertIn("<br>", html)
        self.assertIn('class="math"', html)
        self.assertNotIn("$a+b$", html)

    def test_math_with_angle_brackets_survives_escaping(self):
        html = self.data["out"]["mathLt"]
        self.assertIn('class="math"', html)
        texs = [call["tex"] for call in self.data["calls"]]
        self.assertIn("a<b", texs)
        self.assertIn("x>y", texs)

    def test_paren_and_bracket_delimiters(self):
        paren = self.data["out"]["parenMath"]
        self.assertIn('class="math"', paren)
        self.assertNotIn("\\(", paren)
        bracket = self.data["out"]["bracketMath"]
        self.assertIn('class="math math-display"', bracket)
        texs = [call["tex"] for call in self.data["calls"]]
        self.assertIn("E=mc^2", texs)
        self.assertIn("\\Delta G", texs)

    def test_escaped_dollar_is_not_math(self):
        html = self.data["out"]["escapedDollar"]
        self.assertIn("$100", html)
        self.assertIn('class="math"', html)
        texs = [call["tex"] for call in self.data["calls"]]
        self.assertIn("x", texs)
        self.assertNotIn("100", texs)

    def test_fallback_without_katex_keeps_source(self):
        html = self.data["out"]["noKatex"]
        self.assertIn("math-raw", html)
        self.assertIn("$x$", html)

    def test_existing_markdown_math_and_currency_guard(self):
        inline = self.data["out"]["inlineRender"]
        self.assertIn('class="math"', inline)
        self.assertIn("$100 元", inline, "价格不能被误当成公式")
        block = self.data["out"]["blockRender"]
        self.assertIn('class="math math-display"', block)

    def test_mhchem_source_reaches_katex(self):
        html = self.data["out"]["ce"]
        self.assertIn('class="math"', html)
        texs = [call["tex"] for call in self.data["calls"]]
        self.assertTrue(any(tex.startswith("\\ce{") for tex in texs), texs)


if __name__ == "__main__":
    unittest.main()
