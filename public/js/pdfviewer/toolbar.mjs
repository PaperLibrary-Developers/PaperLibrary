/* PDF 阅读器子模块：工具栏与阅读区 DOM 骨架搭建 */
import { SCALE_STEP, downloadName } from './annot-model.mjs'
import { annotIcon, el, svgNode } from './dom.mjs'

/* ---------------- 工具栏图标（统一 24 视框、描边圆角，贴近 SF Symbols 的观感） ---------------- */
function strokeIcon (size) {
  return svgNode('svg', {
    width: String(size), height: String(size), viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false'
  })
}

/* 新标签页：左上角的框只保留可见的「⌐」，两端正好收在右下方块的边上，
   不再出现悬空的线头（上一版看起来「框没闭合」）。 */
/* 新标签页：两张叠放的圆角方框，前面的框里一个加号。
   后框是一个完整的闭合方框，只是被前框「实心遮住」了右下角——
   这里用 mask 把前框覆盖的那块挖掉（而不是把后框画成断开的 L），
   于是后框看起来是完整的一张纸，不会出现悬空的线头。
   描边粗一点、圆角大一点，接近系统「复制并新建」那类图标的观感。 */
let frameAddSeq = 0
function frameAddIcon () {
  const svg = strokeIcon(16)
  const id = 'lv-newtab-mask-' + (++frameAddSeq)
  const defs = svgNode('defs')
  const mask = svgNode('mask', { id: id, maskUnits: 'userSpaceOnUse', x: '0', y: '0', width: '24', height: '24' })
  mask.appendChild(svgNode('rect', { x: '0', y: '0', width: '24', height: '24', fill: '#fff' }))
  // 挖掉前框（含描边）覆盖的范围，稍微外扩半个描边宽度
  mask.appendChild(svgNode('rect', { x: '7.5', y: '7.5', width: '14.4', height: '14.4', rx: '4.4', fill: '#000' }))
  defs.appendChild(mask)
  svg.appendChild(defs)
  svg.appendChild(svgNode('rect', {
    x: '3.2', y: '3.2', width: '12.6', height: '12.6', rx: '3.4',
    'stroke-width': '2', mask: 'url(#' + id + ')'
  }))
  svg.appendChild(svgNode('rect', { x: '8.6', y: '8.6', width: '12.2', height: '12.2', rx: '3.4', 'stroke-width': '2' }))
  // 加号对准前框的几何中心（8.6 + 12.2/2 = 14.7）
  svg.appendChild(svgNode('path', { d: 'M14.7 11.3v6.8M11.3 14.7h6.8', 'stroke-width': '2.1' }))
  return svg
}

function chevronIcon (dir) {
  const svg = strokeIcon(16)
  svg.appendChild(svgNode('path', {
    d: dir === 'left' ? 'M14.6 5.6 8.2 12l6.4 6.4' : 'M9.4 5.6 15.8 12l-6.4 6.4',
    'stroke-width': '2'
  }))
  return svg
}

function downloadIcon () {
  const svg = strokeIcon(16)
  svg.appendChild(svgNode('path', { d: 'M12 3.6v10.8' }))
  svg.appendChild(svgNode('path', { d: 'm7.6 10 4.4 4.4 4.4-4.4' }))
  svg.appendChild(svgNode('path', { d: 'M4.6 16.4v1.8a2.2 2.2 0 0 0 2.2 2.2h10.4a2.2 2.2 0 0 0 2.2-2.2v-1.8' }))
  return svg
}

/* 框选：虚线选框（把这一块发给模型识别） */
function selectIcon () {
  const svg = strokeIcon(16)
  svg.appendChild(svgNode('rect', {
    x: '3.4', y: '3.4', width: '17.2', height: '17.2', rx: '4',
    'stroke-dasharray': '3 2.7', 'stroke-width': '1.7'
  }))
  return svg
}

