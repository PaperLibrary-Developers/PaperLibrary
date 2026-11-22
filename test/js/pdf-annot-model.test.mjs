import assert from 'node:assert/strict'
import test from 'node:test'
import pdfviewerDefault, {
  getPdfjsBase,
  mount,
  readInvertPref,
  setPdfjsBase,
  writeInvertPref
} from '../../public/js/pdfviewer.mjs'
import {
  ANNOT_BUILTIN_COLORS,
  ANNOT_CUSTOM_MAX,
  ANNOT_DEFAULT_COLOR,
  ANNOT_MAX_RECTS,
  ANNOT_NOTE_MAX,
  ANNOT_PALETTE_KEY,
  ANNOT_QUOTE_MAX,
  DEP_HINT,
  MAX_SCALE,
  MIN_SCALE,
  annotationAnchorY,
  annotPalette,
  clamp01,
  clampNumber,
  clampScale,
  describeError,
  downloadName,
  errText,
  hexToRgba,
  inkOnColor,
  isBuiltinColor,
  isSameHighlight,
  normalizeColor,
  normalizeRect,
  normBox,
  normPoint,
  overlapArea,
  pageAtPoint,
  pageAtRectCenter,
  pct,
  readCustomColors,
  rectDistance,
  sanitizeAnnot,
  writeCustomColors
} from '../../public/js/pdfviewer/annot-model.mjs'

function memoryStorage () {
  const map = new Map()
  return {
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)) },
    removeItem: k => { map.delete(k) },
    map
  }
}

test('color normalization, rgba conversion, contrast ink, and custom palette storage', () => {
  assert.equal(normalizeColor('yellow'), '#f2c433')
  assert.equal(normalizeColor(' GREEN '), '#3f9e77')
  assert.equal(normalizeColor('#AbC'), '#aabbcc')
  assert.equal(normalizeColor('#12AB34'), '#12ab34')
  assert.equal(normalizeColor('red'), '')
  assert.equal(normalizeColor('#12345'), '')
  assert.equal(normalizeColor(null), '')

  assert.equal(hexToRgba('#0f80ff', 0.5), 'rgba(15, 128, 255, 0.5)')
  assert.equal(hexToRgba('invalid', 0.38), hexToRgba(ANNOT_DEFAULT_COLOR, 0.38))

  assert.equal(inkOnColor('#f2c433'), '#3a2d06')
  assert.equal(inkOnColor('#1a2b4c'), '#ffffff')

  assert.equal(isBuiltinColor('blue'), true)
  assert.equal(isBuiltinColor('#F2C433'), true)
  assert.equal(isBuiltinColor('#123456'), false)

  const storage = memoryStorage()
  assert.deepEqual(readCustomColors(storage), [])

  // Corrupt JSON or non-array falls back safely
  storage.setItem(ANNOT_PALETTE_KEY, '{bad json')
  assert.deepEqual(readCustomColors(storage), [])
  storage.setItem(ANNOT_PALETTE_KEY, JSON.stringify({ not: 'array' }))
  assert.deepEqual(readCustomColors(storage), [])

  // Deduplicates, normalizes, and caps at ANNOT_CUSTOM_MAX
  const rawList = ['#abc', '#AABBCC', 'invalid', ANNOT_BUILTIN_COLORS[0]]
  for (let i = 0; i < ANNOT_CUSTOM_MAX + 5; i++) {
    rawList.push('#' + i.toString(16).padStart(6, '0'))
  }
  writeCustomColors(rawList, storage)
  const stored = readCustomColors(storage)
  assert.equal(stored.length, ANNOT_CUSTOM_MAX)
  assert.equal(stored[0], '#aabbcc')

  const palette = annotPalette(storage)
  assert.deepEqual(palette.slice(0, ANNOT_BUILTIN_COLORS.length), ANNOT_BUILTIN_COLORS)
  // Builtin color is not duplicated in palette even if present in custom list
  assert.equal(palette.filter(c => c === ANNOT_BUILTIN_COLORS[0]).length, 1)
})

