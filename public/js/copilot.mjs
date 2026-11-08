/* 侧栏里的 PaperHub Copilot（智能问答助手）。

   设计取舍：
   · 常驻侧栏、可折叠：收起时只占两行（标题 + 实时库藏），展开后在栏内滚动，
     不挤占主内容区，也不像浮层那样盖住列表。
   · 双引擎由后端决定：没配 Key 走离线规则（系统操作 / 检索技巧 / 引文导出 / PDF 阅读 /
     库藏统计），配了 Key 走在线模型，模型调用失败时后端自动降级并回一帧离线答案。
     前端只按 start / done 帧里的 engine 显示状态，不做二次判断。
   · 会话放模块级：侧栏只在启动时渲染一次，切页不会重建，历史自然保留；刷新才清空。
   · 每次增量只改当前气泡的 innerHTML，不整块重绘——流式回答才跟得上。 */

import * as util from './util.mjs'
import * as md from './markdown.mjs'
import { api } from './api.mjs'
import { go, lookupTitle } from './shell/app-api.mjs'
import { getState, subscribe } from './state/store.mjs'

const { h, toast } = util

/** 一键提问：四个入口覆盖导入 / 图表 / 引文 / 库藏，都是新手真会问的 */
const CHIPS = [
  { icon: '📥', label: '导入文献', q: '怎么导入下载好的 PDF 文献？' },
  { icon: '🔎', label: '搜图表', q: '怎么在库里检索实验图表，比如 CV 曲线？' },
  { icon: '📤', label: '导出 BibTeX', q: '如何导出 BibTeX 引用？' },
  { icon: '📊', label: '库藏概况', q: '当前文献库的库藏数据概况如何？' }
]

const OPEN_KEY = 'lv.copilotOpen'

/** 会话：跨页保留，刷新清空 */
const session = {
  messages: [],
  busy: false,
  seq: 0,
  engine: 'offline',
  ready: false,
  providerLabel: '',
  model: '',
  reason: '',
  folders: 0,
  loaded: false,
  loading: false
}

/** 当前挂载的 DOM（回答流可能还在跑，回调必须打到活节点上） */
const view = {
  log: null, input: null, send: null, clear: null, body: null, card: null,
  hint: null, engine: null, scale: null, link: null
}

let mounted = null

function readOpen () {
  try { return localStorage.getItem(OPEN_KEY) === '1' } catch (err) { return false }
}

function writeOpen (open) {
  try { localStorage.setItem(OPEN_KEY, open ? '1' : '0') } catch (err) { /* 隐私模式下忽略 */ }
}

/* ---------------- 库藏规模 ---------------- */

function paintScale () {
  if (!view.scale) return
  const stats = getState().stats
  const total = stats ? stats.total : null
  const tags = stats ? stats.tags : null
  if (total == null) {
    view.scale.textContent = '库藏读取中…'
    return
  }
  const bits = [total + ' 篇']
  if (tags != null) bits.push(tags + ' 标签')
  if (session.folders) bits.push(session.folders + ' 分类')
  view.scale.textContent = bits.join(' · ')
  view.scale.title = '共 ' + total + ' 篇文献' + (stats && stats.starred ? '，收藏 ' + stats.starred + ' 篇' : '')
}

function paintEngine () {
  if (!view.engine) return
  const online = session.engine === 'online'
  view.engine.textContent = online ? ('在线 · ' + (session.providerLabel || '大模型')) : '离线引擎'
  view.engine.classList.toggle('is-online', online)
  view.engine.classList.toggle('is-offline', !online)
  view.engine.title = online
    ? ('已连接 ' + (session.model || session.providerLabel || '大模型') + '，可做跨文献问答与检索词设计')
    : ('无需 API Key：系统操作、检索技巧、引文导出、库藏统计由本地规则回答' + (session.reason ? '（' + session.reason + '）' : ''))
  if (view.link) view.link.textContent = online ? '换模型 →' : '配置在线 →'
  if (view.hint) {
    view.hint.textContent = online
      ? '💡 已连模型：可问检索词设计、文献联想。'
      : '💡 离线可答；配 Key 后深度问答。'
  }
}

/* ---------------- 对话渲染 ---------------- */

/** 面板比侧栏可视高度长：把输入区带到视野底部，别让用户打完字找不到按钮 */
function revealCompose () {
  if (!view.card || typeof view.card.scrollIntoView !== 'function') return
  view.card.scrollIntoView({ block: 'end' })
}

function scrollLog (force) {
  if (!view.log) return
  const gap = view.log.scrollHeight - view.log.scrollTop - view.log.clientHeight
  if (force || gap < 80) view.log.scrollTop = view.log.scrollHeight
}

function sourcesRow (list) {
  if (!list || !list.length) return null
  return h('div.sc-sources', ...list.map(item => h('button.sc-source', {
    type: 'button',
    title: item.title,
    onclick: () => go('#/paper/' + encodeURIComponent(item.id))
  },
    h('span.sc-source-n', '[' + item.index + ']'),
    h('span.sc-source-t', item.title)
  )))
}

