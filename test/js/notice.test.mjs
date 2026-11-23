/**
 * 开屏公告小弹窗逻辑测试（Node，无需浏览器）。
 * 运行：node --test test/js/notice.test.mjs（也由 test/test_notice.py 调起）
 */
import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'
import {
  safeExternalUrl,
  safeImageSrc,
  isNoticeExpired,
  normalizeNotice,
  shouldShowNotice,
  fetchLocalNotice,
  maybeShowStartupNotice
} from '../../public/js/notice.mjs'

test('safeExternalUrl 只允许 https 与 mailto', () => {
  assert.equal(safeExternalUrl('https://github.com/PaperLibrary-Developers/PaperLibrary'),
    'https://github.com/PaperLibrary-Developers/PaperLibrary')
  assert.equal(safeExternalUrl('mailto:hi@example.com'), 'mailto:hi@example.com')
  assert.equal(safeExternalUrl('http://example.com'), '')
  assert.equal(safeExternalUrl('javascript:alert(1)'), '')
  assert.equal(safeExternalUrl('data:text/html,hi'), '')
  assert.equal(safeExternalUrl(''), '')
})

test('safeImageSrc 只允许 https 或安全站内相对路径', () => {
  assert.equal(safeImageSrc('notice-banner.png'), '/notice-banner.png')
  assert.equal(safeImageSrc('/assets/banner.png'), '/assets/banner.png')
  assert.equal(safeImageSrc('https://cdn.example.com/a.png'), 'https://cdn.example.com/a.png')
  assert.equal(safeImageSrc('../secret.png'), '')
  assert.equal(safeImageSrc('javascript:alert(1)'), '')
  assert.equal(safeImageSrc('data:image/png;base64,abc'), '')
  assert.equal(safeImageSrc('//evil.example/a.png'), '')
})

test('isNoticeExpired 正确判断过期与非法日期兜底', () => {
  const now = Date.parse('2026-11-23T12:00:00Z')
  assert.equal(isNoticeExpired({ expiresAt: '' }, now), false)
  assert.equal(isNoticeExpired({ expiresAt: 'not-a-date' }, now), false)
  assert.equal(isNoticeExpired({ expiresAt: '2026-12-01T00:00:00Z' }, now), false)
  assert.equal(isNoticeExpired({ expiresAt: '2026-11-01T00:00:00Z' }, now), true)
})

test('normalizeNotice 清洗结构并忽略不安全链接与空项', () => {
  assert.equal(normalizeNotice(null), null)
  assert.equal(normalizeNotice({ id: '', title: 'x' }), null)
  assert.equal(normalizeNotice({ id: 'n1' }), null)

  const n = normalizeNotice({
    id: '2026-11-v1.9.1',
    badge: ' 版本更新 ',
    title: ' 标题 ',
    body: ' 正文 ',
    bullets: [' 第一条 ', '', '  ', '第二条'],
    image: '../bad.png',
    action: { label: '点我', url: 'javascript:alert(1)' },
    promo: { tag: '招租', title: '广告位', desc: '说明', url: 'https://example.com/ad' }
  })
  assert.equal(n.id, '2026-11-v1.9.1')
  assert.equal(n.enabled, true)
  assert.equal(n.badge, '版本更新')
  assert.deepEqual(n.bullets, ['第一条', '第二条'])
  assert.equal(n.image, '')
  assert.equal(n.action, null)
  assert.equal(n.promo.tag, '招租')
  assert.equal(n.promo.url, 'https://example.com/ad')
})

test('shouldShowNotice 按 enabled / expiresAt / dismissedId / force 判定', () => {
  const now = Date.parse('2026-11-23T12:00:00Z')
  const item = normalizeNotice({
    id: 'n-1',
    enabled: true,
    title: '公告',
    expiresAt: '2026-12-31T00:00:00Z'
  })
  assert.equal(shouldShowNotice(item, {}, { now }), true)
  assert.equal(shouldShowNotice(item, { noticeDismissedId: 'n-0' }, { now }), true)
  assert.equal(shouldShowNotice(item, { noticeDismissedId: 'n-1' }, { now }), false)
  assert.equal(shouldShowNotice(item, { noticeDismissedId: 'n-1' }, { now, force: true }), true)

  const disabled = normalizeNotice({ id: 'n-2', enabled: false, title: '关闭' })
  assert.equal(shouldShowNotice(disabled, {}, { now }), false)
  assert.equal(shouldShowNotice(disabled, {}, { now, force: true }), true)

  const expired = normalizeNotice({ id: 'n-3', enabled: true, title: '过期', expiresAt: '2026-01-01T00:00:00Z' })
  assert.equal(shouldShowNotice(expired, {}, { now }), false)
  assert.equal(shouldShowNotice(expired, {}, { now, force: true }), true)
})

test('fetchLocalNotice 成功解析并静默处理异常响应', async () => {
  const ok = await fetchLocalNotice({
    fetchImpl: async () => ({
      ok: true,
      json: async () => ({ id: 'ok-1', title: '测试公告' })
    })
  })
  assert.equal(ok.id, 'ok-1')

  const notFound = await fetchLocalNotice({
    fetchImpl: async () => ({ ok: false, status: 404 })
  })
  assert.equal(notFound, null)

  const netErr = await fetchLocalNotice({
    fetchImpl: async () => { throw new Error('offline') }
  })
  assert.equal(netErr, null)
})

test('maybeShowStartupNotice 非首页或已读时跳过且不拉取/不弹窗', async () => {
  let fetched = 0
  const skippedRoute = await maybeShowStartupNotice({
    isHomeRoute: () => false,
    fetchNotice: async () => { fetched += 1; return normalizeNotice({ id: 'x', title: 't' }) }
  })
  assert.equal(skippedRoute, null)
  assert.equal(fetched, 0)

  const skippedDismissed = await maybeShowStartupNotice({
    isHomeRoute: () => true,
    getPrefs: () => ({ noticeDismissedId: 'seen-1' }),
    fetchNotice: async () => normalizeNotice({ id: 'seen-1', title: 't' })
  })
  assert.equal(skippedDismissed, null)
})

test('内置 public/notice.json 合法且满足自动弹出条件', () => {
  const file = path.resolve(process.cwd(), 'public', 'notice.json')
  const raw = JSON.parse(fs.readFileSync(file, 'utf8'))
  const item = normalizeNotice(raw)
  assert.ok(item, 'public/notice.json 解析后不能为空')
  assert.equal(item.enabled, true)
  assert.ok(item.title.length > 0)
  assert.ok(item.bullets.length >= 1)
  assert.ok(item.promo && item.promo.title && item.promo.url)
  assert.equal(shouldShowNotice(item, {}), true)
})
