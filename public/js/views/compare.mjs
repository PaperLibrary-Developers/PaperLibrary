/* 横向对比视图 #/compare（合并后新增；PaperLibrary compare_engine 的浏览器前端）
   契约：docs/合并新增端点契约-20261107.md
        §5 GET  /api/compare/templates → { templates:[{id,name,desc,fields[]}] }（至少 4 套，无 Key 也能列）
        §6 POST /api/compare { paperIds[2..8], template } → { result:{ template, oneLine, sections:[{title,rows:[{paperId,value}]}], usage } }
        错误：400（少于 2 条 / 超过 8 条）、404（任一文献不存在）
   无 Key 时服务端只输出本地可算字段与资料摘要 —— 视图在这里显示「本地对比」提示，不假装做过模型归纳。
   依赖：util.mjs / api.mjs / shell/app-api.mjs / views/paper-pick.mjs —— 不 import app.mjs。 */
import * as util from '../util.mjs'
import { api } from '../api.mjs'
import { go } from '../shell/app-api.mjs'
import { paperPicker } from './paper-pick.mjs'

const { h, toast, replace, truncate } = util

const MAX_PAPERS = 8
const MIN_PAPERS = 2

/** 模块级选择：切模板/加文献时原地更新，不写地址 → 不会把用户正在输入的检索框冲掉 */
const selected = []
/** paperId → { title, year, venue }（表格列头与选择条都要用） */
const titleCache = new Map()

const LOCAL_NOTE = '本地对比：当前没有可用的大模型（未配置，或调用失败），下面只有本地能算出来的字段与资料摘要，' +
  '不做模型归纳、不编造内容。在「设置 → 大模型」配置后可以拿到完整的化学字段对比。'

function isKeyMissing (err) {
  if (!err) return false
  if (err.status !== 400 && err.status !== 502) return false
  return /未配置|大模型|密钥|API\s*Key|Key|模型/.test(String(err.message || ''))
}

/** 响应本身能看出是降级结果时也算「本地对比」（usage 全 0，或后端显式标注） */
function resultLooksLocal (result) {
  if (!result || typeof result !== 'object') return false
  if (result.local === true || result.degraded === true || result.fallback === true) return true
  const usage = result.usage || {}
  return (Number(usage.prompt_tokens) || 0) === 0 && (Number(usage.completion_tokens) || 0) === 0
}

/** 单元格文本：契约里 value 是字符串，这里对数组/对象也留条退路 */
function cellText (value) {
  if (value == null) return '—'
  if (Array.isArray(value)) return value.length ? value.map(v => String(v)).join('\n') : '—'
  if (typeof value === 'object') {
    const bits = Object.entries(value).map(([k, v]) => k + '：' + (v == null ? '' : String(v)))
    return bits.length ? bits.join('\n') : '—'
  }
  const s = String(value).trim()
  return s || '—'
}

