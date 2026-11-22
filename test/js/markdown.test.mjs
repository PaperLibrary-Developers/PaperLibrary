import assert from 'node:assert/strict'
import test from 'node:test'
import { refChip, render, renderMathInText } from '../../public/js/markdown.mjs'

test('escapes raw HTML tags in paragraphs, headings, lists, blockquotes and tables', () => {
  const samples = [
    '<img src=x onerror=alert(1)>',
    '# Title <script>alert(1)</script>',
    '- item <svg onload=alert(1)>',
    '> quote <iframe src="javascript:alert(1)"></iframe>',
    '| Col |\n| --- |\n| <img src=x onerror=1> |'
  ]
  for (const input of samples) {
    const html = render(input)
    assert.doesNotMatch(html, /<(?:img|script|svg|iframe)\b/i, `raw tag leaked for: ${input}`)
    assert.match(html, /&lt;(?:img|script|svg|iframe)\b/i, `expected escaped tag for: ${input}`)
  }
})

test('escapes HTML inside LaTeX text commands while keeping formatting tags', () => {
  const html = render('见 \\textbf{<img src=x onerror=1>} 与 \\textit{Cornus officinalis}')
  assert.doesNotMatch(html, /<img\b/i)
  assert.match(html, /<strong>&lt;img src=x onerror=1&gt;<\/strong>/)
  assert.match(html, /<em>Cornus officinalis<\/em>/)
})

test('blocks javascript links and attribute breakout in markdown URLs', () => {
  const jsLink = render('[click](javascript:alert(1))')
  assert.doesNotMatch(jsLink, /href="javascript:/i)

  const breakout = render('[click](https://evil.test/"onclick="x)')
  assert.doesNotMatch(breakout, /\sonclick=/i)
  assert.match(breakout, /href="https:\/\/evil\.test\/&amp;quot;onclick=&amp;quot;x"/)
})

test('preserves query-string ampersands without double-escaping in markdown and bare URLs', () => {
  const mdLink = render('[说明](https://example.com/path?a=1&b=2)')
  assert.match(mdLink, /href="https:\/\/example\.com\/path\?a=1&amp;b=2"/)
  assert.doesNotMatch(mdLink, /&amp;amp;/)

  const bare = render('见 https://example.com/path?a=1&b=2> 结束')
  assert.match(bare, /href="https:\/\/example\.com\/path\?a=1&amp;b=2"/)
  assert.match(bare, />https:\/\/example\.com\/path\?a=1&amp;b=2<\/a>&gt;/)
  assert.doesNotMatch(bare, /&amp;amp;/)
})

test('preserves inline math with < and inline code with dollar signs', () => {
  const html = render('条件 $a < b$ 且示例 `$x$` 不当作公式')
  assert.match(html, /class="math-raw"/)
  assert.match(html, /\$a &lt; b\$/)
  assert.match(html, /<code>\$x\$<\/code>/)
})

test('wiki links look up unescaped titles while rendering escaped HTML', () => {
  const seen = []
  const html = render('参考 [[Host & Guest <2026>]]', {
    byTitle (title) {
      seen.push(title)
      return { id: 'p1' }
    }
  })
  assert.deepEqual(seen, ['Host & Guest <2026>'])
  assert.match(html, /href="#\/paper\/p1"/)
  assert.match(html, />Host &amp; Guest &lt;2026&gt;<\/a>/)
  assert.doesNotMatch(html, /<2026>/)
})

test('renderMathInText and refChip escape HTML safely', () => {
  const mathText = renderMathInText('<img src=x onerror=1>\n$a < b$')
  assert.doesNotMatch(mathText, /<img\b/i)
  assert.match(mathText, /&lt;img src=x onerror=1&gt;<br>/)

  const chip = refChip('1"><script>', 'tip"<b>')
  assert.doesNotMatch(chip, /<script>/i)
  assert.match(chip, /title="tip&quot;&lt;b&gt;"/)
})
