/* 详情页子模块：与文献对话流、提问历史、贴图提问、框选识图弹窗 */
import * as util from '../../util.mjs'
import * as md from '../../markdown.mjs'
import { api } from '../../api.mjs'
import { getState } from '../../state/store.mjs'
import { lookupTitle, savePrefs } from '../../shell/app-api.mjs'

const { h } = util

export function buildChatPanel ({
  paper, signal, isStale, toast, confirm, later, showOverlay, onFulltextReady, onSwitchToChat
}) {
  let scrollFrame = null

  const chatScroll = h('div.chat-scroll', { id: 'chat-body' })
  const chatQList = h('div.chat-q-list')
  const chatQCount = h('span.chat-q-count', '')
  const chatQsPane = h('aside.chat-qs',
    h('div.chat-qs-head', h('span', '提问历史'), chatQCount),
    chatQList
  )
  const chatBody = chatScroll

  function scrollChatToEnd () {
    if (isStale()) return
    if (scrollFrame !== null) window.cancelAnimationFrame(scrollFrame)
    scrollFrame = window.requestAnimationFrame(() => {
      scrollFrame = null
      chatBody.scrollTop = chatBody.scrollHeight
    })
  }

  function cancelScroll () {
    if (scrollFrame !== null) {
      window.cancelAnimationFrame(scrollFrame)
      scrollFrame = null
    }
  }

  const chatInput = h('textarea.textarea', {
    rows: 2,
    id: 'chat-input',
    placeholder: '就这篇文献提问，例如：这篇用了什么方法？结论对什么体系成立？（Enter 发送，Shift + Enter 换行）',
    onkeydown: ev => {
      if (ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey && !ev.metaKey && !ev.isComposing) {
        ev.preventDefault()
        sendQuestion()
        return
      }
      if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); sendQuestion() }
    }
  })
  const chatStatus = h('span.hint', '')

  /* ---------- 贴图提问：待发送的图片（不落库，只随请求发给模型） ---------- */
  let pendingImages = [] // [{ dataUrl, name }]
  const chatThumbs = h('div.chat-thumbs', { hidden: true })

  function renderChatThumbs () {
    chatThumbs.hidden = pendingImages.length === 0
    chatThumbs.replaceChildren(...pendingImages.map((img, idx) => h('div.chat-thumb',
      h('img', { src: img.dataUrl, alt: img.name || '待发送的图片' }),
      h('button.chat-thumb-del', {
        type: 'button',
        title: '移除这张图',
        onclick: () => { pendingImages.splice(idx, 1); renderChatThumbs() }
      }, '×')
    )))
  }

  /** 把 File/Blob 读成 dataURL 后进待发送列表 */
  function addImageFile (file) {
    if (!file || !/^image\//.test(file.type || '')) return false
    if (pendingImages.length >= 4) { toast('一次最多带 4 张图', 'warn'); return false }
    const reader = new FileReader()
    const abort = () => reader.abort()
    signal.addEventListener('abort', abort, { once: true })
    reader.addEventListener('loadend', () => signal.removeEventListener('abort', abort), { once: true })
    reader.onload = () => {
      if (isStale()) return
      pendingImages.push({ dataUrl: String(reader.result || ''), name: file.name || '图片' })
      renderChatThumbs()
    }
    reader.onerror = () => toast('读取图片失败', 'error')
    reader.readAsDataURL(file)
    return true
  }

  const chatFileInput = h('input', {
    type: 'file', accept: 'image/*', multiple: true, style: { display: 'none' },
    onchange: ev => {
      for (const file of Array.from((ev.target && ev.target.files) || [])) addImageFile(file)
      if (ev.target) ev.target.value = ''
    }
  })
  const attachBtn = h('button.btn.sm.ghost', {
    type: 'button',
    title: '贴图提问：选图片，或直接 Ctrl+V 粘贴、把图片拖进来',
    onclick: () => chatFileInput.click()
  }, '🖼 图片')

  const webToggle = h('input.check', {
    type: 'checkbox',
    id: 'chat-web',
    checked: getState().prefs.chatUseWeb === true
  })
  const webLabel = h('label', {
    style: { display: 'flex', gap: '5px', alignItems: 'center', fontSize: '12.5px', color: 'var(--muted)' },
    title: '勾上后这次提问会用 Exa 联网检索（约 $0.011/次，走最便宜的基础档）；不勾就用免费的学术检索。先在「设置 → API服务 → 网络检索」里配好 Exa Key。'
  }, webToggle, '联网搜索')
  webToggle.addEventListener('change', () => {
    savePrefs({ chatUseWeb: !!webToggle.checked }, { silent: true })
  })

  chatInput.addEventListener('paste', ev => {
    const items = (ev.clipboardData && ev.clipboardData.items) || []
    let handled = false
    for (const item of items) {
      if (item.kind === 'file' && /^image\//.test(item.type || '')) {
        const file = item.getAsFile()
        if (file && addImageFile(file)) handled = true
      }
    }
    if (handled) ev.preventDefault()
  })
  chatInput.addEventListener('dragover', ev => { ev.preventDefault() })
  chatInput.addEventListener('drop', ev => {
    const files = (ev.dataTransfer && ev.dataTransfer.files) || []
    if (!files.length) return
    let handled = false
    for (const file of Array.from(files)) if (addImageFile(file)) handled = true
    if (handled) ev.preventDefault()
  })

  function chatBubble (msg, msgIndex, pairIndex) {
    const isUser = msg.role === 'user'
    return h('div.chat-msg' + (isUser ? '.user' : '.ai'), {
      'data-msg-index': String(msgIndex),
      'data-pair-index': String(pairIndex)
    },
      h('div.chat-meta',
        isUser ? '我' : ('AI' + (msg.model ? ' · ' + msg.model : '')),
        msg.at ? h('span', { style: { marginLeft: '8px', color: 'var(--faint)' } }, util.relTime(msg.at)) : null,
        isUser && msg.images
          ? h('span', { style: { marginLeft: '8px', color: 'var(--faint)' } }, '（含 ' + msg.images + ' 张图）')
          : null,
        !isUser && msg.sources && msg.sources.length
          ? h('span', { style: { marginLeft: '8px', color: 'var(--faint)' } },
            '来源：' + msg.sources.map(s => String(s).slice(0, 18)).join('、'))
          : null
      ),
      isUser
        ? h('div.chat-text', { text: msg.content })
        : h('div.chat-text.md', { html: md.render(msg.content, { byTitle: lookupTitle }) })
    )
  }

  function renderChat (messages) {
    const list = messages || []
    const pairs = []
    list.forEach((msg, idx) => {
      if (msg.role === 'user') pairs.push({ index: idx, question: msg.content || '', at: msg.at })
    })
    chatQCount.textContent = pairs.length ? String(pairs.length) : ''

    if (!list.length) {
      chatBody.replaceChildren(h('div.hint', { style: { padding: '10px 0' } },
        '还没有对话。可以问：这篇解决了什么问题？方法的关键假设是什么？结论对哪些体系成立？'))
    } else {
      chatBody.replaceChildren(...list.map((msg, idx) => {
        const owner = pairs.filter(p => p.index <= idx).pop()
        return chatBubble(msg, idx, owner ? owner.index : -1)
      }))
    }

    if (!pairs.length) {
      chatQList.replaceChildren(h('div.chat-q-empty', '还没有提问'))
    } else {
      chatQList.replaceChildren(...pairs.map(pair => h('div.chat-q-item', {
        'data-pair-index': String(pair.index)
      },
        h('button.chat-q-text', {
          type: 'button',
          title: pair.question,
          onclick: () => scrollToPair(pair.index)
        },
          h('span.chat-q-num', 'Q' + (pairs.indexOf(pair) + 1)),
          h('span', { text: pair.question.length > 40 ? pair.question.slice(0, 40) + '…' : pair.question }),
          pair.at ? h('span.chat-q-time', util.relTime(pair.at)) : null
        ),
        h('button.chat-q-del', {
          type: 'button',
          title: '删除这一问一答',
          onclick: async ev => {
            ev.stopPropagation()
            const ok = await confirm({
              title: '删除这条提问？',
              body: '「' + (pair.question.length > 30 ? pair.question.slice(0, 30) + '…' : pair.question) + '」以及它的回答会一起删掉。',
              okText: '删除',
              danger: true
            })
            if (!ok) return
            try {
              const res = await api.deleteChatPair(paper.id, pair.index)
              paper.chat = res.messages || []
              renderChat(paper.chat)
              chatStatus.textContent = '已删除这条提问'
              toast('已删除')
            } catch (err) { toast(err.message, 'error') }
          }
        }, '×')
      )))
    }

    if (list.length) scrollChatToEnd()
  }

  function scrollToPair (pairIndex) {
    const want = String(pairIndex)
    let target = null
    for (const node of chatBody.childNodes || []) {
      if (node.getAttribute && node.getAttribute('data-pair-index') === want) { target = node; break }
    }
    for (const node of chatBody.childNodes || []) {
      if (node.classList) node.classList.remove('hl')
    }
    if (target && target.classList) {
      target.classList.add('hl')
      const box = typeof chatBody.getBoundingClientRect === 'function' ? chatBody.getBoundingClientRect() : null
      const rect = typeof target.getBoundingClientRect === 'function' ? target.getBoundingClientRect() : null
      if (box && rect) {
        chatBody.scrollTop = Math.max(0, chatBody.scrollTop + (rect.top - box.top) - 6)
      } else if (typeof target.offsetTop === 'number') {
        chatBody.scrollTop = Math.max(0, target.offsetTop - 6)
      }
    }
    for (const item of chatQList.childNodes || []) {
      if (item.classList) item.classList.toggle('active', item.getAttribute('data-pair-index') === want)
    }
    if (target && target.classList) later(() => target.classList.remove('hl'), 1600)
  }
  renderChat(paper.chat)

  let streaming = false

  async function askPaper (question, images, options) {
    const opts = options || {}
    const q = String(question || '').trim() || '解释这张图/表说明了什么。'
    if (streaming) return
    const pics = (images || []).slice(0, 4)
    const label = String(opts.label || (pics.length ? '含 ' + pics.length + ' 张图' : ''))
    const readinessNote = h('div.chat-msg.ai', { id: 'chat-stream-wrap' }, h('div.chat-meta', 'AI'),
      h('div.chat-text', { id: 'chat-stream' }, '…'))
    const streamText = readinessNote.querySelector('.chat-text')
    streaming = true
    sendButton.setAttribute('disabled', '')
    chatStatus.textContent = pics.length ? '正在把图片发给模型…' : '正在准备资料…'
    chatStatus.style.color = ''
    const optimistic = (paper.chat || []).concat([{
      role: 'user', content: q, at: new Date().toISOString(), images: pics.length || undefined
    }])
    chatBody.replaceChildren(...optimistic.map((msg, idx) => chatBubble(msg, idx, idx)), readinessNote)
    chatBody.scrollTop = chatBody.scrollHeight
    let acc = ''
    let coverage = null
    try {
      const hooks = {
        onStart: info => {
          if (isStale()) return
          coverage = info.fullRead || null
          const fr = coverage
          chatStatus.textContent = pics.length
            ? ('已发送 ' + pics.length + ' 张图，模型 ' + (info.model || '') + ' 正在回答…')
            : (fr && fr.totalChars
                ? `正在依据正文与 SI 全文回答${fr.missing && fr.missing.length ? `；⚠ ${fr.missing.length} 个 SI 未提取到文字` : ''}…`
                : ('资料来源：' + ((info.sources || []).join('、') || '题录')))
          if (info.autoExtract && info.autoExtract.ok) {
            onFulltextReady()
          }
        },
        onDelta: chunk => {
          if (isStale()) return
          acc += chunk
          streamText.innerHTML = md.render(acc, { byTitle: lookupTitle })
          chatBody.scrollTop = chatBody.scrollHeight
        }
      }
      if (pics.length) {
        await api.paperVisionStream(paper.id, {
          question: q,
          images: pics.map(p => (typeof p === 'string' ? p : p.dataUrl)),
          label
        }, hooks)
      } else {
        await api.paperChatStream(paper.id, q, Object.assign({ web: !!(webToggle && webToggle.checked) }, hooks))
      }
      if (isStale()) return
      chatStatus.textContent = coverage && coverage.totalChars
        ? '已依据文献资料回答，并保存到对话历史'
        : '已保存到这篇文献的对话历史'
      chatStatus.style.color = 'var(--accent-text)'
      await refreshChat()
    } catch (err) {
      if (isStale()) return
      chatStatus.textContent = '失败：' + err.message
      chatStatus.style.color = 'var(--danger)'
      toast('提问失败：' + err.message, 'error')
      await refreshChat()
    } finally {
      streaming = false
      sendButton.removeAttribute('disabled')
    }
  }

  async function sendQuestion () {
    const question = String(chatInput.value || '').trim()
    if (!question && !pendingImages.length) return
    if (streaming) return
    const pics = pendingImages.slice()
    pendingImages = []
    renderChatThumbs()
    chatInput.value = ''
    await askPaper(question, pics, {})
  }

  async function refreshChat () {
    try {
      if (isStale()) return
      const res = await api.getChat(paper.id, signal)
      if (isStale()) return
      paper.chat = res.messages || []
      renderChat(paper.chat)
    } catch {}
  }

  const sendButton = h('button.btn.primary', { type: 'button', onclick: sendQuestion }, '发送')

  function openRegionAsk (dataUrl, info) {
    const pageLabel = (info?.source ? info.source + ' · ' : '') + '第 ' + (info?.page || '?') + ' 页框选'
    const preview = h('img', { src: dataUrl, alt: '框选区域预览', style: { maxWidth: '100%', maxHeight: '180px', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' } })
    const input = h('textarea.textarea', { rows: 3 },
      '解释这张图/表说明了什么。如果是表格，请说明各列含义与主要数据；如果是电镜/形貌图，请描述形貌特征（形状、大小量级、是否聚集）。')
    const answerBox = h('div.md', { style: { maxHeight: '260px', overflow: 'auto' } })
    const status = h('div.hint', '')
    const sendBtn = h('button.btn.primary', { type: 'button' }, '发送')
    const closeBtn = h('button.btn', { type: 'button' }, '关闭')

    const overlay = h('div.modal-back', { onclick: ev => { if (ev.target === overlay) close() } },
      h('div.modal', { role: 'dialog', 'aria-modal': 'true', style: { maxWidth: '720px' } },
        h('h3', '识图提问（' + pageLabel + '）'),
        h('div', { style: { display: 'grid', gap: '10px', maxHeight: '60vh', overflow: 'auto' } },
          preview,
          input,
          status,
          answerBox
        ),
        h('div.actions', sendBtn, closeBtn)
      )
    )
    const close = showOverlay(overlay)
    closeBtn.addEventListener('click', close)

    sendBtn.addEventListener('click', async () => {
      const question = String(input.value || '').trim() || '解释这张图/表说明了什么。'
      sendBtn.setAttribute('disabled', '')
      status.textContent = '正在把图片发给模型…'
      status.style.color = ''
      answerBox.replaceChildren(h('div', { style: { color: 'var(--muted)' } }, '…'))
      try {
        let acc = ''
        await api.paperVisionStream(paper.id, {
          question: question,
          images: [dataUrl],
          label: pageLabel
        }, {
          onDelta: chunk => {
            if (isStale() || !overlay.isConnected) return
            acc += chunk
            answerBox.innerHTML = md.render(acc, { byTitle: lookupTitle })
          }
        })
        if (isStale()) return
        status.textContent = '已保存到这篇文献的对话历史'
        status.style.color = 'var(--accent-text)'
        await refreshChat()
        if (!isStale()) onSwitchToChat()
      } catch (err) {
        status.textContent = '失败：' + err.message
        status.style.color = 'var(--danger)'
      } finally {
        sendBtn.removeAttribute('disabled')
      }
    })
    if (typeof input.focus === 'function') input.focus()
  }

  const readViewChat = h('div.read-view.chat-view', { id: 'read-view-chat', hidden: true },
    h('div.chat-wrap',
      chatQsPane,
      h('div.chat-main',
        chatBody,
        h('div.chat-compose',
          chatThumbs,
          chatInput,
          h('div.chat-compose-foot',
            sendButton,
            attachBtn,
            chatFileInput,
            webLabel,
            chatStatus,
            h('span', { style: { marginLeft: 'auto', fontSize: '12.5px', color: 'var(--faint)' } },
              '可贴图提问 · 资料来自：正文 + 补充材料 + 本库检索 + 联网检索')
          )
        )
      )
    )
  )

  function focusInput () {
    try { chatInput.focus({ preventScroll: true }) } catch { chatInput.focus() }
  }

  return { readViewChat, scrollChatToEnd, focusInput, refreshChat, openRegionAsk, cancelScroll }
}
