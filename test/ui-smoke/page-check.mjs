#!/usr/bin/env node
/**
 * 无头浏览器「页面加载」检查：逐个路由切 hash，收集 JS 错误 / 未捕获异常，并回读 DOM 判断不是白屏。
 *
 * 用法：
 *   node test/ui-smoke/page-check.mjs --base http://127.0.0.1:4397 \
 *        --routes "/#/,/#/figures,/#/compare,/#/deep" [--browser <exe>] [--cdp 4398]
 * 退出码：0 = 各路由无 JS 错误且有内容；1 = 有错误/白屏。
 */
import { openPage, sleep } from './cdp.mjs'

const argv = process.argv.slice(2)
function arg (name, fallback) {
  const i = argv.indexOf('--' + name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback
}

const BASE = arg('base', 'http://127.0.0.1:4397')
// 路由统一取 '#' 之后的部分（MSYS/Git Bash 会把以 '/' 开头的参数当路径改写，取 hash 就稳了）
const ROUTES = arg('routes', '/#/,/#/figures,/#/compare,/#/deep')
  .split(',')
  .map(s => s.trim())
  .filter(Boolean)
  .map(s => (s.includes('#') ? s.slice(s.indexOf('#')) : s))
const CDP_PORT = Number(arg('cdp', '4398'))
const BROWSER = arg('browser', '')

/** 在页面里跑的 DOM 探针（写成函数再 toString，避免脚本里嵌字符串模板） */
function domProbe () {
  const outlet = document.getElementById('outlet')
  const text = outlet ? (outlet.textContent || '') : ''
  const q = sel => document.querySelectorAll(sel).length
  return {
    hash: location.hash,
    booted: document.getElementById('app') ? !document.getElementById('app').hidden : false,
    chars: text.replace(/\s+/g, ' ').trim().length,
    head: text.replace(/\s+/g, ' ').trim().slice(0, 80),
    cards: q('.fig-card'),
    figImgs: q('.fig-thumb img'),
    chips: q('.cmp-chip'),
    cmpTable: q('.cmp-table'),
    deepFields: q('.deep-field'),
    deepScores: q('.deep-score'),
    pickRows: q('.paper-pick-row'),
    navActive: ((document.querySelector('.nav-item.active') || {}).textContent || '').replace(/\s+/g, '')
  }
}

let page = null
let code = 1
try {
  page = await openPage({ base: BASE, browserPath: BROWSER, cdpPort: CDP_PORT })
  console.log('浏览器：' + page.browserPath)
  console.log('基址：' + page.base)
  const problems = []
  const notes = []
  page.takeErrors()

  for (const route of ROUTES) {
    await page.hash(route, 1500)
    await sleep(150)
    const probe = await page.evaluate('(' + domProbe.toString() + ')()')
    const errs = page.takeErrors()
    for (const e of errs.js) problems.push(route + ' → ' + e.kind + '：' + e.text)
    if (errs.network.length) {
      notes.push(route + ' → 接口 4xx/5xx ' + errs.network.length + ' 个（' + errs.network[0].text.slice(0, 70) + '）')
    }
    if (!probe || !probe.booted || probe.chars < 4) {
      problems.push(route + ' → 页面疑似白屏（chars=' + (probe ? probe.chars : '?') + '）')
    }
    const bits = [
      'JS 错误 ' + errs.js.length,
      '白屏 ' + (probe && probe.chars >= 4 ? '否' : '是'),
      'DOM ' + (probe ? probe.chars : 0) + ' 字'
    ]
    if (probe && probe.cards) bits.push('图表卡片 ' + probe.cards + '（图 ' + probe.figImgs + '）')
    if (probe && probe.chips) bits.push('已选对比 ' + probe.chips)
    if (probe && probe.cmpTable) bits.push('对比表 ' + probe.cmpTable)
    if (probe && probe.deepFields) bits.push('深度字段 ' + probe.deepFields + ' / 评分条 ' + probe.deepScores)
    if (probe && probe.pickRows) bits.push('可选文献行 ' + probe.pickRows)
    console.log('  ' + route.padEnd(26) + ' | ' + bits.join(' · '))
    console.log('    导航高亮：' + ((probe && probe.navActive) || '(无)') + ' · 首屏：' + ((probe && probe.head) || '').slice(0, 42))
  }
  if (notes.length) {
    console.log('\n说明：')
    for (const n of notes) console.log('  · ' + n)
  }
  if (problems.length) {
    console.log('\n问题（' + problems.length + ' 条）：')
    for (const p of problems) console.log('  ✗ ' + p)
  } else {
    console.log('\n✓ 无头浏览器加载正常：无 JS 异常、无模块加载错误、各视图有内容。')
    code = 0
  }
} catch (err) {
  console.error('检查失败：' + err.message)
} finally {
  if (page) await page.close()
}
process.exit(code)
