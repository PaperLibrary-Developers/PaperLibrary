import test from 'node:test'
import assert from 'node:assert/strict'
import {
  INSTALL_REQUEST_TYPE, INSTALL_RESULT_TYPE, DEFAULT_INSTALL_TIMEOUT_MS,
  createRequestId, isWebViewHost, requestInstall, fallbackInstallMessage
} from '../../public/js/update-install.mjs'

class FakeHost {
  constructor () { this.listeners = new Set(); this.sent = [] }
  postMessage (message) { this.sent.push(message) }
  addEventListener (type, handler) { if (type === 'message') this.listeners.add(handler) }
  removeEventListener (type, handler) { if (type === 'message') this.listeners.delete(handler) }
  emit (data) { for (const handler of [...this.listeners]) handler({ data }) }
}

test('createRequestId returns non-empty unique ids', () => {
  const a = createRequestId()
  const b = createRequestId()
  assert.ok(a.length > 8)
  assert.notEqual(a, b)
})

test('isWebViewHost only accepts a usable host bridge', () => {
  assert.equal(isWebViewHost({}), false)
  assert.equal(isWebViewHost({ chrome: {} }), false)
  assert.equal(isWebViewHost({ chrome: { webview: new FakeHost() } }), true)
})

test('requestInstall sends frozen request and resolves matching success', async () => {
  const host = new FakeHost()
  const promise = requestInstall({ version: '1.7.1', host, requestId: 'req-1', timeoutMs: 1000 })
  assert.deepEqual(host.sent[0], {
    type: INSTALL_REQUEST_TYPE, requestId: 'req-1', version: '1.7.1'
  })
  host.emit({ type: INSTALL_RESULT_TYPE, requestId: 'other', ok: true, code: 'started' })
  host.emit({ type: INSTALL_RESULT_TYPE, requestId: 'req-1', ok: true, code: 'started', message: 'ok' })
  const result = await promise
  assert.equal(result.ok, true)
  assert.equal(result.code, 'started')
  assert.equal(host.listeners.size, 0)
})

test('requestInstall rejects failure result with code and keeps listener clean', async () => {
  const host = new FakeHost()
  const promise = requestInstall({ version: '1.7.1', host, requestId: 'req-2', timeoutMs: 1000 })
  host.emit({ type: INSTALL_RESULT_TYPE, requestId: 'req-2', ok: false, code: 'helper-missing', message: '缺少助手' })
  await assert.rejects(promise, err => err.code === 'helper-missing' && /缺少助手/.test(err.message))
  assert.equal(host.listeners.size, 0)
})

test('requestInstall accepts JSON string messages', async () => {
  const host = new FakeHost()
  const promise = requestInstall({ host, requestId: 'req-3', timeoutMs: 1000 })
  host.emit(JSON.stringify({ type: INSTALL_RESULT_TYPE, requestId: 'req-3', ok: true, code: 'started' }))
  assert.equal((await promise).code, 'started')
})

test('requestInstall times out without a result', async () => {
  const host = new FakeHost()
  await assert.rejects(requestInstall({ host, requestId: 'req-4', timeoutMs: 20 }),
    err => err.code === 'timeout')
  assert.equal(host.listeners.size, 0)
})

test('requestInstall rejects non-WebView/browser preview', async () => {
  await assert.rejects(requestInstall({ host: null, version: '1.7.1' }),
    err => err.code === 'not-webview' && err.message === fallbackInstallMessage())
})

test('default timeout is 20 seconds', () => {
  assert.equal(DEFAULT_INSTALL_TIMEOUT_MS, 20000)
})
