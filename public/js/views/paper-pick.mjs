/* 文献选择小部件（供 #/compare、#/deep 复用）
   —— 不是视图（没有路由处理器），只导出 paperPicker()。
   契约：GET /api/papers（q 检索 + pageSize）；依赖 util.mjs 与 api.mjs，不 import app.mjs（保持 0 环 import）。 */
import * as util from '../util.mjs'
import { api } from '../api.mjs'

const { h, debounce, authorsText } = util

/**
 * 渲染一个「检索 + 点一行就选中」的文献选择器。
 * options:
 *   placeholder  输入框占位
 *   hint         列表为空时的提示
 *   limit        一次显示多少条（默认 8，即 GET /api/papers 的 pageSize）
 *   actionLabel  每一行右侧的按钮文字（默认「选择」）
 *   isPicked(item) -> bool   已选中的行显示 ✓ 且不可再点
 *   onPick(item) 点击一行的回调
 */
function paperPicker (options) {
  const opts = options || {}
  const listEl = h('div.paper-pick-list')
  const hintEl = h('div.hint.paper-pick-hint', { text: opts.hint || '输入关键词检索文献，或直接看最近更新的几条。' })
  let items = []
  let seq = 0

  const input = h('input.input', {
    type: 'search',
    placeholder: opts.placeholder || '检索标题、作者、期刊…',
    autocomplete: 'off'
  })
  const doSearch = debounce(q => { load(q) }, 240)
  input.addEventListener('input', () => doSearch(input.value))
  input.addEventListener('keydown', ev => {
    if (ev.key === 'Enter') {
      ev.preventDefault()
      doSearch.cancel()
      load(input.value)
    }
  })

  function renderList () {
    if (!items.length) {
      listEl.replaceChildren()
      return
    }
    listEl.replaceChildren(...items.map(item => {
      const picked = typeof opts.isPicked === 'function' && opts.isPicked(item)
      const meta = [authorsText(item.authors, 2), item.year, item.venue].filter(Boolean).join(' · ')
      const row = h('div.paper-pick-row' + (picked ? '.picked' : ''), {
        role: 'button',
        tabindex: '0',
        title: picked ? '已经在列表里了' : '点这一行加入',
        onclick: () => { if (!picked && opts.onPick) opts.onPick(item) },
        onkeydown: ev => {
          if ((ev.key === 'Enter' || ev.key === ' ') && !picked && opts.onPick) {
            ev.preventDefault()
            opts.onPick(item)
          }
        }
      },
        h('div', { style: { minWidth: 0 } },
          h('div.paper-pick-title', { text: item.title || '（未命名文献）' }),
          h('div.paper-pick-meta', { text: meta || '—' })
        ),
        h('span.paper-pick-act', picked ? '✓ 已选' : (opts.actionLabel || '选择'))
      )
      return row
    }))
  }

  /** 拉一页文献（默认按最近更新，空查询也能给出内容） */
  async function load (q) {
    const token = ++seq
    hintEl.textContent = '正在检索…'
    try {
      const res = await api.listPapers({
        q: String(q == null ? input.value : q).trim(),
        pageSize: opts.limit || 8,
        sort: 'updatedAt'
      })
      if (token !== seq) return
      items = res.items || []
      renderList()
      const needle = String(q == null ? input.value : q).trim()
      hintEl.textContent = items.length
        ? (needle ? '匹配 ' + res.total + ' 条，显示前 ' + items.length + ' 条' : '最近更新的 ' + items.length + ' 条')
        : (needle ? '没有匹配的文献' : '文献库还是空的')
    } catch (err) {
      if (token !== seq) return
      items = []
      renderList()
      hintEl.textContent = '检索失败：' + err.message
    }
  }

  const el = h('div.paper-pick',
    h('div.search-wrap.paper-pick-search', h('span.ico', '⌕'), input),
    hintEl,
    listEl
  )

  return {
    el,
    input,
    load: (q) => load(q),
    focus: () => { try { input.focus() } catch {} }
  }
}

export { paperPicker }
export default { paperPicker }
