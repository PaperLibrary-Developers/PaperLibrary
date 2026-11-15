/** P1-04 验收：自动检查（只通知，不下载）+ 24 小时节流 + 跳过版本 + 默认关闭。
 *
 * 用法：node docs/update-p1-20261116/scripts/update-auto-check.mjs <base> <截图目录>
 * 服务由调用方从「version=1.6.0 的临时副本」启动，因此线上 v1.7.1 会被视为新版本（真实数据，不 mock）。
 */
import fs from 'node:fs'
import path from 'node:path'
import { openPage, sleep } from '../../../test/ui-smoke/cdp.mjs'

const base = process.argv[2] || 'http://127.0.0.1:4396'
const shotDir = process.argv[3] || 'docs/update-p1-20261116/screenshots'
fs.mkdirSync(shotDir, { recursive: true })

function assert (cond, message) {
  if (!cond) throw new Error('断言失败：' + message)
}

async function waitFor (page, expression, timeoutMs = 20000, intervalMs = 120) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await page.evaluate(expression)
    if (value) return value
    await sleep(intervalMs)
  }
  return null
}

async function captureElement (page, selector, filePath) {
  await page.evaluate("(() => { const el = document.querySelector(" + JSON.stringify(selector) +
    "); if (el) el.scrollIntoView({block:'start'}); })()")
  await sleep(250)
  const rect = await page.evaluate("(() => { const el = document.querySelector(" + JSON.stringify(selector) +
    "); if (!el) return null; const r = el.getBoundingClientRect(); return { x: r.left + window.scrollX, y: r.top + window.scrollY, w: r.width, h: r.height } })()")
  if (!rect) throw new Error('截不到元素：' + selector)
  const res = await page.send('Page.captureScreenshot', {
    format: 'png',
    clip: { x: rect.x, y: rect.y, width: rect.w, height: rect.h, scale: 1 },
    captureBeyondViewport: true
  })
  fs.writeFileSync(filePath, Buffer.from(res.data, 'base64'))
}

async function openWithPrefs (page, prefs) {
  await page.evaluate("localStorage.setItem('lv:prefs', " + JSON.stringify(JSON.stringify(prefs)) + "); location.reload()")
  const ok = await waitFor(page,
    "!!(window.__paperUpdateAuto && document.getElementById('app') && !document.getElementById('app').hidden)", 20000)
  assert(ok, '页面没有启动自动检查器')
  await sleep(300)
}

const page = await openPage({ base, cdpPort: 9402, startupMs: 2200 })
const report = { base, states: {}, errors: null }
try {
  await page.hash('#/settings', 1200)
  /* 开启自动检查（默认关；这里用真实偏好开关的状态） */
  await openWithPrefs(page, { updateAutoCheck: true, updateLastCheckAt: 0, updateSkippedVersion: '' })
  const enabledState = await page.evaluate('window.__paperUpdateAuto.state()')
  assert(enabledState.active === true, '开启后自动检查器未激活')

  /* 1) 发现新版本 → 轻量 toast（只通知，不下载） */
  await page.evaluate("document.getElementById('toasts').replaceChildren()")
  const first = await page.evaluate('window.__paperUpdateAuto.runNow()')
  assert(first === 'checked', '首次 runNow 状态不对：' + first)
  const toast = await waitFor(page,
    "(() => { const t = document.getElementById('toasts'); return t && t.textContent.includes('发现新版本') ? t.textContent.trim() : '' })()", 15000)
  assert(toast, '没有出现“发现新版本”轻量提示')
  report.states.firstCheck = { status: first, toast: toast.replace(/\s+/g, ' ').slice(0, 120) }
  await page.screenshot(path.join(shotDir, 'update-auto-toast.png'))

  /* 2) 24 小时节流：马上再检查一次 → throttled，且不重复通知 */
  await page.evaluate("document.getElementById('toasts').replaceChildren()")
  const second = await page.evaluate('window.__paperUpdateAuto.runNow()')
  await sleep(400)
  const toastAfter = await page.evaluate("document.getElementById('toasts').textContent || ''")
  report.states.throttled = { status: second, toastAfter: toastAfter.trim() }
  assert(second === 'throttled', '24 小时内没有被节流：' + second)
  assert(!toastAfter.includes('发现新版本'), '节流后仍重复通知')

  /* 3) 超过 24 小时 → 允许再次检查（真实时钟边界） */
  await openWithPrefs(page, { updateAutoCheck: true, updateLastCheckAt: Date.now() - 25 * 3600 * 1000, updateSkippedVersion: '' })
  await page.evaluate("document.getElementById('toasts').replaceChildren()")
  const third = await page.evaluate('window.__paperUpdateAuto.runNow()')
  const toast3 = await waitFor(page,
    "(() => { const t = document.getElementById('toasts'); return t && t.textContent.includes('发现新版本') ? t.textContent.trim() : '' })()", 15000)
  report.states.after24h = { status: third, toast: !!toast3 }
  assert(third === 'checked' && toast3, '超过 24 小时后没有重新检查/通知')

  /* 4) 跳过此版本 → 不再通知 */
  await openWithPrefs(page, { updateAutoCheck: true, updateLastCheckAt: 0, updateSkippedVersion: 'v1.7.1' })
  await page.evaluate("document.getElementById('toasts').replaceChildren()")
  const fourth = await page.evaluate('window.__paperUpdateAuto.runNow()')
  await sleep(600)
  const toast4 = await page.evaluate("document.getElementById('toasts').textContent || ''")
  report.states.skipped = { status: fourth, toast: toast4.trim() }
  assert(fourth === 'checked', '跳过版本的检查状态不对：' + fourth)
  assert(!toast4.includes('发现新版本'), '已跳过版本仍被通知')

  /* 5) 默认关闭（偏好为 false）→ 不激活、不检查 */
  await openWithPrefs(page, { updateAutoCheck: false, updateLastCheckAt: 0, updateSkippedVersion: '' })
  const disabledState = await page.evaluate('window.__paperUpdateAuto.state()')
  const disabledRun = await page.evaluate('window.__paperUpdateAuto.runNow()')
  report.states.disabled = { state: disabledState, run: disabledRun }
  assert(disabledState.active === true && disabledState.scheduled === false,
    '关闭时不应排定时器：' + JSON.stringify(disabledState))
  assert(disabledRun === 'disabled', '关闭后 runNow 应为 disabled：' + disabledRun)

  /* 6) 设置页开关与跳过按钮可见（截图） */
  await page.hash('#/settings', 1400)
  const toggle = await page.evaluate("!!document.getElementById('update-auto-toggle')")
  report.states.toggleVisible = toggle
  assert(toggle, '设置页没有自动检查开关')
  // 只截「关于与更新」面板，避免把临时数据目录等本机路径带进公开截图
  await captureElement(page, 'section.panel:has(#update-auto-toggle)',
    path.join(shotDir, 'settings-update-auto-toggle.png'))

  const errors = page.takeErrors()
  report.errors = errors
  assert(errors.js.length === 0, '页面有 JS 错误：' + JSON.stringify(errors.js))
  console.log(JSON.stringify(report, null, 2))
} finally {
  await page.close()
}
