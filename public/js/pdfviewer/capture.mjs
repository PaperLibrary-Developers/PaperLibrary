/* PDF 阅读器子模块：高清离屏截图导出（整页 / 区域）与框选提问取景层 */
import { clampNumber, normalizeRect, overlapArea, rectDistance } from './annot-model.mjs'
import { clientRectOf, eventPoint } from './dom.mjs'

const CAPTURE_MAX_SCALE = 4
const CAPTURE_MAX_PIXELS = 16 * 1024 * 1024
const CAPTURE_MAX_SIDE = 8192
const DEFAULT_CAPTURE_FORMAT = 'image/jpeg'
const DEFAULT_CAPTURE_QUALITY = 0.85
const DEFAULT_REGION_FORMAT = 'image/png'
const REGION_MIN_SIDE = 8

export function createCaptureController (ctx) {
  const { state, ui, opts } = ctx

  const REGION_HAS_POINTER = typeof window !== 'undefined' && typeof window.PointerEvent === 'function'
  const REGION_DOWN = REGION_HAS_POINTER ? 'pointerdown' : 'mousedown'
  const REGION_MOVE = REGION_HAS_POINTER ? 'pointermove' : 'mousemove'
  const REGION_UP = REGION_HAS_POINTER ? 'pointerup' : 'mouseup'

  function bindTemp (target, type, fn, options) {
    if (!target || typeof target.addEventListener !== 'function') return
    target.addEventListener(type, fn, options || false)
    const item = { target: target, type: type, fn: fn, capture: !!(options && options.capture === true) }
    state.domListeners.push(item)
    state.regionListeners.push(item)
  }

  function unbindTemp () {
    for (let i = 0; i < state.regionListeners.length; i++) {
      const item = state.regionListeners[i]
      try { item.target.removeEventListener(item.type, item.fn, item.capture === true) } catch (err) {}
      const at = state.domListeners.indexOf(item)
      if (at >= 0) state.domListeners.splice(at, 1)
    }
    state.regionListeners.length = 0
  }

  async function renderPageToCanvas (pdfPage, scale, rotation) {
    if (!pdfPage || typeof pdfPage.getViewport !== 'function' || typeof pdfPage.render !== 'function') return null
    const params = { scale: scale }
    if (isFinite(rotation)) params.rotation = rotation
    const viewport = pdfPage.getViewport(params)
    const vw = Math.max(1, viewport.width)
    const vh = Math.max(1, viewport.height)
    const shrink = Math.min(1,
      Math.sqrt(CAPTURE_MAX_PIXELS / Math.max(1, vw * vh)),
      CAPTURE_MAX_SIDE / Math.max(vw, vh))
    const width = Math.max(1, Math.floor(vw * shrink))
    const height = Math.max(1, Math.floor(vh * shrink))
    const canvas = document.createElement('canvas')
    canvas.width = width
    canvas.height = height
    const c2d = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null
    if (!c2d) return null
    c2d.fillStyle = '#ffffff'
    c2d.fillRect(0, 0, width, height)
    const k = width / vw
    const task = pdfPage.render({
      canvas: canvas,
      canvasContext: c2d,
      viewport: viewport,
      transform: [k, 0, 0, k, 0, 0]
    })
    await task.promise
    return { canvas: canvas, viewport: viewport }
  }

  function canvasDataUrl (canvas, format, quality) {
    try {
      const type = format === 'image/png' || format === 'image/webp' ? format : DEFAULT_CAPTURE_FORMAT
      const url = type === 'image/png' ? canvas.toDataURL(type) : canvas.toDataURL(type, quality)
      if (typeof url !== 'string' || url.slice(0, 10) !== 'data:image') return null
      return url
    } catch (err) {
      return null
    }
  }

  function captureOptions (options, defaultFormat, defaultScale) {
    const o = options || {}
    return {
      scale: clampNumber(o.scale, 0.1, CAPTURE_MAX_SCALE, defaultScale),
      format: o.format === 'image/png' || o.format === 'image/webp'
        ? o.format
        : (defaultFormat || DEFAULT_CAPTURE_FORMAT),
      quality: clampNumber(o.quality, 0.1, 1, DEFAULT_CAPTURE_QUALITY)
    }
  }

  async function getPdfPage (pageNumber) {
    const viewer = state.pdfViewer
    if (viewer && typeof viewer.getPageView === 'function') {
      const pageView = viewer.getPageView(pageNumber - 1)
      if (pageView && pageView.pdfPage) return pageView.pdfPage
    }
    const doc = state.pdfDocument
    if (doc && typeof doc.getPage === 'function') return await doc.getPage(pageNumber)
    return null
  }

  function findPageTarget (sel) {
    const candidates = []
    const viewer = state.pdfViewer
    if (viewer && typeof viewer.getPageView === 'function') {
      const total = viewer.pagesCount || 0
      for (let i = 0; i < total; i++) {
        const pageView = viewer.getPageView(i)
        const div = pageView && pageView.div
        if (!div || typeof div.getBoundingClientRect !== 'function') continue
        candidates.push({ pageNumber: i + 1, pageView: pageView, div: div })
      }
    }
    if (!candidates.length && ui.viewer && typeof ui.viewer.querySelectorAll === 'function') {
      const nodes = ui.viewer.querySelectorAll('.page[data-page-number]') || []
      for (let i = 0; i < nodes.length; i++) {
        const node = nodes[i]
        if (!node || typeof node.getAttribute !== 'function') continue
        const num = Math.round(Number(node.getAttribute('data-page-number')))
        if (!isFinite(num) || num < 1) continue
        candidates.push({ pageNumber: num, pageView: null, div: node })
      }
    }
    let best = null
    let bestArea = 0
    let nearest = null
    let nearestDist = Infinity
    for (let i = 0; i < candidates.length; i++) {
      const rect = clientRectOf(candidates[i].div)
      if (!rect || rect.width <= 0 || rect.height <= 0) continue
      const area = overlapArea(sel, rect)
      if (area > bestArea) {
        bestArea = area
        best = { pageNumber: candidates[i].pageNumber, pageView: candidates[i].pageView, rect: rect }
      }
      const dist = rectDistance(sel, rect)
      if (dist < nearestDist) {
        nearestDist = dist
        nearest = { pageNumber: candidates[i].pageNumber, pageView: candidates[i].pageView, rect: rect }
      }
    }
    return best || nearest
  }

  function cropCanvasRegion (canvas, sx, sy, sw, sh, format, quality) {
    const cw = canvas.width
    const ch = canvas.height
    if (!(cw > 0) || !(ch > 0)) return null
    const x = Math.min(Math.max(0, Math.floor(sx)), cw - 1)
    const y = Math.min(Math.max(0, Math.floor(sy)), ch - 1)
    const w = Math.max(1, Math.min(cw - x, Math.round(sw)))
    const h = Math.max(1, Math.min(ch - y, Math.round(sh)))
    const out = document.createElement('canvas')
    out.width = w
    out.height = h
    const c2d = typeof out.getContext === 'function' ? out.getContext('2d') : null
    if (!c2d) return null
    c2d.fillStyle = '#ffffff'
    c2d.fillRect(0, 0, w, h)
    c2d.drawImage(canvas, x, y, w, h, 0, 0, w, h)
    const dataUrl = canvasDataUrl(out, format, quality)
    if (!dataUrl) return null
    return { dataUrl: dataUrl, width: w, height: h }
  }

  async function captureClientRect (clientRect, options) {
    try {
      if (state.destroyed || state.tornDown) return null
      const sel = normalizeRect(clientRect)
      if (!sel || sel.width < 1 || sel.height < 1) return null
      const target = findPageTarget(sel)
      if (!target) return null
      const pageRect = target.rect
      const view = target.pageView && target.pageView.viewport ? target.pageView.viewport : null
      const currentScale = clampNumber(state.pdfViewer && state.pdfViewer.currentScale, 0.1, 10, 1)
      const autoScale = Math.min(CAPTURE_MAX_SCALE, Math.max(2.5, currentScale * 2))
      const o = captureOptions(options, DEFAULT_REGION_FORMAT, autoScale)
      const pdfPage = (target.pageView && target.pageView.pdfPage) || await getPdfPage(target.pageNumber)
      if (!pdfPage || state.destroyed) return null
      const rendered = await renderPageToCanvas(pdfPage, o.scale, view ? view.rotation : undefined)
      if (!rendered || state.destroyed) return null
      const canvas = rendered.canvas
      const kDivToViewport = view && view.width > 0 && pageRect.width > 0 ? view.width / pageRect.width : 1
      const kViewportToCanvas = view && view.width > 0
        ? canvas.width / view.width
        : canvas.width / Math.max(1, pageRect.width)
      const k = kDivToViewport * kViewportToCanvas
      const cropped = cropCanvasRegion(
        canvas,
        (sel.left - pageRect.left) * k,
        (sel.top - pageRect.top) * k,
        sel.width * k,
        sel.height * k,
        o.format, o.quality
      )
      if (!cropped) return null
      return {
        dataUrl: cropped.dataUrl,
        info: { page: target.pageNumber, width: cropped.width, height: cropped.height }
      }
    } catch (err) {
      return null
    }
  }

  /* ---- 框选模式取景层 ---- */
  function syncRegionButton () {
    if (!ui.btnRegion) return
    const on = !!state.regionSelect
    ui.btnRegion.classList.toggle('active', on)
    ui.btnRegion.setAttribute('aria-pressed', on ? 'true' : 'false')
    ui.btnRegion.title = on
      ? '正在框选：在 PDF 上按住鼠标拖一个框（Esc 取消）'
      : '在 PDF 上拖一个框，把这一块发给模型识别'
  }

  function drawRegionBox (x1, y1, x2, y2) {
    if (!ui.regionBox || !state.regionDrag) return
    const base = state.regionDrag.base
    ui.regionBox.style.display = 'block'
    ui.regionBox.style.left = (Math.min(x1, x2) - base.left) + 'px'
    ui.regionBox.style.top = (Math.min(y1, y2) - base.top) + 'px'
    ui.regionBox.style.width = Math.abs(x2 - x1) + 'px'
    ui.regionBox.style.height = Math.abs(y2 - y1) + 'px'
  }

  function onRegionDown (ev) {
    if (state.destroyed || !state.regionSelect) return
    if (ev.button !== undefined && ev.button !== 0) return
    const pt = eventPoint(ev)
    if (!pt) return
    const base = clientRectOf(ui.regionLayer)
    if (!base) return
    if (typeof ev.preventDefault === 'function') ev.preventDefault()
    state.regionDrag = { x0: pt.x, y0: pt.y, base: base }
    drawRegionBox(pt.x, pt.y, pt.x, pt.y)
  }

  function onRegionMove (ev) {
    const drag = state.regionDrag
    if (!drag || state.destroyed || !state.regionSelect) return
    const pt = eventPoint(ev)
    if (!pt) return
    if (typeof ev.preventDefault === 'function') ev.preventDefault()
    drawRegionBox(drag.x0, drag.y0, pt.x, pt.y)
  }

  async function handleRegionSelection (sel) {
    try {
      const cb = opts.onRegionSelected
      if (typeof cb !== 'function') return
      const shot = await captureClientRect(sel, null)
      if (!shot || state.destroyed) return
      try { cb(shot.dataUrl, shot.info) } catch (err) {}
    } catch (err) {}
  }

  function onRegionUp (ev) {
    const drag = state.regionDrag
    if (!drag || state.destroyed || !state.regionSelect) return
    const pt = eventPoint(ev) || { x: drag.x0, y: drag.y0 }
    const sel = normalizeRect({
      left: Math.min(drag.x0, pt.x),
      top: Math.min(drag.y0, pt.y),
      width: Math.abs(pt.x - drag.x0),
      height: Math.abs(pt.y - drag.y0)
    })
    cancelRegionSelect()
    if (!sel || sel.width < REGION_MIN_SIDE || sel.height < REGION_MIN_SIDE) return
    handleRegionSelection(sel)
  }

  function onRegionKeydown (ev) {
    if (state.destroyed || !state.regionSelect || !ev) return
    if (ev.key !== 'Escape' && ev.key !== 'Esc') return
    if (typeof ev.preventDefault === 'function') ev.preventDefault()
    if (typeof ev.stopPropagation === 'function') ev.stopPropagation()
    cancelRegionSelect()
  }

  function onRegionWheel (ev) {
    if (state.destroyed || !state.regionSelect || !ui.scroll || !ev) return
    if (typeof ev.preventDefault === 'function') ev.preventDefault()
    const unit = ev.deltaMode === 1 ? 16 : (ev.deltaMode === 2 ? (ui.scroll.clientHeight || 1) : 1)
    ui.scroll.scrollTop += (Number(ev.deltaY) || 0) * unit
    ui.scroll.scrollLeft += (Number(ev.deltaX) || 0) * unit
  }

  function startRegionSelect () {
    try {
      if (state.destroyed || state.tornDown) return
      if (state.regionSelect) return
      if (!ui.regionLayer || !ui.body) return
      const viewer = state.pdfViewer
      if (!viewer || !viewer.pagesCount) return
      ctx.closeAnnotPop()
      ctx.setAnnotMode(null)
      state.regionSelect = true
      state.regionDrag = null
      if (ui.regionBox) ui.regionBox.style.display = 'none'
      ui.regionLayer.style.display = 'block'
      syncRegionButton()
      bindTemp(ui.regionLayer, REGION_DOWN, onRegionDown, { passive: false })
      bindTemp(window, REGION_MOVE, onRegionMove, { passive: false })
      bindTemp(window, REGION_UP, onRegionUp, { passive: false })
      bindTemp(ui.regionLayer, 'wheel', onRegionWheel, { passive: false })
      bindTemp(document, 'keydown', onRegionKeydown, { capture: true })
    } catch (err) {
      cancelRegionSelect()
    }
  }

  function cancelRegionSelect () {
    try {
      state.regionSelect = false
      state.regionDrag = null
      unbindTemp()
      if (ui.regionLayer) ui.regionLayer.style.display = 'none'
      if (ui.regionBox) ui.regionBox.style.display = 'none'
      syncRegionButton()
    } catch (err) {}
  }

  function toggleRegionSelect () {
    try {
      if (state.destroyed) return
      if (state.regionSelect) cancelRegionSelect()
      else startRegionSelect()
    } catch (err) {}
  }

  async function capturePage (pageNumber, options) {
    try {
      if (state.destroyed || state.tornDown) return null
      const total = ctx.getPageCount()
      const page = Math.round(Number(pageNumber))
      if (!isFinite(page) || page < 1 || page > total) return null
      const o = captureOptions(options, DEFAULT_CAPTURE_FORMAT, 2)
      const pdfPage = await getPdfPage(page)
      if (!pdfPage || state.destroyed) return null
      const rendered = await renderPageToCanvas(pdfPage, o.scale, undefined)
      if (!rendered || state.destroyed) return null
      return canvasDataUrl(rendered.canvas, o.format, o.quality)
    } catch (err) {
      return null
    }
  }

  async function captureRegion (rect, options) {
    try {
      if (state.destroyed || state.tornDown) return null
      const sel = normalizeRect(rect)
      if (!sel || sel.width < 1 || sel.height < 1) return null
      const base = clientRectOf(ui.scroll) || { left: 0, top: 0 }
      return await captureClientRect({
        left: sel.left + base.left,
        top: sel.top + base.top,
        width: sel.width,
        height: sel.height
      }, options)
    } catch (err) {
      return null
    }
  }

  return {
    syncRegionButton,
    startRegionSelect,
    cancelRegionSelect,
    toggleRegionSelect,
    capturePage,
    captureRegion
  }
}
