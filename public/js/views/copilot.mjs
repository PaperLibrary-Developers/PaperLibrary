/* 智能问答助手（Copilot）。

   定位：面向新手的问答机器人。**不依赖大模型也能工作**，按配置情况分两个区域：

   · 未配置大模型 → 离线引导区（buildGuide）：四条上手路径 + 可按关键词搜索的固定 FAQ，
     每条回答都带「去这里」的直达按钮。纯前端数据（public/js/faq.mjs），不发请求、不花钱。
   · 已配置大模型 → 智能问答区（buildAiPanel）：在库内检索相关文献，连同使用说明一起交给模型，
     流式回答并列出引用来源。会话存在模块级，切页不丢、刷新清空。

   切换逻辑：进入页面先 GET /api/copilot/overview，按 ready 决定渲染哪一块；
   页头「重新检测」可重新判定（例如刚在设置里填了 Key 回来）。两种模式不并存。 */
import * as util from '../util.mjs'
import * as md from '../markdown.mjs'
import { api } from '../api.mjs'
import { go, lookupTitle } from '../shell/app-api.mjs'
import { QUICK_START, FAQ_GROUPS, matchFaq } from '../faq.mjs'

  const { h, toast } = util

  /* ---------------- 智能问答区（AI 模式） ---------------- */

  /** 快捷提问：都是新手最常问的入口 */
  const PRESETS = [
    { icon: '📥', label: '怎么导入文献', q: '怎么导入新下载的论文？' },
    { icon: '📊', label: '怎么横向对比', q: '怎么把多篇文献进行横向对比与综合分析？' },
    { icon: '📑', label: '导出 BibTeX', q: '如何导出 BibTeX 引用？' },
    { icon: '🔬', label: '怎么找图表', q: '怎么在库里检索具体的实验图表，比如 CV 曲线？' },
    { icon: '🎯', label: '有无 AI 的区别', q: '配置大模型和不配置有什么区别？' },
    { icon: '🗂', label: '库藏概况', q: '当前文献库的库藏概况如何？' }
  ]

  // 会话：只在本页会话里保留（切页不丢，刷新清空）
  const session = { messages: [], busy: false, seq: 0 }

  // 当前挂载的那一份 DOM。回答是流式的，用户可能中途切走再回来：
  // 回调只认 view 里的节点，重新进入页面时 view 会被换成新节点，回答就能接着往上画。
  const view = { log: null, status: null, input: null, sendBtn: null, busyRow: null }

  function scrollLog () {
    if (!view.log) return
    const gap = view.log.scrollHeight - view.log.scrollTop - view.log.clientHeight
    // 只在本来就贴着底部时自动跟随，用户往上翻看历史时不抢滚动
    if (gap < 120) view.log.scrollTop = view.log.scrollHeight
  }

  function renderLog () {
    if (!view.log) return
    if (!session.messages.length) {
      view.log.replaceChildren(h('div.cp-empty',
        h('div.cp-empty-ico', '💬'),
        h('div.cp-empty-h', '问用法，也问库里的文献'),
        h('p', '比如「怎么把多篇文献横向对比」「我的库里有哪些关于钙钛矿的工作」「有没有配置 AI 有什么区别」。'),
        h('p', { style: { fontSize: '12px', color: 'var(--faint)' } },
          '回答只依据软件说明和你库里的文献；重要结论请点「引用来源」回原文核对。')
      ))
      return
    }
    view.log.replaceChildren(...session.messages.map(bubble))
    scrollLog()
  }

  function syncBusy () {
    if (!view.input || !view.sendBtn) return
    view.input.disabled = session.busy
    view.sendBtn.disabled = session.busy
    view.sendBtn.textContent = session.busy ? '回答中…' : '发送'
    if (view.busyRow) view.busyRow.hidden = !session.busy
  }

  /** 流式增量：只改这一条消息的气泡，不做整页重绘（否则每来一个字就重建整个列表） */
  function paintAnswer (answer) {
    if (!view.log) return
    const node = view.log.querySelector('[data-mid="' + answer.mid + '"]')
    if (!node) { renderLog(); return }
    const body = node.querySelector('.cp-body')
    if (body) body.innerHTML = md.render(answer.content || '', { byTitle: lookupTitle })
    const typing = node.querySelector('.cp-typing')
    if (typing && answer.content) typing.remove()
    scrollLog()
  }

  function sourcesRow (list) {
    if (!list || !list.length) return null
    return h('div.cp-sources',
      h('div.cp-sources-head', '引用来源'),
      ...list.map(item => h('button.cp-source', {
        type: 'button',
        title: item.title,
        onclick: () => go('#/paper/' + encodeURIComponent(item.id))
      },
        h('span.cp-source-n', '[' + item.index + ']'),
        h('span.cp-source-t', item.title),
        h('span.cp-source-meta', [item.year || '', item.venue || ''].filter(Boolean).join(' · '))
      ))
    )
  }

  function bubble (message) {
    const isUser = message.role === 'user'
    const body = h('div.cp-body' + (isUser ? '' : '.md'))
    if (isUser) body.textContent = message.content
    else body.innerHTML = md.render(message.content || '', { byTitle: lookupTitle })
    if (isUser) {
      return h('div.cp-msg.cp-user', { 'data-mid': String(message.mid || '') },
        h('div.cp-avatar', '你'), body)
    }
    return h('div.cp-msg.cp-bot', { 'data-mid': String(message.mid || '') },
      h('div.cp-avatar', 'AI'),
      h('div.cp-wrap', body, message.pending ? h('span.cp-typing', '正在检索库内文献…') : null,
        message.offline ? h('span.cp-offline', '由本地统计生成 · 未调用模型') : null,
        message.sources && message.sources.length ? sourcesRow(message.sources) : null,
        // 未配置模型时给一个直接的出口，而不只是报错文字
        message.action ? h('button.btn.sm', {
          type: 'button',
          onclick: message.action.run
        }, message.action.label) : null)
    )
  }

  async function submit (text) {
    const question = String(text == null ? (view.input ? view.input.value : '') : text).trim()
    if (!question || session.busy) return
    if (view.input) view.input.value = ''
    // 只带最近几轮，避免越聊越贵；本轮问题单独放在最后一条
    const history = session.messages.slice(-8).map(item => ({ role: item.role, content: item.content }))
    session.messages.push({ mid: ++session.seq, role: 'user', content: question })
    const answer = { mid: ++session.seq, role: 'assistant', content: '', sources: [], pending: true }
    session.messages.push(answer)
    session.busy = true
    renderLog()
    syncBusy()

    let acc = ''
    try {
      await api.copilotAsk({ question, history }, {
        onStart: data => {
          answer.sources = data.sources || []
          if (data.offline) answer.offline = true
        },
        onDelta: chunk => {
          acc += chunk
          answer.content = acc
          paintAnswer(answer)
        },
        onDone: data => {
          answer.content = data.text || acc
          answer.sources = data.sources || answer.sources || []
          answer.pending = false
        }
      })
    } catch (err) {
      const message = err && err.message ? err.message : String(err)
      answer.content = '**没能回答这个问题。**\n\n' + message
      answer.pending = false
      if (err && err.status === 400) {
        answer.action = { label: '去设置大模型', run: () => go('#/settings') }
      }
    } finally {
      session.busy = false
      answer.pending = false
      renderLog()
      syncBusy()
    }
  }

  function buildAiPanel () {
    const presetRow = h('div.cp-presets', ...PRESETS.map(item => h('button.cp-preset', {
      type: 'button',
      title: item.q,
      onclick: () => submit(item.q)
    },
      h('span', item.icon),
      h('span', item.label)
    )))

    const log = h('div.cp-log')
    const busyRow = h('div.cp-busy', { hidden: true }, h('span.cp-dot'), '正在检索库内文献并生成回答…')
    const input = h('textarea.cp-input', {
      rows: '2',
      placeholder: '问使用问题，或问库里有什么…（Enter 发送，Shift+Enter 换行）',
      onkeydown: ev => {
        if (ev.key === 'Enter' && !ev.shiftKey && !ev.isComposing) {
          ev.preventDefault()
          submit()
        }
      }
    })
    const sendBtn = h('button.btn.primary.cp-send', { type: 'button', onclick: () => submit() }, '发送')

    view.log = log
    view.busyRow = busyRow
    view.input = input
    view.sendBtn = sendBtn

    const panel = h('section.cp-panel',
      h('div.cp-presets-wrap', presetRow),
      log,
      busyRow,
      h('div.cp-compose', input, sendBtn),
      h('div.cp-foot',
        'Enter 发送 · Shift+Enter 换行 · 回答基于库内检索与使用说明，重要结论请回原文核对 · ',
        h('button.cp-link', { type: 'button', onclick: () => go('#/guide') }, '也可以看使用文档')
      )
    )

    renderLog()
    syncBusy()
    return panel
  }

  /* ---------------- 离线引导区（未配置大模型） ---------------- */

  function buildGuidePanel (onRecheck) {
    const search = h('input.cp-faq-search', {
      type: 'search',
      placeholder: '搜一下，例如：导入、备份、图表、端口…',
      'aria-label': '搜索常见问题',
      oninput: () => paintList()
    })
    const count = h('span.cp-faq-count')
    const listBox = h('div.cp-faq-list')

    const banner = h('div.cp-guide-banner',
      h('span.cp-guide-ico', '🧭'),
      h('div.cp-guide-main',
        h('h2', '离线引导模式（未配置大模型）'),
        h('p', '下面这些问题不需要联网、也不花钱：按步骤点就能完成。想让助手读你的库、自由提问，再去配置一个模型即可。'),
        h('div.cp-guide-actions',
          h('button.btn.sm.primary', { type: 'button', onclick: () => go('#/settings') }, '去配置大模型'),
          h('button.btn.sm', { type: 'button', onclick: () => onRecheck() }, '我配好了，重新检测')
        )
      )
    )

    const steps = h('div.cp-steps',
      ...QUICK_START.map((step, index) => h('button.cp-step', {
        type: 'button',
        onclick: () => go(step.hash)
      },
        h('span.cp-step-n', 'STEP ' + (index + 1)),
        h('span.cp-step-t', step.title),
        h('span.cp-step-d', step.desc),
        h('span.cp-step-go', step.cta + ' →')
      ))
    )

    function entry (item) {
      return h('details.cp-faq-item',
        h('summary', h('span.cp-faq-q', item.q)),
        h('div.cp-faq-a',
          h('p', item.a),
          item.links && item.links.length
            ? h('div.cp-faq-links', ...item.links.map(link => h('button.btn.sm', {
              type: 'button',
              onclick: () => go(link.hash)
            }, link.label)))
            : null
        )
      )
    }

    function paintList () {
      const query = search.value.trim()
      if (!query) {
        listBox.replaceChildren(...FAQ_GROUPS.map(group => h('div.cp-faq-group',
          h('h3', group.name),
          ...group.items.map(entry)
        )))
        const total = FAQ_GROUPS.reduce((sum, group) => sum + group.items.length, 0)
        count.textContent = '共 ' + total + ' 条 · 点问题展开'
        return
      }
      const hits = matchFaq(query)
      count.textContent = hits.length ? '找到 ' + hits.length + ' 条' : '没有匹配'
      if (!hits.length) {
        listBox.replaceChildren(h('div.cp-faq-empty',
          h('div.cp-empty-ico', '🔍'),
          h('p', '没有匹配「' + query + '」的问题。试试更短的关键词，比如「导入」「备份」「端口」。'),
          h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
            h('button.btn.sm', { type: 'button', onclick: () => { search.value = ''; paintList(); search.focus() } }, '清空搜索'),
            h('button.btn.sm', { type: 'button', onclick: () => go('#/guide') }, '打开使用文档')
          )
        ))
        return
      }
      listBox.replaceChildren(...hits.map(hit => h('div.cp-faq-group',
        h('h3', hit.group),
        entry(hit.item)
      )))
    }

    const faqPanel = h('section.cp-faq-panel',
      h('div.cp-faq-head',
        h('div',
          h('div.cp-faq-title', '常用问题（离线可查）'),
          count
        ),
        search
      ),
      listBox
    )

    const footer = h('div.cp-guide-foot',
      '以上内容和使用文档同源；看完仍不清楚，可以直接打开手册按章节找。',
      h('button.btn.sm', { type: 'button', onclick: () => go('#/guide') }, '📖 打开使用文档')
    )

    paintList()
    return h('div.cp-guide', banner, steps, faqPanel, footer)
  }

  /* ---------------- 页面：按配置切换两个区域 ---------------- */

  function copilotHandler (ctx) {
    const { outlet } = ctx
    const status = h('div.cp-status')
    const actions = h('div.cp-head-actions')
    const modeBox = h('div.cp-mode')
    let mode = null

    const head = h('div.page-head',
      h('div',
        h('h1.page-title', '智能问答助手'),
        h('p.page-sub', '新手指引 + 库内问答：没配模型也能查操作，配了模型就能自由提问。')
      ),
      h('div.cp-head-side', status, actions)
    )

    outlet.replaceChildren(h('div.content.cp-page', head, modeBox))

    function clearChat () {
      if (session.busy) return
      session.messages.length = 0
      renderLog()
      toast('已清空对话')
    }

    function paintStatus (data) {
      const s = data.stats || {}
      status.replaceChildren(
        h('span.cp-pill' + (data.ready ? '.ok' : '.warn'),
          h('span.cp-pill-dot'),
          data.ready ? '已连接 ' + (data.model || '大模型') : '未配置大模型 · 离线引导'
        ),
        h('span.cp-pill', '共 ' + (s.total || 0) + ' 篇'),
        h('span.cp-pill', (s.tags || 0) + ' 个标签'),
        h('span.cp-pill', (s.folders || 0) + ' 个分类')
      )
      actions.replaceChildren()
      if (data.ready) {
        actions.appendChild(h('button.btn.sm.ghost', {
          type: 'button',
          title: '清空这次对话（不影响文献库）',
          onclick: clearChat
        }, '清空对话'))
      } else {
        actions.appendChild(h('button.btn.sm', {
          type: 'button',
          title: data.reason || '',
          onclick: () => go('#/settings')
        }, '去配置'))
      }
      actions.appendChild(h('button.btn.sm.ghost', {
        type: 'button',
        title: '重新读取配置（在别处填好 Key 后点这里）',
        onclick: () => load(true)
      }, '重新检测'))
    }

    async function load (manual) {
      if (manual) status.replaceChildren(h('span.cp-pill', h('span.cp-pill-dot'), '正在检测配置…'))
      let data
      try {
        data = await api.copilotOverview()
      } catch (err) {
        if (ctx.isStale && ctx.isStale()) return
        status.replaceChildren(h('span.cp-pill.warn', h('span.cp-pill-dot'), '读取配置失败'))
        actions.replaceChildren(h('button.btn.sm', { type: 'button', onclick: () => load(true) }, '重试'))
        if (!modeBox.childElementCount) {
          modeBox.replaceChildren(h('div.cp-empty',
            h('div.cp-empty-ico', '⚠️'),
            h('div.cp-empty-h', '读不到服务状态'),
            h('p', (err && err.message) || '请确认本地服务在运行，然后点「重试」。')
          ))
        }
        return
      }
      if (ctx.isStale && ctx.isStale()) return
      paintStatus(data)
      const next = data.ready ? 'ai' : 'guide'
      if (next === mode) {
        if (manual) toast('配置没有变化')
        return
      }
      mode = next
      // 切到引导区时把 AI 的 DOM 引用清掉：正在流式的回调不应该往已卸载的节点里画
      if (next !== 'ai') {
        view.log = null
        view.input = null
        view.sendBtn = null
        view.busyRow = null
      }
      modeBox.replaceChildren(next === 'ai' ? buildAiPanel() : buildGuidePanel(() => load(true)))
      if (next === 'ai' && view.input) view.input.focus()
    }

    load(false)
    return () => { /* 没有常驻监听；会话留在模块级，切页回来还能看到 */ }
  }

  export { copilotHandler }
  export default { copilotHandler }
