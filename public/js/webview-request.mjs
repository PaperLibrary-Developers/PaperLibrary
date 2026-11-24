export function createRequestId () {
  const cryptoObj = globalThis.crypto
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') return cryptoObj.randomUUID()
  return 'req-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12)
}

export function webViewError (code, message, cause) {
  const err = new Error(message || code)
  err.code = code
  if (cause) err.cause = cause
  return err
}

function isUsableHost (host) {
  return !!(host && typeof host.postMessage === 'function' &&
    typeof host.addEventListener === 'function')
}

export function isWebViewHost (win = globalThis) {
  return isUsableHost(win?.chrome?.webview)
}

export function requestWebView (request, resultType, options) {
  const { timeoutMs, failureCode, failureMessage, postFailureMessage, noHostMessage } = options
  const host = options.host || globalThis.chrome?.webview
  if (!isUsableHost(host)) return Promise.reject(webViewError('not-webview', noHostMessage))

  return new Promise((resolve, reject) => {
    let settled = false
    let timer = null
    const finish = (fn, value) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      try { host.removeEventListener('message', onMessage) } catch { /* ignore */ }
      fn(value)
    }
    const onMessage = event => {
      let data = event?.data
      if (typeof data === 'string') {
        try { data = JSON.parse(data) } catch { return }
      }
      if (!data || data.type !== resultType || data.requestId !== request.requestId) return
      if (data.ok) {
        finish(resolve, data)
      } else {
        finish(reject, webViewError(data.code || failureCode, data.message || failureMessage))
      }
    }

    host.addEventListener('message', onMessage)
    timer = setTimeout(() => {
      finish(reject, webViewError('timeout', '启动器未在限定时间内响应，请重试'))
    }, timeoutMs)
    try {
      host.postMessage(request)
    } catch (err) {
      finish(reject, webViewError('post-failed', postFailureMessage, err))
    }
  })
}