function actionsRow (list) {
  if (!list || !list.length) return null
  return h('div.sc-actions-links', ...list.map(item => h('button.sc-act', {
    type: 'button',
    onclick: () => go(item.hash || '#/')
  }, item.label + ' →')))
}

function bubble (message) {
  const isUser = message.role === 'user'
  if (isUser) {
    return h('div.sc-msg.sc-user', { 'data-mid': String(message.mid || '') },
      h('div.sc-bubble', message.content))
  }
  const body = h('div.sc-bubble.sc-md.md')
  body.innerHTML = md.render(message.content || '', { byTitle: lookupTitle })
  if (message.pending && !message.content) {
    body.replaceChildren(h('span.sc-typing', h('span.sc-dot'), '思考中…'))
  }
  return h('div.sc-msg.sc-bot', { 'data-mid': String(message.mid || '') },
    h('div.sc-wrap', body,
      message.pending && message.content ? h('span.sc-typing', h('span.sc-dot'), '正在回答…') : null,
      message.offline && !message.pending
        ? h('span.sc-note', message.degraded ? '⚠ 模型不可用 · 已用离线知识回答' : '⚡ 离线回答 · 未调用模型')
        : null,
      sourcesRow(message.sources),
      actionsRow(message.actions)
    ))
}

function renderLog () {
  if (!view.log) return
  if (!session.messages.length) {
    view.log.replaceChildren(h('div.sc-empty',
      h('p', '问操作，也问库里有什么。'),
      h('p.sc-empty-sub', '可直接点上面的快捷提问。')
    ))
    return
  }
  view.log.replaceChildren(...session.messages.map(bubble))
  scrollLog(true)
}

/** 流式增量：只更新这一条消息，整页重绘会把滚动位置和输入焦点都弄丢 */
function paintAnswer (answer) {
  if (!view.log) return
  const node = view.log.querySelector('[data-mid="' + answer.mid + '"]')
  if (!node) { renderLog(); return }
  const body = node.querySelector('.sc-bubble')
  if (body) body.innerHTML = md.render(answer.content || '', { byTitle: lookupTitle })
  const typing = node.querySelector('.sc-typing')
  if (typing && answer.content) typing.remove()
  scrollLog(false)
}

/* ---------------- 问答 ---------------- */

function syncBusy () {
  if (!view.input || !view.send) return
  view.input.disabled = session.busy
  view.send.disabled = session.busy
  view.send.classList.toggle('is-busy', session.busy)
  view.send.textContent = session.busy ? '⏳ 回答中…' : '📨 发送提问'
  if (view.clear) view.clear.disabled = !session.messages.length
}

async function submit (text) {
  const question = String(text == null ? (view.input ? view.input.value : '') : text).trim()
  if (!question || session.busy) return
  if (view.input) view.input.value = ''

  const history = session.messages
    .filter(item => item.content)
    .slice(-8)
    .map(item => ({ role: item.role, content: item.content }))
  const user = { mid: ++session.seq, role: 'user', content: question }
  const answer = { mid: ++session.seq, role: 'assistant', content: '', pending: true, sources: [], actions: [] }
  session.messages.push(user, answer)
  session.busy = true
  renderLog()
  syncBusy()
  revealCompose()

  let acc = ''
  try {
    await api.copilotAsk({ question, history }, {
      onStart: data => {
        if (data.engine) session.engine = data.engine
        if (data.model) session.model = data.model
        answer.sources = data.sources || []
        // 离线帧里可能带 actions；在线帧的 actions 一般在 done 里
        if (data.actions && data.actions.length) answer.actions = data.actions
        if (data.offline) answer.offline = true
        if (typeof data.ready === 'boolean') session.ready = data.ready
        paintEngine()
      },
      onDelta: chunk => {
        acc += chunk
        answer.content = acc
        answer.pending = true
        paintAnswer(answer)
      },
      onDone: data => {
        answer.content = data.text || acc
        answer.sources = data.sources || answer.sources || []
        answer.actions = data.actions || answer.actions || []
        answer.offline = !!data.offline
        answer.degraded = !!data.degraded
        if (data.engine) session.engine = data.engine
        answer.pending = false
      }
    })
  } catch (err) {
    answer.content = '**这次没答上来。**\n\n' + (err && err.message ? err.message : String(err))
    answer.pending = false
  } finally {
    session.busy = false
    answer.pending = false
    renderLog()
    syncBusy()
    paintEngine()
    // 日志长到 max-height 后卡片才最终变高，这时再把输入区露出来
    revealCompose()
  }
}

function clearChat () {
  if (session.busy) { toast('等这条回答结束再清空'); return }
  if (!session.messages.length) return
  session.messages.length = 0
  renderLog()
  syncBusy()
  toast('已清空 Copilot 对话')
}

/* ---------------- 配置 / 状态 ---------------- */

async function loadOverview () {
  if (session.loading) return
  session.loading = true
  try {
    const data = await api.copilotOverview()
    session.loaded = true
    session.ready = !!data.ready
    session.engine = data.engine || (data.ready ? 'online' : 'offline')
    session.providerLabel = data.providerLabel || ''
    session.model = data.model || ''
    session.reason = data.reason || ''
    session.folders = (data.stats && data.stats.folders) || 0
    paintEngine()
    paintScale()
  } catch (err) {
    session.loaded = true
    session.engine = 'offline'
    session.reason = '读取配置失败'
    paintEngine()
  } finally {
    session.loading = false
  }
}

