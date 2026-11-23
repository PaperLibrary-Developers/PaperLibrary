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
    { path: '电催化/自旋与磁场', name: '自旋与磁场', parent: '电催化', depth: 1, count: 1, countDeep: 2, sort: 4 },
    { path: '电催化/自旋与磁场/洛伦兹力', name: '洛伦兹力', parent: '电催化/自旋与磁场', depth: 2, count: 1, countDeep: 1 },
    { path: '电催化/自旋与磁场/洛伦兹力/细分', name: '细分', parent: '电催化/自旋与磁场/洛伦兹力', depth: 3, count: 0, countDeep: 0 },
    { path: '电催化/CO₂ 还原', name: 'CO₂ 还原', parent: '电催化', depth: 1, count: 1, countDeep: 1, sort: 2 }
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

test('a delayed folder refresh cannot replace a newer paper list', async () => {
  const doc = makeDoc()
  const origDoc = globalThis.document
  const origFetch = globalThis.fetch
  const origLoc = globalThis.location
  const ac = new AbortController()
  const older = Promise.withResolvers()
  let paperReads = 0
  const results = title => new Response(JSON.stringify({
    ok: true, total: 1, page: 1, pageCount: 1,
    items: [{ id: 'p1', title, authors: ['A'], type: 'journal', folder: '电催化' }]
  }))
  globalThis.document = doc
  globalThis.location = { hash: '#/?folder=%E7%94%B5%E5%82%AC%E5%8C%96' }
  globalThis.fetch = async url => {
    if (url.startsWith('/api/folders')) return new Response(JSON.stringify(SAMPLE_FOLDERS))
    if (++paperReads === 1) return older.promise
    return results('Current paper title')
  }

  try {
    const outlet = new doc.El('div')
    listHandler({
      outlet, path: '/', params: new URLSearchParams('folder=电催化'),
      signal: ac.signal, isStale: () => ac.signal.aborted
    })
    doc.dispatchEvent({ type: 'folders:changed' })
    await flush()
    assert.equal(paperReads, 2)
    assert.ok(outlet.textContent.includes('Current paper title'))

    older.resolve(results('Stale paper title'))
    await flush()
    assert.ok(outlet.textContent.includes('Current paper title'))
    assert.ok(!outlet.textContent.includes('Stale paper title'))
  } finally {
    ac.abort()
    globalThis.document = origDoc
    globalThis.fetch = origFetch
    globalThis.location = origLoc
  }
})

test('folder views show one ordered grid of direct child cards and restore the URL scope', async () => {
  const doc = makeDoc()
  const origDoc = globalThis.document
  const origFetch = globalThis.fetch
  const origLoc = globalThis.location
  const paperUrls = []
  globalThis.document = doc
  globalThis.location = { hash: '#/' }
  globalThis.fetch = async url => {
    if (url.startsWith('/api/folders')) return new Response(JSON.stringify(SAMPLE_FOLDERS))
    paperUrls.push(url)
    return new Response(JSON.stringify({
      ok: true, total: 1, page: 1, pageCount: 1,
      items: [{ id: 'p1', title: 'Sample Paper', authors: ['A'], year: 2025, type: 'journal' }]
    }))
  }

  let scope
  async function renderList (path, query) {
    scope?.abort()
    scope = new AbortController()
    const { signal } = scope
    const outlet = new doc.El('div')
    listHandler({ outlet, path, params: new URLSearchParams(query), signal, isStale: () => signal.aborted })
    await flush()
    return outlet
  }
  const childPaths = outlet => outlet.queryAll(n => n.classList.contains('fld-child'))
    .map(card => card.getAttribute('data-path'))

  try {
    const root = await renderList('/', 'folder=电催化')
    assert.match(paperUrls.at(-1), /includeSubfolders=false/)
    assert.deepEqual(childPaths(root), ['电催化/CO₂ 还原', '电催化/自旋与磁场'])
    assert.equal(root.queryAll(n => n.classList.contains('fld-back-btn'))[0].textContent, '← 主页')
    assert.deepEqual(root.queryAll(n => n.classList.contains('fld-crumb')).map(n => n.textContent), ['主页', '电催化'])

    const child = await renderList('/', 'folder=电催化/自旋与磁场')
    assert.deepEqual(childPaths(child), ['电催化/自旋与磁场/洛伦兹力'])

    const grandchild = await renderList('/', 'folder=电催化/自旋与磁场/洛伦兹力')
    assert.deepEqual(childPaths(grandchild), ['电催化/自旋与磁场/洛伦兹力/细分'])
    assert.equal(grandchild.queryAll(n => n.classList.contains('fld-children-grid')).length, 1)

    const leaf = await renderList('/', 'folder=电催化/自旋与磁场/洛伦兹力/细分')
    assert.equal(leaf.queryAll(n => n.classList.contains('fld-children'))[0].hidden, true)
    assert.deepEqual(childPaths(leaf), [])

    const recursive = await renderList('/', 'folder=电催化&deep=1')
    assert.match(paperUrls.at(-1), /includeSubfolders=true/)
    const checkbox = recursive.queryAll(n => n.getAttribute('type') === 'checkbox')[0]
    assert.equal(checkbox.getAttribute('checked'), '')
    assert.deepEqual(childPaths(recursive), childPaths(root))

    const all = await renderList('/all', '')
    assert.equal(all.queryAll(n => n.classList.contains('fld-home-card')).length, 0)
    assert.deepEqual(childPaths(all), [])
    assert.ok(all.textContent.includes('Sample Paper'))
  } finally {
    scope?.abort()
    globalThis.document = origDoc
    globalThis.fetch = origFetch
    globalThis.location = origLoc
  }
})
