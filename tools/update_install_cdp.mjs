#!/usr/bin/env node
/* P2-09R/P2-11R 的 CDP 验收脚本：点击“立即安装”，截取确认框与关闭前状态。
 * 用法：PAPERLIB_WEBVIEW_DEBUG_PORT=9222 node tools/update_install_cdp.mjs --out <shots>
 * 前置：应用已启动、设置页已打开、更新已 ready；脚本只做点击与截图，不修改数据。
 */
import { writeFileSync, mkdirSync } from 'node:fs'
import { join } from 'node:path'

const args = process.argv.slice(2)
const argValue = (name, fallback) => {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : fallback
}
const port = Number(process.env.PAPERLIB_WEBVIEW_DEBUG_PORT || argValue('--port', '9222'))
const outDir = argValue('--out', 'update-install-shots')
const mockApply = args.includes('--mock-apply')
mkdirSync(outDir, { recursive: true })

const targets = await fetch('http://127.0.0.1:' + port + '/json/list').then(r => r.json())
const page = targets.find(t => t.type === 'page' && /127\.0\.0\.1:4321/.test(t.url || ''))
if (!page) throw new Error('找不到 PaperLibrary 页面；请确认启动器已用调试端口打开')

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
  const item = pending.get(msg.id)
  pending.delete(msg.id)
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
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text || 'evaluate failed')
  return result.result && result.result.value
}
async function shot (name) {
  const result = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false })
  const path = join(outDir, name)
  writeFileSync(path, Buffer.from(result.data, 'base64'))
  console.log('screenshot: ' + path)
}
await send('Page.enable')
await send('Runtime.enable')
if (mockApply) {
  await send('Page.addScriptToEvaluateOnNewDocument', {
    source: "(function(){var of=window.fetch;window.fetch=function(input,init){if(String(input).indexOf('/api/update/apply')>=0){return Promise.resolve(new Response(JSON.stringify({ok:true,apply:{version:'1.7.2'}}),{status:200,headers:{'Content-Type':'application/json'}}));}return of.apply(this,arguments);};})();"
  })
  await send('Page.reload', { ignoreCache: true })
  await new Promise(resolve => setTimeout(resolve, 1000))
}
await evaluate("location.hash = '#/settings'")
await new Promise(resolve => setTimeout(resolve, 600))
await evaluate("(function(){var e=document.querySelector('#update-check-status');if(e)e.scrollIntoView({block:'center'});return true;})()")
await new Promise(resolve => setTimeout(resolve, 300))
await shot('01-before-click.png')

const clicked = await evaluate("(function(){var b=document.querySelector('#update-install-btn');if(!b)return 'no-install-button';b.click();return 'clicked';})()")
console.log('install button: ' + clicked)
await new Promise(resolve => setTimeout(resolve, 500))
await shot('02-confirm-dialog.png')

const confirmed = await evaluate("(function(){var bs=Array.from(document.querySelectorAll('button'));var b=bs.find(function(x){return (x.textContent||'').indexOf('立即安装并关闭')>=0;});if(!b)return 'no-confirm-button';b.click();return 'confirmed';})()")
console.log('confirm dialog: ' + confirmed)
await new Promise(resolve => setTimeout(resolve, 1200))
const status = await evaluate("(function(){var e=document.querySelector('#update-check-status');return e?e.textContent:'';})()")
console.log('status: ' + status)
await evaluate("(function(){var e=document.querySelector('#update-check-status');if(e)e.scrollIntoView({block:'center'});return true;})()")
await new Promise(resolve => setTimeout(resolve, 200))
await shot('03-after-confirm.png')
ws.close()