/* 批注列表：三条等长的横线（Apple `line.3.horizontal` 的三横画法，圆头端点） */
function listIcon () {
  const svg = strokeIcon(16)
  svg.appendChild(svgNode('path', { d: 'M3.6 6.6h16.8M3.6 12h16.8M3.6 17.4h16.8', 'stroke-width': '2' }))
  return svg
}

/* 放大镜：工具栏按钮与搜索栏里的「搜索」按钮共用 */
function searchIcon () {
  const svg = strokeIcon(16)
  svg.appendChild(svgNode('circle', { cx: '10.6', cy: '10.6', r: '6.2' }))
  svg.appendChild(svgNode('path', { d: 'M15.2 15.2 20.2 20.2' }))
  return svg
}

const SEARCH_ID = 'lv-pdf-search'

export function buildViewerUi (ctx) {
  const { state, ui, opts, url } = ctx
  const downloadHref = opts.downloadUrl ? String(opts.downloadUrl) : url

  ui.btnPrevPage = el('button.lv-pdf-btn.lv-pdf-iconbtn', {
    type: 'button', title: '上一页（PageUp / ↑）', 'aria-label': '上一页'
  }, chevronIcon('left'))
  ui.pageInput = el('input.lv-pdf-input.lv-pdf-page', {
    type: 'text', value: '1', inputmode: 'numeric', autocomplete: 'off', spellcheck: 'false',
    'aria-label': '当前页码', title: '输入页码后按回车跳转'
  })
  ui.total = el('span.lv-pdf-num', { text: '/ —' })
  ui.btnNextPage = el('button.lv-pdf-btn.lv-pdf-iconbtn', {
    type: 'button', title: '下一页（PageDown / ↓）', 'aria-label': '下一页'
  }, chevronIcon('right'))

  ui.btnZoomOut = el('button.lv-pdf-btn', { type: 'button', title: '缩小（最小 50%）', text: '−' })
  ui.zoom = el('span.lv-pdf-zoom', { text: '100%' })
  ui.btnZoomIn = el('button.lv-pdf-btn', { type: 'button', title: '放大（最大 300%）', text: '＋' })
  /* 适宽 / 适页：一个连体按钮，中间一道分隔线，点哪半边就是哪种 */
  ui.btnFitWidth = el('button.lv-pdf-seg-btn', { type: 'button', title: '页面宽度铺满阅读区', text: '适宽' })
  ui.btnFitPage = el('button.lv-pdf-seg-btn', { type: 'button', title: '整页完整显示在阅读区', text: '适页' })
  ui.fitSeg = el('div.lv-pdf-seg', { role: 'group', 'aria-label': '缩放方式' }, [ui.btnFitWidth, ui.btnFitPage])

  /* 搜索：工具栏只留一个放大镜，点开右侧搜索栏（Word「导航」式的结果列表） */
  ui.btnSearch = el('button.lv-pdf-btn.lv-pdf-iconbtn', {
    type: 'button', title: '在文档中搜索（Ctrl + F）', 'aria-label': '在文档中搜索',
    'aria-expanded': 'false', 'aria-controls': SEARCH_ID
  }, searchIcon())

  ui.btnRegion = el('button.lv-pdf-btn.lv-pdf-iconbtn', {
    type: 'button', 'aria-pressed': 'false',
    title: '框选：在 PDF 上拖一个框，把这一块发给模型识别', 'aria-label': '框选'
  }, selectIcon())

  /* 荧光笔 / 便签：只留两个图标（参考 GoodNotes 的工具栏）。
     点哪个就等于「拿起哪支笔」，并在工具栏下方弹出那支笔自己的取色条。 */
  ui.highlightIcon = el('span.lv-pdf-icon', { 'aria-hidden': 'true' }, [annotIcon('highlight', state.annotColors.highlight)])
  ui.noteIcon = el('span.lv-pdf-icon', { 'aria-hidden': 'true' }, [annotIcon('note', state.annotColors.note)])
  ui.btnHighlight = el('button.lv-pdf-btn.lv-pdf-iconbtn.lv-pdf-inkbtn', {
    type: 'button', 'aria-pressed': 'false', 'aria-expanded': 'false', 'aria-label': '荧光笔',
    title: '荧光笔（H）：选中文字即可高亮；再点一下关闭，颜色在下方选'
  }, ui.highlightIcon)
  ui.btnNote = el('button.lv-pdf-btn.lv-pdf-iconbtn', {
    type: 'button', 'aria-pressed': 'false', 'aria-expanded': 'false', 'aria-label': '便签',
    title: '便签（N）：在页面上点一下放一张便签'
  }, ui.noteIcon)
  ui.palette = el('div.lv-pdf-inkbar', { hidden: true, role: 'toolbar', 'aria-label': '荧光笔颜色' })
  /* 批注列表：图标 + 数量角标（0 条时不显示角标） */
  ui.annotBadge = el('span.lv-pdf-badge', { hidden: true })
  ui.btnAnnotList = el('button.lv-pdf-btn.lv-pdf-iconbtn.lv-pdf-badged', {
    type: 'button', 'aria-pressed': 'false', 'aria-label': '批注列表',
    title: '展开批注列表：点一条就跳到它在哪一页'
  }, [listIcon(), ui.annotBadge])

  /* 荧光笔 / 便签 / 批注列表：一个连体框，顺序就是这三个 */
  ui.toolSeg = el('div.lv-pdf-toolseg', { role: 'group', 'aria-label': '批注工具' },
    [ui.btnHighlight, ui.btnNote, ui.btnAnnotList])

  ui.linkDownload = el('a.lv-pdf-btn.lv-pdf-iconbtn', {
    href: downloadHref, title: '下载这个 PDF 文件', 'aria-label': '下载',
    download: downloadName(opts.title)
  }, downloadIcon())
  /* 新标签页打开：图标按钮（叠放的方框 + 加号），含义见 title / aria-label */
  ui.linkOpen = el('a.lv-pdf-btn.lv-pdf-iconbtn', {
    href: url, target: '_blank', rel: 'noopener noreferrer',
    title: '在新标签页里用浏览器自带查看器打开', 'aria-label': '在新标签页打开'
  }, frameAddIcon())

  /* 工具栏整体居中、按需换行：宽度够就一行排完，不够才折行（不再固定两排）。 */
  ui.toolbar = el('div.lv-pdf-toolbar', {
    role: 'toolbar', 'aria-label': 'PDF 工具栏'
  }, [
    el('div.lv-pdf-group', null, [ui.btnPrevPage, ui.pageInput, ui.total, ui.btnNextPage]),
    el('div.lv-pdf-group', null, [ui.btnZoomOut, ui.zoom, ui.btnZoomIn]),
    el('div.lv-pdf-group', null, [ui.fitSeg]),
    el('div.lv-pdf-group', null, [ui.toolSeg]),
    el('div.lv-pdf-group', null, [ui.btnRegion]),
    el('div.lv-pdf-group', null, [ui.btnSearch, ui.linkDownload, ui.linkOpen]),
    ui.palette
  ])

  /* ---------------- 右侧搜索栏（只建一次，打开靠 class 控制位移，不重建 DOM） ---------------- */
  ui.searchInput = el('input.lv-pdf-search-input', {
    type: 'text', placeholder: '在文档中搜索', autocomplete: 'off', spellcheck: 'false',
    'aria-label': '在文档中搜索', enterkeyhint: 'search'
  })
  ui.searchGo = el('button.lv-pdf-search-go', {
    type: 'button', title: '搜索（回车）', 'aria-label': '搜索'
  }, searchIcon())
  ui.searchCount = el('span.lv-pdf-search-count', { text: '输入关键词后回车' })
  ui.searchPrev = el('button.lv-pdf-search-step', { type: 'button', title: '上一个结果（Shift + 回车）', 'aria-label': '上一个结果', text: '↑' })
  ui.searchNext = el('button.lv-pdf-search-step', { type: 'button', title: '下一个结果（回车）', 'aria-label': '下一个结果', text: '↓' })
  ui.searchList = el('div.lv-pdf-search-list', { role: 'listbox', 'aria-label': '搜索结果' })
  /* ---------------- 左侧导航栏：批注与搜索共用一个抽屉 ----------------
     只建一次，打开靠宽度过渡把 PDF 往右挤开（不遮挡要定位的内容），
     标题随呼出来源切换，两个面板切 hidden 即可。 */
  ui.searchPane = el('div.lv-pdf-drawer-pane', { 'data-pane': 'search', hidden: true }, [
    el('div.lv-pdf-drawer-searchrow', null, [ui.searchInput, ui.searchGo]),
    el('div.lv-pdf-search-meta', null, [
      ui.searchCount,
      el('div.lv-pdf-search-steps', null, [ui.searchPrev, ui.searchNext])
    ]),
    ui.searchList
  ])

  ui.viewer = el('div.pdfViewer')
  ui.scroll = el('div.lv-pdf-scroll', {
    tabindex: '0',
    style: 'position:absolute;top:0;right:0;bottom:0;left:0;overflow:auto'
  }, ui.viewer)
  /* pdf.js 要求挂载容器是绝对定位的，所以外面再套一层占位的 flex 子项 */
  ui.main = el('div.lv-pdf-main', { style: 'position:relative;flex:1 1 auto;min-width:0' }, [ui.scroll])
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
  ui.annotPane = el('div.lv-pdf-drawer-pane', { 'data-pane': 'annot' }, [ui.annotList])

  ui.drawerTitle = el('span.lv-pdf-drawer-title', { text: '批注' })
  ui.drawerClose = el('button.lv-pdf-drawer-close', {
    type: 'button', title: '收起侧栏（Esc）', 'aria-label': '收起侧栏', text: '×'
  })
  ui.drawer = el('aside.lv-pdf-drawer', { id: SEARCH_ID, 'aria-label': '导航侧栏', 'aria-hidden': 'true' }, [
    el('div.lv-pdf-drawer-inner', null, [
      el('div.lv-pdf-drawer-head', null, [ui.drawerClose, ui.drawerTitle]),
      ui.annotPane,
      ui.searchPane
    ])
  ])

  ui.annotHint = el('div.lv-annot-hint', { hidden: true })

  ui.body = el('div.lv-pdf-body', {
    style: 'position:relative;display:flex;flex:1 1 auto;min-height:0;overflow:hidden'
  }, [ui.drawer, ui.main, ui.annotHint, ui.regionLayer, ui.status])
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
  ui.btnSearch.onclick = function () { ctx.toggleSearchPanel() }
  ui.drawerClose.onclick = function () { ctx.closeDrawer() }
  ui.searchGo.onclick = function () { ctx.runSearch(false) }
  ui.searchPrev.onclick = function () { ctx.stepResult(-1) }
  ui.searchNext.onclick = function () { ctx.stepResult(1) }
  ui.root.addEventListener('keydown', ctx.onViewerKeydown)
  state.domListeners.push({ target: ui.root, type: 'keydown', fn: ctx.onViewerKeydown })

  ui.btnRegion.onclick = function () { ctx.toggleRegionSelect() }
  ctx.syncRegionButton()

  ui.btnHighlight.onclick = function () { ctx.setAnnotMode('highlight') }
  ui.btnNote.onclick = function () { ctx.setAnnotMode('note') }
  ui.btnAnnotList.onclick = function () { ctx.toggleDrawer('annot') }
  ctx.onGlobal(ui.scroll, 'mousedown', ctx.onAnnotDown)
  ctx.onGlobal(ui.scroll, 'mouseup', ctx.onAnnotUp)
  ctx.renderAnnotPanel()
  ctx.syncAnnotButtons()

  ctx.syncPageButtons()
  ctx.syncMatchButtons()
  ctx.syncZoom()
}