function ensureOverview () {
  if (!session.loaded && !session.loading) loadOverview()
}

/* ---------------- 面板 ---------------- */

function buildPanel () {
  const scale = h('span.sc-scale', '库藏读取中…')
  const engine = h('span.sc-engine.is-offline', '离线引擎')

  const log = h('div.sc-log', { role: 'log', 'aria-live': 'polite', 'aria-label': '对话记录' })

  const input = h('input.sc-input', {
    type: 'text',
    placeholder: '输入使用问题或库藏咨询…',
    'aria-label': '向 Copilot 提问',
    autocomplete: 'off',
    onkeydown: ev => {
      if (ev.key === 'Enter' && !ev.isComposing) {
        ev.preventDefault()
        submit()
      }
    }
  })

  const send = h('button.sc-send', {
    type: 'button',
    title: '发送提问（Enter）',
    onclick: () => submit()
  }, '📨 发送提问')

  const clear = h('button.sc-clear', {
    type: 'button',
    title: '清空对话记录（不影响文献库）',
    onclick: clearChat
  }, h('span', '🗑'), h('span', '清空'))

  const chips = h('div.sc-chips', ...CHIPS.map(chip => h('button.sc-chip', {
    type: 'button',
    title: chip.q,
    onclick: () => submit(chip.q)
  },
    h('span.sc-chip-ico', chip.icon),
    h('span.sc-chip-t', chip.label)
  )))

  const hint = h('p.sc-hint', session.ready
    ? '💡 已连模型：可问检索词设计、文献联想。'
    : '💡 离线可答；配 Key 后深度问答。')

  const link = h('button.sc-link', {
    type: 'button',
    title: '配置 DeepSeek / Claude Key',
    onclick: () => go('#/settings')
  }, session.ready ? '换模型 →' : '配置在线 →')

  const body = h('div.sc-body',
    h('div.sc-engine-row', engine, link),
    chips,
    log,
    h('div.sc-label', '向 Copilot 提问'),
    h('div.sc-compose', input),
    h('div.sc-actions', send, clear),
    hint
  )

  const header = h('button.sc-card-head', {
    type: 'button',
    'aria-expanded': 'false',
    'aria-controls': 'copilot-card-body',
    title: '展开 / 收起问答面板',
    onclick: () => toggle()
  },
    h('span.sc-card-title', '系统与全库问答顾问'),
    scale,
    h('span.sc-card-caret', '▾')
  )

  const card = h('div.sc-card', header, h('div.sc-card-body', { id: 'copilot-card-body' }, body))
  const root = h('section.sc', { 'aria-label': '智能问答助手' },
    h('div.sc-top',
      h('span.sc-top-ico', '💬'),
      h('span.sc-top-title', '智能问答助手'),
      h('span.sc-tag', 'COPILOT')
    ),
    card
  )

  view.scale = scale
  view.engine = engine
  view.log = log
  view.input = input
  view.send = send
  view.clear = clear
  view.body = body
  view.card = card
  view.hint = hint
  view.link = link

  // 状态：初次展开才请求配置，省一个无谓的请求
  function paintOpen (open) {
    root.classList.toggle('is-open', open)
    card.classList.toggle('is-open', open)
    header.setAttribute('aria-expanded', open ? 'true' : 'false')
    header.title = open ? '收起问答面板' : '展开问答面板'
    if (open) {
      ensureOverview()
      renderLog()
      syncBusy()
      // 侧栏是滚动容器：展开后把输入区带到视野里，省得用户自己找
      revealCompose()
      if (view.input && !session.busy) setTimeout(() => view.input.focus(), 40)
    }
  }

  function toggle () {
    const next = !root.classList.contains('is-open')
    writeOpen(next)
    paintOpen(next)
  }

  toggleHandler = toggle

  paintOpen(readOpen())
  if (!session.messages.length) renderLog()
  paintScale()
  paintEngine()

  return root
}

/** 供外部展开面板（例如从别的页面点进来时） */
let toggleHandler = null
export function toggleCopilot () { if (toggleHandler) toggleHandler() }

/* ---------------- 实时数据 ---------------- */

let wired = false
function wire () {
  if (wired) return
  wired = true
  // 文献增删 / 标签变化 → 侧栏的 state.stats 会更新，这里跟着刷新库藏数字
  subscribe((state, changed) => {
    if (!changed || changed.includes('stats')) paintScale()
  })
  document.addEventListener('folders:changed', () => {
    loadOverview()
  })
}

/**
 * 挂载侧栏面板。只能在侧栏里调用一次（侧栏本身只渲染一次）。
 * @returns {HTMLElement}
 */
export function mountCopilot () {
  if (mounted) return mounted
  mounted = buildPanel()
  wire()
  ensureOverview()
  return mounted
}

export default { mountCopilot, toggleCopilot }
