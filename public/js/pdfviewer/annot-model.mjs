/* PDF 阅读器纯模型层：批注坐标/序列化、颜色/调色盘、矩形几何与错误文案（零 DOM 依赖，Node 可直接单测） */

export const MIN_SCALE = 0.5
export const MAX_SCALE = 3
export const SCALE_STEP = 0.15

export const DEP_HINT = '内置阅读器需要可选依赖 pdfjs-dist：请在项目目录执行 npm install'
export const GENERIC_FAIL = '内置阅读器打不开这个 PDF'

/* 取色条的内置颜色：前四个是历史内置色（黄/绿/蓝/粉），保留下来是为了让旧批注和
   blue/green/pink 这些别名仍然算「内置色」；后四个是参考 GoodNotes 补的柔和色，
   压成 38% 透明后彼此仍分得清。第一个仍是默认黄色。 */
export const ANNOT_BUILTIN_COLORS = [
  '#f2c433', '#3f9e77', '#4f8fd6', '#dd7a9d',
  '#f2a65a', '#5bb8c9', '#b18ede', '#c9c4b8'
]
export const ANNOT_LEGACY_COLORS = { yellow: '#f2c433', green: '#3f9e77', blue: '#4f8fd6', pink: '#dd7a9d' }
export const ANNOT_DEFAULT_COLOR = ANNOT_BUILTIN_COLORS[0]
export const ANNOT_PALETTE_KEY = 'lv:annot-palette'
/* 上次用的颜色：换页面/重开阅读器都延续，跟 GoodNotes 的「记住当前笔色」一致。
   荧光笔沿用旧键（老用户的颜色不会丢），便签用自己的键，两支笔各记各的。 */
export const ANNOT_COLOR_KEY = 'lv:annot-color'
export const ANNOT_NOTE_SUFFIX = ':note'
export const ANNOT_CUSTOM_MAX = 12
export const ANNOT_HEX_RE = /^#([0-9a-f]{6}|[0-9a-f]{3})$/i
export const ANNOT_HL_ALPHA = 0.38
export const ANNOT_MAX_RECTS = 120
export const ANNOT_MIN_DRAG = 4
export const ANNOT_QUOTE_MAX = 600
export const ANNOT_NOTE_MAX = 4000
export const ANNOT_FLASH_MS = 1400
/* 撤销栈上限：批注是纯前端状态，几百步足够，再往里存只会白占内存 */
export const ANNOT_HISTORY_MAX = 80
export const ANNOT_LAYER_CLASS = 'lv-annot-layer'

/* ------------------------------ 颜色与调色盘 ------------------------------ */

export function normalizeColor (value) {
  const raw = String(value || '').trim().toLowerCase()
  if (raw && ANNOT_LEGACY_COLORS[raw]) return ANNOT_LEGACY_COLORS[raw]
  if (ANNOT_HEX_RE.test(raw)) {
    return raw.length === 4
      ? '#' + raw[1] + raw[1] + raw[2] + raw[2] + raw[3] + raw[3]
      : raw
  }
  return ''
}

export function hexToRgba (hex, alpha) {
  const value = normalizeColor(hex) || ANNOT_DEFAULT_COLOR
  return 'rgba(' + parseInt(value.slice(1, 3), 16) + ', ' +
    parseInt(value.slice(3, 5), 16) + ', ' +
    parseInt(value.slice(5, 7), 16) + ', ' + alpha + ')'
}

export function inkOnColor (hex) {
  const value = normalizeColor(hex) || ANNOT_DEFAULT_COLOR
  const luma = (0.299 * parseInt(value.slice(1, 3), 16) +
    0.587 * parseInt(value.slice(3, 5), 16) +
    0.114 * parseInt(value.slice(5, 7), 16)) / 255
  return luma > 0.62 ? '#3a2d06' : '#ffffff'
}

function resolveStorage (storage) {
  if (storage !== undefined) return storage
  try {
    return typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage
  } catch (err) {
    return null
  }
}

/* 颜色按工具分开存：第一个参数可以只给工具名（'highlight' / 'note'），
   也可以直接给 storage（兼容老调用 readCustomColors(storage)）。 */
function scopeOf (kindOrStorage, storage) {
  if (kindOrStorage && typeof kindOrStorage.getItem === 'function') {
    return { kind: 'highlight', storage: kindOrStorage }
  }
  const kind = kindOrStorage === 'note' ? 'note' : 'highlight'
  return { kind: kind, storage: storage }
}

function scopedKey (base, kind) {
  return kind === 'note' ? base + ANNOT_NOTE_SUFFIX : base
}

