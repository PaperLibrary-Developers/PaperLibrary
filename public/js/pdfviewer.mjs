/* 内置 PDF 阅读器门面入口（子模块拆分在 js/pdfviewer/ 下，对外导出保持 100% 兼容） */
import { ANNOT_DEFAULT_COLOR, GENERIC_FAIL, MAX_SCALE, MIN_SCALE, clampScale, readAnnotColor } from './pdfviewer/annot-model.mjs'
import { cancelRaf, isNightTheme, requestRaf, scrollPagePointIntoView, viewportAnchor } from './pdfviewer/dom.mjs'
import { FALLBACK_FIND_STATE, MIN_CONTAINER_SIZE, getPdfjsBase, loadPdfDocument, pdfjsUrls, setPdfjsBase } from './pdfviewer/loader.mjs'
import { injectStyle, injectViewerCss } from './pdfviewer/styles.mjs'
import { createSearchController } from './pdfviewer/search.mjs'
import { createCaptureController } from './pdfviewer/capture.mjs'
import { createAnnotationController } from './pdfviewer/annotations.mjs'
import { buildViewerUi } from './pdfviewer/toolbar.mjs'

export { setPdfjsBase, getPdfjsBase }

/* 阅读锚点在视口里的纵向位置（0 = 顶部，1 = 底部）。
   存视图状态和恢复视图状态都用这一个数：记录时取这一点的页内位置，
   恢复时再把这一点放回同一个高度，换源/换标签页才能停在原处。 */
const VIEW_ANCHOR = 0.6

