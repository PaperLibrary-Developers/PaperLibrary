import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import test from 'node:test'

/** 极简 DOM 桩：足够运行 util.h() 与 listHandler 的渲染分支 */
function makeDoc () {
  const listeners = new Map()
  class El {
    constructor (tag) {
      this.nodeType = 1
      this.tagName = String(tag || 'div').toUpperCase()
      this.className = ''
      this.id = ''
      this.hidden = false
      this.style = {}
      this.dataset = {}
      this.attrs = new Map()
      this.children = []
      this.parentNode = null
      this.innerHTML = ''
      this._text = ''
      this.classList = {
        add: (...cls) => {
          const set = new Set(this.className.split(/\s+/).filter(Boolean))
          cls.forEach(c => set.add(c))
          this.className = [...set].join(' ')
        },
        remove: (...cls) => {
          const set = new Set(this.className.split(/\s+/).filter(Boolean))
          cls.forEach(c => set.delete(c))
          this.className = [...set].join(' ')
        },
        toggle: (c, on) => {
          const set = new Set(this.className.split(/\s+/).filter(Boolean))
          const want = on === undefined ? !set.has(c) : !!on
          if (want) set.add(c); else set.delete(c)
          this.className = [...set].join(' ')
        },
        contains: c => this.className.split(/\s+/).includes(c)
      }
    }
    set textContent (v) { this._text = String(v ?? ''); this.children = [] }
    get textContent () {
      if (this.children.length) return this.children.map(c => typeof c === 'string' ? c : c.textContent).join('')
      return this._text
    }
    setAttribute (k, v) { this.attrs.set(k, String(v)); if (k === 'id') this.id = String(v) }
    getAttribute (k) { return this.attrs.has(k) ? this.attrs.get(k) : null }
    removeAttribute (k) { this.attrs.delete(k) }
    appendChild (child) {
      if (child && typeof child === 'object') child.parentNode = this
      this.children.push(child)
      return child
    }
    replaceChildren (...nodes) {
      this.children = []
      this._text = ''
      for (const n of nodes) {
        if (n == null || n === false) continue
        if (typeof n === 'object') n.parentNode = this
        this.children.push(n)
      }
    }
    remove () {
      if (!this.parentNode) return
      this.parentNode.children = this.parentNode.children.filter(c => c !== this)
      this.parentNode = null
    }
    addEventListener () {}
    removeEventListener () {}
    queryAll (pred) {
      const out = []
      const walk = node => {
        if (!node || typeof node !== 'object') return
        if (pred(node)) out.push(node)
        for (const c of node.children || []) walk(c)
      }
      walk(this)
      return out
    }
  }

  return {
    El,
    createElement: tag => new El(tag),
    createTextNode: text => String(text),
    getElementById: () => null,
    addEventListener: (ev, fn, opts) => {
      if (!listeners.has(ev)) listeners.set(ev, new Set())
      listeners.get(ev).add(fn)
      if (opts && opts.signal) opts.signal.addEventListener('abort', () => listeners.get(ev)?.delete(fn), { once: true })
    },
    removeEventListener: (ev, fn) => listeners.get(ev)?.delete(fn),
    dispatchEvent: ev => { for (const fn of listeners.get(ev.type) || []) fn(ev) }
  }
}

const hooks = registerHooks({
  resolve (specifier, context, nextResolve) {
    const url = specifier === '/shared/html-text.mjs'
      ? new URL('../../shared/html-text.mjs', import.meta.url).href
      : specifier
    return nextResolve(url, context)
  }
})
const { listHandler } = await import('../../public/js/views/list.mjs')
hooks.deregister()

const SAMPLE_FOLDERS = {
  ok: true,
  total: 5,
  rootCount: 1,
  folders: [
    { path: '电催化', name: '电催化', parent: '', depth: 0, count: 1, countDeep: 4 },
    { path: '电催化/自旋与磁场', name: '自旋与磁场', parent: '电催化', depth: 1, count: 1, countDeep: 2 },
    { path: '电催化/自旋与磁场/洛伦兹力', name: '洛伦兹力', parent: '电催化/自旋与磁场', depth: 2, count: 1, countDeep: 1 },
    { path: '电催化/CO₂ 还原', name: 'CO₂ 还原', parent: '电催化', depth: 1, count: 1, countDeep: 1 }
  ]
}

async function flush () {
  for (let i = 0; i < 6; i++) await new Promise(r => setTimeout(r, 0))
}

