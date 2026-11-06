#!/usr/bin/env node
/**
 * 无头浏览器页面加载检查（只用 Node 标准库：child_process + fetch + WebSocket）。
 *
 * 做三件事：
 *   1. 用 --headless 的 Edge/Chrome 打开前端（走 CDP，不需要 puppeteer）
 *   2. 逐个路由切 hash，收集 console 报错与未捕获异常（import 失败、模块找不到都会在这里显形）
 *   3. 每个路由回读 DOM：视图真的渲染出了卡片/表格/字段（不是白屏）
 *
 * 用法：
 *   node test/ui-smoke/page-check.mjs --base http://127.0.0.1:4397 \
 *        --routes "/#/,/#/figures,/#/compare,/#/deep" [--browser <exe>] [--cdp 4398]
 * 退出码：0 = 页面无 JS 错误且各视图有内容；1 = 有错误/白屏。
 */
import { spawn } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

const argv = process.argv.slice(2)
function arg (name, fallback) {
  const i = argv.indexOf('--' + name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback
}

const BASE = arg('base', 'http://127.0.0.1:4397').replace(/\/$/, '')
const ROUTES = arg('routes', '/#/,/#/figures,/#/compare,/#/deep').split(',').map(s => s.trim()).filter(Boolean)
const CDP_PORT = Number(arg('cdp', '4398'))
const BROWSERS = [
  arg('browser', ''),
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  'C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe'
].filter(Boolean)

const browser = BROWSERS.find(p => { try { return fs.statSync(p).isFile() } catch { return false } })
if (!browser) {
  console.log('没有找到 Edge/Chrome，跳过无头检查（这一步是可选的）。')
  process.exit(0)
}

const profile = fs.mkdtempSync(path.join(os.tmpdir(), 'nexus-pagecheck-'))
const child = spawn(browser, [
  '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check',
  '--disable-extensions', '--disable-background-networking', '--mute-audio',
  '--remote-debugging-port=' + CDP_PORT, '--user-data-dir=' + profile, 'about:blank'
], { stdio: 'ignore' })

const sleep = ms => new Promise(r => setTimeout(r, ms))

async function pageTarget () {
  for (let i = 0; i < 60; i++) {
    try {
      const res = await fetch('http://127.0.0.1:' + CDP_PORT + '/json/list')
      const list = await res.json()
      const page = list.find(t => t.type === 'page' && t.webSocketDebuggerUrl)
      if (page) return page
    } catch {}
    await sleep(250)
  }
  throw new Error('等不到浏览器的调试端口（CDP ' + CDP_PORT + '）')
}

let seq = 0
const pending = new Map()
let ws = null
let sink = []          // 当前路由的事件缓存

function send (method, params) {
  const id = ++seq
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    ws.send(JSON.stringify({ id, method, params: params || {} }))
  })
}

const problems = []
const notes = []

function onEvent (msg) {
  if (msg.method === 'Runtime.exceptionThrown') {
    const d = msg.params.exceptionDetails || {}
    const desc = (d.exception && d.exception.description) || d.text || '未捕获异常'
    sink.push({ kind: 'exception', text: String(desc).split('\n')[0] })
  } else if (msg.method === 'Runtime.consoleAPICalled') {
    const type = msg.params.type
    if (type !== 'error' && type !== 'assert') return
    const text = (msg.params.args || []).map(a => a.value != null ? a.value : (a.description || a.type)).join(' ')
    sink.push({ kind: 'console', text: String(text) })
  } else if (msg.method === 'Log.entryAdded') {
    const e = msg.params.entry || {}
    if (e.level !== 'error') return
    // 网络 4xx/5xx 单独归类：后端端点还没实现时会出现，不算前端 JS 错误
    const net = e.source === 'network'
    sink.push({ kind: net ? 'network' : 'log', text: (e.text || '') + (e.url ? ' ' + e.url : '') })
  }
}

async function evaluate (expression) {
  const res = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })
  if (res.exceptionDetails) throw new Error(res.exceptionDetails.text || 'evaluate 失败')
  return res.result && res.result.value
}

