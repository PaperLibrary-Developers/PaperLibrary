import assert from 'node:assert/strict'
import { registerHooks } from 'node:module'
import test from 'node:test'
import { request } from '../../public/js/api.mjs'

const hooks = registerHooks({
  resolve (specifier, context, nextResolve) {
    const url = specifier === '/shared/html-text.mjs'
      ? new URL('../../shared/html-text.mjs', import.meta.url).href
      : specifier
    return nextResolve(url, context)
  }
})
const { render, route, setShellHooks } = await import('../../public/js/shell/app-api.mjs')
hooks.deregister()

test('leaving a route aborts its scope and ignores errors from its old render', async () => {
  const chrome = []
  let oldScope
  const outlet = { replaceChildren () { assert.fail('a stale error replaced the new view') } }
  globalThis.location = { hash: '#/qa-old' }
  globalThis.document = { getElementById: () => outlet }
  setShellHooks({ syncChrome: path => chrome.push(path) })
  route('/qa-old', ({ signal }) => new Promise((resolve, reject) => {
    oldScope = signal
    signal.addEventListener('abort', () => reject(signal.reason), { once: true })
  }))
  route('/qa-new', () => {})

  const old = render()
  try {
    assert.equal(oldScope.aborted, false)
    location.hash = '#/qa-new'
    await render()
    await old
    assert.equal(oldScope.aborted, true)
    assert.deepEqual(chrome, ['/qa-new'])
  } finally {
    delete globalThis.location
    delete globalThis.document
  }
})

test('cancelling a route fetch preserves its AbortError', async () => {
  const originalFetch = globalThis.fetch
  const controller = new AbortController()
  globalThis.fetch = (url, { signal }) => new Promise((resolve, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true })
  })
  try {
    const pending = request('GET', '/fixture', { signal: controller.signal })
    controller.abort()
    await assert.rejects(pending, { name: 'AbortError' })
  } finally {
    globalThis.fetch = originalFetch
  }
})
