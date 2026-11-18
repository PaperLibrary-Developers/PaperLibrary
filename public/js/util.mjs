/* 通用工具：DOM 构建、格式化、防抖、剪贴板（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as htmlText from '/shared/html-text.mjs'

  /* ---------------- DOM ---------------- */

  /** h('div.card', { title: 'x' }, h('span', '文字'), '纯文本') */
  function h (tag, props, ...children) {
    const parts = String(tag || 'div').split(/(?=[.#])/)
    const el = document.createElement(parts[0] || 'div')
    for (let i = 1; i < parts.length; i++) {
      const token = parts[i]
      if (token.startsWith('#')) el.id = token.slice(1)
      else if (token.startsWith('.')) el.classList.add(token.slice(1))
    }
    let attrs = props
    if (children.length && props && (props.nodeType || typeof props === 'string' || Array.isArray(props))) {
      // 第二个参数其实是子节点
      children.unshift(props)
      attrs = null
    }
    if (attrs && typeof attrs === 'object' && !attrs.nodeType && !Array.isArray(attrs)) {
      for (const [key, value] of Object.entries(attrs)) {
        if (value == null || value === false) continue
        if (key === 'class' || key === 'className') { String(value).split(/\s+/).filter(Boolean).forEach(c => el.classList.add(c)); continue }
        if (key === 'style' && typeof value === 'object') { Object.assign(el.style, value); continue }
        if (key === 'dataset' && typeof value === 'object') { Object.assign(el.dataset, value); continue }
        if (key === 'html') { el.innerHTML = value; continue }
        if (key === 'text') { el.textContent = value; continue }
        if (key.startsWith('on') && typeof value === 'function') { el.addEventListener(key.slice(2).toLowerCase(), value); continue }
        if (value === true) { el.setAttribute(key, ''); continue }
        el.setAttribute(key, String(value))
      }
    } else if (attrs != null) {
      children.unshift(attrs)
    }
    append(el, children)
    return el
  }

  function append (parent, children) {
    for (const child of children) {
      if (child == null || child === false || child === true) continue
      if (Array.isArray(child)) append(parent, child)
      else if (child.nodeType) parent.appendChild(child)
      else parent.appendChild(document.createTextNode(String(child)))
    }
    return parent
  }

  /**
   * 文案里用 **重点** 标出加粗：返回「文本 + <b>」的节点数组。
   *
   * 界面里大量提示是纯文本拼出来的（不走 markdown 渲染），直接写 ** 会原样显示星号，
   * 之前「删除文件夹」确认框、设置页几处提示就露着 `**`。统一走这个函数，少写一堆拆分。
   */
  function rich (text) {
    const src = String(text == null ? '' : text)
    const out = []
    const re = /\*\*([^*]+)\*\*/g
    let last = 0
    let m
    while ((m = re.exec(src))) {
      if (m.index > last) out.push(src.slice(last, m.index))
      out.push(h('b', m[1]))
      last = m.index + m[0].length
    }
    if (last < src.length) out.push(src.slice(last))
    return out
  }

  /**
   * 安全地替换子节点：等于 el.replaceChildren(...) 的「会跳过空值」版本。
   *
   * 为什么需要它：浏览器里 `el.replaceChildren(node, null)` 会把 null **当成字符串**插进去，
   * 页面上就多出一个写着 "null" 的文本节点（项目里踩过：书库筛选条、详情页的几个条件面板）。
   * 所以凡是可能传 null 的地方都用这个函数，行为跟 h() 一致。
   */
  function replace (el, ...nodes) {
    if (!el || typeof el.replaceChildren !== 'function') return el
    const flat = []
    const push = item => {
      if (item == null || item === false || item === true) return
      if (Array.isArray(item)) { item.forEach(push); return }
      flat.push(item)
    }
    nodes.forEach(push)
    el.replaceChildren(...flat)
    return el
  }

  /** 用 HTML 字符串生成节点（只用于自己拼好的、已转义的内容） */
  function fromHtml (html) {
    const tpl = document.createElement('template')
    tpl.innerHTML = String(html || '').trim()
    return tpl.content
  }

  function mount (el) {
    const app = document.getElementById('app')
    app.hidden = false
    app.replaceChildren(el)
    const boot = document.getElementById('boot')
    if (boot) boot.remove()
    return el
  }

  function on (el, event, selector, handler) {
    el.addEventListener(event, ev => {
      const target = ev.target.closest(selector)
      if (target && el.contains(target)) handler(ev, target)
    })
  }

  /* ---------------- 格式化 ---------------- */

  function escapeHtml (s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
  }

  /**
   * 标题 / 期刊这类「从别处搬来的文本」显示前的兜底清洗。
   * 服务端抓取时已经清过一遍，这里再兜一次是给**旧缓存**用的
   * （切走再回来的 lastSession 里可能是修复前抓到的、带 `&lt;i&gt;` 的结果）。
   * 实现是 shared/html-text.mjs，与 server/utils.js 的 cleanTitle 同一份逻辑。
   */
  /**
   * 标题 / 期刊这类「从别处搬来的文本」显示前的兜底清洗。
   * 服务端抓取时已经清过一遍，这里再兜一次是给**旧缓存**用的
   * （切走再回来的 lastSession 里可能是修复前抓到的、带 `&lt;i&gt;` 的结果）。
   * 实现是 html-text.mjs，与 server/utils.js 的 cleanTitle 同一份逻辑。
   */
  function toPlainText (s) {
    return htmlText.toPlainText(s)
  }

  function formatDate (iso) {
    if (!iso) return '—'
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return String(iso)
    const pad = n => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
  }

  function relTime (iso) {
    if (!iso) return ''
    const d = new Date(iso).getTime()
    if (Number.isNaN(d)) return ''
    const diff = Date.now() - d
    const min = Math.round(diff / 60000)
    if (min < 1) return '刚刚'
    if (min < 60) return min + ' 分钟前'
    const hour = Math.round(min / 60)
    if (hour < 24) return hour + ' 小时前'
    const day = Math.round(hour / 24)
    if (day < 30) return day + ' 天前'
    const month = Math.round(day / 30)
    if (month < 12) return month + ' 个月前'
    return Math.round(month / 12) + ' 年前'
  }

  function fileSize (bytes) {
    const n = Number(bytes) || 0
    if (n < 1024) return n + ' B'
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB'
    return (n / 1024 / 1024).toFixed(1) + ' MB'
  }

  function debounce (fn, wait) {
    let timer = null
    const wrapped = function (...args) {
      clearTimeout(timer)
      timer = setTimeout(() => {
        timer = null
        fn.apply(this, args)
      }, wait)
    }
    // 允许取消：用户中途点了别的东西时，不能让排队中的旧值晚到并覆盖新状态
    wrapped.cancel = function () {
      clearTimeout(timer)
      timer = null
    }
    wrapped.pending = function () { return timer !== null }
    return wrapped
  }

  function throttle (fn, wait) {
    let last = 0
    return function (...args) {
      const now = Date.now()
      if (now - last < wait) return
      last = now
      fn.apply(this, args)
    }
  }

  async function copyText (text) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      try {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.select()
        const ok = document.execCommand('copy')
        ta.remove()
        return ok
      } catch {
        return false
      }
    }
  }

  function deburr (s) { return String(s || '').trim() }

  /**
   * 规整一个「待添加的标签」。
   * 用户经常一次粘贴多个（用逗号/分号/顿号分隔），这里按第一个分隔符切开只取第一个，
   * 同时去掉多余空白与首尾标点——之前直接 trim，导致「a, b」变成一个怪标签。
   */
  function normalizeTagInput (raw) {
    const s = String(raw == null ? '' : raw)
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/^[,，;；、\s]+/, '')
      .replace(/[,，;；、\s]+$/, '')
    if (!s) return ''
    const first = s.split(/[,，;；、]/)[0]
    return first.trim()
  }

  /** 把一段文本按常见分隔符切成多个标签（用于粘贴多个标签） */
  function splitTagInput (raw) {
    return String(raw == null ? '' : raw)
      .split(/[,，;；、\n]+/)
      .map(s => s.replace(/\s+/g, ' ').trim())
      .filter(Boolean)
  }

  /* ---------------- Toast / 对话框 ---------------- */

  function toast (message, kind) {
    const stack = document.getElementById('toasts')
    if (!stack) return
    const el = h('div.toast' + (kind ? '.' + kind : ''), { text: message })
    stack.appendChild(el)
    setTimeout(() => {
      el.style.transition = 'opacity .2s, transform .2s'
      el.style.opacity = '0'
      el.style.transform = 'translateY(6px)'
      setTimeout(() => el.remove(), 220)
    }, kind === 'error' ? 4200 : 2400)
  }

  function confirmDialog (options) {
    const opts = options || {}
    const signal = opts.signal
    if (signal && signal.aborted) return Promise.resolve(false)
    return new Promise(resolve => {
      const close = answer => {
        back.remove()
        document.removeEventListener('keydown', onKey, true)
        if (signal) signal.removeEventListener('abort', onAbort)
        resolve(answer)
      }
      const onAbort = () => close(false)
      const onKey = ev => {
        if (ev.key !== 'Escape' && ev.key !== 'Enter') return
        ev.stopImmediatePropagation()
        ev.preventDefault()
        if (ev.key === 'Escape') close(false)
        if (ev.key === 'Enter') close(true)
      }
      const body = h('div', opts.body || '')
      const back = h('div.modal-back', { onclick: ev => { if (ev.target === back) close(false) } },
        h('div.modal', { role: 'dialog', 'aria-modal': 'true' },
          h('h3', opts.title || '请确认'),
          body,
          h('div.actions',
            h('button.btn', { onclick: () => close(false) }, opts.cancelText || '取消'),
            h('button.btn' + (opts.danger ? '.danger' : '.primary'), { onclick: () => close(true) }, opts.okText || '确定')
          )
        )
      )
      document.body.appendChild(back)
      document.addEventListener('keydown', onKey, true)
      if (signal) signal.addEventListener('abort', onAbort, { once: true })
      const first = back.querySelector('.btn.primary, .btn.danger')
      if (first) first.focus()
    })
  }

  /* ---------------- 卡片元信息 ---------------- */

  const TYPE_LABELS = {
    journal: '期刊',
    conference: '会议',
    preprint: '预印本',
    book: '图书',
    thesis: '学位论文',
    report: '报告',
    web: '网页',
    other: '其他'
  }
  const STATUS_LABELS = { unread: '未读', reading: '在读', read: '已读', archived: '归档' }

  function typeLabel (t) { return TYPE_LABELS[t] || '其他' }
  function statusLabel (s) { return STATUS_LABELS[s] || s || '未读' }

  function authorsText (authors, max) {
    const list = Array.isArray(authors) ? authors : []
    if (!list.length) return '佚名'
    const n = max || 4
    if (list.length <= n) return list.join(', ')
    return list.slice(0, n).join(', ') + ` 等 ${list.length} 人`
  }

  function starsHtml (rating) {
    const r = Math.max(0, Math.min(5, Number(rating) || 0))
    let html = ''
    for (let i = 1; i <= 5; i++) html += i <= r ? '★' : '<span class="off">★</span>'
    return html
  }

  function truncate (text, max) {
    const s = String(text == null ? '' : text)
    if (s.length <= max) return s
    return s.slice(0, max - 1) + '…'
  }

  /**
   * 把文献标题变成安全的文件名片段。
   * Windows 禁用 \ / : * ? " < > |，控制字符也去掉；其余空格归一。
   */
  function safeFileName (title, maxLength) {
    let name = String(title == null ? '' : title)
      .replace(/[\\/:*?"<>|\u0000-\u001f]/g, ' ')  // 非法字符换空格
      .replace(/\s+/g, ' ')
      .trim()
      .replace(/^[.\s]+/, '')                        // 不能以点或空格开头
      .replace(/[.\s]+$/, '')                        // 结尾的点在 Windows 上也会出问题
    if (name.length > (maxLength || 70)) name = name.slice(0, maxLength || 70).trim()
    return name || 'document'
  }

  const exported = {
    h, append, fromHtml, mount, on, rich,
    escapeHtml, toPlainText, formatDate, relTime, fileSize, debounce, throttle, copyText, deburr,
    safeFileName, normalizeTagInput, splitTagInput, replace, truncate,
    toast, confirmDialog,
    typeLabel, statusLabel, authorsText, starsHtml,
    TYPE_LABELS, STATUS_LABELS
  }
  export default exported
  export {
    h, append, fromHtml, mount, on, rich,
    escapeHtml, toPlainText, formatDate, relTime, fileSize, debounce, throttle, copyText, deburr,
    safeFileName, normalizeTagInput, splitTagInput, replace, truncate,
    toast, confirmDialog,
    typeLabel, statusLabel, authorsText, starsHtml,
    TYPE_LABELS, STATUS_LABELS
  }