async function mount (container, options) {
  const opts = options || {}
  const signal = opts.signal
  const url = opts.url === null || opts.url === undefined ? '' : String(opts.url).trim()

  if (!container || container.nodeType !== 1) {
    return { ok: false, reason: '内置阅读器没拿到有效的挂载点' }
  }
  if (!url) return { ok: false, reason: '内置阅读器没拿到 PDF 地址' }
  if (signal && signal.aborted) return { ok: false, reason: '阅读器已关闭' }

  const state = {
    destroyed: false,
    tornDown: false,
    loadingTask: null,
    pdfViewer: null,
    findController: null,
    linkService: null,
    eventBus: null,
    resizeObserver: null,
    themeObserver: null,
    busHandlers: [],
    domListeners: [],
    rafId: 0,
    presetScale: 'page-width',
    matchQuery: '',
    drawer: null,
    drawerAnimating: false,
    drawerTimer: 0,
    findState: FALLBACK_FIND_STATE,
    pdfDocument: null,
    regionSelect: false,
    regionDrag: null,
    regionListeners: [],
    annotations: [],
    annotMode: null,
    annotColors: {
      highlight: readAnnotColor('highlight') || ANNOT_DEFAULT_COLOR,
      note: readAnnotColor('note') || ANNOT_DEFAULT_COLOR
    },
    paletteFor: 'highlight',
    annotPress: null,
    paletteOpen: false,
    paletteListeners: [],
    annotPop: null,
    annotPopId: '',
    annotPopFlush: null,
    annotPopDocListeners: [],
    annotPanelOpen: false,
    annotFlashId: '',
    annotFlashTimer: 0
  }

  const ui = {
    root: null, toolbar: null, body: null, scroll: null, viewer: null, status: null, statusText: null,
    pageInput: null, total: null, btnPrevPage: null, btnNextPage: null,
    zoom: null, btnZoomOut: null, btnZoomIn: null, btnFitWidth: null, btnFitPage: null,
    btnSearch: null, searchPanel: null, searchInput: null, searchGo: null, searchClose: null,
    searchCount: null, searchPrev: null, searchNext: null, searchList: null,
    linkDownload: null, linkOpen: null,
    btnRegion: null, regionLayer: null, regionBox: null, regionHint: null,
    btnHighlight: null, btnNote: null, btnAnnotList: null,
    highlightIcon: null, noteIcon: null,
    palette: null, colorInput: null,
    annotPanel: null, annotList: null, annotHint: null
  }

  /* ------------------------------ 基础导航 / 缩放 / 主题 ------------------------------ */
  function hideStatus () {
    if (ui.status && ui.status.parentNode) ui.status.parentNode.removeChild(ui.status)
    ui.status = null
    ui.statusText = null
  }

  function onLoadProgress (data) {
    if (state.destroyed || !ui.statusText) return
    const loaded = Number(data && data.loaded) || 0
    const total = Number(data && data.total) || 0
    if (total > 0 && loaded > 0) {
      ui.statusText.textContent = '正在加载 PDF… ' + Math.min(100, Math.round(loaded / total * 100)) + '%'
    }
  }

  function applyTheme () {
    if (state.tornDown) return
    if (ui.root) ui.root.classList.toggle('lv-night', isNightTheme())
  }

  function syncPageButtons () {
    const viewer = state.pdfViewer
    const total = viewer && viewer.pagesCount ? viewer.pagesCount : 0
    const current = viewer ? (viewer.currentPageNumber || 1) : 1
    if (ui.btnPrevPage) ui.btnPrevPage.disabled = !total || current <= 1
    if (ui.btnNextPage) ui.btnNextPage.disabled = !total || current >= total
  }

  function syncZoom () {
    const viewer = state.pdfViewer
    const scale = viewer && viewer.currentScale ? viewer.currentScale : 1
    if (ui.zoom) ui.zoom.textContent = Math.round(scale * 100) + '%'
    if (ui.btnZoomOut) ui.btnZoomOut.disabled = scale <= MIN_SCALE + 0.001
    if (ui.btnZoomIn) ui.btnZoomIn.disabled = scale >= MAX_SCALE - 0.001
    if (ui.btnFitWidth) ui.btnFitWidth.classList.toggle('active', state.presetScale === 'page-width')
    if (ui.btnFitPage) ui.btnFitPage.classList.toggle('active', state.presetScale === 'page-fit')
  }

  function syncPageInput () {
    const viewer = state.pdfViewer
    if (!ui.pageInput || !viewer) return
    if (document.activeElement !== ui.pageInput) ui.pageInput.value = String(viewer.currentPageNumber || 1)
    syncPageButtons()
  }

  function jumpToPage (value) {
    const viewer = state.pdfViewer
    if (!viewer || !viewer.pagesCount) return
    const raw = Number(value)
    if (!isFinite(raw)) { syncPageInput(); return }
    const target = Math.min(Math.max(Math.round(raw), 1), viewer.pagesCount)
    try { viewer.currentPageNumber = target } catch (err) {}
    syncPageInput()
  }

  function stepPage (delta) {
    const viewer = state.pdfViewer
    if (!viewer || !viewer.pagesCount) return
    jumpToPage((viewer.currentPageNumber || 1) + delta)
  }

  function applyZoom (scale) {
    const viewer = state.pdfViewer
    if (!viewer) return
    state.presetScale = null
    const next = clampScale(Math.round(scale * 1000) / 1000)
    try { viewer.currentScale = next } catch (err) {}
    syncZoom()
  }

  function zoomBy (delta) {
    const viewer = state.pdfViewer
    if (!viewer) return
    applyZoom((viewer.currentScale || 1) + delta)
  }

  function applyFit (value, force) {
    state.presetScale = value
    const viewer = state.pdfViewer
    if (!viewer) { syncZoom(); return }
    if (!force && ui.scroll &&
      (ui.scroll.clientWidth < MIN_CONTAINER_SIZE || ui.scroll.clientHeight < MIN_CONTAINER_SIZE)) {
      syncZoom()
      return
    }
    try { viewer.currentScaleValue = value } catch (err) {}
    syncZoom()
  }

  function scheduleScaleRefresh () {
    if (state.destroyed || state.tornDown || state.rafId) return
    // 侧栏正在展开/收起：宽度每帧都在变，这时候重排 PDF 会一卡一卡的
    if (state.drawerAnimating) return
    state.rafId = requestRaf(function () {
      state.rafId = 0
      if (state.destroyed || state.tornDown || !state.pdfViewer) return
      if (state.presetScale) applyFit(state.presetScale, true)
      else syncZoom()
    })
  }

  function onGlobal (target, type, fn) {
    if (!target || typeof target.addEventListener !== 'function') return
    target.addEventListener(type, fn, { passive: true })
    state.domListeners.push({ target: target, type: type, fn: fn })
  }

  function getPageCount () {
    try {
      if (state.destroyed) return 0
      const viewer = state.pdfViewer
      const total = (viewer && viewer.pagesCount) || (state.pdfDocument && state.pdfDocument.numPages) || 0
      const n = Math.round(Number(total))
      return isFinite(n) && n > 0 ? n : 0
    } catch (err) {
      return 0
    }
  }

  function goToPage (pageNumber) {
    try {
      if (state.destroyed || state.tornDown) return
      jumpToPage(pageNumber)
    } catch (err) {}
  }

  /* ------------------------------ 装配子控制器 ------------------------------ */
  const ctx = {
    state,
    ui,
    opts,
    url,
    stepPage,
    jumpToPage,
    goToPage,
    getPageCount,
    syncPageInput,
    syncPageButtons,
    syncZoom,
    zoomBy,
    applyFit,
    onGlobal
  }

  const annot = createAnnotationController(ctx)
  Object.assign(ctx, annot)

  const capture = createCaptureController(ctx)
  Object.assign(ctx, capture)

  const search = createSearchController(ctx)
  Object.assign(ctx, search)

  /* ------------------------------ 左侧导航栏（批注 / 搜索共用） ------------------------------
     复用同一个抽屉：宽度从 0 过渡到 320px，阅读区被挤开，文档不会被侧栏遮住；
     收起时宽度回 0。过渡期间先不重排 PDF（ResizeObserver 每帧都触发会很卡），
     等动画结束再按当前缩放方式重新适配一次。 */
  function syncDrawerUi () {
    const mode = state.drawer
    if (ui.drawer) {
      ui.drawer.classList.toggle('is-open', !!mode)
      ui.drawer.setAttribute('aria-hidden', mode ? 'false' : 'true')
    }
    if (ui.drawerTitle) ui.drawerTitle.textContent = mode === 'search' ? '搜索' : '批注'
    if (ui.annotPane) ui.annotPane.hidden = mode !== 'annot'
    if (ui.searchPane) ui.searchPane.hidden = mode !== 'search'
    if (ui.btnAnnotList) {
      const on = mode === 'annot'
      ui.btnAnnotList.classList.toggle('active', on)
      ui.btnAnnotList.setAttribute('aria-pressed', on ? 'true' : 'false')
    }
    if (ui.btnSearch) {
      const on = mode === 'search'
      ui.btnSearch.classList.toggle('active', on)
      ui.btnSearch.setAttribute('aria-expanded', on ? 'true' : 'false')
    }
  }

  function startDrawerAnim () {
    state.drawerAnimating = true
    if (state.drawerTimer) {
      try { clearTimeout(state.drawerTimer) } catch (err) {}
    }
    state.drawerTimer = setTimeout(function () {
      state.drawerTimer = 0
      if (state.destroyed || state.tornDown) return
      state.drawerAnimating = false
      scheduleScaleRefresh()
    }, 320)
  }

  function openDrawer (mode) {
    if (state.destroyed || state.tornDown) return
    const want = mode === 'search' ? 'search' : 'annot'
    const first = !state.drawer
    state.drawer = want
    state.annotPanelOpen = want === 'annot'
    syncDrawerUi()
    if (first) startDrawerAnim()
    if (want === 'annot') {
      state.annotPanelOpen = true
      try { annot.renderAnnotPanel() } catch (err) {}
    }
  }

  function closeDrawer () {
    if (!state.drawer) return
    state.drawer = null
    state.annotPanelOpen = false
    syncDrawerUi()
    startDrawerAnim()
    try { ui.searchInput.blur() } catch (err) {}
  }

  function toggleDrawer (mode) {
    const want = mode === 'search' ? 'search' : 'annot'
    if (state.drawer === want) closeDrawer()
    else openDrawer(want)
  }

  Object.assign(ctx, { openDrawer, closeDrawer, toggleDrawer })

  /* ------------------------------ 清理与销毁 ------------------------------ */
  function teardown () {
    if (state.tornDown) return
    state.tornDown = true
    if (signal) signal.removeEventListener('abort', destroy)

    if (state.rafId) {
      try { cancelRaf(state.rafId) } catch (err) {}
      state.rafId = 0
    }

    const bus = state.eventBus
    if (bus) {
      for (let i = 0; i < state.busHandlers.length; i++) {
        try { bus.off(state.busHandlers[i].type, state.busHandlers[i].fn) } catch (err) {}
      }
    }
    state.busHandlers.length = 0

    if (state.resizeObserver) {
      try { state.resizeObserver.disconnect() } catch (err) {}
      state.resizeObserver = null
    }
    if (state.themeObserver) {
      try { state.themeObserver.disconnect() } catch (err) {}
      state.themeObserver = null
    }

    for (let i = 0; i < state.domListeners.length; i++) {
      const item = state.domListeners[i]
      try { item.target.removeEventListener(item.type, item.fn, item.capture === true) } catch (err) {}
    }
    state.domListeners.length = 0
    state.regionListeners.length = 0
    state.regionSelect = false
    state.regionDrag = null

    if (state.annotFlashTimer) {
      try { clearTimeout(state.annotFlashTimer) } catch (err) {}
      state.annotFlashTimer = 0
    }
    try { annot.closeAnnotPop({ keepChanges: false }) } catch (err) {}
    try { annot.closePalette() } catch (err) {}
    try { annot.destroyPicker() } catch (err) {}
    try { search.cancelJumpGuard() } catch (err) {}
    if (state.drawerTimer) {
      try { clearTimeout(state.drawerTimer) } catch (err) {}
      state.drawerTimer = 0
    }
    state.drawerAnimating = false
    state.paletteListeners.length = 0
    state.paletteOpen = false
    state.annotMode = null
    state.annotPress = null
    state.annotPanelOpen = false
    state.annotations = []

    const viewer = state.pdfViewer
    state.pdfViewer = null
    state.pdfDocument = null
    if (viewer) {
      try { viewer.setDocument(null) } catch (err) {}
      try { if (typeof viewer.cleanup === 'function') viewer.cleanup() } catch (err) {}
    }
    if (state.findController) {
      try { state.findController.setDocument(null) } catch (err) {}
      state.findController = null
    }
    if (state.linkService) {
      try { state.linkService.setDocument(null) } catch (err) {}
      state.linkService = null
    }
    const task = state.loadingTask
    state.loadingTask = null
    if (task) {
      try {
        const pending = typeof task.destroy === 'function' ? task.destroy() : null
        if (pending && typeof pending.catch === 'function') pending.catch(function () {})
      } catch (err) {}
    }
    state.eventBus = null

    try {
      const root = ui.root
      if (root && root.parentNode) {
        if (typeof root.remove === 'function') root.remove()
        else if (typeof root.parentNode.removeChild === 'function') root.parentNode.removeChild(root)
        else root.parentNode.childNodes = (root.parentNode.childNodes || []).filter(n => n !== root)
      }
    } catch (err) {}
    ui.root = null; ui.toolbar = null; ui.body = null
    ui.scroll = null; ui.viewer = null; ui.status = null; ui.statusText = null
    ui.btnRegion = null; ui.regionLayer = null; ui.regionBox = null; ui.regionHint = null
  }

  function fail (reason) {
    state.destroyed = true
    teardown()
    return { ok: false, reason: reason || GENERIC_FAIL }
  }

  function destroy () {
    if (state.destroyed) return
    state.destroyed = true
    teardown()
  }

  if (signal) signal.addEventListener('abort', destroy, { once: true })

  /* ------------------------------ pdf.js 生命周期事件 ------------------------------ */
  function onPagesInit () {
    if (state.destroyed) return
    const viewer = state.pdfViewer
    const total = viewer && viewer.pagesCount ? viewer.pagesCount : 0
    if (ui.total) ui.total.textContent = '/ ' + total
    if (ui.pageInput && viewer) ui.pageInput.value = String(viewer.currentPageNumber || 1)
    syncPageButtons()
    const prior = opts.initialView
    if (prior && prior.preset === null && Number.isFinite(prior.scale)) applyZoom(prior.scale)
    else applyFit(prior?.preset === 'page-fit' ? 'page-fit' : 'page-width')
    if (prior && prior.page > 1) jumpToPage(prior.page)
    if (prior?.page <= total && Number.isFinite(prior.y)) {
      scrollPagePointIntoView(viewer, ui.scroll, prior.page, prior.y, VIEW_ANCHOR, 'instant')
    }
    hideStatus()
    annot.paintAllAnnotations()
    try {
      const active = document.activeElement
      if (ui.scroll && (!active || active === document.body)) ui.scroll.focus({ preventScroll: true })
    } catch (err) {}
  }

  function onPageChanging (evt) {
    if (state.destroyed || !evt) return
    if (ui.pageInput && document.activeElement !== ui.pageInput) {
      ui.pageInput.value = String(evt.pageNumber || 1)
    }
    syncPageButtons()
  }

  function onScaleChanging (evt) {
    if (state.destroyed) return
    const scale = (evt && evt.scale) || (state.pdfViewer && state.pdfViewer.currentScale) || 1
    if (ui.zoom) ui.zoom.textContent = Math.round(scale * 100) + '%'
    syncZoom()
  }

  /* -------------------------------- 正式流程 -------------------------------- */
  try {
    try {
      injectStyle()
      injectViewerCss(pdfjsUrls().css)
    } catch (err) {}
    buildViewerUi(ctx)
    container.appendChild(ui.root)
    applyTheme()
    annot.setAnnotations(opts.annotations || [])

    if (typeof window.MutationObserver === 'function' && document.documentElement) {
      state.themeObserver = new window.MutationObserver(function () { applyTheme() })
      state.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    }

    const loaded = await loadPdfDocument({
      url,
      state,
      ui,
      onLoadProgress,
      busEvents: {
        pagesinit: onPagesInit,
        pagechanging: onPageChanging,
        scalechanging: onScaleChanging,
        pagerendered: annot.onPageRendered,
        updatefindmatchescount: search.onMatchesCount,
        updatefindcontrolstate: search.onFindControlState
      }
    })
    if (!loaded.ok) return fail(loaded.reason)

    if (typeof window.ResizeObserver === 'function') {
      state.resizeObserver = new window.ResizeObserver(function () { scheduleScaleRefresh() })
      state.resizeObserver.observe(ui.body)
    }
    onGlobal(window, 'resize', scheduleScaleRefresh)

    return {
      ok: true,
      destroy: destroy,
      getPageCount: getPageCount,
      capturePage: capture.capturePage,
      captureRegion: capture.captureRegion,
      startRegionSelect: capture.startRegionSelect,
      cancelRegionSelect: capture.cancelRegionSelect,
      goToPage: goToPage,
      /* 记住「页码 + 页内位置」：只存页码的话，换源或换标签页后只会回到那一页的顶部。
         锚点与 onPagesInit 的恢复用同一个 viewportFraction，来回切换不会漂。 */
      getViewState: () => {
        const viewer = state.pdfViewer
        const anchor = viewportAnchor(viewer, ui.scroll, VIEW_ANCHOR)
        const view = {
          page: (anchor && anchor.page) || viewer?.currentPageNumber || 1,
          scale: viewer?.currentScale,
          preset: state.presetScale
        }
        if (anchor) view.y = anchor.y
        return view
      },
      closeAnnotationEditor: () => annot.closeAnnotPop(),
      getAnnotations: annot.getAnnotations,
      setAnnotations: annot.setAnnotations,
      goToAnnotation: annot.locateAnnotation
    }
  } catch (err) {
    return fail('内置阅读器初始化失败：' + String((err && err.message) || err))
  }
}

export { mount }
export default { mount }
