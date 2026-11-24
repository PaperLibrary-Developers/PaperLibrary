import assert from 'node:assert/strict'
import test from 'node:test'
import { api } from '../../public/js/api.mjs'

test('opening the papers directory in the desktop app sends a path-free request to the host', async t => {
  const host = new EventTarget()
  host.postMessage = request => {
    assert.deepEqual(Object.keys(request).sort(), ['requestId', 'type'])
    assert.equal(request.type, 'paperlibrary:papers-directory.request')
    assert.ok(request.requestId)
    host.dispatchEvent(new MessageEvent('message', { data: {
      type: 'paperlibrary:papers-directory.result', requestId: request.requestId, ok: true
    } }))
  }
  globalThis.chrome = { webview: host }
  t.after(() => { delete globalThis.chrome })
  const fetch = t.mock.method(globalThis, 'fetch')

  assert.equal((await api.openPapersDirectory()).ok, true)
  assert.equal(fetch.mock.callCount(), 0)
})

test('opening the papers directory in an external browser uses the existing HTTP endpoint', async t => {
  const fetch = t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: true }))

  assert.deepEqual(await api.openPapersDirectory(), { ok: true })
  const [[url, init]] = fetch.mock.calls.map(call => call.arguments)
  assert.equal(url, '/api/papers/open-directory')
  assert.equal(init.method, 'POST')
  assert.equal(init.body, '{}')
})