/** 在页面里跑的 DOM 探针（写成函数再 toString，避免在脚本里嵌字符串模板） */
function domProbe () {
  const outlet = document.getElementById('outlet')
  const text = outlet ? (outlet.textContent || '') : ''
  const q = sel => document.querySelectorAll(sel).length
  return {
    hash: location.hash,
    booted: !document.getElementById('app') ? false : !document.getElementById('app').hidden,
    outletChars: text.replace(/\s+/g, ' ').trim().length,
    head: text.replace(/\s+/g, ' ').trim().slice(0, 90),
    cards: q('.fig-card'),
    figImgs: q('.fig-thumb img'),
    chips: q('.cmp-chip'),
    cmpTable: q('.cmp-table'),
    deepFields: q('.deep-field'),
    deepScores: q('.deep-score'),
    pickRows: q('.paper-pick-row'),
    navItems: q('.nav-item'),
    navActive: (document.querySelector('.nav-item.active') || {}).textContent || ''
  }
}
const DOM_PROBE = '(' + domProbe.toString() + ')()'

async function main () {
  const page = await pageTarget()
  ws = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    ws.onopen = resolve
    ws.onerror = () => reject(new Error('连不上 CDP WebSocket'))
  })
  ws.onmessage = ev => {
    let msg
    try { msg = JSON.parse(ev.data) } catch { return }
    if (msg.id && pending.has(msg.id)) {
      const p = pending.get(msg.id)
      pending.delete(msg.id)
      if (msg.error) p.reject(new Error(msg.error.message || 'CDP 错误'))
      else p.resolve(msg.result)
      return
    }
    onEvent(msg)
  }

  await send('Runtime.enable')
  await send('Log.enable')
  await send('Page.enable')
  await send('Page.navigate', { url: BASE + '/' })
  await sleep(2500)

  const rows = []
  for (const route of ROUTES) {
    sink = []
    await evaluate('location.hash = ' + JSON.stringify(route.replace(/^.*?#/, '#')))
    await sleep(1600)
    const probe = await evaluate(DOM_PROBE)
    const errors = sink.filter(s => s.kind === 'exception' || s.kind === 'console' || s.kind === 'log')
    const netFails = sink.filter(s => s.kind === 'network')
    for (const e of errors) problems.push(route + ' → ' + e.kind + '：' + e.text)
    if (netFails.length) notes.push(route + ' → 接口未就绪 ' + netFails.length + ' 个（' + netFails[0].text.slice(0, 80) + '）')
    if (!probe || !probe.booted || probe.outletChars < 4) {
      problems.push(route + ' → 页面疑似白屏（outletChars=' + (probe ? probe.outletChars : '?') + '）')
    }
    rows.push({ route, probe, errors: errors.length, net: netFails.length })
  }

  console.log('浏览器：' + browser)
  console.log('基址：' + BASE)
  for (const row of rows) {
    const p = row.probe || {}
    const bits = [
      'JS 错误 ' + row.errors,
      '白屏 ' + (p.outletChars < 4 ? '是' : '否'),
      'DOM ' + p.outletChars + ' 字'
    ]
    if (p.cards) bits.push('图表卡片 ' + p.cards + '（图 ' + p.figImgs + '）')
    if (p.chips) bits.push('已选对比 ' + p.chips)
    if (p.cmpTable) bits.push('对比表 ' + p.cmpTable)
    if (p.deepFields) bits.push('深度字段 ' + p.deepFields + ' / 评分条 ' + p.deepScores)
    if (p.pickRows) bits.push('可选文献行 ' + p.pickRows)
    console.log('  ' + row.route.padEnd(26) + ' | ' + bits.join(' · '))
    console.log('    导航高亮：' + (p.navActive || '(无)') + ' · 首屏文字：' + (p.head || '').slice(0, 46))
  }
  if (notes.length) {
    console.log('\n说明（后端端点尚未就绪时前端已按契约容错）：')
    for (const n of notes) console.log('  · ' + n)
  }
  if (problems.length) {
    console.log('\n问题（' + problems.length + ' 条）：')
    for (const p of problems) console.log('  ✗ ' + p)
    return 1
  }
  console.log('\n✓ 无头浏览器加载正常：无 JS 异常、无模块加载错误、各视图有内容。')
  return 0
}

let code = 1
try {
  code = await main()
} catch (err) {
  console.error('检查失败：' + err.message)
} finally {
  try { ws && ws.close() } catch {}
  try { child.kill() } catch {}
  await sleep(300)
  try { fs.rmSync(profile, { recursive: true, force: true }) } catch {}
}
process.exit(code)
