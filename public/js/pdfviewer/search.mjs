/* PDF 阅读器子模块：右侧搜索栏（Word 导航式结果列表）、页内高亮与键盘快捷键 */
import { FALLBACK_FIND_STATE } from './loader.mjs'
import { el, isEditableTarget, scrollPagePointIntoView } from './dom.mjs'

const MAX_RESULTS = 300
const MAX_PAGES = 600
const SNIPPET_BEFORE = 30
const SNIPPET_AFTER = 46
/* 命中结果落在视口高度的这个位置（上面留出上下文，参考 Word 导航的观感） */
const RESULT_VIEW_OFFSET = 0.28
/* 命中后在这几个时间点再校正一次：pdf.js 会在文本层渲染完成后把滚动位置重置回页顶 */
const JUMP_GUARD_DELAYS = [140, 380, 780]

export function createSearchController (ctx) {
  const { state, ui } = ctx

  let token = 0            // 每次搜索自增；异步扫描用它判断结果是否过期
  let results = []
  let activeIndex = -1
  let lastQuery = ''
  let scanning = false
  let jumpTimers = []
  let jumpRelease = null

  /* ---------- 搜索：和批注列表共用左侧抽屉，这里只切到「搜索」这一页 ---------- */
  function openSearchPanel () {
    if (state.destroyed || state.tornDown) return
    if (typeof ctx.openDrawer === 'function') ctx.openDrawer('search')
    setTimeout(function () {
      if (state.destroyed || state.drawer !== 'search') return
      try {
        ui.searchInput.focus({ preventScroll: true })
        if (ui.searchInput.value) ui.searchInput.select()
      } catch (err) {}
    }, 90)
  }

  function closeSearchPanel () {
    if (state.drawer !== 'search') return
    // 收起时清掉页内的黄色高亮（查询词保留，下次打开还在输入框里）
    if (state.matchQuery && state.eventBus) {
      try { state.eventBus.dispatch('findbarclose', { source: state }) } catch (err) {}
    }
    if (typeof ctx.closeDrawer === 'function') ctx.closeDrawer()
    try { ui.searchInput.blur() } catch (err) {}
  }

  function toggleSearchPanel () {
    if (state.drawer === 'search') closeSearchPanel()
    else openSearchPanel()
  }

  /* ---------- 计数与结果列表 ---------- */
  function setCount (text) {
    if (ui.searchCount) ui.searchCount.textContent = text || ''
  }

  function syncMatchButtons () {
    const ready = results.length > 0
    if (ui.searchPrev) ui.searchPrev.disabled = !ready
    if (ui.searchNext) ui.searchNext.disabled = !ready
  }

  function paintActive () {
    if (!ui.searchList) return
    const rows = ui.searchList.children || []
    for (let i = 0; i < rows.length; i++) {
      const on = i === activeIndex
      rows[i].classList.toggle('is-active', on)
      rows[i].setAttribute('aria-selected', on ? 'true' : 'false')
    }
    if (activeIndex >= 0 && results[activeIndex]) {
      setCount('第 ' + (activeIndex + 1) + ' 个结果，共 ' + results.length + ' 个结果')
    }
  }

  function renderEmpty (text) {
    if (!ui.searchList) return
    ui.searchList.replaceChildren(el('div.lv-pdf-search-empty', { text: text }))
  }

  function renderResults () {
    if (!ui.searchList) return
    if (!results.length) { renderEmpty('没有找到「' + lastQuery + '」'); return }
    const rows = results.map(function (item, index) {
      const text = el('span.lv-pdf-hit-text', null, [
        item.before ? item.before : null,
        el('mark', { text: item.match }),
        item.after ? item.after : null
      ])
      return el('button.lv-pdf-hit', {
        type: 'button', 'data-idx': String(index), role: 'option',
        'data-page': String(item.page), 'data-y': String(Math.round(item.y * 1000) / 1000),
        'aria-selected': 'false',
        title: '跳到第 ' + item.page + ' 页的这处匹配'
      }, [
        el('span.lv-pdf-hit-page', { text: 'P' + item.page }),
        text
      ])
    })
    ui.searchList.replaceChildren.apply(ui.searchList, rows)
    for (let i = 0; i < ui.searchList.children.length; i++) {
      const row = ui.searchList.children[i]
      row.onclick = function () { goToResult(Number(row.getAttribute('data-idx'))) }
    }
    paintActive()
  }

  /* 跳到某条结果：pdf.js 在文本层渲染完成后会把滚动位置重置回「当前页顶部」，
     所以命中后短时间内再校正两次，位置才不会被打回去；用户一自己滚就立刻放手。 */
  function clearJumpGuard () {
    for (let i = 0; i < jumpTimers.length; i++) clearTimeout(jumpTimers[i])
    jumpTimers = []
    if (jumpRelease) { jumpRelease(); jumpRelease = null }
  }

  function startJumpGuard (item) {
    clearJumpGuard()
    const y = Number.isFinite(item.y) ? item.y : 0.1
    const apply = function () {
      if (state.destroyed || state.tornDown) { clearJumpGuard(); return }
      scrollPagePointIntoView(state.pdfViewer, ui.scroll, item.page, y, RESULT_VIEW_OFFSET, 'auto')
    }
    jumpTimers = JUMP_GUARD_DELAYS.map(function (delay) { return setTimeout(apply, delay) })
    if (!ui.scroll || typeof ui.scroll.addEventListener !== 'function') return
    const release = function () { clearJumpGuard() }
    ui.scroll.addEventListener('wheel', release, { passive: true, once: true })
    ui.scroll.addEventListener('touchstart', release, { passive: true, once: true })
    ui.scroll.addEventListener('keydown', release, { once: true })
    jumpRelease = function () {
      try {
        ui.scroll.removeEventListener('wheel', release)
        ui.scroll.removeEventListener('touchstart', release)
        ui.scroll.removeEventListener('keydown', release)
      } catch (err) {}
    }
  }

  function goToResult (index) {
    const item = results[index]
    if (!item) return
    activeIndex = index
    paintActive()
    const y = Number.isFinite(item.y) ? item.y : 0.1
    const ok = scrollPagePointIntoView(state.pdfViewer, ui.scroll, item.page, y, RESULT_VIEW_OFFSET, 'auto')
    if (!ok) ctx.goToPage(item.page)
    startJumpGuard(item)
  }

  function cancelJumpGuard () {
    clearJumpGuard()
  }

  function stepResult (delta) {
    if (!results.length) return
    const next = activeIndex < 0
      ? (delta > 0 ? 0 : results.length - 1)
      : (activeIndex + delta + results.length) % results.length
    goToResult(next)
  }

  /* ---------- 正文扫描：给结果列表准备页码、页内位置与上下文 ---------- */
  function snippetOf (text, at, len) {
    const from = Math.max(0, at - SNIPPET_BEFORE)
    const to = Math.min(text.length, at + len + SNIPPET_AFTER)
    return {
      before: (from > 0 ? '…' : '') + text.slice(from, at).replace(/\s+/g, ' '),
      match: text.slice(at, at + len).replace(/\s+/g, ' '),
      after: text.slice(at + len, to).replace(/\s+/g, ' ') + (to < text.length ? '…' : '')
    }
  }

  function pageTextIndex (content) {
    const spans = []
    let text = ''
    const items = (content && content.items) || []
    for (let i = 0; i < items.length; i++) {
      const item = items[i]
      if (!item || !item.str) continue
      spans.push({ start: text.length, end: text.length + item.str.length, t: item.transform })
      text += item.str
      if (item.hasEOL) text += ' '
    }
    return { text: text, spans: spans }
  }

  async function scanPage (doc, pageNumber, needle) {
    const page = await doc.getPage(pageNumber)
    const content = await page.getTextContent()
    const viewport = page.getViewport({ scale: 1 })
    const index = pageTextIndex(content)
    const hay = index.text.toLocaleLowerCase()
    if (!hay || hay.indexOf(needle) < 0) return []
    const out = []
    let from = 0
    while (from <= hay.length) {
      const at = hay.indexOf(needle, from)
      if (at < 0) break
      // 匹配落在哪个文字块上：用它把结果定位到页内的纵向位置
      let span = null
      for (let i = 0; i < index.spans.length; i++) {
        const s = index.spans[i]
        if (at >= s.start && at < s.end) { span = s; break }
        if (s.start > at) { span = s; break }
      }
      let y = 0.08
      if (span && span.t) {
        try {
          const point = viewport.convertToViewportPoint(span.t[4], span.t[5])
          const ratio = point[1] / viewport.height
          if (Number.isFinite(ratio)) y = Math.max(0, Math.min(1, ratio))
        } catch (err) {}
      }
      const parts = snippetOf(index.text, at, needle.length)
      out.push({ page: pageNumber, y: y, before: parts.before, match: parts.match, after: parts.after })
      from = at + Math.max(1, needle.length)
      if (out.length >= MAX_RESULTS) break
    }
    return out
  }

  async function scanDocument (query, myToken) {
    const doc = state.pdfDocument
    if (!doc) return []
    const total = Math.min(doc.numPages || 0, MAX_PAGES)
    const needle = query.toLocaleLowerCase()
    const all = []
    for (let p = 1; p <= total; p++) {
      if (state.destroyed || state.tornDown || myToken !== token) return all
      try {
        const found = await scanPage(doc, p, needle)
        for (let i = 0; i < found.length; i++) all.push(found[i])
      } catch (err) {
        // 单页取文字失败（损坏页 / 渲染中）就跳过，不影响其它页
      }
      if (all.length >= MAX_RESULTS) break
      // 每几页让出一次主线程，长文档扫描时界面不卡
      if (p % 4 === 0) await new Promise(function (resolve) { setTimeout(resolve, 0) })
    }
    return all.slice(0, MAX_RESULTS)
  }

  /* ---------- 对外动作 ---------- */
  function clearResults (message) {
    token++
    results = []
    activeIndex = -1
    scanning = false
    syncMatchButtons()
    renderEmpty(message || '输入关键词后回车')
  }

  function clearSearch () {
    const had = !!state.matchQuery
    state.matchQuery = ''
    lastQuery = ''
    if (ui.searchInput) ui.searchInput.value = ''
    clearResults('输入关键词后回车')
    if (had && state.eventBus) {
      try { state.eventBus.dispatch('findbarclose', { source: state }) } catch (err) {}
    }
  }

  /* pdf.js 命中后会自己把匹配滚到「视口顶部」（内部 scrollMatchIntoView），
     会和我们要的「放在视口上方 1/3 处」打架，而且它是在页面渲染完才异步触发，
     清内部标志位赶不上。这里把这个方法一次性置空，滚动位置完全由
     scrollPagePointIntoView 决定。 */
  function silenceNativeMatchScroll () {
    const fc = state.findController
    if (!fc || fc.__paperlibOwnScroll) return
    try {
      fc.__paperlibOwnScroll = true
      fc.scrollMatchIntoView = function () {}
    } catch (err) {}
  }

  function dispatchFind (findPrevious, highlightAll) {
    const bus = state.eventBus
    if (!bus) return
    try {
      bus.dispatch('find', {
        source: state,
        type: '',
        query: state.matchQuery || '',
        caseSensitive: false,
        entireWord: false,
        highlightAll: highlightAll !== false,
        findPrevious: !!findPrevious,
        matchDiacritics: false
      })
      silenceNativeMatchScroll()
    } catch (err) {}
  }

  async function runSearch (findPrevious) {
    const raw = ui.searchInput ? String(ui.searchInput.value) : ''
    const query = raw.trim()
    if (!query) { clearSearch(); return }
    // 关键词没变：回车=下一个、Shift+回车=上一个（和 Word 的导航一致）
    if (query === lastQuery && results.length && !scanning) {
      stepResult(findPrevious ? -1 : 1)
      return
    }
    const myToken = ++token
    lastQuery = query
    state.matchQuery = query
    results = []
    activeIndex = -1
    scanning = true
    syncMatchButtons()
    renderEmpty('正在搜索…')
    setCount('正在搜索…')
    dispatchFind(false)
    const found = await scanDocument(query, myToken)
    if (state.destroyed || state.tornDown || myToken !== token) return
    scanning = false
    results = found
    if (results.length) {
      activeIndex = 0
      renderResults()
      syncMatchButtons()
      goToResult(0)
      if (results.length >= MAX_RESULTS) setCount('前 ' + results.length + ' 个结果（已截断）')
    } else {
      activeIndex = -1
      renderResults()
      syncMatchButtons()
      setCount('没有找到「' + query + '」')
    }
  }

  function findAgain (findPrevious) {
    if (state.matchQuery) dispatchFind(findPrevious)
  }

  /* pdf.js 自己的计数：我们自己的扫描还没出结果时先用它给个即时反馈 */
  function onMatchesCount (evt) {
    if (state.destroyed || !state.matchQuery || !scanning) return
    const counts = (evt && evt.matchesCount) || {}
    const total = Number(counts.total) || 0
    if (total > 0) setCount('共 ' + total + ' 处，正在整理结果…')
  }

  function onFindControlState (evt) {
    if (state.destroyed || !state.matchQuery || !scanning) return
    const findState = state.findState || FALLBACK_FIND_STATE
    if (evt && evt.state === findState.NOT_FOUND) setCount('没有找到匹配')
  }

  /* ---------- 键盘 ---------- */
  function onViewerKeydown (ev) {
    if (state.destroyed || ev.defaultPrevented) return
    if ((ev.ctrlKey || ev.metaKey) && !ev.altKey && (ev.key === 'f' || ev.key === 'F')) {
      ev.preventDefault()
      openSearchPanel()
      return
    }
    if (state.regionSelect) {
      if (ev.key === 'Escape' || ev.key === 'Esc') {
        ev.preventDefault()
        ev.stopPropagation()
        ctx.cancelRegionSelect()
      }
      return
    }
    if (state.annotMode && (ev.key === 'Escape' || ev.key === 'Esc')) {
      ev.preventDefault()
      ev.stopPropagation()
      ctx.setAnnotMode(null)
      return
    }
    /* 侧栏开着时 Esc 先收侧栏（批注/搜索都算），并拦住事件——
       详情页在 document 上还有「Esc 返回列表」，不拦住会直接跳走。 */
    if ((ev.key === 'Escape' || ev.key === 'Esc') && state.drawer) {
      ev.preventDefault()
      ev.stopPropagation()
      if (typeof ctx.closeDrawer === 'function') ctx.closeDrawer()
      return
    }
    if (ev.ctrlKey || ev.metaKey || ev.altKey) return
    if (isEditableTarget(ev.target)) return
    if (ev.key === 'h' || ev.key === 'H') { ev.preventDefault(); ctx.setAnnotMode('highlight'); return }
    if (ev.key === 'n' || ev.key === 'N') { ev.preventDefault(); ctx.setAnnotMode('note'); return }
    switch (ev.key) {
      case 'PageUp': ev.preventDefault(); ctx.stepPage(-1); break
      case 'PageDown': ev.preventDefault(); ctx.stepPage(1); break
      case 'ArrowUp': ev.preventDefault(); ctx.stepPage(-1); break
      case 'ArrowDown': ev.preventDefault(); ctx.stepPage(1); break
      case 'Home':
        ev.preventDefault()
        ctx.jumpToPage(1)
        break
      case 'End':
        ev.preventDefault()
        ctx.jumpToPage(state.pdfViewer ? state.pdfViewer.pagesCount : 1)
        break
      default:
        break
    }
  }

  function onPageInputKeydown (ev) {
    if (ev.key !== 'Enter') return
    ev.preventDefault()
    ev.stopPropagation()
    ctx.jumpToPage(ui.pageInput ? ui.pageInput.value : 1)
  }

  function onSearchKeydown (ev) {
    if (ev.key === 'Enter') {
      ev.preventDefault()
      ev.stopPropagation()
      runSearch(ev.shiftKey)
    } else if (ev.key === 'Escape' || ev.key === 'Esc') {
      ev.preventDefault()
      ev.stopPropagation()
      closeSearchPanel()
    }
  }

  return {
    clearSearch,
    runSearch,
    findAgain,
    syncMatchButtons,
    onMatchesCount,
    onFindControlState,
    onViewerKeydown,
    onPageInputKeydown,
    onSearchKeydown,
    openSearchPanel,
    closeSearchPanel,
    toggleSearchPanel,
    stepResult,
    cancelJumpGuard
  }
}
