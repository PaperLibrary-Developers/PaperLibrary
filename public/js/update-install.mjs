/* WebView2 立即安装握手（P2-09R）。
 *
 * UI 只在用户明确确认后调用 requestInstall()；启动器负责预检、启动包外 helper、
 * 停止 Python 服务并关闭窗口。失败/超时一律 reject，窗口保持打开，由设置页显示错误。
 */
export const INSTALL_REQUEST_TYPE = 'paperlibrary:install.request'
export const INSTALL_RESULT_TYPE = 'paperlibrary:install.result'
export const DEFAULT_INSTALL_TIMEOUT_MS = 20000

export function createRequestId () {
  const cryptoObj = globalThis.crypto
  if (cryptoObj && typeof cryptoObj.randomUUID === 'function') return cryptoObj.randomUUID()
  return 'req-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12)
}

export function installError (code, message, cause) {
  const err = new Error(message || code)
  err.code = code
  if (cause) err.cause = cause
  return err
}

export function isWebViewHost (win = globalThis) {
  const host = win && win.chrome && win.chrome.webview
  return !!(host && typeof host.postMessage === 'function' &&
    typeof host.addEventListener === 'function')
}

export function fallbackInstallMessage () {
  return '当前不在桌面应用内，无法自动关闭并安装。请关闭窗口后安装，或使用桌面版。'
}

/**
 * 向启动器发送安装请求并等待 result。
 * @param {{version?: string, timeoutMs?: number, host?: any, requestId?: string}} options
 * @returns {Promise<{type: string, requestId: string, ok: boolean, code: string, message: string}>}
 */
export function requestInstall (options = {}) {
  const version = options.version || ''
  const timeoutMs = Number(options.timeoutMs) > 0 ? Number(options.timeoutMs) : DEFAULT_INSTALL_TIMEOUT_MS
  const requestId = options.requestId || createRequestId()
  const host = options.host || (globalThis.chrome && globalThis.chrome.webview)
  if (!host || typeof host.postMessage !== 'function' || typeof host.addEventListener !== 'function') {
    return Promise.reject(installError('not-webview', fallbackInstallMessage()))
  }

  return new Promise((resolve, reject) => {
    let settled = false
    let timer = null
    const finish = (fn, value) => {
      if (settled) return
      settled = true
      if (timer) clearTimeout(timer)
      try { host.removeEventListener('message', onMessage) } catch (err) { /* ignore */ }
      fn(value)
    }
    const onMessage = event => {
      let data = event && event.data
      if (typeof data === 'string') {
        try { data = JSON.parse(data) } catch (err) { return }
      }
      if (!data || data.type !== INSTALL_RESULT_TYPE || data.requestId !== requestId) return
      if (data.ok) {
        finish(resolve, data)
      } else {
        finish(reject, installError(data.code || 'install-failed',
          data.message || '启动器拒绝或安装启动失败'))
      }
    }

    host.addEventListener('message', onMessage)
    timer = setTimeout(() => {
      finish(reject, installError('timeout', '启动器未在限定时间内响应，请重试'))
    }, timeoutMs)
    try {
      host.postMessage({ type: INSTALL_REQUEST_TYPE, requestId, version })
    } catch (err) {
      finish(reject, installError('post-failed', '无法向启动器发送安装请求', err))
    }
  })
}
