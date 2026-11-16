#!/usr/bin/env node
import { writeFileSync } from 'node:fs'
const port = Number(process.argv[2] || '9235')
const out = process.argv[3]
if (!out) throw new Error('usage: node capture_current_page.mjs <port> <out.png>')
const targets = await fetch('http://127.0.0.1:' + port + '/json/list').then(r => r.json())
const page = targets.find(t => t.type === 'page' && /127\.0\.0\.1:4321/.test(t.url || ''))
if (!page) throw new Error('no page')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve, reject) => {
  ws.addEventListener('open', resolve, { once: true })
  ws.addEventListener('error', reject, { once: true })
})
let id = 1
const pending = new Map()
ws.addEventListener('message', event => {
  const msg = JSON.parse(event.data)
  if (!msg.id || !pending.has(msg.id)) return
  const item = pending.get(msg.id); pending.delete(msg.id)
  if (msg.error) item.reject(new Error(JSON.stringify(msg.error)))
  else item.resolve(msg.result)
})
const send = (method, params = {}) => {
  const i = id++
  ws.send(JSON.stringify({ id: i, method, params }))
  return new Promise((resolve, reject) => pending.set(i, { resolve, reject }))
}
await send('Page.enable')
await new Promise(r => setTimeout(r, 700))
const shot = await send('Page.captureScreenshot', { format: 'png' })
writeFileSync(out, Buffer.from(shot.data, 'base64'))
console.log('screenshot: ' + out)
ws.close()
