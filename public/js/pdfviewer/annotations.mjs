/* PDF 阅读器子模块：批注（荧光笔 / 便签）渲染、编辑气泡、右侧列表抽屉与调色盘 */
import {
  ANNOT_CUSTOM_MAX,
  ANNOT_DEFAULT_COLOR,
  ANNOT_FLASH_MS,
  ANNOT_HL_ALPHA,
  ANNOT_LAYER_CLASS,
  ANNOT_MAX_RECTS,
  ANNOT_MIN_DRAG,
  ANNOT_NOTE_MAX,
  ANNOT_QUOTE_MAX,
  annotId,
  annotPalette,
  annotationAnchorY,
  hexToRgba,
  inkOnColor,
  isBuiltinColor,
  isSameHighlight,
  normBox,
  normPoint,
  normalizeColor,
  nowStamp,
  pageAtPoint,
  pageAtRectCenter,
  pct,
  readCustomColors,
  sanitizeAnnot,
  writeAnnotColor,
  writeCustomColors
} from './annot-model.mjs'
import { annotIcon, clientRectOf, el, eventPoint, pageInfoOf, scrollPagePointIntoView } from './dom.mjs'

export function createAnnotationController (ctx) {
  const { state, ui, opts } = ctx

  function pageInfos () {
    const out = []
    const viewer = state.pdfViewer
    if (!viewer || typeof viewer.getPageView !== 'function') return out
    const total = Math.min(viewer.pagesCount || 0, 4000)
    for (let i = 0; i < total; i++) {
      let pageView = null
      try { pageView = viewer.getPageView(i) } catch (err) { pageView = null }
      const div = pageView && pageView.div
      if (!div) continue
      const rect = clientRectOf(div)
      if (!rect || rect.width <= 0 || rect.height <= 0) continue
      out.push({ pageNumber: i + 1, div: div, rect: rect })
    }
    return out
  }

  function findAnnotLayer (pageDiv) {
    if (!pageDiv) return null
    const children = pageDiv.children || []
    for (let i = children.length - 1; i >= 0; i--) {
      const node = children[i]
      if (node && node.classList && node.classList.contains(ANNOT_LAYER_CLASS)) return node
    }
    return null
  }

  function ensureAnnotLayer (pageDiv) {
    if (!pageDiv) return null
    const exist = findAnnotLayer(pageDiv)
    if (exist) return exist
    const layer = el('div.' + ANNOT_LAYER_CLASS, { 'aria-hidden': 'true' })
    try { pageDiv.appendChild(layer) } catch (err) { return null }
    return layer
  }

  function annotNodes (item) {
    const nodes = []
    const flash = !!state.annotFlashId && state.annotFlashId === item.id
    const color = normalizeColor(item.color) || ANNOT_DEFAULT_COLOR
    if (item.type === 'note') {
      const pt = item.point || { x: 0, y: 0 }
      const btn = el('button.lv-annot-note', {
        type: 'button',
        'data-annot-id': item.id,
        title: (item.note ? String(item.note).slice(0, 60) : '（空便签）') + ' · 点击查看 / 编辑',
        'aria-label': '便签：' + (item.note || '（空）')
      })
      btn.style.left = pct(pt.x)
      btn.style.top = pct(pt.y)
      btn.style.background = color
      btn.style.color = inkOnColor(color)
      btn.style.borderColor = hexToRgba(color, 0.45)
      if (flash) btn.classList.add('lv-annot-flash')
      btn.onclick = ev => {
        if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
        let anchor = null
        if (ev && (ev.clientX || ev.clientY)) anchor = { x: ev.clientX, y: ev.clientY }
        else {
          const rect = clientRectOf(btn)
          if (rect) anchor = { x: rect.left + rect.width / 2, y: rect.top + rect.height }
        }
        openAnnotPop(item, { anchor: anchor })
      }
      nodes.push(btn)
      return nodes
    }
    const rects = Array.isArray(item.rects) ? item.rects : []
    for (let i = 0; i < rects.length; i++) {
      const r = rects[i]
      if (!r) continue
      const mark = el('div.lv-annot-mark.lv-annot-hl', { 'data-annot-id': item.id })
      mark.style.left = pct(r.x)
      mark.style.top = pct(r.y)
      mark.style.width = pct(r.w)
      mark.style.height = pct(r.h)
      mark.style.background = hexToRgba(color, ANNOT_HL_ALPHA)
      if (flash) mark.classList.add('lv-annot-flash')
      nodes.push(mark)
    }
    return nodes
  }

  function paintPageAnnotations (pageNumber) {
    if (state.destroyed || state.tornDown) return
    const info = pageInfoOf(state.pdfViewer, pageNumber)
    if (!info) return
    const nodes = []
    for (let i = 0; i < state.annotations.length; i++) {
      const item = state.annotations[i]
      if (Number(item.page) !== Number(pageNumber)) continue
      const group = annotNodes(item)
      for (let k = 0; k < group.length; k++) nodes.push(group[k])
    }
    let layer = findAnnotLayer(info.div)
    if (!nodes.length && !layer) return
    if (!layer) layer = ensureAnnotLayer(info.div)
    if (!layer) return
    try { layer.replaceChildren.apply(layer, nodes) } catch (err) { layer.textContent = '' }
  }

  function paintAllAnnotations () {
    const infos = pageInfos()
    for (let i = 0; i < infos.length; i++) paintPageAnnotations(infos[i].pageNumber)
  }

  function findAnnotNode (id) {
    if (!id || !ui.viewer || typeof ui.viewer.querySelectorAll !== 'function') return null
    const nodes = ui.viewer.querySelectorAll('[data-annot-id]')
    for (let i = 0; i < nodes.length; i++) {
      if (nodes[i].getAttribute('data-annot-id') === id) return nodes[i]
    }
    return null
  }

  function emitAnnotations () {
    if (state.destroyed || state.tornDown) return
    const cb = opts.onAnnotationsChange
    if (typeof cb !== 'function') return
    try { cb(state.annotations.slice()) } catch (err) {}
  }

  function setAnnotations (items) {
    if (state.destroyed || state.tornDown) return
    const next = []
    const list = Array.isArray(items) ? items : []
    for (let i = 0; i < list.length; i++) {
      const item = sanitizeAnnot(list[i])
      if (item) next.push(item)
    }
    state.annotations = next
    paintAllAnnotations()
    renderAnnotPanel()
    syncAnnotCount()
  }

  function getAnnotations () {
    return state.annotations.slice()
  }

  function selectionSnapshot () {
    const sel = typeof window.getSelection === 'function' ? window.getSelection() : null
    if (!sel || !sel.rangeCount || sel.isCollapsed) return null
    const range = sel.getRangeAt(0)
    if (!range || typeof range.getClientRects !== 'function') return null
    const text = String(sel.toString() || '').replace(/\s+/g, ' ').trim()
    const list = range.getClientRects()
    const rects = []
    for (let i = 0; i < list.length && rects.length < ANNOT_MAX_RECTS; i++) {
      const r = list[i]
      if (!r || !(r.width > 1) || !(r.height > 1)) continue
      rects.push({ left: r.left, top: r.top, width: r.width, height: r.height })
    }
    if (!rects.length) return null
    return { text: text.slice(0, ANNOT_QUOTE_MAX), rects: rects }
  }

  function flashAnnotation (id, pageNumber) {
    state.annotFlashId = id
    if (state.annotFlashTimer) {
      try { clearTimeout(state.annotFlashTimer) } catch (err) {}
    }
    try { paintPageAnnotations(pageNumber) } catch (err) {}
    state.annotFlashTimer = setTimeout(() => {
      state.annotFlashTimer = 0
      if (state.destroyed || state.tornDown) return
      state.annotFlashId = ''
      try { paintPageAnnotations(pageNumber) } catch (err) {}
    }, ANNOT_FLASH_MS)
  }

  function createHighlightFromSelection () {
    const snap = selectionSnapshot()
    if (!snap) return false
    const infos = pageInfos()
    if (!infos.length) return false
    const groups = new Map()
    for (let i = 0; i < snap.rects.length; i++) {
      const target = pageAtRectCenter(infos, snap.rects[i])
      if (!target) continue
      const box = normBox(snap.rects[i], target.rect)
      if (!box) continue
      if (!groups.has(target.pageNumber)) groups.set(target.pageNumber, [])
      const bucket = groups.get(target.pageNumber)
      if (bucket.length < ANNOT_MAX_RECTS) bucket.push(box)
    }
    if (!groups.size) return false
    const stamp = nowStamp()
    const created = []
    let added = 0
    groups.forEach((rects, page) => {
      const item = {
        id: annotId(),
        type: 'highlight',
        page: page,
        color: state.annotColor,
        rects: rects,
        point: null,
        quote: snap.text,
        note: '',
        createdAt: stamp,
        updatedAt: stamp
      }
      const dup = state.annotations.find(other => other.type === 'highlight' && isSameHighlight(other, item))
      if (dup) {
        created.push(dup)
        return
      }
      state.annotations.push(item)
      added++
      created.push(item)
    })
    try {
      const sel = window.getSelection()
      if (sel && typeof sel.removeAllRanges === 'function') sel.removeAllRanges()
    } catch (err) {}
    for (let i = 0; i < created.length; i++) paintPageAnnotations(created[i].page)
    renderAnnotPanel()
    syncAnnotCount()
    if (added) emitAnnotations()
    if (created.length) flashAnnotation(created[0].id, created[0].page)
    return added > 0
  }

  function locateAnnotation (id) {
    const item = state.annotations.find(a => a.id === id)
    if (!item) return
    const positioned = scrollPagePointIntoView(state.pdfViewer, ui.scroll, item.page, annotationAnchorY(item), 0.3, 'smooth')
    if (!positioned) ctx.goToPage(item.page)
    paintPageAnnotations(item.page)
    flashAnnotation(item.id, item.page)
    if (state.annotPanelOpen) renderAnnotPanel()
  }

  function deleteAnnotation (id) {
    const index = state.annotations.findIndex(a => a.id === id)
    if (index < 0) return
    const removed = state.annotations.splice(index, 1)[0]
    closeAnnotPop({ keepChanges: state.annotPopId !== id })
    if (removed) paintPageAnnotations(removed.page)
    renderAnnotPanel()
    syncAnnotCount()
    emitAnnotations()
  }

  /* ---- 编辑气泡 ---- */
  function bindAnnotDoc (type, fn) {
    if (typeof document.addEventListener !== 'function') return
    document.addEventListener(type, fn, true)
    const item = { target: document, type: type, fn: fn, capture: true }
    state.annotPopDocListeners.push(item)
    state.domListeners.push(item)
  }

  function unbindAnnotDoc () {
    for (let i = 0; i < state.annotPopDocListeners.length; i++) {
      const item = state.annotPopDocListeners[i]
      try { item.target.removeEventListener(item.type, item.fn, true) } catch (err) {}
      const at = state.domListeners.indexOf(item)
      if (at >= 0) state.domListeners.splice(at, 1)
    }
    state.annotPopDocListeners.length = 0
  }

  function closeAnnotPop (options) {
    const o = options || {}
    const flush = state.annotPopFlush
    state.annotPopFlush = null
    if (flush && o.keepChanges !== false) {
      try { flush() } catch (err) {}
    }
    unbindAnnotDoc()
    const wrap = state.annotPop
    state.annotPop = null
    state.annotPopId = ''
    try {
      if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap)
    } catch (err) {}
  }

  function placeAnnotPop (pop, anchor) {
    if (!pop) return
    const margin = 8
    const vw = (typeof window !== 'undefined' && window.innerWidth) || 1024
    const vh = (typeof window !== 'undefined' && window.innerHeight) || 768
    const width = pop.offsetWidth || 306
    const height = pop.offsetHeight || 200
    const baseX = anchor ? anchor.x : vw - width - 28
    const baseY = anchor ? anchor.y : Math.round(vh / 3)
    let left = baseX - width / 2
    let top = baseY + 14
    left = Math.min(Math.max(margin, left), Math.max(margin, vw - width - margin))
    if (top + height > vh - margin) top = Math.max(margin, baseY - height - 14)
    pop.style.left = Math.round(left) + 'px'
    pop.style.top = Math.round(top) + 'px'
  }

  function openAnnotPop (item, options) {
    if (state.destroyed || state.tornDown || !item) return
    closeAnnotPop()
    const o = options || {}
    const isDraft = !!o.draft
    const draft = {
      id: item.id,
      type: item.type === 'note' ? 'note' : 'highlight',
      page: Math.max(1, Math.round(Number(item.page) || 1)),
      color: normalizeColor(item.color) || ANNOT_DEFAULT_COLOR,
      rects: Array.isArray(item.rects) ? item.rects.slice(0, ANNOT_MAX_RECTS) : [],
      point: item.point || null,
      quote: String(item.quote || '').slice(0, ANNOT_QUOTE_MAX),
      note: String(item.note || '').slice(0, ANNOT_NOTE_MAX),
      createdAt: item.createdAt || nowStamp(),
      updatedAt: item.updatedAt || ''
    }
    const isNote = draft.type === 'note'

    const wrap = el('div.lv-annot-popwrap')
    const pop = el('div.lv-annot-pop', {
      role: 'dialog',
      'aria-modal': 'false',
      'aria-label': isDraft ? (isNote ? '新建便签' : '编辑荧光笔') : '编辑批注'
    })

    const head = el('div.lv-annot-pop-head', null, [
      el('b', { text: isNote ? '便签' : '荧光笔' }),
      el('span', { text: '· 第 ' + draft.page + ' 页' }),
      el('span', { style: 'margin-left:auto', text: isDraft ? '新批注' : '自动保存' })
    ])
    pop.appendChild(head)

    if (draft.quote) {
      pop.appendChild(el('div.lv-annot-quote', { text: '“' + draft.quote + '”' }))
    }

    const ta = el('textarea.lv-annot-input', {
      rows: '3',
      placeholder: isNote ? '写点什么…（边写边自动保存）' : '备注（可留空，边写边自动保存）',
      'aria-label': isNote ? '便签内容' : '荧光笔备注'
    })
    ta.value = draft.note
    pop.appendChild(ta)

    const swatches = el('div.lv-annot-swatches')
    annotPalette(draft.type).forEach(hex => {
      const dot = el('button.lv-annot-swatch', {
        type: 'button',
        'data-color': hex,
        title: hex,
        'aria-label': '用颜色 ' + hex,
        'aria-pressed': hex === draft.color ? 'true' : 'false'
      })
      dot.style.background = hex
      if (hex === draft.color) dot.classList.add('active')
      dot.onclick = () => {
        draft.color = hex
        const all = swatches.children || []
        for (let i = 0; i < all.length; i++) {
          const on = all[i].getAttribute('data-color') === hex
          all[i].classList.toggle('active', on)
          all[i].setAttribute('aria-pressed', on ? 'true' : 'false')
        }
        applyChanges()
      }
      swatches.appendChild(dot)
    })

    const foot = el('div.lv-annot-pop-foot')
    let armed = false
    let armTimer = 0
    const del = el('button.lv-annot-btn.danger', { type: 'button', text: '删除' })
    del.onclick = () => {
      if (!armed) {
        armed = true
        del.classList.add('armed')
        del.textContent = '确认删除'
        if (armTimer) clearTimeout(armTimer)
        armTimer = setTimeout(() => {
          armed = false
          del.classList.remove('armed')
          del.textContent = '删除'
        }, 2600)
        return
      }
      if (!persisted) { closeAnnotPop({ keepChanges: false }); return }
      deleteAnnotation(draft.id)
    }
    foot.appendChild(del)
    foot.appendChild(swatches)

    const status = el('span.lv-annot-status', { text: isDraft ? '输入即自动保存' : '已自动保存' })
    if (!isDraft) status.classList.add('saved')
    const actions = el('div.right')
    actions.appendChild(status)
    const done = el('button.lv-annot-btn', { type: 'button', text: '完成', title: '关闭（内容已自动保存）' })
    done.onclick = () => closeAnnotPop()
    actions.appendChild(done)
    foot.appendChild(actions)

    let savedText = draft.note
    let savedColor = draft.color
    let persisted = !isDraft
    let saveTimer = 0

    function applyChanges () {
      if (state.destroyed || state.tornDown) return false
      const raw = String(ta.value || '').replace(/\r\n/g, '\n').slice(0, ANNOT_NOTE_MAX)
      const text = raw.trim()
      if (!persisted && !text) {
        status.textContent = '输入即自动保存'
        status.classList.remove('saved')
        return false
      }
      if (persisted && text === savedText && draft.color === savedColor) {
        status.textContent = '已自动保存'
        status.classList.add('saved')
        return false
      }
      const next = {
        id: draft.id,
        type: draft.type,
        page: draft.page,
        color: draft.color,
        rects: draft.rects,
        point: draft.type === 'note' ? draft.point : null,
        quote: draft.quote,
        note: text,
        createdAt: draft.createdAt,
        updatedAt: nowStamp()
      }
      const index = state.annotations.findIndex(a => a.id === next.id)
      if (index >= 0) state.annotations[index] = next
      else state.annotations.push(next)
      const firstSave = !persisted
      persisted = true
      savedText = text
      savedColor = next.color
      paintPageAnnotations(next.page)
      renderAnnotPanel()
      syncAnnotCount()
      emitAnnotations()
      status.textContent = '已自动保存'
      status.classList.add('saved')
      if (firstSave) flashAnnotation(next.id, next.page)
      return true
    }

    function flushNow () {
      if (saveTimer) {
        try { clearTimeout(saveTimer) } catch (err) {}
        saveTimer = 0
      }
      try { applyChanges() } catch (err) {}
    }
    state.annotPopFlush = flushNow

    ta.oninput = () => {
      status.textContent = '保存中…'
      status.classList.remove('saved')
      if (saveTimer) {
        try { clearTimeout(saveTimer) } catch (err) {}
      }
      saveTimer = setTimeout(() => {
        saveTimer = 0
        try { applyChanges() } catch (err) {}
      }, 500)
    }

    pop.appendChild(foot)
    wrap.appendChild(pop)
    wrap.onclick = ev => { if (ev.target === wrap) closeAnnotPop() }

    pop.style.visibility = 'hidden'
    document.body.appendChild(wrap)
    state.annotPop = wrap
    state.annotPopId = draft.id
    placeAnnotPop(pop, o.anchor || null)
    pop.style.visibility = ''

    ta.onkeydown = ev => {
      if (ev.key === 'Escape' || ev.key === 'Esc') {
        ev.preventDefault()
        ev.stopPropagation()
        closeAnnotPop()
        return
      }
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault()
        closeAnnotPop()
      }
    }
    bindAnnotDoc('keydown', ev => {
      if (ev.key !== 'Escape' && ev.key !== 'Esc') return
      if (state.annotPop !== wrap) return
      if (typeof ev.preventDefault === 'function') ev.preventDefault()
      closeAnnotPop()
    })
    try {
      ta.focus()
      const end = ta.value.length
      if (typeof ta.setSelectionRange === 'function') ta.setSelectionRange(end, end)
    } catch (err) {}
  }

  /* ---- 右侧批注抽屉 ---- */
  function syncAnnotCount () {
    if (!ui.btnAnnotList) return
    const total = state.annotations.length
    // 按钮本身是图标，条数走右上角的小角标（0 条时不显示）
    if (ui.annotBadge) {
      ui.annotBadge.textContent = total ? String(total) : ''
      ui.annotBadge.hidden = !total
    }
    ui.btnAnnotList.title = total
      ? '共 ' + total + ' 条批注（点击展开列表，再点可定位）'
      : '还没有批注：用「荧光笔」选文字，或用「便签」点页面'
    ui.btnAnnotList.setAttribute('aria-label', total ? '批注列表（' + total + ' 条）' : '批注列表')
  }

  function renderAnnotPanel () {
    if (!ui.annotList) return
    const list = state.annotations.slice().sort((a, b) => {
      const byPage = Number(a.page) - Number(b.page)
      if (byPage) return byPage
      return String(a.createdAt || '').localeCompare(String(b.createdAt || ''))
    })
    if (!list.length) {
      ui.annotList.replaceChildren(el('div.lv-annot-empty', {
        text: '还没有批注。\n用工具栏的「荧光笔」选中文字，\n或用「便签」在页面上点一下。'
      }))
      return
    }
    const rows = []
    list.forEach(item => {
      const quote = String(item.quote || '')
      const note = String(item.note || '')
      const kindLabel = item.type === 'note' ? '便签' : '荧光笔'
      const row = el('div.lv-annot-item', {
        'data-annot-id': item.id,
        'data-kind': item.type,
        title: '点击定位到第 ' + item.page + ' 页的这一处（' + kindLabel + '）'
      }, [
        el('div.meta', null, [
          el('div.lv-annot-page', { text: 'P' + item.page }),
          el('div.lv-annot-kind', { 'aria-hidden': 'true' }, [annotIcon(item.type, item.color)])
        ]),
        el('div.body', null, [
          quote ? el('div.lv-annot-quote-text', { text: quote }) : null,
          note ? el('div.lv-annot-note-text', { text: note }) : (quote ? null : el('div.lv-annot-note-text', { text: '（空）' }))
        ]),
        el('div.acts', null, [
          el('button.del', { type: 'button', text: '✎', title: '打开这条批注（可改备注 / 换颜色）', 'aria-label': '打开这条批注' }),
          el('button.del', { type: 'button', text: '✕', title: '删除这条批注', 'aria-label': '删除这条批注' })
        ])
      ])
      if (state.annotFlashId && state.annotFlashId === item.id) row.classList.add('active')
      row.onclick = () => locateAnnotation(item.id)

      const acts = row.lastChild
      const editBtn = acts.children[0]
      const del = acts.children[1]
      editBtn.onclick = ev => {
        if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
        const node = findAnnotNode(item.id)
        const rect = node ? clientRectOf(node) : null
        openAnnotPop(item, {
          anchor: rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height } : null
        })
      }
      let armed = false
      del.onclick = ev => {
        if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
        if (armed) { deleteAnnotation(item.id); return }
        armed = true
        del.textContent = '✕?'
        del.style.color = 'var(--danger, #b4453a)'
        setTimeout(() => {
          if (!del.parentNode) return
          armed = false
          del.textContent = '✕'
          del.style.color = ''
        }, 2600)
      }
      rows.push(row)
    })
    ui.annotList.replaceChildren.apply(ui.annotList, rows)
  }

  /* 批注列表和搜索结果共用左侧同一个抽屉：这里只负责请求打开/关闭 */
  function showAnnotPanel (on) {
    state.annotPanelOpen = !!on
    if (state.annotPanelOpen) {
      if (typeof ctx.openDrawer === 'function') ctx.openDrawer('annot')
      else renderAnnotPanel()
    } else if (state.drawer === 'annot' && typeof ctx.closeDrawer === 'function') {
      ctx.closeDrawer()
    }
  }