export function readCustomColors (kindOrStorage, storage) {
  const scope = scopeOf(kindOrStorage, storage)
  const store = resolveStorage(scope.storage)
  if (!store) return []
  let raw = null
  try { raw = store.getItem(scopedKey(ANNOT_PALETTE_KEY, scope.kind)) } catch (err) { return [] }
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    const out = []
    for (let i = 0; i < parsed.length && out.length < ANNOT_CUSTOM_MAX; i++) {
      const hex = normalizeColor(parsed[i])
      if (hex && out.indexOf(hex) < 0) out.push(hex)
    }
    return out
  } catch (err) {
    return []
  }
}

export function writeCustomColors (list, kindOrStorage, storage) {
  const scope = scopeOf(kindOrStorage, storage)
  const store = resolveStorage(scope.storage)
  if (!store) return
  try { store.setItem(scopedKey(ANNOT_PALETTE_KEY, scope.kind), JSON.stringify(list || [])) } catch (err) {}
}

export function readAnnotColor (kindOrStorage, storage) {
  const scope = scopeOf(kindOrStorage, storage)
  const store = resolveStorage(scope.storage)
  if (!store) return ''
  let raw = null
  try { raw = store.getItem(scopedKey(ANNOT_COLOR_KEY, scope.kind)) } catch (err) { return '' }
  return normalizeColor(raw)
}

export function writeAnnotColor (hex, kindOrStorage, storage) {
  const scope = scopeOf(kindOrStorage, storage)
  const store = resolveStorage(scope.storage)
  const value = normalizeColor(hex)
  if (!store || !value) return
  try { store.setItem(scopedKey(ANNOT_COLOR_KEY, scope.kind), value) } catch (err) {}
}

export function annotPalette (kindOrStorage, storage) {
  const scope = scopeOf(kindOrStorage, storage)
  const out = ANNOT_BUILTIN_COLORS.slice()
  const custom = readCustomColors(scope.kind, scope.storage)
  for (let i = 0; i < custom.length; i++) {
    if (out.indexOf(custom[i]) < 0) out.push(custom[i])
  }
  return out
}

export function isBuiltinColor (hex) {
  return ANNOT_BUILTIN_COLORS.indexOf(normalizeColor(hex)) >= 0
}

/* ------------------------------ HSV（自定义取色盘用） ------------------------------ */

export function hexToHsv (hex) {
  const value = normalizeColor(hex)
  if (!value) return null
  const r = parseInt(value.slice(1, 3), 16) / 255
  const g = parseInt(value.slice(3, 5), 16) / 255
  const b = parseInt(value.slice(5, 7), 16) / 255
  const max = Math.max(r, g, b)
  const min = Math.min(r, g, b)
  const d = max - min
  let h = 0
  if (d) {
    if (max === r) h = ((g - b) / d) % 6
    else if (max === g) h = (b - r) / d + 2
    else h = (r - g) / d + 4
    h *= 60
    if (h < 0) h += 360
  }
  return { h: h, s: max ? d / max : 0, v: max }
}

export function hsvToHex (h, s, v) {
  const hue = ((Number(h) % 360) + 360) % 360 || 0
  const sat = Math.min(1, Math.max(0, Number(s) || 0))
  const val = Math.min(1, Math.max(0, Number(v) || 0))
  const c = val * sat
  const x = c * (1 - Math.abs((hue / 60) % 2 - 1))
  const m = val - c
  let rgb
  if (hue < 60) rgb = [c, x, 0]
  else if (hue < 120) rgb = [x, c, 0]
  else if (hue < 180) rgb = [0, c, x]
  else if (hue < 240) rgb = [0, x, c]
  else if (hue < 300) rgb = [x, 0, c]
  else rgb = [c, 0, x]
  let out = '#'
  for (let i = 0; i < 3; i++) {
    const n = Math.round((rgb[i] + m) * 255)
    out += (n < 16 ? '0' : '') + n.toString(16)
  }
  return out
}

/* ------------------------------ 撤销 / 重做 ------------------------------ */