test('numeric clamping and rectangle/coordinate geometry helpers', () => {
  assert.equal(clampScale(NaN), 1)
  assert.equal(clampScale(-2), 1)
  assert.equal(clampScale(0.1), MIN_SCALE)
  assert.equal(clampScale(9), MAX_SCALE)
  assert.equal(clampScale(1.25), 1.25)

  assert.equal(clampNumber('bad', 0, 10, 5), 5)
  assert.equal(clampNumber(15, 0, 10, 5), 10)
  assert.equal(clamp01(-0.5), 0)
  assert.equal(clamp01(1.5), 1)
  assert.equal(clamp01('x'), 0)
  assert.equal(pct(0.12345), '12.35%')

  assert.deepEqual(normalizeRect({ left: 100, top: 80, width: -40, height: -20 }), {
    left: 60, top: 60, width: 40, height: 20
  })
  assert.equal(normalizeRect({ left: 0, top: 0, width: NaN, height: 10 }), null)
  assert.equal(normalizeRect(null), null)

  const a = { left: 0, top: 0, width: 10, height: 10 }
  const b = { left: 5, top: 5, width: 10, height: 10 }
  const c = { left: 13, top: 14, width: 5, height: 5 }
  assert.equal(overlapArea(a, b), 25)
  assert.equal(overlapArea(a, c), 0)
  assert.equal(rectDistance(a, b), 0)
  assert.equal(rectDistance(a, c), 5)

  const pages = [
    { pageNumber: 1, rect: { left: 10, top: 0, width: 200, height: 300 } },
    { pageNumber: 2, rect: { left: 10, top: 320, width: 200, height: 300 } }
  ]
  assert.equal(pageAtPoint(pages, 50, 150)?.pageNumber, 1)
  assert.equal(pageAtPoint(pages, 50, 310), null)
  assert.equal(pageAtRectCenter(pages, { left: 20, top: 350, width: 40, height: 40 })?.pageNumber, 2)

  // normBox clips to page bounds and rejects outside/sub-pixel slivers
  const pageRect = { left: 100, top: 200, width: 400, height: 800 }
  assert.deepEqual(normBox({ left: 80, top: 200, width: 120, height: 200 }, pageRect), {
    x: 0, y: 0, w: 0.25, h: 0.25
  })
  assert.equal(normBox({ left: 0, top: 0, width: 50, height: 50 }, pageRect), null)
  assert.equal(normBox({ left: 100, top: 200, width: 0.5, height: 10 }, pageRect), null)

  assert.deepEqual(normPoint({ x: 300, y: 1200 }, pageRect), { x: 0.5, y: 1 })
  assert.equal(normPoint({ x: 10, y: 10 }, { left: 0, top: 0, width: 0, height: 100 }), null)
})

test('sanitizeAnnot validates, normalizes, clamps, and truncates highlights and notes', () => {
  assert.equal(sanitizeAnnot(null), null)
  assert.equal(sanitizeAnnot({ id: 'a1', type: 'unknown' }), null)
  assert.equal(sanitizeAnnot({ id: '', type: 'highlight', rects: [{ x: 0.1, y: 0.1, w: 0.2, h: 0.05 }] }), null)
  assert.equal(sanitizeAnnot({ id: 'h1', type: 'highlight', rects: [{ x: 0.1, y: 0.1, w: 0, h: 0.1 }] }), null)
  assert.equal(sanitizeAnnot({ id: 'n1', type: 'note' }), null)

  const hl = sanitizeAnnot({
    id: 'h1',
    type: 'highlight',
    page: 0,
    color: 'pink',
    rects: [
      { x: -0.2, y: 0.4, w: 0.3, h: 0.05 },
      { x: 0.1, y: 0.2, w: -1, h: 0.1 },
      { x: 0.1, y: 0.2, w: 0.5, h: 0.04 }
    ],
    quote: 'q'.repeat(ANNOT_QUOTE_MAX + 25),
    note: 'n'.repeat(ANNOT_NOTE_MAX + 25),
    createdAt: '2026-01-01T00:00:00Z'
  })
  assert.equal(hl.page, 1)
  assert.equal(hl.color, '#dd7a9d')
  assert.equal(hl.point, null)
  assert.deepEqual(hl.rects, [
    { x: 0, y: 0.4, w: 0.3, h: 0.05 },
    { x: 0.1, y: 0.2, w: 0.5, h: 0.04 }
  ])
  assert.equal(hl.quote.length, ANNOT_QUOTE_MAX)
  assert.equal(hl.note.length, ANNOT_NOTE_MAX)
  assert.equal(annotationAnchorY(hl), 0.2)

  // Caps rects length at ANNOT_MAX_RECTS
  const manyRects = Array.from({ length: ANNOT_MAX_RECTS + 20 }, (_, i) => ({
    x: 0.1, y: (i % 10) * 0.05, w: 0.2, h: 0.02
  }))
  assert.equal(sanitizeAnnot({ id: 'h2', type: 'highlight', rects: manyRects }).rects.length, ANNOT_MAX_RECTS)

  // Note derives point from first rect when point is omitted
  const noteFromRect = sanitizeAnnot({
    id: 'n1',
    type: 'note',
    page: 3,
    color: 'unknown',
    rects: [{ x: 0.25, y: 0.75, w: 0.1, h: 0.05 }]
  })
  assert.equal(noteFromRect.color, ANNOT_DEFAULT_COLOR)
  assert.deepEqual(noteFromRect.point, { x: 0.25, y: 0.75 })
  assert.equal(annotationAnchorY(noteFromRect), 0.75)
})

