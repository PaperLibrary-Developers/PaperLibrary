/**
 * P1-04 自动检查调度逻辑测试（Node，无需浏览器）。
 * 运行：node test/js/update-auto.test.mjs（由 test/test_update_auto.py 调起）
 */
import assert from 'node:assert/strict'
import {
  STARTUP_MIN_MS, STARTUP_MAX_MS, INTERVAL_MS,
  startupDelay, withinInterval, autoCheckEnabled, autoDownloadEnabled, createAutoChecker
} from '../../public/js/update-auto.mjs'

let passed = 0
function test (name, fn) {
  return Promise.resolve().then(fn).then(() => { passed += 1; console.log('ok - ' + name) })
}

function harness (opts) {
  const state = { now: 1_700_000_000_000, last: 0, enabled: true, skipped: '',
    checks: [], notified: [], errors: [], downloads: [], autoDownloaded: [] }
  const timers = new Map()
  let seq = 0
  const api = {
    now: () => state.now,
    setTimer: (fn, ms) => { const id = ++seq; timers.set(id, { fn, ms }); return id },
    clearTimer: id => { timers.delete(id) },
    enabled: () => state.enabled,
    getLastCheckAt: () => state.last,
    saveLastCheckAt: ts => { state.last = ts },
    getSkipped: () => state.skipped,
    check: opts && opts.check ? () => opts.check(state) : () => { state.checks.push(true); return Promise.resolve({ available: false }) },
    notify: latest => { state.notified.push(latest.tag) },
    onError: err => { state.errors.push(err) },
    autoDownload: opts && opts.autoDownload ? () => opts.autoDownload(state) : () => false,
    download: opts && opts.download ? () => opts.download(state) : undefined,
    onAutoDownload: latest => { state.autoDownloaded.push(latest.tag) },
    random: () => 0.5
  }
  const checker = createAutoChecker(api)
  return { state, timers, checker, api, fire: async () => {
    const entry = [...timers.entries()][0]
    assert.ok(entry, '没有待触发的定时器')
    timers.delete(entry[0])
    await entry[1].fn()
    return entry[1].ms
  } }
}

await test('startupDelay 在 10–30 秒之间', () => {
  assert.ok(startupDelay(() => 0) >= STARTUP_MIN_MS)
  assert.ok(startupDelay(() => 0) < STARTUP_MAX_MS)
  assert.ok(startupDelay(() => 0.999999) <= STARTUP_MAX_MS)
  assert.ok(startupDelay(() => 0.999999) >= STARTUP_MAX_MS - 1)
})

await test('withinInterval 24 小时节流', () => {
  const now = 1_700_000_000_000
  assert.equal(withinInterval(0, now), false)
  assert.equal(withinInterval(now - 60_000, now), true)
  assert.equal(withinInterval(now - INTERVAL_MS / 2, now), true)
  assert.equal(withinInterval(now - INTERVAL_MS - 1, now), false)
})

await test('默认开启：未设置或 true 为开，仅显式 false 关闭', () => {
  assert.equal(autoCheckEnabled(undefined), true)
  assert.equal(autoCheckEnabled({}), true)
  assert.equal(autoCheckEnabled({ updateAutoCheck: true }), true)
  assert.equal(autoCheckEnabled({ updateAutoCheck: false }), false)
})

await test('显式关闭时 start 不排定时器、runNow 返回 disabled', async () => {
  const h = harness()
  h.state.enabled = false
  h.checker.start()
  assert.equal(h.timers.size, 0)
  assert.equal(await h.checker.runNow(), 'disabled')
  assert.equal(h.state.checks.length, 0)
})

await test('开启后按启动延迟检查一次，并排下一次 24 小时', async () => {
  const h = harness()
  h.checker.start()
  assert.equal(h.timers.size, 1)
  const delay = [...h.timers.values()][0].ms
  assert.ok(delay >= STARTUP_MIN_MS && delay <= STARTUP_MAX_MS)
  await h.fire()
  assert.equal(h.state.checks.length, 1)
  assert.equal(h.state.last, h.state.now)
  assert.equal(h.timers.size, 1)
  assert.equal([...h.timers.values()][0].ms, INTERVAL_MS)
})

await test('24 小时内再次 runNow 被节流，不发请求', async () => {
  const h = harness()
  h.state.last = h.state.now - 60 * 60 * 1000
  assert.equal(await h.checker.runNow(), 'throttled')
  assert.equal(h.state.checks.length, 0)
})

