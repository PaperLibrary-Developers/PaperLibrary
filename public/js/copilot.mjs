/* 智能问答助手（Copilot）：左下角对话框，入口只有侧栏「使用文档」上方那一行。

   交互形态：
   · 入口 —— 侧栏底部一行（💬 + 智能问答助手 + COPILOT 徽标），跟「使用文档」同一款；
     页面上不再有浮动气泡，收起时左下角不留任何浮标，不挡正文也不挡列表。
   · 展开 —— 点入口后贴着内容区左下角弹出 392px 宽的对话框：页头（标题 / 库藏 / 清空 / ✕）、
     欢迎区（问候 + 四条直接可点的提问）、对话记录、输入区。
     收起：页头 ✕、Esc、再点一次入口。

   为什么不做成侧栏里的常驻卡片（最初那版）：侧栏可用宽度只有 222px，回答正文一带
   markdown 就被全局 .md 规则撑到 15px（标题 16.5/21px 衬线），一屏只剩十来个字，
   看起来就是「字号异常」。对话框有 392px，字号和行宽都回到正常阅读尺度。

   引擎：没配 Key 走离线规则（系统操作 / 检索技巧 / 引文导出 / PDF 阅读 / 库藏统计），
   配了 Key 走在线模型，模型调用失败时后端自动降级并回一帧离线答案。前端只按 start / done
   帧里的 engine 显示状态，不做二次判断。
   会话放模块级：切页、收起再展开都不会丢，刷新才清空。
   每次增量只改当前气泡的 innerHTML，不整块重绘——流式回答才跟得上。 */

import * as util from './util.mjs'
import * as md from './markdown.mjs'
import { api } from './api.mjs'
import { go, lookupTitle } from './shell/app-api.mjs'
import { getState, subscribe } from './state/store.mjs'

const { h, toast } = util

/** 一键提问：四个入口覆盖导入 / 图表 / 引文 / 库藏，都是新手真会问的 */
const CHIPS = [
  { icon: '📥', label: '怎么导入文献', desc: '单篇 PDF、整批或整个文件夹', q: '怎么导入下载好的 PDF 文献？' },
  { icon: '🔎', label: '搜实验图表', desc: '按图注、曲线、材料名找图', q: '怎么在库里检索实验图表，比如 CV 曲线？' },
  { icon: '📤', label: '导出 BibTeX', desc: '单篇引用或整库导出', q: '如何导出 BibTeX 引用？' },
  { icon: '📊', label: '库藏概况', desc: '共多少篇、标签与分类分布', q: '当前文献库的库藏数据概况如何？' }
]

/* 发送键用内联 SVG（白色箭头），emoji 箭头在不同系统上大小和基线都不稳 */
const ICON_SEND =
  '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">' +
  '<path d="M12 19.2V6.6M6.6 11.8 12 6.4l5.4 5.4" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round" stroke-linejoin="round"/>' +
  '</svg>'
/* 清空对话：垃圾桶也用 SVG。🗑 在 Windows 上是灰蓝色 emoji，套不进红色的危险色，
   而且各系统字面大小差一截；描边图标能直接吃 currentColor。 */
const ICON_TRASH =
  '<svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true" focusable="false">' +
  '<path d="M4.8 6.7h14.4M9.5 6.7V5.4c0-.8.7-1.5 1.5-1.5h2c.8 0 1.5.7 1.5 1.5v 1.3M7.1 6.7l.8 11.3c.1.9.8 1.6 1.7 1.6h4.8c.9 0 1.6-.7 1.7-1.6l.8-11.3M10.4 10.2v5.4M13.6 10.2v5.4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/>' +
  '</svg>'

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
  dock: null, panel: null, entry: null,
  log: null, scale: null, engine: null, hint: null, link: null,
  input: null, send: null, clear: null
}

let mounted = null

function isOpen () {
  return !!view.panel && !view.panel.hidden
}

/* ---------------- 库藏规模 / 引擎状态 ---------------- */

