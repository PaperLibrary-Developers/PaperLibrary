/**
 * 极简 CDP 驱动（无头 Edge/Chrome），只用 Node 标准库：child_process + fetch + 全局 WebSocket。
 * 给 test/ui-smoke 下的两个冒烟脚本共用，避免同一套「启动浏览器 / 连调试端口 / 收异常」写两遍。
 *
 *   const page = await openPage({ base: 'http://127.0.0.1:4397' })
 *   await page.hash('#/figures')
 *   const probe = await page.evaluate('document.title')
 *   const errs = page.takeErrors()      // 自上次取走之后累积的 JS 错误
 *   await page.close()
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const DEFAULT_BROWSERS = [
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'
]

export const sleep = ms => new Promise(r => setTimeout(r, ms))

export function findBrowser (hint) {
  const list = (hint ? [hint] : []).concat(DEFAULT_BROWSERS)
  return list.find(p => { try { return fs.statSync(p).isFile() } catch { return false } }) || ''
}

/**
 * options: { base, browserPath, cdpPort, startupMs }
 * 返回的 page 对象见文件头；所有 evaluate 的异常都会抛成 Error。
 */
export async function openPage (options) {
  const opts = options || {}
  const base = String(opts.base || 'http://127.0.0.1:4397').replace(/\/$/, '')
  const cdpPort = Number(opts.cdpPort || 4398)
  const browserPath = opts.browserPath || findBrowser('')
  if (!browserPath) throw new Error('没有找到 Edge/Chrome')

  const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'helix-cdp-'))
  const child = spawn(browserPath, [
    '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
    '--disable-extensions', '--disable-background-networking', '--mute-audio',
    '--remote-debugging-port=' + cdpPort, '--user-data-dir=' + profile, 'about:blank'
  ], { stdio: 'ignore' })

  let page = null
  for (let i = 0; i < 60 && !page; i++) {
    try {
      const res = await fetch('http://127.0.0.1:' + cdpPort + '/json/list')
      const list = await res.json()
      page = list.find(t => t.type === 'page' && t.webSocketDebuggerUrl) || null
    } catch {}
    if (!page) await sleep(250)
  }
  if (!page) {
    try { child.kill() } catch {}
    throw new Error('等不到浏览器调试端口（CDP ' + cdpPort + '）')
  }

  let seq = 0
  const pending = new Map()
  let errors = []
  const ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    ws.onopen = resolve
    ws.onerror = () => reject(new Error('连不上 CDP WebSocket'))
  })
  ws.onmessage = ev => {
    let msg
    try { msg = JSON.parse(ev.data) } catch { return }
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id)
      pending.delete(msg.id)
      if (msg.error) p.reject(new Error(msg.error.message || 'CDP 错误'))
      else p.resolve(msg.result)
      return
    }
    if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails || {}
      const desc = (d.exception && d.exception.description) || d.text || '未捕获异常'
      errors.push({ kind: 'exception', text: String(desc).split('\n')[0] })
    } else if (msg.method === 'Runtime.consoleAPICalled') {
      if (msg.params.type !== 'error' && msg.params.type !== 'assert') return
      const text = (msg.params.args || []).map(a => a.value != null ? a.value : (a.description || a.type)).join(' ')
      errors.push({ kind: 'console', text: String(text) })
    } else if (msg.method === 'Log.entryAdded') {
      const e = msg.params.entry || {}
      if (e.level !== 'error') return
      const network = e.source === 'network'
      errors.push({ kind: network ? 'network' : 'log', text: (e.text || '') + (e.url ? ' ' + e.url : '') })
    }
  }

  const send = (method, params) => {
    const id = ++seq
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject })
      ws.send(JSON.stringify({ id, method, params: params || {} }))
    })
  }

  await send('Runtime.enable')
  await send('Log.enable')
  await send('Page.enable')
  await send('Page.navigate', { url: base + '/' })
  await sleep(opts.startupMs || 2300)

  async function evaluate (expression) {
    const res = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
    if (res.exceptionDetails) {
      const d = res.exceptionDetails
      throw new Error((d.exception && d.exception.description) || d.text || '页面内执行失败')
    }
    return res.result && res.result.value
  }

  return {
    base,
    browserPath,
    /** 原始 CDP 命令（escape hatch：截图、模拟输入等） */
    send,
    /** 切到某个 hash（保持同一个页面，等价于用户点导航） */
    async hash (route, waitMs) {
      const target = route.includes('#') ? route.slice(route.indexOf('#')) : '#' + route
      await evaluate('location.hash = ' + JSON.stringify(target))
      await sleep(waitMs || 1400)
    },
    evaluate,
    /** 点一次元素（不存在时抛错，好定位） */
    async click (selector, waitMs) {
      const hit = await evaluate('(() => { const el = document.querySelector(' + JSON.stringify(selector) +
        '); if (!el) return 0; el.click(); return 1 })()')
      if (!hit) throw new Error('点不到元素：' + selector)
      await sleep(waitMs || 600)
      return hit
    },
    async count (selector) {
      return Number(await evaluate('document.querySelectorAll(' + JSON.stringify(selector) + ').length')) || 0
    },
    async text (selector) {
      return String(await evaluate('(() => { const el = document.querySelector(' + JSON.stringify(selector) +
        '); return el ? (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 160) : "" })()') || '')
    },
    /** 存一张截图（调试/人工核对用；文件路径由调用方给） */
    async screenshot (filePath) {
      const res = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
      fs.writeFileSync(filePath, Buffer.from(res.data, 'base64'))
      return filePath
    },
    /** 取走累积的错误（默认只算 JS 类，网络 4xx/5xx 单独返回） */
    takeErrors () {
      const all = errors
      errors = []
      return {
        js: all.filter(e => e.kind !== 'network'),
        network: all.filter(e => e.kind === 'network')
      }
    },
    async close () {
      try { ws.close() } catch {}
      try { child.kill() } catch {}
      await sleep(250)
      try { fs.rmSync(profile, { recursive: true, force: true }) } catch {}
    }
  }
}