export function createAnnotHistory (limit) {
  const max = Math.max(1, Math.round(Number(limit) || ANNOT_HISTORY_MAX))
  const undoStack = []
  const redoStack = []
  let coalesceKey = ''
  let coalesceAt = 0

  function snapshot (items) {
    // 深拷一份：批注项里有 rects / point 这类嵌套结构，共享引用会让撤销回到「同一份」。
    return (Array.isArray(items) ? items : []).map(item => ({
      id: item.id,
      type: item.type,
      page: item.page,
      color: item.color,
      rects: (Array.isArray(item.rects) ? item.rects : []).map(r => ({ x: r.x, y: r.y, w: r.w, h: r.h })),
      point: item.point ? { x: item.point.x, y: item.point.y } : null,
      quote: item.quote,
      note: item.note,
      createdAt: item.createdAt,
      updatedAt: item.updatedAt
    }))
  }

  /* 记一步。coalesce 给同一类连续操作（比如拖橡皮擦一路划过去）用一个 key，
     在 coalesceMs 内复用同一条历史，不会一次擦除塞进十几步。 */
  function push (items, options) {
    const o = options || {}
    const now = Date.now()
    const key = o.coalesce || ''
    if (key && key === coalesceKey && now - coalesceAt <= (o.coalesceMs || 900)) {
      coalesceAt = now
      return false
    }
    coalesceKey = key
    coalesceAt = now
    undoStack.push(snapshot(items))
    if (undoStack.length > max) undoStack.shift()
    redoStack.length = 0
    return true
  }

  function undo (current) {
    if (!undoStack.length) return null
    const prev = undoStack.pop()
    redoStack.push(snapshot(current))
    coalesceKey = ''
    return prev
  }

  function redo (current) {
    if (!redoStack.length) return null
    const next = redoStack.pop()
    undoStack.push(snapshot(current))
    coalesceKey = ''
    return next
  }

  function reset () {
    undoStack.length = 0
    redoStack.length = 0
    coalesceKey = ''
    coalesceAt = 0
  }

  function state () {
    return { canUndo: undoStack.length > 0, canRedo: redoStack.length > 0, depth: undoStack.length, redoDepth: redoStack.length }
  }

  return { push: push, undo: undo, redo: redo, reset: reset, state: state, snapshot: snapshot }
}

/* ------------------------------ 数值与几何 ------------------------------ */

export function clampScale (value) {
  if (!isFinite(value) || value <= 0) return 1
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value))
}

export function clampNumber (value, min, max, fallback) {
  const n = Number(value)
  if (!isFinite(n)) return fallback
  return Math.min(max, Math.max(min, n))
}

export function clamp01 (value) {
  const n = Number(value)
  if (!isFinite(n)) return 0
  return Math.min(1, Math.max(0, n))
}

export function pct (value) {
  return (Math.round(clamp01(value) * 10000) / 100) + '%'
}

export function normalizeRect (raw) {
  if (!raw) return null
  const x1 = Number(raw.left)
  const y1 = Number(raw.top)
  const x2 = x1 + Number(raw.width)
  const y2 = y1 + Number(raw.height)
  if (!isFinite(x1) || !isFinite(y1) || !isFinite(x2) || !isFinite(y2)) return null
  return {
    left: Math.min(x1, x2),
    top: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1)
  }
}

export function overlapArea (a, b) {
  const w = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left)
  const h = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top)
  return w > 0 && h > 0 ? w * h : 0
}

export function rectDistance (a, b) {
  const dx = Math.max(0, Math.max(a.left - (b.left + b.width), b.left - (a.left + a.width)))
  const dy = Math.max(0, Math.max(a.top - (b.top + b.height), b.top - (a.top + a.height)))
  return Math.sqrt(dx * dx + dy * dy)
}

export function pageAtPoint (infos, x, y) {
  for (let i = 0; i < infos.length; i++) {
    const r = infos[i].rect
    if (x >= r.left && x <= r.left + r.width && y >= r.top && y <= r.top + r.height) return infos[i]
  }
  return null
}

export function pageAtRectCenter (infos, rect) {
  const x = rect.left + rect.width / 2
  const y = rect.top + rect.height / 2
  return pageAtPoint(infos, x, y)
}

export function normBox (rect, pageRect) {
  if (!rect || !pageRect || !(pageRect.width > 0) || !(pageRect.height > 0)) return null
  const left = Math.max(rect.left, pageRect.left)
  const top = Math.max(rect.top, pageRect.top)
  const right = Math.min(rect.left + rect.width, pageRect.left + pageRect.width)
  const bottom = Math.min(rect.top + rect.height, pageRect.top + pageRect.height)
  if (right - left < 1 || bottom - top < 1) return null
  return {
    x: (left - pageRect.left) / pageRect.width,
    y: (top - pageRect.top) / pageRect.height,
    w: (right - left) / pageRect.width,
    h: (bottom - top) / pageRect.height
  }
}

export function normPoint (pt, pageRect) {
  if (!pt || !pageRect || !(pageRect.width > 0) || !(pageRect.height > 0)) return null
  return {
    x: clamp01((pt.x - pageRect.left) / pageRect.width),
    y: clamp01((pt.y - pageRect.top) / pageRect.height)
  }
}

/* ------------------------------ 批注数据规整 ------------------------------ */

export function annotId () {
  return 'n' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7)
}

export function nowStamp () {
  return new Date().toISOString()
}