await test('超过 24 小时可以再次检查', async () => {
  const h = harness()
  h.state.last = h.state.now - INTERVAL_MS - 1000
  assert.equal(await h.checker.runNow(), 'checked')
  assert.equal(h.state.checks.length, 1)
})

await test('检查失败静默：记录时间、不抛错、走 onError', async () => {
  const h = harness({ check: () => Promise.reject(new Error('network down')) })
  const status = await h.checker.runNow()
  assert.equal(status, 'error')
  assert.equal(h.state.last, h.state.now)
  assert.equal(h.state.notified.length, 0)
  assert.equal(h.state.errors.length, 1)
})

await test('发现新版本通知一次；跳过版本不通知', async () => {
  const h = harness({ check: () => Promise.resolve({ available: true, latest: { tag: 'v9.9.9' } }) })
  assert.equal(await h.checker.runNow(), 'checked')
  assert.deepEqual(h.state.notified, ['v9.9.9'])
  h.state.last = 0
  h.state.skipped = 'v9.9.9'
  assert.equal(await h.checker.runNow(), 'checked')
  assert.deepEqual(h.state.notified, ['v9.9.9'])
})

await test('已是最新版本不通知', async () => {
  const h = harness({ check: () => Promise.resolve({ available: false, reason: 'current', latest: { tag: 'v1.0.0' } }) })
  await h.checker.runNow()
  assert.equal(h.state.notified.length, 0)
})

await test('自动下载默认关；只有显式 true 开启', () => {
  assert.equal(autoDownloadEnabled(undefined), false)
  assert.equal(autoDownloadEnabled({}), false)
  assert.equal(autoDownloadEnabled({ updateAutoDownload: false }), false)
  assert.equal(autoDownloadEnabled({ updateAutoDownload: true }), true)
})

await test('开启自动下载：发现新版本调用 download，不走普通通知', async () => {
  const h = harness({
    autoDownload: () => true,
    download: state => { state.downloads.push('started'); return Promise.resolve({ phase: 'downloading' }) },
    check: () => Promise.resolve({ available: true, latest: { tag: 'v9.9.9' } })
  })
  await h.checker.runNow()
  assert.deepEqual(h.state.downloads, ['started'])
  assert.deepEqual(h.state.notified, [])
  assert.deepEqual(h.state.autoDownloaded, ['v9.9.9'])
})

await test('关闭自动下载：仍只通知，不下载', async () => {
  const h = harness({
    autoDownload: () => false,
    download: state => { state.downloads.push('started'); return Promise.resolve({}) },
    check: () => Promise.resolve({ available: true, latest: { tag: 'v9.9.9' } })
  })
  await h.checker.runNow()
  assert.deepEqual(h.state.downloads, [])
  assert.deepEqual(h.state.notified, ['v9.9.9'])
})

await test('跳过版本不自动下载', async () => {
  const h = harness({
    autoDownload: () => true,
    download: state => { state.downloads.push('started'); return Promise.resolve({}) },
    check: () => Promise.resolve({ available: true, latest: { tag: 'v9.9.9' } })
  })
  h.state.skipped = 'v9.9.9'
  await h.checker.runNow()
  assert.deepEqual(h.state.downloads, [])
  assert.deepEqual(h.state.notified, [])
})

await test('自动下载失败静默走 onError', async () => {
  const h = harness({
    autoDownload: () => true,
    download: () => Promise.reject(new Error('offline')),
    check: () => Promise.resolve({ available: true, latest: { tag: 'v9.9.9' } })
  })
  assert.equal(await h.checker.runNow(), 'checked')
  assert.equal(h.state.errors.length, 1)
  assert.deepEqual(h.state.notified, [])
})

await test('stop 清除定时器，stop 后不再检查', async () => {
  const h = harness()
  h.checker.start()
  h.checker.stop()
  assert.equal(h.timers.size, 0)
  assert.equal(h.checker.state().active, false)
})

await test('setEnabled(true) 重新启动，setEnabled(false) 停止', async () => {
  const h = harness()
  h.state.enabled = false
  h.checker.start()
  h.state.enabled = true
  h.checker.setEnabled(true)
  assert.equal(h.timers.size, 1)
  h.checker.setEnabled(false)
  assert.equal(h.timers.size, 0)
})

console.log('\n' + passed + ' tests passed')
