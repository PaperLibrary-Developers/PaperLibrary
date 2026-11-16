#!/usr/bin/env node
/* P2-20 真实发布验收：旧客户端设置页 检查→下载→立即安装→退出。 */
import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

const args = process.argv.slice(2)
const argValue = (name, fallback) => {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const port = Number(argValue('--port', '9235'))
const outDir = argValue('--out', 'p2-20-shots')
mkdirSync(outDir, { recursive: true })

const targets = await fetch('http://127.0.0.1:' + port + '/json/list').then(r => r.json())
const page = targets.find(t => t.type === 'page' && /127\.0\.0\.1:4321/.test(t.url || ''))
if (!page) throw new Error('no page')

const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true })
  ws.addEventListener('error', reject, { once: true })
})
let nextId = 1
const pending = new Map()
ws.addEventListener('message', event => {
  const msg = JSON.parse(event.data)
  if (!msg.id || !pending.has(msg.id)) return
  const item = pending.get(msg.id); pending.delete(msg.id)
  if (msg.error) item.reject(new Error(JSON.stringify(msg.error)))
  else item.resolve(msg.result)
})
function send (method, params = {}) {
  const id = nextId++
  ws.send(JSON.stringify({ id, method, params }))
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }))
}
async function evaluate (expression) {
  const result = await send('Runtime.evaluate', {
    expression, returnByValue: true, awaitPromise: true, userGesture: true
  })
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'eval failed')
  return result.result && result.result.value
}
async function shot (name) {
  await evaluate("(function(){var e=document.querySelector('#update-check-status');if(e)e.scrollIntoView({block:'center'});return true;})()")
  await new Promise(r => setTimeout(r, 300))
  const result = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  const path = join(outDir, name)
  writeFileSync(path, Buffer.from(result.data, 'base64'))
  console.log('screenshot: ' + path)
}
async function waitFor (expression, timeoutMs, interval = 500) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const value = await evaluate(expression)
    if (value) return value
    await new Promise(r => setTimeout(r, interval))
  }
  throw new Error('timeout: ' + expression)
}

await send('Page.enable')
await send('Runtime.enable')
await evaluate("location.hash = '#/settings'")
await new Promise(r => setTimeout(r, 1200))
await shot('01-old-version.png')
console.log('old version: ' + await evaluate("(function(){var e=document.querySelector('#update-current-version');return e?e.textContent:'';})()"))

await evaluate("(function(){var b=document.querySelector('#update-check-btn');if(b)b.click();return true;})()")
const found = await waitFor("(function(){var e=document.querySelector('#update-check-status');return e&&e.textContent.indexOf('发现新版本')>=0?e.textContent:'';})()", 60000)
console.log('check: ' + found)
await shot('02-found.png')

await evaluate("(function(){var b=document.querySelector('#update-download-btn');if(b)b.click();return true;})()")
const ready = await waitFor("(function(){var b=document.querySelector('#update-install-btn');if(!b)return '';var e=document.querySelector('#update-check-status');return e?e.textContent:'';})()", 240000, 1000)
console.log('ready: ' + ready)
await shot('03-ready.png')

await evaluate("(function(){var b=document.querySelector('#update-install-btn');if(b)b.click();return true;})()")
await new Promise(r => setTimeout(r, 600))
await shot('04-confirm.png')
const confirmed = await evaluate("(function(){var bs=Array.from(document.querySelectorAll('button'));var b=bs.find(function(x){return (x.textContent||'').indexOf('立即安装并关闭')>=0;});if(!b)return 'no-confirm';b.click();return 'confirmed';})()")
console.log('confirm: ' + confirmed)
await new Promise(r => setTimeout(r, 900))
try { await shot('05-closing.png') } catch (err) { console.log('closing screenshot skipped: ' + err.message) }
ws.close()