export function sanitizeAnnot (raw) {
  if (!raw || typeof raw !== 'object') return null
  const type = raw.type === 'note' ? 'note' : (raw.type === 'highlight' ? 'highlight' : '')
  if (!type) return null
  const id = String(raw.id || '').slice(0, 64)
  if (!id) return null
  const rects = []
  const rawRects = Array.isArray(raw.rects) ? raw.rects : []
  for (let i = 0; i < rawRects.length; i++) {
    const r = rawRects[i]
    if (!r) continue
    const w = Number(r.w)
    const h = Number(r.h)
    if (!isFinite(w) || !isFinite(h) || w <= 0 || h <= 0) continue
    rects.push({ x: clamp01(r.x), y: clamp01(r.y), w: clamp01(w), h: clamp01(h) })
    if (rects.length >= ANNOT_MAX_RECTS) break
  }
  const pointRaw = raw.point && typeof raw.point === 'object' ? raw.point : null
  const point = pointRaw
    ? { x: clamp01(pointRaw.x), y: clamp01(pointRaw.y) }
    : (rects[0] ? { x: rects[0].x, y: rects[0].y } : null)
  if (type === 'highlight' && !rects.length) return null
  if (type === 'note' && !point) return null
  return {
    id: id,
    type: type,
    page: Math.max(1, Math.round(Number(raw.page) || 1)),
    color: normalizeColor(raw.color) || ANNOT_DEFAULT_COLOR,
    rects: rects,
    point: type === 'note' ? point : null,
    quote: String(raw.quote || '').slice(0, ANNOT_QUOTE_MAX),
    note: String(raw.note || '').slice(0, ANNOT_NOTE_MAX),
    createdAt: String(raw.createdAt || ''),
    updatedAt: String(raw.updatedAt || '')
  }
}

export function isSameHighlight (a, b) {
  if (!a || !b || Number(a.page) !== Number(b.page)) return false
  const ra = Array.isArray(a.rects) ? a.rects : []
  const rb = Array.isArray(b.rects) ? b.rects : []
  if (ra.length !== rb.length) return false
  for (let i = 0; i < ra.length; i++) {
    if (Math.abs(ra[i].x - rb[i].x) > 0.004 || Math.abs(ra[i].y - rb[i].y) > 0.004 ||
      Math.abs(ra[i].w - rb[i].w) > 0.004 || Math.abs(ra[i].h - rb[i].h) > 0.004) return false
  }
  return true
}

/* 橡皮擦命中：一个点是否落在某条批注上。
   荧光笔一条记录 = 一次划过的笔迹（可能多行矩形），只要碰到任一矩形就算命中，
   于是「按笔画擦除」= 删掉整条记录，而不是把矩形切碎。 */
export function hitAnnotation (item, page, x, y, pad) {
  if (!item || Number(item.page) !== Number(page)) return false
  const grow = Number.isFinite(pad) ? Math.max(0, pad) : 0
  if (item.type === 'note') {
    const pt = item.point
    if (!pt) return false
    // 便签是 20px 的方块，按页内比例折算一个容差
    return Math.abs(pt.x - x) <= 0.02 + grow && Math.abs(pt.y - y) <= 0.03 + grow
  }
  const rects = Array.isArray(item.rects) ? item.rects : []
  for (let i = 0; i < rects.length; i++) {
    const r = rects[i]
    if (!r) continue
    if (x >= r.x - grow && x <= r.x + r.w + grow && y >= r.y - grow && y <= r.y + r.h + grow) return true
  }
  return false
}

export function annotationAnchorY (item) {
  if (!item) return 0
  if (item.type === 'note') return clamp01(item.point ? item.point.y : 0)
  const rects = Array.isArray(item.rects) ? item.rects : []
  let y = 1
  for (let i = 0; i < rects.length; i++) {
    const value = Number(rects[i] && rects[i].y)
    if (isFinite(value) && value < y) y = value
  }
  return y === 1 ? 0 : clamp01(y)
}

/* ------------------------------ 错误文案与文件名 ------------------------------ */

export function errText (err) {
  if (!err) return '未知错误'
  if (typeof err === 'string') return err
  return String(err.message || err.name || err)
}

export function describeError (err) {
  const name = (err && err.name) || ''
  const msg = errText(err)
  if (name === 'MissingPDFException') return '找不到这个 PDF 文件（可能已被移动或删除）'
  if (name === 'InvalidPDFException') return '这个文件不是有效的 PDF，或者已经损坏'
  if (name === 'PasswordException') return '这个 PDF 有密码保护，内置阅读器暂时打不开'
  if (name === 'UnexpectedResponseException') return '取 PDF 时服务端返回了异常（' + msg + '）'
  if (/fake worker|worker|dynamically imported|Failed to fetch|NetworkError|404/i.test(msg)) {
    return DEP_HINT + '（' + msg + '）'
  }
  return '打不开这个 PDF：' + msg
}

export function downloadName (title) {
  const base = String(title || 'document').replace(/\s+/g, ' ').trim().replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_')
  const safe = base.slice(0, 80).replace(/[. ]+$/, '') || 'document'
  return safe + (/\.[Pp][Dd][Ff]$/.test(safe) ? '' : '.pdf')
}