function compareHandler (ctx) {
  const { outlet, params, isStale } = ctx

  /* ---------------- 从地址带入（详情页「⇄ 对比」会带 ids） ---------------- */
  const idsFromUrl = String(params.get('ids') || '')
    .split(',')
    .map(s => s.trim())
    .filter(Boolean)
  for (const id of idsFromUrl) {
    if (selected.length >= MAX_PAPERS) break
    if (!selected.includes(id)) selected.push(id)
  }

  let templates = []
  let currentTemplate = params.get('template') || ''
  let templLoadError = ''

  /* ---------------- 三个容器 ---------------- */
  const setupBox = h('div')
  const resultBox = h('div')
  outlet.replaceChildren(h('div.content',
    h('div.page-head', h('div',
      h('div.page-title', '横向对比'),
      h('div.page-sub', '选 2–8 篇同类文献，按模板抽出同样的字段排成一张表；没有大模型时退化成「本地对比」。')
    )),
    setupBox,
    resultBox
  ))

  /* ---------------- 选择器（只建一次，保住输入状态） ---------------- */
  const picker = paperPicker({
    placeholder: '检索要对比的文献（标题 / 作者 / 期刊）…',
    actionLabel: '加入对比',
    limit: 8,
    isPicked: item => selected.includes(item.id),
    onPick: item => addPaper(item)
  })

  const chipsBox = h('div.cmp-chips')
  const countHint = h('span.cmp-count')
  const tmplDesc = h('div.hint')
  const noteBox = h('div.cmp-note', { hidden: true })
  const runBtn = h('button.btn.primary', { type: 'button', onclick: () => runCompare() }, '开始对比')
  const manualInput = h('input.input', {
    type: 'text',
    placeholder: '也可以直接粘贴文献 id（逗号分隔）…',
    onkeydown: ev => {
      if (ev.key !== 'Enter') return
      ev.preventDefault()
      const raw = String(ev.target.value || '')
      ev.target.value = ''
      addIds(raw)
    }
  })
  const tmplSelect = h('select.select', { title: '对比模板' })

  /* ---------------- 选择 / 模板状态 ---------------- */

  async function ensureTitle (id) {
    if (titleCache.has(id)) return titleCache.get(id)
    try {
      const res = await api.getPaper(id)
      const paper = (res && res.paper) || {}
      const entry = { title: paper.title || id, year: paper.year || '', venue: paper.venue || '' }
      titleCache.set(id, entry)
      return entry
    } catch {
      const entry = { title: id, year: '', venue: '' }
      titleCache.set(id, entry)
      return entry
    }
  }

  async function addPaper (item) {
    if (selected.includes(item.id)) return
    if (selected.length >= MAX_PAPERS) {
      toast('最多只能对比 ' + MAX_PAPERS + ' 篇', 'warn')
      return
    }
    titleCache.set(item.id, {
      title: item.title || item.id,
      year: item.year || '',
      venue: item.venue || ''
    })
    selected.push(item.id)
    await refreshSelection()
  }

  async function addIds (raw) {
    const ids = String(raw || '').split(/[\s,，;；]+/).map(s => s.trim()).filter(Boolean)
    if (!ids.length) return
    for (const id of ids) {
      if (selected.includes(id)) continue
      if (selected.length >= MAX_PAPERS) { toast('最多只能对比 ' + MAX_PAPERS + ' 篇', 'warn'); break }
      selected.push(id)
    }
    await refreshSelection()
  }

  function removePaper (id) {
    const idx = selected.indexOf(id)
    if (idx >= 0) selected.splice(idx, 1)
    refreshSelection()
  }

  function renderChips () {
    countHint.textContent = '已选 ' + selected.length + ' / ' + MAX_PAPERS + ' 篇'
    countHint.classList.toggle('ok', selected.length >= MIN_PAPERS)
    if (!selected.length) {
      chipsBox.replaceChildren(h('span.hint', '还没有选文献：下面检索并点一行加入（至少 2 篇，最多 8 篇）。'))
      return
    }
    chipsBox.replaceChildren(...selected.map(id => {
      const info = titleCache.get(id) || { title: id }
      return h('span.cmp-chip',
        h('span.cmp-chip-title', { text: truncate(info.title, 40), title: info.title }),
        h('button', { type: 'button', title: '从对比里移除', onclick: () => removePaper(id) }, '×')
      )
    }))
  }

  function syncRunBtn () {
    const ok = selected.length >= MIN_PAPERS && selected.length <= MAX_PAPERS
    runBtn.disabled = !ok
    runBtn.title = ok ? '开始对比' : ('请选 ' + MIN_PAPERS + '–' + MAX_PAPERS + ' 篇文献')
  }

  async function refreshSelection () {
    renderChips()
    syncRunBtn()
    renderTemplateDesc()
    // 已选文献的标题可能来自搜索行，也可能只来自 id —— 补取一次
    await Promise.all(selected.map(id => ensureTitle(id)))
    renderChips()
    renderPickerRefresh()
  }

  function renderPickerRefresh () {
    // 让选择器里已选的行显示 ✓（重跑一次同样的检索即可，输入框内容不丢）
    picker.load(picker.input ? picker.input.value : '')
  }

  function renderTemplateDesc () {
    const tmpl = templates.find(t => t.id === currentTemplate)
    if (templLoadError) {
      tmplDesc.textContent = templLoadError
      return
    }
    if (!tmpl) {
      tmplDesc.textContent = templates.length
        ? '不指定模板时使用服务端默认模板。'
        : '正在读取模板…'
      return
    }
    const fields = Array.isArray(tmpl.fields) ? tmpl.fields : []
    tmplDesc.textContent = (tmpl.desc || '') + (fields.length ? '（字段：' + fields.join('、') + '）' : '')
  }

  function renderTemplateSelect () {
    if (!templates.length) {
      replace(tmplSelect, h('option', { value: '' }, templLoadError ? '模板不可用（用默认）' : '读取中…'))
      return
    }
    replace(tmplSelect, ...templates.map(t => h('option', { value: t.id, selected: t.id === currentTemplate }, t.name || t.id)))
    if (!currentTemplate && templates.length) currentTemplate = templates[0].id
    tmplSelect.value = currentTemplate
  }

  tmplSelect.addEventListener('change', () => {
    currentTemplate = tmplSelect.value
    renderTemplateDesc()
  })

  /* ---------------- 对比设置面板 ---------------- */

  const setupPanel = h('section.panel', { style: { marginBottom: '16px' } },
    h('div.panel-head', '对比设置', h('div.right', countHint, runBtn)),
    h('div.panel-body', { style: { display: 'grid', gap: '12px' } },
      h('div.cmp-row',
        h('span.cmp-label', '模板'),
        tmplSelect,
        h('span', { style: { flex: '1 1 auto' } })
      ),
      tmplDesc,
      chipsBox,
      noteBox,
      h('details.cmp-add', { open: selected.length < MIN_PAPERS },
        h('summary', '添加文献（检索，或粘贴 id）'),
        h('div', { style: { display: 'grid', gap: '8px', paddingTop: '8px' } },
          picker.el,
          h('div.cmp-manual',
            h('span.cmp-label', '或输入 id'),
            manualInput
          )
        )
      )
    )
  )
  setupBox.replaceChildren(setupPanel)

  /* ---------------- 运行对比 ---------------- */

  function showLocalNote (show) {
    noteBox.hidden = !show
    if (show) noteBox.textContent = LOCAL_NOTE
  }

  async function runCompare () {
    if (selected.length < MIN_PAPERS) { toast('至少选 ' + MIN_PAPERS + ' 篇才能对比', 'warn'); return }
    if (selected.length > MAX_PAPERS) { toast('最多只能对比 ' + MAX_PAPERS + ' 篇', 'warn'); return }
    runBtn.setAttribute('disabled', '')
    const oldLabel = runBtn.textContent
    runBtn.textContent = '对比中…'
    showLocalNote(false)
    resultBox.replaceChildren(h('div.empty',
      h('div.big', '正在对比…'),
      h('p', { text: '先给每篇抽同样的字段，再合并成一张表；没有大模型时只走本地字段。' })))
    try {
      const res = await api.compareRun(selected.slice(), currentTemplate)
      if (isStale()) return
      showLocalNote(resultLooksLocal(res && res.result))
      renderResult((res && res.result) || null)
    } catch (err) {
      if (isStale()) return
      renderError(err)
    } finally {
      runBtn.removeAttribute('disabled')
      runBtn.textContent = oldLabel
      syncRunBtn()
    }
  }

  /* ---------------- 结果 ---------------- */

  function paperLink (id) {
    const info = titleCache.get(id) || { title: id }
    return h('a.cmp-col-title', {
      href: '#/paper/' + encodeURIComponent(id),
      title: info.title
    }, truncate(info.title, 34))
  }

  function renderResult (result) {
    if (!result) {
      resultBox.replaceChildren(h('div.empty', h('div.big', '没有返回对比结果')))
      return
    }
    const sections = Array.isArray(result.sections) ? result.sections : []
    const usage = result.usage || {}
    const tmpl = templates.find(t => t.id === result.template)
    const usageText = (Number(usage.prompt_tokens) || Number(usage.completion_tokens))
      ? '模型用量：' + (Number(usage.prompt_tokens) || 0) + ' + ' + (Number(usage.completion_tokens) || 0) + ' tokens'
      : '未使用模型（本地）'

    // 列顺序跟选择顺序一致；sections 里没出现的 paperId 一律显示 —
    const columns = selected.slice()
    const head = h('tr',
      h('th.cmp-th-field', '字段'),
      ...columns.map(id => h('th', paperLink(id)))
    )
    const bodyRows = sections.map(section => {
      const byId = new Map()
      for (const row of section.rows || []) {
        if (row && row.paperId != null) byId.set(String(row.paperId), row.value)
      }
      return h('tr',
        h('th.cmp-td-field', { text: section.title || '（未命名字段）' }),
        ...columns.map(id => h('td.cmp-td-value', { text: cellText(byId.get(String(id))) }))
      )
    })

    const bodyBits = [
      h('div.panel-head', '对比结果',
        h('div.right',
          h('span.hint', { text: (tmpl ? tmpl.name : (result.template || '默认模板')) + ' · ' + usageText })
        )
      )
    ]
    if (result.oneLine) {
      bodyBits.push(h('div.panel-body.tight', h('div.cmp-oneline', { text: result.oneLine })))
    }
    bodyBits.push(h('div.panel-body',
      sections.length
        ? h('div.cmp-scroll', h('table.cmp-table',
          h('thead', head),
          h('tbody', ...bodyRows)
        ))
        : h('div.hint', '这次对比没有返回任何字段（后端可能还没实现这个模板）。')
    ))

    resultBox.replaceChildren(h('section.panel', ...bodyBits))
  }

  function renderError (err) {
    const keyMissing = isKeyMissing(err)
    showLocalNote(keyMissing)
    resultBox.replaceChildren(h('section.panel',
      h('div.panel-head', '对比结果'),
      h('div.panel-body', { style: { display: 'grid', gap: '10px' } },
        h('div.cmp-error', { text: err && err.message ? err.message : '对比失败' }),
        h('div.hint', {
          text: keyMissing
            ? '这多半是没配置大模型（或模型调用失败）。配置后重试即可；其他错误请检查选中的文献是否还在库里。'
            : '可以：减少文献数量（2–8 篇）、确认这些文献没有被删除、换个模板再试。'
        }),
        h('div', { style: { display: 'flex', gap: '8px' } },
          h('button.btn', { type: 'button', onclick: () => runCompare() }, '重试'),
          h('button.btn.ghost', { type: 'button', onclick: () => go('settings') }, '去配置模型')
        )
      )
    ))
  }

  /* ---------------- 启动 ---------------- */

  async function loadTemplates () {
    renderTemplateSelect()
    renderTemplateDesc()
    try {
      const res = await api.compareTemplates()
      if (isStale()) return
      templates = Array.isArray(res.templates) ? res.templates : []
      if (!currentTemplate && templates.length) currentTemplate = templates[0].id
      templLoadError = ''
    } catch (err) {
      if (isStale()) return
      templates = []
      templLoadError = '模板接口暂时不可用（' + err.message + '）：仍可点「开始对比」，由服务端用默认模板。'
    }
    renderTemplateSelect()
    renderTemplateDesc()
  }

  renderChips()
  syncRunBtn()
  loadTemplates()
  picker.load('')
  if (selected.length) refreshSelection()
}

export { compareHandler }
export default compareHandler
