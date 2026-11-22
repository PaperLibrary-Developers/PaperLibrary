/* PDF 阅读器子模块：页内全文搜索与键盘快捷键 */
import { FALLBACK_FIND_STATE } from './loader.mjs'
import { isEditableTarget } from './dom.mjs'

export function createSearchController (ctx) {
  const { state, ui } = ctx

  function setCount (text) {
    if (ui.count) ui.count.textContent = text || ''
  }

  function dispatchFind (type, findPrevious) {
    const bus = state.eventBus
    if (!bus) return
    try {
      bus.dispatch('find', {
        source: state,
        type: type || '',
        query: state.matchQuery || '',
        caseSensitive: false,
        entireWord: false,
        highlightAll: true,
        findPrevious: !!findPrevious,
        matchDiacritics: false
      })
    } catch (err) {}
  }

  function syncMatchButtons () {
    const ready = !!state.matchQuery && !!state.pdfViewer
    if (ui.btnPrevMatch) ui.btnPrevMatch.disabled = !ready
    if (ui.btnNextMatch) ui.btnNextMatch.disabled = !ready
  }

  function clearSearch () {
    const had = !!state.matchQuery
    state.matchQuery = ''
    if (ui.searchInput) ui.searchInput.value = ''
    setCount('')
    syncMatchButtons()
    if (had && state.eventBus) {
      try { state.eventBus.dispatch('findbarclose', { source: state }) } catch (err) {}
    }
  }

  function runSearch (findPrevious) {
    const raw = ui.searchInput ? String(ui.searchInput.value) : ''
    if (!raw.trim()) { clearSearch(); return }
    state.matchQuery = raw
    setCount('搜索中…')
    syncMatchButtons()
    dispatchFind('', !!findPrevious)
  }

  function findAgain (findPrevious) {
    if (!state.matchQuery) return
    dispatchFind('again', !!findPrevious)
  }

  function onMatchesCount (evt) {
    if (state.destroyed || !state.matchQuery) return
    const counts = (evt && evt.matchesCount) || {}
    const total = Number(counts.total) || 0
    const current = Number(counts.current) || 0
    if (!total) { setCount('搜索中…'); return }
    setCount(current > 0 ? '第 ' + current + ' / 共 ' + total + ' 处' : '共 ' + total + ' 处')
  }

  function onFindControlState (evt) {
    if (state.destroyed || !state.matchQuery) return
    const findState = state.findState || FALLBACK_FIND_STATE
    const value = evt && evt.state
    if (value === findState.NOT_FOUND) setCount('没有找到匹配')
    else if (value === findState.PENDING && ui.count && !ui.count.textContent) setCount('搜索中…')
  }

  function onViewerKeydown (ev) {
    if (state.destroyed || ev.defaultPrevented) return
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
    } else if (ev.key === 'Escape') {
      ev.preventDefault()
      ev.stopPropagation()
      clearSearch()
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
    onSearchKeydown
  }
}
