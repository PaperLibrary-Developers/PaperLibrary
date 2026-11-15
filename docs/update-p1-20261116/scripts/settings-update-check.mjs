/** P1-03 验收：设置页「关于与更新」只读区（加载/成功/失败三态）+ 截图。
 *
 * 用法：node docs/update-p1-20261116/scripts/settings-update-check.mjs <base> <截图目录>
 * 需要先起一个本地服务（--no-open），脚本用 headless Edge/Chrome 打开 #/settings。
 */
import fs from 'node:fs'
import path from 'node:path'
import { openPage, sleep } from '../../../test/ui-smoke/cdp.mjs'

const base = process.argv[2] || 'http://127.0.0.1:4397'
const shotDir = process.argv[3] || 'docs/update-p1-20261116/screenshots'
fs.mkdirSync(shotDir, { recursive: true })

function assert (cond, message) {
  if (!cond) throw new Error('断言失败：' + message)
}

async function waitFor (page, expression, timeoutMs = 25000, intervalMs = 150) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await page.evaluate(expression)
    if (value) return value
    await sleep(intervalMs)
  }
  return null
}

const page = await openPage({ base, cdpPort: 9401, startupMs: 2600 })
const report = { base, states: {}, errors: null }
try {
  await page.hash('#/settings', 1500)
  const ready = await waitFor(page, "!!document.getElementById('update-check-btn')", 15000)
  assert(ready, '设置页没有渲染「关于与更新」面板')

  const current = await page.text('#update-current-version')
  assert(current && current !== '—', '当前版本没有显示：' + current)
  report.currentVersion = current

  await page.evaluate("document.getElementById('update-check-btn').scrollIntoView({block:'center'})")
  await page.screenshot(path.join(shotDir, 'settings-update-idle.png'))

  /* 1) 加载态：点完立即读状态（真实检查要访问 GitHub，约 1–3 秒） */
  await page.click('#update-check-btn', 80)
  const loading = await page.text('#update-check-status')
  const loadingClass = await page.evaluate("document.getElementById('update-check-status').className")
  await page.screenshot(path.join(shotDir, 'settings-update-loading.png'))
  report.states.loading = { text: loading, className: loadingClass }
  assert(/检查中|正在检查/.test(loading), '加载态文案不对：' + loading)

  /* 2) 成功态：等状态从「正在检查」变成别的 */
  const success = await waitFor(page,
    "(() => { const el = document.getElementById('update-check-status'); const t = (el.textContent||'').trim(); return t && !t.includes('正在检查') && t !== '尚未检查' ? t : '' })()",
    25000)
  assert(success, '等待检查结果超时')
  const successClass = await page.evaluate("document.getElementById('update-check-status').className")
  const latestVersion = await page.text('#update-latest')
  await page.screenshot(path.join(shotDir, 'settings-update-success.png'))
  report.states.success = { text: success, className: successClass, latest: latestVersion }
  assert(/is-ok/.test(successClass), '成功态 class 不对：' + successClass)
  assert(/最新版本/.test(latestVersion), '成功态没有显示最新版本信息：' + latestVersion)

  /* 3) 失败态：拦掉 /api/update/check，模拟连不上更新源 */
  await page.send('Network.enable')
  await page.send('Network.setBlockedURLs', { urls: ['*api/update/check*'] })
  await page.click('#update-check-btn', 900)
  const failure = await page.text('#update-check-status')
  const failureClass = await page.evaluate("document.getElementById('update-check-status').className")
  const failureHint = await page.text('#update-latest')
  await page.screenshot(path.join(shotDir, 'settings-update-failure.png'))
  report.states.failure = { text: failure, className: failureClass, hint: failureHint }
  assert(/is-err/.test(failureClass), '失败态 class 不对：' + failureClass)
  assert(/检查失败/.test(failure), '失败态文案不对：' + failure)
  await page.send('Network.setBlockedURLs', { urls: [] })

  const errors = page.takeErrors()
  report.errors = errors
  assert(errors.js.length === 0, '页面有 JS 错误：' + JSON.stringify(errors.js))
  console.log(JSON.stringify(report, null, 2))
} finally {
  await page.close()
}