function paintScale () {
  if (!view.scale) return
  const stats = getState().stats
  const total = stats ? stats.total : null
  if (total == null) {
    view.scale.textContent = '库藏读取中…'
    return
  }
  const bits = [total + ' 篇']
  if (stats.tags != null) bits.push(stats.tags + ' 标签')
  if (session.folders) bits.push(session.folders + ' 分类')
  view.scale.textContent = bits.join(' · ')
  view.scale.title = '共 ' + total + ' 篇文献' + (stats && stats.starred ? '，收藏 ' + stats.starred + ' 篇' : '')
}

function paintEngine () {
  if (!view.engine) return
  const online = session.engine === 'online'
  view.engine.textContent = online ? ('在线 · ' + (session.providerLabel || '大模型')) : '离线引擎'
  view.engine.classList.toggle('is-online', online)
  view.engine.title = online
    ? ('已连接 ' + (session.model || session.providerLabel || '大模型') + '，可做跨文献问答与检索词设计')
    : ('无需 API Key：系统操作、检索技巧、引文导出、库藏统计由本地规则回答' + (session.reason ? '（' + session.reason + '）' : ''))
  if (view.link) view.link.textContent = online ? '换模型 →' : '配置在线 →'
  if (view.hint) {
    view.hint.textContent = online
      ? '💡 已连模型：可以问检索词设计，也可以问库里有什么。'
      : '💡 离线也能答操作问题；配 Key 后可问库里有什么。'
  }
}

/* ---------------- 对话渲染 ---------------- */

function scrollLog (force) {
  if (!view.log) return
  const gap = view.log.scrollHeight - view.log.scrollTop - view.log.clientHeight
  // 只在本来就贴着底部时自动跟随，用户往上翻看历史时不抢滚动
  if (force || gap < 80) view.log.scrollTop = view.log.scrollHeight
}

function sourcesRow (list) {
  if (!list || !list.length) return null
  return h('div.cw-sources',
    h('div.cw-sources-head', '引用来源'),
    ...list.map(item => h('button.cw-source', {
      type: 'button',
      title: item.title,
      onclick: () => go('#/paper/' + encodeURIComponent(item.id))
    },
      h('span.cw-source-n', '[' + item.index + ']'),
      h('span.cw-source-t', item.title)
    )))
}

function actionsRow (list) {
  if (!list || !list.length) return null
  return h('div.cw-actions-links', ...list.map(item => h('button.cw-act', {
    type: 'button',
    onclick: () => go(item.hash || '#/')
  }, item.label + ' →')))
}

function bubble (message) {
  const isUser = message.role === 'user'
  if (isUser) {
    return h('div.cw-msg.cw-user', { 'data-mid': String(message.mid || '') },
      h('div.cw-bubble', message.content))
  }
  const body = h('div.cw-bubble.cw-md')
  body.innerHTML = md.render(message.content || '', { byTitle: lookupTitle })
  if (message.pending && !message.content) {
    body.replaceChildren(h('span.cw-typing', h('span.cw-dot'), '思考中…'))
  }
  return h('div.cw-msg.cw-bot', { 'data-mid': String(message.mid || '') },
    h('div.cw-wrap', body,
      message.pending && message.content ? h('span.cw-typing', h('span.cw-dot'), '正在回答…') : null,
      message.offline && !message.pending
        ? h('span.cw-note', message.degraded ? '⚠ 模型不可用 · 已用离线知识回答' : '⚡ 离线回答 · 未调用模型')
        : null,
      sourcesRow(message.sources),
      actionsRow(message.actions)
    ))
}

/** 欢迎区：问候 + 四条直接可点的提问（还没有对话时才出现） */
function buildHello () {
  const online = session.engine === 'online'
  return h('div.cw-hello',
    h('p.cw-hello-t', '👋 你好，需要帮什么？'),
    h('p.cw-hello-sub', online
      ? '配了模型：可以问库里有什么，也可以问这个软件怎么用。'
      : '没配模型也能用：问操作、问库藏，都从本机回答，不联网也不花钱。'),
    h('div.cw-quicks', ...CHIPS.map(chip => h('button.cw-quick', {
      type: 'button',
      title: chip.q,
      onclick: () => submit(chip.q)
    },
      h('span.cw-quick-ico', chip.icon),
      h('span.cw-quick-text',
        h('span.cw-quick-t', chip.label),
        h('span.cw-quick-d', chip.desc)
      ),
      h('span.cw-quick-arw', '›')
    )))
  )
}

