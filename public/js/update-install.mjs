/* WebView2 立即安装握手（P2-09R）。
 *
 * UI 只在用户明确确认后调用 requestInstall()；启动器负责预检、启动包外 helper、
 * 停止 Python 服务并关闭窗口。失败/超时一律 reject，窗口保持打开，由设置页显示错误。
 */
import { createRequestId, requestWebView } from './webview-request.mjs'

export { createRequestId, isWebViewHost, webViewError as installError } from './webview-request.mjs'
export const INSTALL_REQUEST_TYPE = 'paperlibrary:install.request'
export const INSTALL_RESULT_TYPE = 'paperlibrary:install.result'
export const DEFAULT_INSTALL_TIMEOUT_MS = 20000

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
  return requestWebView({ type: INSTALL_REQUEST_TYPE, requestId, version }, INSTALL_RESULT_TYPE, {
    host: options.host,
    timeoutMs,
    failureCode: 'install-failed',
    failureMessage: '启动器拒绝或安装启动失败',
    postFailureMessage: '无法向启动器发送安装请求',
    noHostMessage: fallbackInstallMessage()
  })
}
