import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const settings = readFileSync(join(here, '../../public/js/views/settings.mjs'), 'utf8')

test('settings page uses the frozen immediate-install handshake', () => {
  assert.match(settings, /from '\.\.\/update-install\.mjs'/)
  assert.match(settings, /requestInstall\(\{ version: appliedVersion \}\)/)
  assert.match(settings, /isWebViewHost\(\)/)
})

test('confirmation copy covers immediate close, auto reopen, data and AI interruption', () => {
  assert.match(settings, /立即关闭/)
  assert.match(settings, /自动重新打开/)
  assert.match(settings, /文献库数据（data\/）会保留/)
  assert.match(settings, /AI 请求会被中断/)
  assert.match(settings, /不会静默安装/)
  assert.match(settings, /okText: '立即安装并关闭'/)
})

test('auto-download toggle exists, defaults off and never auto-installs', () => {
  assert.match(settings, /id: 'update-auto-download-toggle'/)
  assert.match(settings, /autoDownloadEnabled\(state\.prefs\)/)
  assert.match(settings, /自动下载默认关闭/)
  assert.match(settings, /不会静默安装/)
})

test('apply happens before install request and browser preview has fallback', () => {
  const applyAt = settings.indexOf('await api.applyUpdate()')
  const requestAt = settings.indexOf('await requestInstall(')
  assert.ok(applyAt > 0 && requestAt > applyAt, 'apply 必须先于启动器请求')
  assert.match(settings, /fallbackInstallMessage\(\)/)
  assert.doesNotMatch(settings, /重启并安装/)
  assert.doesNotMatch(settings, /关闭窗口后自动更新/)
})