function renderLog () {
  if (!view.log) return
  if (!session.messages.length) {
    view.log.replaceChildren(buildHello())
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
  const body = node.querySelector('.cw-bubble')
  if (body) body.innerHTML = md.render(answer.content || '', { byTitle: lookupTitle })
  const typing = node.querySelector('.cw-typing')
  if (typing && answer.content) typing.remove()
  scrollLog(false)
}

/* ---------------- 问答 ---------------- */

function syncBusy () {
  if (!view.input || !view.send) return
  view.input.disabled = session.busy
  view.send.disabled = session.busy
  view.input.placeholder = session.busy ? '正在回答，稍等一下…' : '问我怎么用，或问库里有什么…'
  if (view.clear) view.clear.disabled = !session.messages.length
}

/** 输入框跟着内容长高，最多四行左右，再多就内部滚动 */
function autoGrow () {
  if (!view.input) return
  view.input.style.height = 'auto'
  view.input.style.height = Math.min(116, Math.max(40, view.input.scrollHeight)) + 'px'
}

async function submit (text) {
  const question = String(text == null ? (view.input ? view.input.value : '') : text).trim()
  if (!question || session.busy) return
  if (view.input) { view.input.value = ''; autoGrow() }

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
    // 空对话时欢迎区的文案跟引擎有关，状态变化后重画一次
    if (!session.messages.length) renderLog()
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

/* ---------------- 展开 / 收起 ---------------- */

function setOpen (open) {
  if (!view.dock) return
  const next = !!open
  const wasOpen = isOpen()
  view.panel.hidden = !next
  // 没有浮动气泡了：开合状态挂在侧栏那行入口上（读屏与 title 也才好跟着变）
  if (view.entry) {
    view.entry.classList.toggle('active', next)
    view.entry.setAttribute('aria-expanded', next ? 'true' : 'false')
    view.entry.title = next ? '收起智能问答助手' : '打开智能问答助手'
  }
  if (!next) {
    // 焦点本来在对话框里（✕ / Esc / 入口再点一次）：还给入口，别让它落到 body 上
    if (wasOpen) {
      const active = document.activeElement
      if (!active || active === document.body || view.panel.contains(active)) {
        if (view.entry) { try { view.entry.focus({ preventScroll: true }) } catch (err) {} }
      }
    }
    return
  }
  ensureOverview()
  renderLog()
  syncBusy()
  autoGrow()
  // 等展开动画那一帧过去再聚焦，否则焦点会带着页面滚动
  setTimeout(() => {
    if (view.input && !session.busy && isOpen()) {
      try { view.input.focus({ preventScroll: true }) } catch (err) { view.input.focus() }
    }
  }, 80)
}

export function openCopilot () {
  mountCopilotDock()
  setOpen(true)
}

export function closeCopilot () {
  if (isOpen()) setOpen(false)
}

export function toggleCopilot () {
  if (isOpen()) setOpen(false)
  else openCopilot()
}

/* ---------------- 面板 ---------------- */

function buildDock () {
  const scale = h('span.cw-sub', '库藏读取中…')
  const engine = h('span.cw-engine', '离线引擎')
  const hint = h('p.cw-hint')
  const link = h('button.cw-link', {
    type: 'button',
    title: '配置 DeepSeek / Claude Key',
    onclick: () => go('#/settings')
  }, '配置在线 →')

  const log = h('div.cw-log', { role: 'log', 'aria-live': 'polite', 'aria-label': '对话记录' })

  const input = h('textarea.cw-input', {
    rows: '1',
    placeholder: '问我怎么用，或问库里有什么…',
    'aria-label': '向 Copilot 提问',
    oninput: () => autoGrow(),
    onkeydown: ev => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
        ev.preventDefault()
        submit()
      }
    }
  })

  const send = h('button.cw-send', {
    type: 'button',
    html: ICON_SEND,
    title: '发送（Enter）',
    'aria-label': '发送提问',
    onclick: () => submit()
  })

  // 清空对话：红底的危险操作按钮，图标走 SVG（emoji 没法染色，也不跟字号一起缩放）
  const clear = h('button.cw-ico-btn.is-danger', {
    type: 'button',
    html: ICON_TRASH,
    title: '清空对话记录（不影响文献库）',
    'aria-label': '清空对话',
    onclick: clearChat
  })

  const close = h('button.cw-ico-btn', {
    type: 'button',
    title: '收起（Esc）',
    'aria-label': '收起问答助手',
    onclick: () => setOpen(false)
  }, '✕')

  const panel = h('section.cw-panel', {
    id: 'cw-panel',
    role: 'dialog',
    'aria-modal': 'false',
    'aria-label': '智能问答助手',
    hidden: true
  },
    h('header.cw-head',
      h('span.cw-head-ico', '💬'),
      h('div.cw-head-text',
        h('div.cw-title', '智能问答助手', h('span.cw-tag', 'COPILOT')),
        h('div.cw-sub-row',
          h('span.cw-sub-t', '系统与全库问答顾问'),
          h('span.cw-sub-dot', '·'),
          scale
        )
      ),
      h('div.cw-head-actions', clear, close)
    ),
    log,
    h('div.cw-compose',
      h('div.cw-input-row', input, send),
      h('div.cw-foot-row', engine, link),
      hint
    )
  )

  // 容器只是个定位锚点（左下角那一点），面板自己往上长；页面上不再有浮动气泡
  const dock = h('div.cw', panel)

  view.dock = dock
  view.panel = panel
  view.log = log
  view.scale = scale
  view.engine = engine
  view.hint = hint
  view.link = link
  view.input = input
  view.send = send
  view.clear = clear

  // Esc 收起：只接管「面板开着」时的那一次，抽屉自己的 Esc 逻辑照样生效
  // （焦点由 setOpen 统一还到侧栏入口，这里不再抢）
  document.addEventListener('keydown', ev => {
    if (ev.key !== 'Escape' || !isOpen()) return
    setOpen(false)
  })

  paintScale()
  paintEngine()
  renderLog()
  syncBusy()

  // 每次加载都是收起态：对话框是浮层，自动弹开会挡住正文（切页时则保持模块里的状态）
  setOpen(false)
  return dock
}

