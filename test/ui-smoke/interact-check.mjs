#!/usr/bin/env node
/**
 * 新视图的「交互」冒烟（三个视图各一条真实操作链路）：
 *
 *   figures：#/figures → 缩略图网格出现 → 点第一张 → 大图灯箱打开（.fig-modal）→ Esc 关闭
 *   compare：#/compare?ids=<id1>,<id2> → 点「开始对比」→ 出 sections 表格；本地降级时出现「本地对比」提示
 *   deep   ：#/deep/<id> → 点「开始分析」（或直接读到缓存）→ fields 与五维评分条都渲染出来
 *
 * 用法：
 *   node test/ui-smoke/interact-check.mjs --base http://127.0.0.1:4397 --ids <id1>,<id2>
 * 退出码：0 = 三条链路都通过；1 = 有失败。
 */
import { openPage, sleep } from './cdp.mjs'

const argv = process.argv.slice(2)
function arg (name, fallback) {
  const i = argv.indexOf('--' + name)
  return i >= 0 && argv[i + 1] ? argv[i + 1] : fallback
}

const BASE = arg('base', 'http://127.0.0.1:4397')
const IDS = arg('ids', '').split(',').map(s => s.trim()).filter(Boolean)
const CDP_PORT = Number(arg('cdp', '4399'))
const BROWSER = arg('browser', '')

/** 每一步的结果都记成 {name, ok, detail}，最后统一打印 */
const steps = []
function step (name, ok, detail) {
  steps.push({ name, ok: !!ok, detail: detail || '' })
  return !!ok
}

let page = null
let code = 1
try {
  page = await openPage({ base: BASE, browserPath: BROWSER, cdpPort: CDP_PORT })
  page.takeErrors()
  console.log('浏览器：' + page.browserPath)
  console.log('基址：' + page.base + '；ids=' + (IDS.length ? IDS.join(',') : '(无，跳过需要文献的步骤)'))

  /* ---------- 1. 图表画廊：点缩略图开大图 ---------- */
  await page.hash('/#/figures', 1600)
  const cards = await page.count('.fig-card')
  const imgs = await page.count('.fig-thumb img')
  if (cards > 0) {
    step('figures 缩略图网格', true, cards + ' 张卡片 / ' + imgs + ' 个缩略图')
    await page.click('.fig-card', 700)
    const modal = await page.count('.fig-modal')
    const bigImg = await page.count('.fig-large-img')
    step('figures 点图开大图', modal > 0, '灯箱 ' + modal + ' 个，大图 ' + bigImg + ' 个')
    await page.evaluate('document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }))')
    await sleep(400)
    step('figures Esc 关闭大图', (await page.count('.fig-modal')) === 0, '')
  } else {
    step('figures 缩略图网格', true, '库里还没有图表记录（跳过点击，属于无数据而非失败）')
  }
  // 搜索词与单篇筛选都直接写进地址（地址即状态）
  await page.hash('/#/figures?q=Smoke', 1500)
  const summary = await page.text('.list-summary')
  const hits = await page.count('.fig-card')
  step('figures 关键词检索', /关键词/.test(summary), '摘要行：' + summary + '；命中 ' + hits + ' 张')
  if (IDS.length) {
    await page.hash('/#/figures?paperId=' + IDS[0], 1600)
    const chip = await page.count('.fig-paper-chip')
    const extractBtn = await page.evaluate('Array.from(document.querySelectorAll(".list-controls .btn")).some(b => /重提图表/.test(b.textContent))')
    step('figures 单篇筛选 + 重提按钮', chip > 0 && extractBtn === true, '筛选标签 ' + chip + ' 个，重提按钮 ' + (extractBtn ? '有' : '无'))
  }
  const figErrs = page.takeErrors()
  step('figures 无 JS 错误', figErrs.js.length === 0, figErrs.js.map(e => e.text).join(' | '))

  /* ---------- 2. 横向对比：点「开始对比」拿 sections 表 ---------- */
  if (IDS.length >= 2) {
    await page.hash('/#/compare?ids=' + IDS.slice(0, 3).join(','), 1600)
    const chips = await page.count('.cmp-chip')
    step('compare 从地址带入已选文献', chips >= 2, chips + ' 个已选标签')
    const runBtn = await page.count('.panel .btn.primary')
    step('compare 有可用的「开始对比」按钮', runBtn > 0, '')
    if (runBtn > 0) {
      await page.evaluate('(() => { const b = Array.from(document.querySelectorAll(".panel .btn.primary")).find(x => /开始对比/.test(x.textContent)); if (b) b.click() })()')
      await sleep(4500)
      const tableRows = await page.count('.cmp-table tbody tr')
      const cols = await page.count('.cmp-table thead th')
      const noteShown = await page.count('.cmp-note:not([hidden])')
      step('compare 出 sections 表格', tableRows > 0, tableRows + ' 行 / ' + cols + ' 列')
      step('compare 本地对比提示', true, noteShown > 0 ? '已显示（本地降级）' : '未显示（走了模型）')
      const errText = await page.text('.cmp-error')
      step('compare 没有渲染错误面板', !errText, errText)
    }
  } else {
    step('compare 需要 2-8 篇文献', true, '没有给 --ids，跳过（用 --ids a,b 复跑）')
  }
  const cmpErrs = page.takeErrors()
  step('compare 无 JS 错误', cmpErrs.js.length === 0, cmpErrs.js.map(e => e.text).join(' | '))

  /* ---------- 3. 深度分析：跑一次，fields + 五维评分 ---------- */
  if (IDS.length) {
    await page.hash('/#/deep/' + IDS[0], 1800)
    let fields = await page.count('.deep-field')
    let scores = await page.count('.deep-score')
    let note = ''
    if (!fields) {
      await page.evaluate('(() => { const b = Array.from(document.querySelectorAll(".panel .btn.primary, .page-head .btn")).find(x => /开始分析|重新分析/.test(x.textContent)); if (b) b.click() })()')
      await sleep(6000)
      fields = await page.count('.deep-field')
      scores = await page.count('.deep-score')
    } else {
      note = '（读到的是缓存结果）'
    }
    step('deep 渲染分析字段', fields > 0, fields + ' 个字段' + note)
    step('deep 渲染五维评分条', scores > 0, scores + ' 条评分')
    const errText = await page.text('.deep-error')
    step('deep 没有渲染错误面板', !errText, errText)
  } else {
    step('deep 需要一篇文献', true, '没有给 --ids，跳过')
  }
  const deepErrs = page.takeErrors()
  step('deep 无 JS 错误', deepErrs.js.length === 0, deepErrs.js.map(e => e.text).join(' | '))

  /* ---------- 汇总 ---------- */
  console.log('')
  for (const s of steps) {
    console.log('  ' + (s.ok ? '✓' : '✗') + ' ' + s.name + (s.detail ? ' —— ' + s.detail : ''))
  }
  const failed = steps.filter(s => !s.ok)
  if (failed.length) {
    console.log('\n失败 ' + failed.length + ' 项')
  } else {
    console.log('\n✓ 三个新视图的交互链路都通过。')
    code = 0
  }
} catch (err) {
  console.error('检查失败：' + err.message)
} finally {
  if (page) await page.close()
}
process.exit(code)