test('isSameHighlight checks page, rect count, and 0.004 coordinate tolerance', () => {
  const base = { page: 2, rects: [{ x: 0.1, y: 0.2, w: 0.3, h: 0.04 }] }
  const near = { page: 2, rects: [{ x: 0.103, y: 0.197, w: 0.302, h: 0.041 }] }
  const far = { page: 2, rects: [{ x: 0.11, y: 0.2, w: 0.3, h: 0.04 }] }
  const otherPage = { page: 3, rects: [{ x: 0.1, y: 0.2, w: 0.3, h: 0.04 }] }
  const extraRect = { page: 2, rects: [{ x: 0.1, y: 0.2, w: 0.3, h: 0.04 }, { x: 0.1, y: 0.3, w: 0.2, h: 0.04 }] }

  assert.equal(isSameHighlight(base, near), true)
  assert.equal(isSameHighlight(base, far), false)
  assert.equal(isSameHighlight(base, otherPage), false)
  assert.equal(isSameHighlight(base, extraRect), false)
  assert.equal(isSameHighlight(base, null), false)
})

test('downloadName sanitizes filenames and describeError formats pdf.js exceptions', () => {
  assert.equal(downloadName('Catalysis: A/B*C? "2026"'), 'Catalysis_ A_B_C_ _2026_.pdf')
  assert.equal(downloadName('Report.PDF'), 'Report.PDF')
  assert.equal(downloadName('   ...   '), 'document.pdf')
  assert.equal(downloadName('a'.repeat(120)).length, 84) // 80 chars + ".pdf"

  assert.equal(errText(null), '未知错误')
  assert.equal(errText('boom'), 'boom')
  assert.equal(describeError({ name: 'MissingPDFException' }), '找不到这个 PDF 文件（可能已被移动或删除）')
  assert.equal(describeError({ name: 'InvalidPDFException' }), '这个文件不是有效的 PDF，或者已经损坏')
  assert.equal(describeError({ name: 'PasswordException' }), '这个 PDF 有密码保护，内置阅读器暂时打不开')
  assert.match(describeError({ name: 'UnexpectedResponseException', message: '502' }), /502/)
  assert.match(describeError(new Error('Failed to fetch worker 404')), new RegExp(DEP_HINT.slice(0, 12)))
  assert.equal(describeError(new Error('unexpected failure')), '打不开这个 PDF：unexpected failure')
})

test('pdfviewer facade exports stay backwards-compatible and guard invalid mount calls', async () => {
  assert.equal(typeof mount, 'function')
  assert.equal(typeof setPdfjsBase, 'function')
  assert.equal(typeof getPdfjsBase, 'function')
  assert.equal(typeof readInvertPref, 'function')
  assert.equal(typeof writeInvertPref, 'function')
  assert.equal(pdfviewerDefault.mount, mount)
  assert.equal(pdfviewerDefault.readInvertPref, readInvertPref)
  assert.equal(pdfviewerDefault.writeInvertPref, writeInvertPref)

  const noContainer = await mount(null, { url: '/api/papers/p1/pdf' })
  assert.equal(noContainer.ok, false)

  const fakeEl = { nodeType: 1 }
  const noUrl = await mount(fakeEl, { url: '   ' })
  assert.equal(noUrl.ok, false)

  const ac = new AbortController()
  ac.abort()
  const aborted = await mount(fakeEl, { url: '/api/papers/p1/pdf', signal: ac.signal })
  assert.equal(aborted.ok, false)
})
