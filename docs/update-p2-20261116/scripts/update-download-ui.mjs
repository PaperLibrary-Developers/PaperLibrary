/** P2-08 验收：设置页下载交互（检查→下载→取消→重新下载→ready→稍后）真实浏览器驱动。
 *
 * 用法：node docs/update-p2-20261116/scripts/update-download-ui.mjs <base> <dataDir> <shotDir>
 */
import fs from 'node:fs'
import path from 'node:path'
import { openPage, sleep } from '../../../test/ui-smoke/cdp.mjs'

const base = process.argv[2] || 'http://127.0.0.1:4389'
const dataDir = process.argv[3] || ''
const shotDir = process.argv[4] || 'docs/update-p2-20261116/screenshots'
fs.mkdirSync(shotDir, { recursive: true })

function assert (cond, message) {
  if (!cond) throw new Error('断言失败：' + message)
}

async function waitFor (page, expression, timeoutMs = 30000, intervalMs = 150) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await page.evaluate(expression)
    if (value) return value
    await sleep(intervalMs)
  }
  return null
}

const textOf = selector => '(() => { const el = document.querySelector(' + JSON.stringify(selector) +
  '); return el ? (el.textContent || "").replace(/\\s+/g, " ").trim() : "" })()'

const page = await openPage({ base, cdpPort: 9403, startupMs: 2400 })
const report = { states: {}, errors: null }
try {
  await page.hash('#/settings', 1400)
  await waitFor(page, "!!document.getElementById('update-check-btn')", 15000)

  /* 1) 检查 → 发现新版本 + 下载按钮 */
  await page.click('#update-check-btn', 200)
  const available = await waitFor(page,
    "(() => document.getElementById('update-check-status').textContent.includes('发现新版本'))()", 30000)
  assert(available, '没有发现新版本')
  assert(await page.count('#update-download-btn') === 1, '缺少下载按钮')
  report.states.available = {
    status: await page.text('#update-check-status'),
    latest: await page.text('#update-latest')
  }
  await page.screenshot(path.join(shotDir, 'settings-update-available.png'))

  /* 2) 下载 → 进度 */
  await page.click('#update-download-btn', 300)
  const cancelShown = await waitFor(page, "!!document.getElementById('update-cancel-btn')", 25000)
  assert(cancelShown, '下载中没有出现取消按钮')
  const downloading = await waitFor(page,
    "(() => { const t = document.getElementById('update-check-status').textContent || ''; return /下载中 (?:[1-9]\\d|100)%/.test(t) ? t.trim() : '' })()", 20000)
  assert(downloading, '没有出现下载百分比进度')
  report.states.downloading = { status: await page.text('#update-check-status') }
  await page.screenshot(path.join(shotDir, 'settings-update-downloading.png'))

  /* 3) 取消 → 方案 B：文件被清理 */
  await page.click('#update-cancel-btn', 1200)
  const cancelled = await waitFor(page,
    "(() => document.getElementById('update-check-status').textContent.includes('已取消'))()", 15000)
  assert(cancelled, '取消后状态没有更新')
  report.states.cancelled = { status: await page.text('#update-check-status') }
  if (dataDir) {
    const updatesDir = path.join(dataDir, 'updates')
    const leftovers = fs.existsSync(updatesDir) ? fs.readdirSync(updatesDir) : []
    report.states.cancelled.updatesDirExists = fs.existsSync(updatesDir)
    report.states.cancelled.leftovers = leftovers
    assert(!fs.existsSync(updatesDir) || leftovers.length === 0, '取消后仍残留 updates 目录内容：' + leftovers)
  }
  await page.screenshot(path.join(shotDir, 'settings-update-cancelled.png'))

  /* 4) 重新检查 → 重新下载 → ready（取消后不再有可用包，按用户流程重新检查） */
  await page.click('#update-check-btn', 200)
  await waitFor(page, "(() => document.getElementById('update-check-status').textContent.includes('发现新版本'))()", 30000)
  await waitFor(page, "!!document.getElementById('update-download-btn')", 5000)
  await page.click('#update-download-btn', 800)
  const ready = await waitFor(page,
    "(() => document.getElementById('update-check-status').textContent.includes('更新已准备好'))()", 90000)
  assert(ready, '等待 ready 超时')
  report.states.ready = {
    status: await page.text('#update-check-status'),
    latest: await page.text('#update-latest'),
    actions: await page.text('#update-actions')
  }
  await page.screenshot(path.join(shotDir, 'settings-update-ready.png'))

  /* 5) 稍后 → 停止轮询/显示可恢复入口 */
  await page.click('#update-later-btn', 300)
  const later = await page.text('#update-check-status')
  assert(later.includes('已稍后'), '稍后状态不对：' + later)
  assert(await page.count('#update-resume-btn') === 1, '缺少“查看进度”入口')
  report.states.later = { status: later }
  await page.click('#update-resume-btn', 1500)
  const resumed = await page.text('#update-check-status')
  assert(resumed.includes('更新已准备好'), '恢复进度后状态不对：' + resumed)

  /* 6) 跳过此版本（回到可更新状态后） */
  await page.click('#update-check-btn', 200)
  await waitFor(page, "(() => document.getElementById('update-check-status').textContent.includes('发现新版本'))()", 30000)
  await page.click('#update-skip-btn', 600)
  const skipped = await page.text('#update-check-status')
  report.states.skip = { status: skipped }
  assert(skipped.includes('已跳过'), '跳过状态不对：' + skipped)

  const errors = page.takeErrors()
  report.errors = errors
  assert(errors.js.length === 0, '页面有 JS 错误：' + JSON.stringify(errors.js))
  console.log(JSON.stringify(report, null, 2))
} finally {
  await page.close()
}