test('folder home renders top-level folder cards plus independent uncategorized card', async () => {
  const doc = makeDoc()
  const origDoc = globalThis.document
  const origFetch = globalThis.fetch
  const origLoc = globalThis.location
  const calls = []
  globalThis.document = doc
  globalThis.location = { hash: '#/' }
  globalThis.fetch = async url => {
    calls.push(url)
    if (url.startsWith('/api/folders')) {
      return { ok: true, status: 200, text: async () => JSON.stringify(SAMPLE_FOLDERS) }
    }
    return { ok: true, status: 200, text: async () => JSON.stringify({ ok: true, items: [], total: 0, page: 1, pageCount: 1 }) }
  }

  try {
    const outlet = new doc.El('div')
    const ac = new AbortController()
    listHandler({ outlet, path: '/', params: new URLSearchParams(''), signal: ac.signal, isStale: () => ac.signal.aborted })
    await flush()

    const cards = outlet.queryAll(n => n.classList.contains('fld-home-card'))
    const uncat = outlet.queryAll(n => n.classList.contains('fld-home-card--uncat'))
    assert.equal(cards.length, 2, '1 top-level folder card + 1 uncategorized card')
    assert.equal(uncat.length, 1, 'independent uncategorized card shown when rootCount > 0')
    assert.match(cards[0].textContent, /电催化/)
    assert.match(cards[0].textContent, /4 篇/)
    assert.match(uncat[0].textContent, /未分类/)
    assert.equal(calls.some(u => u.startsWith('/api/papers')), false, 'folder home should not fetch all papers')
    ac.abort()
  } finally {
    globalThis.document = origDoc
    globalThis.fetch = origFetch
    globalThis.location = origLoc
  }
})

test('entering folder shows browser tabs defaulting to direct papers, and nested subbar keeps sibling tabs in place', async () => {
  const doc = makeDoc()
  const origDoc = globalThis.document
  const origFetch = globalThis.fetch
  const origLoc = globalThis.location
  const paperUrls = []
  globalThis.document = doc
  globalThis.location = { hash: '#/?folder=%E7%94%B5%E5%82%AC%E5%8C%96' }
  globalThis.fetch = async url => {
    if (url.startsWith('/api/folders')) {
      return { ok: true, status: 200, text: async () => JSON.stringify(SAMPLE_FOLDERS) }
    }
    paperUrls.push(url)
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({
        ok: true,
        items: [{ id: 'p1', title: 'Sample Paper', authors: ['A'], year: 2025, type: 'journal', folder: '电催化' }],
        total: 1,
        page: 1,
        pageCount: 1
      })
    }
  }

  try {
    // 1) 一级目录默认仅直属
    const outlet1 = new doc.El('div')
    const ac1 = new AbortController()
    listHandler({ outlet: outlet1, path: '/', params: new URLSearchParams('folder=电催化'), signal: ac1.signal, isStale: () => ac1.signal.aborted })
    await flush()

    assert.match(paperUrls[0], /includeSubfolders=false/)
    const tabs1 = outlet1.queryAll(n => n.classList.contains('fld-btab'))
    assert.equal(tabs1.length, 4, '仅直属 + 全部 + 2 个二级子目录')
    assert.equal(tabs1[0].classList.contains('active'), true)
    assert.match(tabs1[0].textContent, /仅电催化直属/)
    assert.equal(tabs1[1].classList.contains('active'), false)
    ac1.abort()

    // 2) 切到二级子目录：同级浏览器标签原地保留，且下方展开三级子目录胶囊
    const outlet2 = new doc.El('div')
    const ac2 = new AbortController()
    listHandler({ outlet: outlet2, path: '/', params: new URLSearchParams('folder=电催化/自旋与磁场'), signal: ac2.signal, isStale: () => ac2.signal.aborted })
    await flush()

    const tabs2 = outlet2.queryAll(n => n.classList.contains('fld-btab'))
    assert.equal(tabs2.length, 4, 'top-level browser tabs stay in place')
    assert.equal(tabs2.some(t => t.textContent.includes('CO₂ 还原')), true)
    assert.equal(tabs2.some(t => t.textContent.includes('自旋与磁场') && t.classList.contains('active')), true)
    const pills = outlet2.queryAll(n => n.classList.contains('fld-subpill'))
    assert.equal(pills.length, 3, '仅直属 + 全部 + 洛伦兹力')
    assert.equal(pills[0].classList.contains('active'), true)
    assert.equal(pills.some(p => p.textContent.includes('洛伦兹力')), true)
    ac2.abort()

    // 3) deep=1 激活「全部（含子目录）」并请求 includeSubfolders=true
    const outlet3 = new doc.El('div')
    const ac3 = new AbortController()
    listHandler({ outlet: outlet3, path: '/', params: new URLSearchParams('folder=电催化&deep=1'), signal: ac3.signal, isStale: () => ac3.signal.aborted })
    await flush()

    assert.match(paperUrls[2], /includeSubfolders=true/)
    const tabs3 = outlet3.queryAll(n => n.classList.contains('fld-btab'))
    assert.equal(tabs3[1].classList.contains('active'), true)
    ac3.abort()

    // 4) /all 直接渲染全部文献列表，不渲染文件夹卡片
    const outlet4 = new doc.El('div')
    const ac4 = new AbortController()
    listHandler({ outlet: outlet4, path: '/all', params: new URLSearchParams(''), signal: ac4.signal, isStale: () => ac4.signal.aborted })
    await flush()

    assert.equal(outlet4.queryAll(n => n.classList.contains('fld-home-card')).length, 0)
    assert.equal(outlet4.queryAll(n => n.classList.contains('fld-btab')).length, 0)
    assert.ok(outlet4.textContent.includes('Sample Paper'))
    ac4.abort()
  } finally {
    globalThis.document = origDoc
    globalThis.fetch = origFetch
    globalThis.location = origLoc
  }
})
