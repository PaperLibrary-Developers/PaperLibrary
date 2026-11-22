/* 文献详情页协调入口（子模块拆分在 views/detail/ 下，对外保持 { detailHandler } 不变） */
import * as util from '../util.mjs'
import { api } from '../api.mjs'
import { getState } from '../state/store.mjs'
import { go, savePrefs } from '../shell/app-api.mjs'
import { buildDetailHeaderAndMeta } from './detail/meta.mjs'
import { buildSummaryAndAi } from './detail/summary.mjs'
import { buildChatPanel } from './detail/chat.mjs'
import { buildPdfAndAttachments } from './detail/attachments.mjs'

const { h, confirmDialog } = util

async function detailHandler (ctx) {
  const { outlet, routeParams, isStale, signal } = ctx
  const id = routeParams.id
  const toast = (...args) => { if (!isStale()) util.toast(...args) }
  const timers = new Set()
  let readingOriginal = false

  function later (callback, delay) {
    if (isStale()) return null
    const timer = setTimeout(() => {
      timers.delete(timer)
      callback()
    }, delay)
    timers.add(timer)
    return timer
  }

  function cancelLater (timer) {
    clearTimeout(timer)
    timers.delete(timer)
  }

  function showOverlay (node, onClose = () => {}) {
    function close () {
      node.remove()
      document.removeEventListener('keydown', onKey, true)
      signal.removeEventListener('abort', close)
      onClose()
    }
    function onKey (ev) {
      if (ev.key !== 'Escape') return
      ev.stopImmediatePropagation()
      close()
    }
    document.body.appendChild(node)
    document.addEventListener('keydown', onKey, true)
    signal.addEventListener('abort', close, { once: true })
    return close
  }

  const confirm = options => confirmDialog({ ...options, signal })
  outlet.replaceChildren(h('div.content', h('div.empty', h('div.big', '正在打开文献…'))))

  let data
  try {
    data = await api.getPaper(id, signal)
  } catch (err) {
    if (isStale()) return
    outlet.replaceChildren(h('div.content', h('div.empty',
      h('div.big', '找不到这条文献'),
      h('p', { text: err.message }),
      h('button.btn', { type: 'button', onclick: () => { go('#/') } }, '← 回到书库')
    )))
    return
  }
  if (isStale()) return
  const paper = data.paper
  const pdfOk = data.pdfExists

  // 延迟绑定的跨模块回调（在所有面板构建完毕后立即就绪）
  let syncHeight = () => {}
  let summaryMod = null
  let chatMod = null
  let setReadMode = () => {}

  const pdfMod = buildPdfAndAttachments({
    paper,
    pdfOk,
    signal,
    isStale,
    toast,
    confirm,
    isReadingOriginal: () => readingOriginal,
    syncHeight: () => syncHeight(),
    onRegionSelected: (dataUrl, info) => chatMod?.openRegionAsk(dataUrl, info)
  })

  chatMod = buildChatPanel({
    paper,
    signal,
    isStale,
    toast,
    confirm,
    later,
    showOverlay,
    onFulltextReady: () => summaryMod?.markFulltextReady(),
    onSwitchToChat: () => setReadMode('chat')
  })

  summaryMod = buildSummaryAndAi({
    paper,
    ftInfo: data.fulltext || {},
    signal,
    isStale,
    toast,
    confirm,
    later,
    cancelLater,
    selectPdfTab: (file, focus) => pdfMod.selectPdfTab(file, focus),
    getActivePdfViewer: () => pdfMod.getActivePdfViewer(),
    isReadingOriginal: () => readingOriginal,
    setReadingOriginal: value => { readingOriginal = !!value },
    refreshChat: () => chatMod.refreshChat()
  })

  const { header, tagRow, meta } = buildDetailHeaderAndMeta({
    paper,
    pdfOk,
    signal,
    isStale,
    toast,
    confirm,
    later,
    cancelLater,
    showOverlay,
    onExportPdf: () => summaryMod.exportPdf()
  })

  signal.addEventListener('abort', () => {
    timers.forEach(clearTimeout)
    timers.clear()
    chatMod.cancelScroll()
    pdfMod.abortViewer()
  }, { once: true })

  /* ---------- 合并面板：AI 总结 / 与文献对话 ---------- */
  const tabSummary = h('button.read-tab.active', { type: 'button' }, 'AI 总结')
  const tabChat = h('button.read-tab', { type: 'button' }, '与文献对话')

  setReadMode = function (mode) {
    const chat = mode === 'chat'
    summaryMod.readViewSummary.hidden = chat
    chatMod.readViewChat.hidden = !chat
    summaryMod.aiPanelActions.hidden = chat
    tabSummary.classList.toggle('active', !chat)
    tabChat.classList.toggle('active', chat)
    if (chat) {
      chatMod.scrollChatToEnd()
      chatMod.focusInput()
    }
  }
  tabSummary.addEventListener('click', () => setReadMode('summary'))
  tabChat.addEventListener('click', () => setReadMode('chat'))

  const mergedPanel = h('section.panel', { id: 'read-panel' },
    h('div.panel-head',
      h('div.read-tabs', tabSummary, tabChat),
      summaryMod.aiPanelActions
    ),
    h('div.panel-body.read-panel-body', summaryMod.readViewSummary, chatMod.readViewChat)
  )

  /* ---------- 可拖动的分隔条（自己调 PDF 与总结的宽度） ---------- */
  const readGrid = h('div.read-grid')
  const resizer = h('div.read-resizer', {
    title: '按住左右拖动，可以调整 PDF 与右侧面板的宽度比例',
    role: 'separator',
    'aria-orientation': 'vertical',
    tabindex: '0',
    'aria-label': '调整 PDF 与右侧面板的宽度'
  })
  const savedRatio = Number(getState().prefs.readRatio)
  if (savedRatio >= 0.35 && savedRatio <= 0.78) {
    readGrid.style.setProperty('--read-ratio', String(savedRatio))
  }

  let dragStyles = null
  function onDragStart (ev) {
    if (dragStyles) return
    dragStyles = { userSelect: document.body.style.userSelect, cursor: document.body.style.cursor }
    resizer.classList.add('dragging')
    document.body.style.userSelect = 'none'
    document.body.style.cursor = 'col-resize'
    ev.preventDefault()
  }
  function onDragMove (ev) {
    if (!dragStyles) return
    const rect = readGrid.getBoundingClientRect()
    if (!rect.width) return
    const ratio = Math.min(0.78, Math.max(0.35, (ev.clientX - rect.left) / rect.width))
    readGrid.style.setProperty('--read-ratio', String(Math.round(ratio * 1000) / 1000))
  }
  function finishDrag (persist) {
    if (!dragStyles) return
    resizer.classList.remove('dragging')
    document.body.style.userSelect = dragStyles.userSelect
    document.body.style.cursor = dragStyles.cursor
    dragStyles = null
    const current = Number(readGrid.style.getPropertyValue('--read-ratio'))
    if (persist && Number.isFinite(current) && current > 0) {
      savePrefs({ readRatio: current }, { silent: true })
    }
  }
  resizer.addEventListener('mousedown', onDragStart)
  document.addEventListener('mousemove', onDragMove, { signal })
  document.addEventListener('mouseup', () => finishDrag(true), { signal })
  signal.addEventListener('abort', () => finishDrag(false), { once: true })
  resizer.addEventListener('keydown', ev => {
    const current = Number(readGrid.style.getPropertyValue('--read-ratio')) || 0.68
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') {
      ev.preventDefault()
      const next = Math.min(0.78, Math.max(0.35, current + (ev.key === 'ArrowLeft' ? -0.03 : 0.03)))
      readGrid.style.setProperty('--read-ratio', String(next))
      savePrefs({ readRatio: next }, { silent: true })
    }
  })

  /* ---------- 组装 ---------- */
  const readSide = h('div.read-side', mergedPanel, pdfMod.morePanel)
  readGrid.replaceChildren(pdfMod.pdfPanel, resizer, readSide)

  outlet.replaceChildren(h('div.content.detail',
    header,
    tagRow,
    summaryMod.summaryPanel,
    readGrid,
    meta,
    summaryMod.refsPanel
  ))

  syncHeight = function () {
    if (window.innerWidth <= 1200) {
      readSide.style.height = ''
      return
    }
    const height = pdfMod.pdfPanel.getBoundingClientRect().height || pdfMod.pdfPanel.offsetHeight
    if (height >= 160) readSide.style.height = Math.round(height) + 'px'
  }

  /* ---------- 启动内置阅读器并对齐高度 ---------- */
  pdfMod.selectPdfTab('')
  syncHeight()
  for (const delay of [120, 400, 1000, 2000]) later(syncHeight, delay)
  let resizeTimer = null
  window.addEventListener('resize', () => {
    cancelLater(resizeTimer)
    resizeTimer = later(syncHeight, 60)
  }, { signal })

  // 详情页快捷键：e 编辑，Esc 返回
  document.addEventListener('keydown', ev => {
    const tag = (ev.target.tagName || '').toLowerCase()
    if (ev.defaultPrevented || tag === 'input' || tag === 'textarea' || ev.target.isContentEditable) return
    if (ev.key === 'e') go('#/edit/' + encodeURIComponent(paper.id))
    if (ev.key === 'Escape') go('#/')
  }, { signal })
}

export { detailHandler }