/* ---- 标注模式与调色盘 ---- */
  function syncAnnotIcon (host, kind) {
    if (!host) return
    if (host.getAttribute('data-color') === state.annotColor && host.firstChild) return
    host.setAttribute('data-color', state.annotColor)
    try { host.replaceChildren(annotIcon(kind, state.annotColor)) } catch (err) {}
  }

  function syncAnnotButtons () {
    if (ui.btnHighlight) {
      const on = state.annotMode === 'highlight'
      ui.btnHighlight.classList.toggle('active', on)
      ui.btnHighlight.setAttribute('aria-pressed', on ? 'true' : 'false')
    }
    if (ui.btnNote) {
      const on = state.annotMode === 'note'
      ui.btnNote.classList.toggle('active', on)
      ui.btnNote.setAttribute('aria-pressed', on ? 'true' : 'false')
    }
    syncAnnotIcon(ui.highlightIcon, 'highlight')
    syncAnnotIcon(ui.noteIcon, 'note')
    syncAnnotCount()
  }

  function setAnnotMode (mode) {
    const want = mode === 'highlight' || mode === 'note' ? mode : null
    state.annotMode = want && want === state.annotMode ? null : want
    state.annotPress = null
    if (ui.root) {
      ui.root.classList.toggle('lv-annot-picking', state.annotMode === 'highlight')
      ui.root.classList.toggle('lv-annot-pinning', state.annotMode === 'note')
    }
    if (ui.annotHint) {
      ui.annotHint.hidden = !state.annotMode
      if (state.annotMode === 'highlight') ui.annotHint.textContent = '荧光笔：在 PDF 上选中文字即可高亮（Esc 退出）'
      else if (state.annotMode === 'note') ui.annotHint.textContent = '便签：在 PDF 上点一下放便签（Esc 退出）'
    }
    syncAnnotButtons()
    if (state.annotMode) togglePalette(true)
    else togglePalette(false)
    if (!state.annotMode) closeAnnotPop()
  }

  function setAnnotColor (hex) {
    const next = normalizeColor(hex) || ANNOT_DEFAULT_COLOR
    state.annotColor = next
    writeAnnotColor(next)          // 取色记忆：下次打开阅读器还用这个颜色
    syncAnnotButtons()
    if (state.paletteOpen) renderPalette()
  }

  function addCustomColor (hex) {
    const value = normalizeColor(hex)
    if (!value) return false
    const list = readCustomColors()
    if (list.indexOf(value) >= 0) return true
    if (list.length >= ANNOT_CUSTOM_MAX) list.shift()
    list.push(value)
    writeCustomColors(list)
    return true
  }

  function removeCustomColor (hex) {
    const value = normalizeColor(hex)
    writeCustomColors(readCustomColors().filter(item => item !== value))
    if (state.annotColor === value) setAnnotColor(ANNOT_DEFAULT_COLOR)
    renderPalette()
  }

  /* 取色条：一排圆点 + 末尾的自定义颜色。选完不收起——笔还开着，
     继续换色是常态（GoodNotes 同样是「工具常驻、颜色随手换」）。 */
  function renderPalette () {
    if (!ui.palette) return
    const colors = annotPalette()
    const customCount = readCustomColors().length
    const nodes = colors.map(function (hex) {
      const custom = !isBuiltinColor(hex)
      const dot = el('button.lv-ink-dot', {
        type: 'button',
        'data-color': hex,
        title: custom ? hex + '（自定义，点右上角 × 可删除）' : hex,
        'aria-label': '颜色 ' + hex,
        'aria-pressed': hex === state.annotColor ? 'true' : 'false'
      })
      dot.style.background = hex
      if (hex === state.annotColor) dot.classList.add('active')
      if (custom) {
        dot.classList.add('custom')
        const remove = el('span.lv-ink-del', { text: '×', 'aria-hidden': 'true' })
        remove.onclick = ev => {
          if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
          removeCustomColor(hex)
        }
        dot.appendChild(remove)
      }
      dot.onclick = () => setAnnotColor(hex)
      return dot
    })
    const full = customCount >= ANNOT_CUSTOM_MAX
    const add = el('button.lv-ink-add', {
      type: 'button', text: '＋',
      title: full ? ('自定义颜色最多 ' + ANNOT_CUSTOM_MAX + ' 个，先删一个再加') : '加一个自己的颜色',
      disabled: full ? true : null,
      'aria-label': '添加自定义颜色'
    })
    add.onclick = () => {
      if (full) return
      try { ui.colorInput.value = state.annotColor } catch (err) {}
      try { ui.colorInput.click() } catch (err) {}
    }
    nodes.push(add)
    ui.palette.replaceChildren.apply(ui.palette, nodes)
  }

  function placePalette () {
    if (!ui.palette) return
    const rect = clientRectOf(ui.btnHighlight)
    const base = clientRectOf(ui.toolbar)
    if (!rect || !base) return
    // 贴着整个工具栏的下沿浮在正文上（工具栏可能换行，按它的实际高度算），
    // 横向和「荧光笔」按钮对齐再夹进可视范围
    const width = ui.palette.offsetWidth || 274
    const maxLeft = Math.max(4, base.width - width - 4)
    const left = Math.min(Math.max(4, rect.left - base.left - 10), maxLeft)
    ui.palette.style.left = Math.round(left) + 'px'
    ui.palette.style.top = Math.round(base.height + 6) + 'px'
  }

  function bindPaletteDoc () {
    if (typeof document.addEventListener !== 'function') return
    const onDown = ev => {
      const target = ev && ev.target
      if (!target) return
      if (ui.palette && ui.palette.contains(target)) return
      if (ui.btnHighlight && ui.btnHighlight.contains(target)) return
      if (ui.btnNote && ui.btnNote.contains(target)) return
      closePalette()
    }
    const onKey = ev => {
      if (ev && (ev.key === 'Escape' || ev.key === 'Esc')) closePalette()
    }
    document.addEventListener('mousedown', onDown, true)
    document.addEventListener('keydown', onKey, true)
    const items = [
      { target: document, type: 'mousedown', fn: onDown, capture: true },
      { target: document, type: 'keydown', fn: onKey, capture: true }
    ]
    for (let i = 0; i < items.length; i++) {
      state.paletteListeners.push(items[i])
      state.domListeners.push(items[i])
    }
  }

  function unbindPaletteDoc () {
    for (let i = 0; i < state.paletteListeners.length; i++) {
      const item = state.paletteListeners[i]
      try { item.target.removeEventListener(item.type, item.fn, true) } catch (err) {}
      const at = state.domListeners.indexOf(item)
      if (at >= 0) state.domListeners.splice(at, 1)
    }
    state.paletteListeners.length = 0
  }

  function togglePalette (force) {
    if (state.destroyed || state.tornDown) return
    const open = force === undefined ? !state.paletteOpen : !!force
    if (!open) { closePalette(); return }
    if (state.paletteOpen) return
    state.paletteOpen = true
    renderPalette()
    if (ui.palette) {
      ui.palette.hidden = false
      ui.palette.classList.add('is-open')
    }
    placePalette()
    if (ui.btnHighlight) ui.btnHighlight.setAttribute('aria-expanded', 'true')
    if (ui.btnNote) ui.btnNote.setAttribute('aria-expanded', 'true')
    bindPaletteDoc()
  }

  function closePalette () {
    if (!state.paletteOpen) return
    state.paletteOpen = false
    if (ui.palette) {
      ui.palette.classList.remove('is-open')
      ui.palette.hidden = true
    }
    if (ui.btnHighlight) ui.btnHighlight.setAttribute('aria-expanded', 'false')
    if (ui.btnNote) ui.btnNote.setAttribute('aria-expanded', 'false')
    unbindPaletteDoc()
  }  function onAnnotDown (ev) {
    if (state.destroyed || !state.annotMode) return
    if (ev.button !== undefined && ev.button !== 0) return
    if (state.annotMode !== 'note') { state.annotPress = null; return }
    const pt = eventPoint(ev)
    state.annotPress = pt ? { x: pt.x, y: pt.y, snap: selectionSnapshot() } : null
  }

  function onAnnotUp (ev) {
    if (state.destroyed || !state.annotMode) return
    const target = ev && ev.target
    if (target && typeof target.closest === 'function' && target.closest('.lv-annot-note')) {
      state.annotPress = null
      return
    }
    if (state.annotMode === 'highlight') {
      createHighlightFromSelection()
      return
    }
    const press = state.annotPress
    state.annotPress = null
    const pt = eventPoint(ev)
    if (!pt) return
    if (press && (Math.abs(pt.x - press.x) > ANNOT_MIN_DRAG || Math.abs(pt.y - press.y) > ANNOT_MIN_DRAG)) return
    const infos = pageInfos()
    const page = pageAtPoint(infos, pt.x, pt.y)
    if (!page) return
    const point = normPoint(pt, page.rect)
    if (!point) return
    openAnnotPop({
      id: annotId(),
      type: 'note',
      page: page.pageNumber,
      color: state.annotColor,
      rects: [],
      point: point,
      quote: press && press.snap ? press.snap.text : '',
      note: '',
      createdAt: nowStamp(),
      updatedAt: ''
    }, { draft: true, anchor: pt })
  }

  function onPageRendered (evt) {
    if (state.destroyed || state.tornDown || !evt) return
    const pageNumber = Math.round(Number(evt.pageNumber))
    if (!(pageNumber > 0)) return
    paintPageAnnotations(pageNumber)
  }

  return {
    paintPageAnnotations,
    paintAllAnnotations,
    setAnnotations,
    getAnnotations,
    locateAnnotation,
    deleteAnnotation,
    closeAnnotPop,
    openAnnotPop,
    renderAnnotPanel,
    showAnnotPanel,
    syncAnnotCount,
    setAnnotMode,
    syncAnnotButtons,
    setAnnotColor,
    addCustomColor,
    renderPalette,
    togglePalette,
    closePalette,
    onAnnotDown,
    onAnnotUp,
    onPageRendered
  }
}
