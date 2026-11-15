/** P2-09 验收：安装确认提示（必须用户确认；拒绝保持现状；确认后写 apply.json）。
 * 用法：node docs/update-p2-20261116/scripts/update-install-confirm.mjs <base> <dataDir> <shotDir>
 */
import fs from 'node:fs'
import path from 'node:path'
import { openPage, sleep } from '../../../test/ui-smoke/cdp.mjs'

const base = process.argv[2] || 'http://127.0.0.1:4388'
const dataDir = process.argv[3] || ''
const shotDir = process.argv[4] || 'docs/update-p2-20261116/screenshots'
fs.mkdirSync(shotDir, { recursive: true })
const applyFile = dataDir ? path.join(dataDir, 'updates', 'apply.json') : ''

function assert (cond, message) {
  if (!cond) throw new Error('断言失败：' + message)
}

async function waitFor (page, expression, timeoutMs = 90000, intervalMs = 150) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await page.evaluate(expression)
    if (value) return value
    await sleep(intervalMs)
  }
  return null
}

const page = await openPage({ base, cdpPort: 9404, startupMs: 2400 })
const report = { states: {}, errors: null }
try {
  await page.hash('#/settings', 1400)
  await waitFor(page, "!!document.getElementById('update-check-btn')", 15000)

  /* 准备一份 ready staging */
  await page.click('#update-check-btn', 200)
  await waitFor(page, "(() => document.getElementById('update-check-status').textContent.includes('发现新版本'))()", 30000)
  await waitFor(page, "!!document.getElementById('update-download-btn')", 5000)
  await page.click('#update-download-btn', 500)
  const ready = await waitFor(page,
    "(() => document.getElementById('update-check-status').textContent.includes('更新已准备好'))()", 120000)
  assert(ready, '等待 ready 超时')
  assert(await page.count('#update-install-btn') === 1, 'ready 后没有安装按钮')

  /* 1) 打开确认框 → 取消 → 不写 apply.json */
  await page.click('#update-install-btn', 400)
  assert(await page.count('.modal') === 1, '没有出现安装确认框')
  const modalText = await page.evaluate("(() => { const m = document.querySelector('.modal'); return m ? m.textContent.replace(/\\s+/g, ' ').trim() : '' })()")
  report.states.modal = { text: modalText }
  for (const needle of ['关闭窗口', '数据', '中断', '不会静默安装']) {
    assert(modalText.includes(needle), '确认框缺少说明：' + needle)
  }
  await page.screenshot(path.join(shotDir, 'settings-update-install-confirm.png'))
  await page.evaluate("document.querySelector('.modal .actions .btn:not(.primary)').click()")
  await sleep(400)
  assert(await page.count('.modal') === 0, '取消后确认框没有关闭')
  report.states.cancelled = {
    status: await page.text('#update-check-status'),
    applyExists: applyFile ? fs.existsSync(applyFile) : false
  }
  if (applyFile) assert(!fs.existsSync(applyFile), '拒绝确认后不应写 apply.json')

  /* 2) 再次确认安装 → 写 apply.json，状态变为“安装包已准备好” */
  await page.click('#update-install-btn', 400)
  assert(await page.count('.modal') === 1, '第二次没有出现确认框')
  await page.evaluate("document.querySelector('.modal .actions .btn.primary').click()")
  const installed = await waitFor(page,
    "(() => document.getElementById('update-check-status').textContent.includes('安装包已准备好'))()", 15000)
  assert(installed, '确认后状态没有变成“安装包已准备好”')
  report.states.confirmed = {
    status: await page.text('#update-check-status'),
    applyExists: applyFile ? fs.existsSync(applyFile) : null
  }
  if (applyFile) {
    assert(fs.existsSync(applyFile), '确认后没有写 apply.json')
    report.states.apply = JSON.parse(fs.readFileSync(applyFile, 'utf-8'))
  }
  await page.screenshot(path.join(shotDir, 'settings-update-install-prepared.png'))

  const errors = page.takeErrors()
  report.errors = errors
  assert(errors.js.length === 0, '页面有 JS 错误：' + JSON.stringify(errors.js))
  console.log(JSON.stringify(report, null, 2))
} finally {
  await page.close()
}
