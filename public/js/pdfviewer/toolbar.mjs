/* PDF 阅读器子模块：工具栏与阅读区 DOM 骨架搭建 */
import { ANNOT_DEFAULT_COLOR, SCALE_STEP, downloadName, normalizeColor } from './annot-model.mjs'
import { annotIcon, el } from './dom.mjs'

export function buildViewerUi (ctx) {
  const { state, ui, opts, url } = ctx
  const downloadHref = opts.downloadUrl ? String(opts.downloadUrl) : url

  ui.btnPrevPage = el('button.lv-pdf-btn', { type: 'button', title: '上一页（PageUp / ↑）', text: '‹ 上一页' })
  ui.pageInput = el('input.lv-pdf-input.lv-pdf-page', {
    type: 'text', value: '1', inputmode: 'numeric', autocomplete: 'off', spellcheck: 'false',
    'aria-label': '当前页码', title: '输入页码后按回车跳转'
  })
  ui.total = el('span.lv-pdf-num', { text: '/ —' })
  ui.btnNextPage = el('button.lv-pdf-btn', { type: 'button', title: '下一页（PageDown / ↓）', text: '下一页 ›' })

  ui.btnZoomOut = el('button.lv-pdf-btn', { type: 'button', title: '缩小（最小 50%）', text: '−' })
  ui.zoom = el('span.lv-pdf-zoom', { text: '100%' })
  ui.btnZoomIn = el('button.lv-pdf-btn', { type: 'button', title: '放大（最大 300%）', text: '＋' })
  ui.btnFitWidth = el('button.lv-pdf-btn', { type: 'button', title: '页面宽度铺满阅读区', text: '适宽' })
  ui.btnFitPage = el('button.lv-pdf-btn', { type: 'button', title: '整页完整显示在阅读区', text: '适页' })

  ui.searchInput = el('input.lv-pdf-input.lv-pdf-search', {
    type: 'text', placeholder: '搜索全文，回车查找', autocomplete: 'off', spellcheck: 'false',
    'aria-label': '在 PDF 里搜索'
  })
  ui.count = el('span.lv-pdf-count', { text: '' })
  ui.btnPrevMatch = el('button.lv-pdf-btn', { type: 'button', title: '上一个匹配（Shift + 回车）', text: '上一条' })
  ui.btnNextMatch = el('button.lv-pdf-btn', { type: 'button', title: '下一个匹配（回车）', text: '下一条' })

  ui.btnInvert = el('button.lv-pdf-btn', {
    type: 'button', text: '反色', 'aria-pressed': 'false', title: '反色显示（点一次就记住，下次打开还是这个状态）'
  })
  ui.btnRegion = el('button.lv-pdf-btn', {
    type: 'button', 'aria-pressed': 'false',
    title: '在 PDF 上拖一个框，把这一块发给模型识别'
  }, [el('span.lv-pdf-icon-frame', { 'aria-hidden': 'true' }), '框选'])

  ui.highlightIcon = el('span.lv-pdf-icon', { 'aria-hidden': 'true' }, [annotIcon('highlight', state.annotColor)])
  ui.noteIcon = el('span.lv-pdf-icon', { 'aria-hidden': 'true' }, [annotIcon('note', state.annotColor)])
  ui.btnHighlight = el('button.lv-pdf-btn', {
    type: 'button', 'aria-pressed': 'false',
    title: '荧光笔：开着的时候在 PDF 上选中文字，就会画上高亮'
  }, [ui.highlightIcon, '荧光笔'])
  ui.btnNote = el('button.lv-pdf-btn', {
    type: 'button', 'aria-pressed': 'false',
    title: '便签：开着的时候在 PDF 上点一下，就贴一张便签'
  }, [ui.noteIcon, '便签'])
  ui.colorDot = el('span.lv-pdf-color-dot')
  ui.btnColor = el('button.lv-pdf-color', {
    type: 'button', 'aria-expanded': 'false', 'aria-haspopup': 'true',
    title: '批注颜色（点开可以选色 / 加自己的颜色）', 'aria-label': '批注颜色'
  }, [ui.colorDot])
  ui.palette = el('div.lv-pdf-palette', { hidden: true, role: 'group', 'aria-label': '批注调色盘' })
  ui.colorInput = el('input', {
    type: 'color', value: ANNOT_DEFAULT_COLOR,
    style: 'position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;left:-10px;top:-10px',
    'aria-hidden': 'true', tabindex: '-1'
  })
  ui.btnAnnotList = el('button.lv-pdf-btn', {
    type: 'button', 'aria-pressed': 'false', text: '批注',
    title: '展开批注列表：点一条就跳到它在哪一页'
  })

  ui.linkDownload = el('a.lv-pdf-btn', {
    href: downloadHref, title: '下载这个 PDF 文件', text: '下载',
    download: downloadName(opts.title)
  })
  ui.linkOpen = el('a.lv-pdf-btn', {
    href: url, target: '_blank', rel: 'noopener noreferrer',
    title: '在新标签页里用浏览器自带查看器打开', text: '新标签页打开'
  })

  ui.toolbar = el('div.lv-pdf-toolbar', {
    role: 'toolbar', 'aria-label': 'PDF 工具栏'
  }, [
    // 不再用竖线分隔：工具栏会自动换行，竖线换行后会孤零零吊在行尾/行首。
    // 分组靠组间距（.lv-pdf-toolbar 的 column-gap）与组内小间距区分。
    el('div.lv-pdf-group', null, [ui.btnPrevPage, ui.pageInput, ui.total, ui.btnNextPage]),
    el('div.lv-pdf-group', null, [ui.btnZoomOut, ui.zoom, ui.btnZoomIn, ui.btnFitWidth, ui.btnFitPage]),
    el('div.lv-pdf-group', null, [ui.btnHighlight, ui.btnNote, ui.btnColor]),
    el('div.lv-pdf-group.grow', null, [ui.searchInput, ui.count, ui.btnPrevMatch, ui.btnNextMatch]),
    el('div.lv-pdf-group.right', null, [ui.btnAnnotList, ui.btnRegion, ui.btnInvert, ui.linkDownload, ui.linkOpen]),
    ui.palette,
    ui.colorInput
  ])

  ui.viewer = el('div.pdfViewer')
  ui.scroll = el('div.lv-pdf-scroll', {
    tabindex: '0',
    style: 'position:absolute;top:0;right:0;bottom:0;left:0;overflow:auto'
  }, ui.viewer)
  ui.regionBox = el('div.lv-pdf-region-box', { 'aria-hidden': 'true' })
  ui.regionHint = el('div.lv-pdf-region-hint', {
    text: '按住鼠标拖一个框，把这一块发给模型识别（Esc 取消）'
  })
  ui.regionLayer = el('div.lv-pdf-region-layer', {
    style: 'display:none', 'aria-hidden': 'true'
  }, [ui.regionHint, ui.regionBox])
  ui.statusText = el('span', { text: '正在加载 PDF…' })
  ui.status = el('div.lv-pdf-status', { role: 'status' }, [el('span.lv-pdf-spinner'), ui.statusText])

  ui.annotList = el('div.lv-annot-list')
  ui.annotPanel = el('div.lv-annot-panel', { hidden: true }, [
    el('div.lv-annot-panel-head', null, [
      el('span', { text: '批注' }),
      el('div.right', null, [el('button.lv-pdf-btn', {
        type: 'button', text: '关闭',
        title: '收起批注列表',
        onclick: () => ctx.showAnnotPanel(false)
      })])
    ]),
    ui.annotList
  ])
  ui.annotHint = el('div.lv-annot-hint', { hidden: true })

  ui.body = el('div.lv-pdf-body', { style: 'position:relative;flex:1 1 auto;min-height:0' },
    [ui.scroll, ui.annotPanel, ui.annotHint, ui.regionLayer, ui.status])
  ui.root = el('div.lv-pdf-viewer', {
    style: 'position:relative;display:flex;flex-direction:column;width:100%;height:100%;overflow:hidden'
  }, [ui.toolbar, ui.body])

  ui.btnPrevPage.onclick = function () { ctx.stepPage(-1) }
  ui.btnNextPage.onclick = function () { ctx.stepPage(1) }
  ui.pageInput.onkeydown = ctx.onPageInputKeydown
  ui.pageInput.onfocus = function () { try { ui.pageInput.select() } catch (err) {} }
  ui.pageInput.onblur = function () { ctx.syncPageInput() }
  ui.btnZoomOut.onclick = function () { ctx.zoomBy(-SCALE_STEP) }
  ui.btnZoomIn.onclick = function () { ctx.zoomBy(SCALE_STEP) }
  ui.btnFitWidth.onclick = function () { ctx.applyFit('page-width') }
  ui.btnFitPage.onclick = function () { ctx.applyFit('page-fit') }
  ui.searchInput.onkeydown = ctx.onSearchKeydown
  ui.searchInput.oninput = function () { if (!ui.searchInput.value) ctx.clearSearch() }
  ui.btnPrevMatch.onclick = function () { ctx.findAgain(true) }
  ui.btnNextMatch.onclick = function () { ctx.findAgain(false) }
  ui.btnInvert.onclick = function () { ctx.setInvert(!state.invert, true) }
  ui.root.addEventListener('keydown', ctx.onViewerKeydown)
  state.domListeners.push({ target: ui.root, type: 'keydown', fn: ctx.onViewerKeydown })

  ui.btnRegion.onclick = function () { ctx.toggleRegionSelect() }
  ctx.syncRegionButton()

  ui.btnHighlight.onclick = function () { ctx.setAnnotMode('highlight') }
  ui.btnNote.onclick = function () { ctx.setAnnotMode('note') }
  ui.btnColor.onclick = function () { ctx.togglePalette() }
  ui.colorInput.onchange = function () {
    const hex = normalizeColor(ui.colorInput.value)
    if (!hex) return
    ctx.addCustomColor(hex)
    ctx.setAnnotColor(hex)
    ctx.renderPalette()
  }
  ui.btnAnnotList.onclick = function () { ctx.showAnnotPanel(!state.annotPanelOpen) }
  ctx.onGlobal(ui.scroll, 'mousedown', ctx.onAnnotDown)
  ctx.onGlobal(ui.scroll, 'mouseup', ctx.onAnnotUp)
  ctx.setAnnotColor(state.annotColor)
  ctx.renderAnnotPanel()
  ctx.syncAnnotButtons()

  ctx.syncPageButtons()
  ctx.syncMatchButtons()
  ctx.syncZoom()
}
