/* 深度分析视图 #/deep 与 #/deep/:id（合并后新增的浏览器视图）
   移植自 PaperLibrary app/deep_analysis.py（22 字段 + 五维评分报告的展示面），
   数据面换成 Nexus 的 HTTP 端点。契约：docs/合并新增端点契约-20261107.md
        §7 GET  /api/papers/:id/deep-analysis → { analysis: null | {paperId,updatedAt,model,fields{},scores{},oneLine} }
        §8 POST /api/papers/:id/deep-analysis { force } → { analysis, saved:true }
           无 Key 时服务端做本地降级，**仍返回 200**（fields 里会标注「未配置大模型，仅本地分析」）
   依赖：util.mjs / api.mjs / shell/app-api.mjs / views/paper-pick.mjs —— 不 import app.mjs。 */
import * as util from '../util.mjs'
import { api } from '../api.mjs'
import { go } from '../shell/app-api.mjs'
import { paperPicker } from './paper-pick.mjs'

const { h, toast, formatDate, relTime, truncate } = util

/** 本地降级结果的识别：契约要求无 Key 时 fields 里标注这句话 */
const LOCAL_RE = /未配置大模型|仅本地分析|本地分析/

function analysisLooksLocal (analysis) {
  if (!analysis) return false
  if (analysis.local === true || analysis.degraded === true) return true
  try {
    return LOCAL_RE.test(JSON.stringify(analysis.fields || {}))
  } catch {
    return false
  }
}

/** 字段值渲染：字符串直接显示，数组成列表，对象再展开一层（后端字段结构可能是有嵌套的） */
function renderValue (value, depth) {
  const level = depth || 0
  if (value == null) return h('span.deep-dim', '—')
  if (Array.isArray(value)) {
    if (!value.length) return h('span.deep-dim', '—')
    return h('ul.deep-list', value.map(v => h('li', renderValue(v, level + 1))))
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value)
    if (!entries.length) return h('span.deep-dim', '—')
    if (level >= 2) return h('div.deep-text', { text: JSON.stringify(value) })
    return h('div.deep-nest', entries.map(([k, v]) =>
      h('div.deep-nest-row',
        h('span.deep-nest-k', { text: k }),
        renderValue(v, level + 1)
      )))
  }
  const text = String(value).trim()
  if (!text) return h('span.deep-dim', '—')
  return h('div.deep-text', { text })
}

/** 五维评分（契约示例是 0-5 的整数；若出现 >5 的值按 10 分制画条） */
function scoreBlock (scores) {
  const entries = Object.entries(scores || {})
  if (!entries.length) return null
  const maxScore = entries.some(([, v]) => Number(v) > 5) ? 10 : 5
  return h('div.deep-scores', entries.map(([key, value]) => {
    const n = Number(value)
    const shown = Number.isFinite(n) ? n : 0
    const pct = Math.max(0, Math.min(100, (shown / maxScore) * 100))
    return h('div.deep-score',
      h('div.deep-score-top',
        h('span.deep-score-k', { text: key }),
        h('span.deep-score-v', { text: Number.isFinite(n) ? (n + ' / ' + maxScore) : '—' })
      ),
      h('div.deep-score-bar', h('i', { style: { width: pct + '%' } }))
    )
  }))
}

