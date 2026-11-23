/**
 * 开屏公告小弹窗（方案 A：本地内置 public/notice.json）。
 *
 * 设计约束：
 *   - 独立于 update-manifest.json（更新清单契约 schema=1 已冻结，不能加未知字段）
 *   - 纯文本 + 结构化字段渲染，绝不插入原始 HTML；链接与图片做协议白名单校验
 *   - 按公告 id 去重（记入 data/prefs.json 与 localStorage），同条公告不重复打扰
 *   - 读取失败或断网静默跳过，不阻塞应用启动
 *   - 零外部模块依赖，便于在 Node 中直接单测（test/js/notice.test.mjs）
 */

export const NOTICE_URL = '/notice.json'
const FETCH_TIMEOUT_MS = 3000

/** 轻量 DOM 节点构造（只写 textContent，不碰 innerHTML） */
function h (tag, props, ...children) {
  const parts = String(tag || 'div').split(/(?=[.#])/)
  const node = document.createElement(parts[0] || 'div')
  for (let i = 1; i < parts.length; i++) {
    const token = parts[i]
    if (token.startsWith('#')) node.id = token.slice(1)
    else if (token.startsWith('.')) node.classList.add(token.slice(1))
  }
  let attrs = props
  if (children.length && props && (props.nodeType || typeof props === 'string' || Array.isArray(props))) {
    children.unshift(props)
    attrs = null
  }
  if (attrs && typeof attrs === 'object' && !attrs.nodeType && !Array.isArray(attrs)) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue
      if (key.startsWith('on') && typeof value === 'function') {
        node.addEventListener(key.slice(2).toLowerCase(), value)
      } else if (value === true) {
        node.setAttribute(key, '')
      } else {
        node.setAttribute(key, String(value))
      }
    }
  } else if (attrs != null) {
    children.unshift(attrs)
  }
  const appendAll = list => {
    for (const item of list) {
      if (item == null || item === false || item === true) continue
      if (Array.isArray(item)) appendAll(item)
      else if (item.nodeType) node.appendChild(item)
      else node.appendChild(document.createTextNode(String(item)))
    }
  }
  appendAll(children)
  return node
}

/** 只放行安全外链协议（https / mailto），拒绝 javascript:/data:/http: 等 */
export function safeExternalUrl (raw) {
  const s = String(raw == null ? '' : raw).trim()
  if (!s) return ''
  if (/^mailto:[^\s<>"]+$/i.test(s)) return s
  try {
    const u = new URL(s)
    if (u.protocol === 'https:') return u.href
  } catch {}
  return ''
}

/** 只放行 https 图片或安全的站内相对静态路径（不含 .. 与非法协议） */
export function safeImageSrc (raw) {
  const s = String(raw == null ? '' : raw).trim()
  if (!s) return ''
  if (/^https:\/\//i.test(s)) {
    try {
      const u = new URL(s)
      return u.protocol === 'https:' ? u.href : ''
    } catch {
      return ''
    }
  }
  if (/^[a-z][a-z0-9+.-]*:/i.test(s) || s.startsWith('//')) return ''
  const cleaned = s.replace(/\\/g, '/')
  if (cleaned.split('/').some(seg => seg === '..')) return ''
  return cleaned.startsWith('/') ? cleaned : '/' + cleaned
}

/** 判断公告是否已过期；未填或格式不可解析时按「不过期」处理 */
export function isNoticeExpired (notice, nowMs) {
  if (!notice || !notice.expiresAt) return false
  const ts = Date.parse(notice.expiresAt)
  if (Number.isNaN(ts)) return false
  const now = typeof nowMs === 'number' ? nowMs : Date.now()
  return now > ts
}

/** 校验并规整公告 JSON；缺少 id 或内容全空返回 null */
export function normalizeNotice (raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return null
  const id = String(raw.id == null ? '' : raw.id).trim()
  if (!id) return null

  const title = String(raw.title == null ? '' : raw.title).trim()
  const body = String(raw.body == null ? '' : raw.body).trim()
  const bullets = Array.isArray(raw.bullets)
    ? raw.bullets.map(item => String(item == null ? '' : item).trim()).filter(Boolean)
    : []

  if (!title && !body && !bullets.length) return null

  const badge = String(raw.badge == null ? '' : raw.badge).trim()
  const date = String(raw.date == null ? '' : raw.date).trim()
  const image = safeImageSrc(raw.image)
  const expiresAt = String(raw.expiresAt == null ? '' : raw.expiresAt).trim()

  let action = null
  if (raw.action && typeof raw.action === 'object') {
    const label = String(raw.action.label == null ? '' : raw.action.label).trim()
    const url = safeExternalUrl(raw.action.url)
    if (label && url) action = { label, url }
  }

  let promo = null
  if (raw.promo && typeof raw.promo === 'object') {
    const pTitle = String(raw.promo.title == null ? '' : raw.promo.title).trim()
    const pDesc = String(raw.promo.desc == null ? '' : raw.promo.desc).trim()
    if (pTitle || pDesc) {
      promo = {
        tag: String(raw.promo.tag == null ? '' : raw.promo.tag).trim(),
        title: pTitle,
        desc: pDesc,
        linkLabel: String(raw.promo.linkLabel == null ? '了解详情 ↗' : raw.promo.linkLabel).trim() || '了解详情 ↗',
        url: safeExternalUrl(raw.promo.url)
      }
    }
  }

  return {
    id,
    enabled: raw.enabled !== false,
    badge,
    date,
    title,
    image,
    body,
    bullets,
    action,
    promo,
    expiresAt
  }
}

/** 判断本次启动是否需要自动弹出 */
export function shouldShowNotice (notice, prefs, options) {
  const opts = options || {}
  if (!notice) return false
  if (opts.force) return true
  if (!notice.enabled) return false
  if (isNoticeExpired(notice, opts.now)) return false
  const dismissedId = String((prefs && prefs.noticeDismissedId) || '').trim()
  if (dismissedId && dismissedId === notice.id) return false
  return true
}

/** 从本地静态资源拉取 notice.json（超时或异常静默返回 null） */
export async function fetchLocalNotice (options) {
  const opts = options || {}
  const fetchImpl = opts.fetchImpl || (typeof fetch === 'function' ? fetch : null)
  if (!fetchImpl) return null
  const url = opts.url || NOTICE_URL
  const timeoutMs = Number(opts.timeoutMs) > 0 ? Number(opts.timeoutMs) : FETCH_TIMEOUT_MS

  const controller = typeof AbortController === 'function' ? new AbortController() : null
  const timer = controller ? setTimeout(() => controller.abort(), timeoutMs) : null
  try {
    const res = await fetchImpl(url, {
      cache: 'no-cache',
      signal: controller ? controller.signal : undefined
    })
    if (!res || !res.ok) return null
    const data = await res.json()
    return normalizeNotice(data)
  } catch {
    return null
  } finally {
    if (timer) clearTimeout(timer)
  }
}

/**
 * 渲染并打开开屏公告小弹窗。
 * 返回控制句柄 { close, element }，方便测试或外部主动关闭。
 */
export function openNoticeModal (notice, options) {
  const opts = options || {}
  const item = normalizeNotice(notice)
  if (!item || typeof document === 'undefined') return null

  const existing = document.querySelector('.notice-modal-back')
  if (existing) existing.remove()

  const prevFocus = document.activeElement
  const defaultChecked = opts.defaultDismissChecked !== false

  const dismissInput = h('input', {
    type: 'checkbox',
    id: 'notice-dismiss-checkbox'
  })
  dismissInput.checked = defaultChecked

  let closed = false
  function close () {
    if (closed) return
    closed = true
    window.removeEventListener('keydown', onKeyDown, true)
    back.remove()
    if (typeof opts.onClose === 'function') {
      try { opts.onClose({ id: item.id, dismiss: !!dismissInput.checked }) } catch {}
    }
    if (prevFocus && typeof prevFocus.focus === 'function' && document.contains(prevFocus)) {
      try { prevFocus.focus({ preventScroll: true }) } catch {}
    }
  }

  function onKeyDown (ev) {
    if (ev.key === 'Escape') {
      ev.preventDefault()
      ev.stopImmediatePropagation()
      close()
    }
  }

  const headMeta = h('div.notice-meta',
    item.badge ? h('span.notice-pill', h('span.dot'), item.badge) : null,
    item.date ? h('span.notice-date', item.date) : null
  )

  const closeBtn = h('button.notice-close', {
    type: 'button',
    title: '关闭（Esc）',
    'aria-label': '关闭公告',
    onclick: () => close()
  }, '✕')

  let bannerEl = null
  if (item.image) {
    const img = h('img.notice-banner-img', {
      src: item.image,
      alt: item.title || '公告配图',
      onerror: () => { if (bannerEl) bannerEl.remove() }
    })
    bannerEl = h('div.notice-banner', img)
  }

  const titleBlock = (item.title || item.body)
    ? h('div.notice-title-block',
      item.title ? h('h3.notice-title', { id: 'notice-modal-title' }, item.title) : null,
      item.body ? h('p.notice-lead', item.body) : null
    )
    : null

  const listEl = item.bullets.length
    ? h('ul.notice-list', item.bullets.map(line => h('li', line)))
    : null

  let promoEl = null
  if (item.promo) {
    const p = item.promo
    const left = h('div.notice-promo-left',
      (p.tag || p.title)
        ? h('div.notice-promo-top',
          p.tag ? h('span.notice-promo-tag', p.tag) : null,
          p.title ? h('span.notice-promo-title', p.title) : null
        )
        : null,
      p.desc ? h('div.notice-promo-desc', p.desc) : null
    )
    const link = p.url
      ? h('a.notice-promo-link', {
        href: p.url,
        target: '_blank',
        rel: 'noopener noreferrer'
      }, p.linkLabel)
      : null
    promoEl = h('div.notice-promo', left, link)
  }

  const primaryBtn = h('button.btn.primary', {
    type: 'button',
    id: 'notice-ok-btn',
    onclick: () => close()
  }, '我知道了')

  const actionLink = item.action
    ? h('a.btn', {
      href: item.action.url,
      target: '_blank',
      rel: 'noopener noreferrer'
    }, item.action.label)
    : null

  const foot = h('div.notice-foot',
    h('label.notice-check', dismissInput, h('span', '不再提示此公告')),
    h('div.notice-actions', actionLink, primaryBtn)
  )

  const card = h('div.modal.notice-modal', {
    role: 'dialog',
    'aria-modal': 'true',
    'aria-labelledby': item.title ? 'notice-modal-title' : undefined
  },
    h('div.notice-accent-bar'),
    h('div.notice-inner',
      h('div.notice-head', headMeta, closeBtn),
      bannerEl,
      titleBlock,
      listEl,
      promoEl,
      foot
    )
  )

  const back = h('div.modal-back.notice-modal-back', {
    onclick: ev => { if (ev.target === back) close() }
  }, card)

  document.body.appendChild(back)
  window.addEventListener('keydown', onKeyDown, true)
  try { primaryBtn.focus({ preventScroll: true }) } catch {}

  return { close, element: back, notice: item }
}

/** 启动时按规则尝试弹出公告（仅首页触发，失败静默） */
export async function maybeShowStartupNotice (options) {
  const opts = options || {}
  if (typeof opts.isHomeRoute === 'function' && !opts.isHomeRoute()) return null
  const notice = typeof opts.fetchNotice === 'function'
    ? await opts.fetchNotice()
    : await fetchLocalNotice()
  const prefs = typeof opts.getPrefs === 'function' ? opts.getPrefs() : {}
  if (!shouldShowNotice(notice, prefs, { force: !!opts.force, now: opts.now })) return null

  return openNoticeModal(notice, {
    defaultDismissChecked: true,
    onClose: ({ id, dismiss }) => {
      if (dismiss && typeof opts.savePrefs === 'function') {
        opts.savePrefs({ noticeDismissedId: id }, { silent: true })
      }
    }
  })
}