/**
 * 挂载对话框，只能在启动时调用一次（挂到 body 上，不随路由重建）。
 * 入口在侧栏，见 copilotSideEntry()。
 * @returns {HTMLElement}
 */
export function mountCopilotDock () {
  if (mounted) return mounted
  mounted = buildDock()
  document.body.appendChild(mounted)
  wire()
  return mounted
}

/**
 * 侧栏底部的一行入口（和「使用文档」同一行样式，就在它上方）：
 * 点它开对话框，再点一次收起；窄屏开面板时顺手把抽屉收起来，省得遮罩挡住对话框。
 * @param {{ onOpen?: () => void }} [options]
 * @returns {HTMLElement}
 */
export function copilotSideEntry (options) {
  if (view.entry) return view.entry
  const opts = options || {}
  view.entry = h('button.side-doc.side-copilot', {
    type: 'button',
    title: '打开智能问答助手',
    'aria-expanded': 'false',
    'aria-controls': 'cw-panel',
    onclick: () => {
      // 已经开着就再点一次收起：入口自己就是开关，不用另外找 ✕
      if (isOpen()) { setOpen(false); return }
      openCopilot()
      if (opts.onOpen) opts.onOpen()
    }
  },
    h('span.nav-ico', '💬'),
    h('span', '智能问答助手'),
    h('span.side-doc-badge', 'COPILOT')
  )
  return view.entry
}

/* ---------------- 实时数据 ---------------- */

let wired = false
function wire () {
  if (wired) return
  wired = true
  // 文献增删 / 标签变化 → state.stats 会更新，这里跟着刷新库藏数字
  subscribe((state, changed) => {
    if (!changed || changed.includes('stats')) paintScale()
  })
  document.addEventListener('folders:changed', () => {
    loadOverview()
  })
}

export default { mountCopilotDock, copilotSideEntry, openCopilot, closeCopilot, toggleCopilot }