function deepHandler (ctx) {
  const { outlet, params, routeParams, isStale } = ctx
  const paperId = String((routeParams && routeParams.id) || params.get('paperId') || params.get('id') || '').trim()

  /* ---------------- 没带文献：先选一篇 ---------------- */
  if (!paperId) {
    const picker = paperPicker({
      placeholder: '检索要做深度分析的文献…',
      actionLabel: '分析这篇',
      limit: 8,
      onPick: item => go('deep/' + encodeURIComponent(item.id))
    })
    outlet.replaceChildren(h('div.content',
      h('div.page-head', h('div',
        h('div.page-title', '深度分析'),
        h('div.page-sub', '单篇文献的 22 个字段 + 五维评分；结果存本地，下次直接看缓存。')
      )),
      h('section.panel',
        h('div.panel-head', '选一篇文献'),
        h('div.panel-body', { style: { display: 'grid', gap: '10px' } },
          h('div.hint', '点一行进入这篇的深度分析。也可以从文献详情页点「🔬 深度分析」直接进来。'),
          picker.el
        )
      )
    ))
    picker.load('')
    return
  }

  /* ---------------- 单篇：读题录 + 缓存的分析 ---------------- */
  const headTitle = h('div.page-title', '深度分析')
  const headSub = h('div.page-sub', '正在读取…')
  const redoBtn = h('button.btn.sm', { type: 'button', disabled: true, onclick: () => runAnalysis(true) }, '重新分析')
  const detailBtn = h('button.btn.sm.ghost', {
    type: 'button',
    onclick: () => go('paper/' + encodeURIComponent(paperId))
  }, '打开文献')
  const body = h('div')

  outlet.replaceChildren(h('div.content',
    h('div.page-head',
      h('div', headTitle, headSub),
      h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } }, detailBtn, redoBtn)
    ),
    body
  ))

  let paper = null
  let analysis = null
  let running = false

  async function loadPaper () {
    try {
      const res = await api.getPaper(paperId)
      paper = (res && res.paper) || null
    } catch (err) {
      paper = null
      if (isStale()) return
      headSub.textContent = '读不到这条文献：' + err.message
    }
    if (paper) {
      headTitle.textContent = '深度分析 · ' + truncate(paper.title || '', 60)
      headSub.textContent = [paper.year, paper.venue].filter(Boolean).join(' · ') || '《' + (paper.title || paperId) + '》'
    }
  }

  /* ---------------- 分析结果 ---------------- */

  function renderAnalysis () {
    redoBtn.textContent = analysis ? '重新分析' : '开始分析'
    redoBtn.disabled = running
    if (running) {
      body.replaceChildren(h('section.panel',
        h('div.panel-head', '分析中'),
        h('div.panel-body', { style: { display: 'grid', gap: '8px' } },
          h('div.deep-running', '正在分析…（读摘要与正文节选 → 22 个字段 → 五维评分）'),
          h('div.hint', '这一步可能要 30–90 秒；没有配置大模型时会在几秒内退回本地结果，不会失败。')
        )
      ))
      return
    }
    if (!analysis) {
      body.replaceChildren(h('section.panel',
        h('div.panel-head', '还没有做深度分析'),
        h('div.panel-body', { style: { display: 'grid', gap: '10px' } },
          h('p', { text: '深度分析会把这篇文献拆成 22 个可归档的字段（研究问题、方法、条件、主要结果、机理证据、局限…），' +
            '再给五维评分（创新性 / 严谨性 / 影响力 / 可复现性 / 可读性），结果存在本地，不会重复计费。' }),
          h('div.hint', '没有配置大模型也能跑：会基于题录、摘要与正文做本地分析，并在字段里标明「未配置大模型，仅本地分析」。'),
          h('button.btn.primary', { type: 'button', onclick: () => runAnalysis(false) }, '开始分析')
        )
      ))
      return
    }

    const fields = analysis.fields && typeof analysis.fields === 'object' ? analysis.fields : {}
    const fieldEntries = Object.entries(fields)
    const local = analysisLooksLocal(analysis)
    const metaBits = []
    if (analysis.updatedAt) {
      metaBits.push('缓存于 ' + formatDate(analysis.updatedAt) + (relTime(analysis.updatedAt) ? '（' + relTime(analysis.updatedAt) + '）' : ''))
    }
    if (analysis.model) metaBits.push('模型：' + analysis.model)
    metaBits.push('字段：' + fieldEntries.length + ' 个')

    const children = [
      h('div.panel-head', '分析结果',
        h('div.right', h('span.hint', { text: metaBits.join(' · ') }))
      )
    ]
    if (local) {
      children.push(h('div.panel-body.tight',
        h('div.deep-note', '本地降级分析：当前没有可用的大模型，这些内容来自题录、摘要与正文的本地处理，' +
          '五维评分是按启发式规则给的，仅供参考。在「设置 → 大模型」配置后点「重新分析」可得到完整结果。')
      ))
    }
    if (analysis.oneLine) {
      children.push(h('div.panel-body.tight', h('div.deep-oneline', { text: analysis.oneLine })))
    }
    const scores = scoreBlock(analysis.scores)
    if (scores) {
      children.push(h('div.panel-body', h('div.deep-block-title', '五维评分'), scores))
    }
    children.push(h('div.panel-body',
      h('div.deep-block-title', '分析字段' + (fieldEntries.length ? '（' + fieldEntries.length + '）' : '')),
      fieldEntries.length
        ? h('div.deep-fields', fieldEntries.map(([key, value]) =>
          h('div.deep-field',
            h('div.deep-field-k', { text: key }),
            h('div.deep-field-v', renderValue(value))
          )))
        : h('div.hint', '这次分析没有返回任何字段（后端可能还没实现，或该篇内容为空）。')
    ))
    body.replaceChildren(h('section.panel', ...children))
  }

  /* ---------------- 运行（§8） ---------------- */

  async function runAnalysis (force) {
    if (running) return
    running = true
    renderAnalysis()
    try {
      const res = await api.runDeepAnalysis(paperId, { force: !!force })
      if (isStale()) return
      analysis = (res && res.analysis) || null
      running = false
      renderAnalysis()
      if (analysisLooksLocal(analysis)) {
        toast('已生成本地分析（没有配置大模型）')
      } else {
        toast(force ? '已重新分析并保存' : '分析完成并保存')
      }
    } catch (err) {
      if (isStale()) return
      running = false
      body.replaceChildren(h('section.panel',
        h('div.panel-head', '分析失败'),
        h('div.panel-body', { style: { display: 'grid', gap: '10px' } },
          h('div.deep-error', { text: err.message || '分析失败' }),
          h('div.hint', '可以重试；如果是「文献不存在」，说明这条记录已经被删掉了。没有配置模型时不该出现失败——那属于缺陷。'),
          h('div', { style: { display: 'flex', gap: '8px' } },
            h('button.btn', { type: 'button', onclick: () => runAnalysis(!!force) }, '重试'),
            h('button.btn.ghost', { type: 'button', onclick: () => go('settings') }, '去配置模型')
          )
        )
      ))
      redoBtn.disabled = false
      redoBtn.textContent = analysis ? '重新分析' : '开始分析'
    }
  }

  /* ---------------- 启动 ---------------- */

  async function boot () {
    await loadPaper()
    if (isStale()) return
    try {
      const res = await api.getDeepAnalysis(paperId)
      if (isStale()) return
      analysis = (res && res.analysis) || null
      redoBtn.disabled = false
      renderAnalysis()
    } catch (err) {
      if (isStale()) return
      redoBtn.disabled = false
      body.replaceChildren(h('section.panel',
        h('div.panel-head', '读取失败'),
        h('div.panel-body', { style: { display: 'grid', gap: '10px' } },
          h('div.deep-error', { text: err.message || '读取深度分析失败' }),
          h('div', { style: { display: 'flex', gap: '8px' } },
            h('button.btn', { type: 'button', onclick: () => boot() }, '重试'),
            h('button.btn.primary', { type: 'button', onclick: () => runAnalysis(false) }, '开始分析')
          )
        )
      ))
    }
  }

  boot().catch(err => {
    console.error('[deep] 读取失败：', err)
    if (!isStale()) {
      body.replaceChildren(h('div.empty', h('div.big', '页面出错了'), h('p', { text: err.message })))
    }
  })
}

export { deepHandler }
export default deepHandler
