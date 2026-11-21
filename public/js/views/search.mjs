/* 独立搜索页（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from '../util.mjs'
import * as components from '../components.mjs'
import * as searchSyntax from '../search.mjs'
import { api } from '../api.mjs'
import { getState, patch } from '../state/store.mjs'
import { go, refreshMeta, rememberTitles, savePrefs, scheduleRender, setView } from '../shell/app-api.mjs'
import * as foldersUi from '../folders.mjs'

  const { h, toast, rich, typeLabel, authorsText, toPlainText } = util
  const { paperCard, paperRow } = components
  
  const TYPE_OPTIONS = [['all', '全部类型'], ['journal', '期刊'], ['conference', '会议'], ['preprint', '预印本'],
    ['book', '图书'], ['thesis', '学位论文'], ['report', '报告'], ['web', '网页'], ['other', '其他']]

  /* hero 字标的逐字母配色，与素材 components/hero-google.html 的学术搜索页版本逐字一致 */
  const HERO_PAPER_LIBRARY = [
    ['P', 'blue'], ['a', 'red'], ['p', 'yellow'], ['e', 'blue'], ['r', 'green'], ['L', 'red'],
    ['i', 'blue'], ['b', 'yellow'], ['r', 'green'], ['a', 'blue'], ['r', 'red'], ['y', 'yellow']
  ]

  function trim (v) { return String(v == null ? '' : v).trim() }

  /**
   * 上一次搜索的会话快照：条件 + 结果 + 网络结果 + 滚动位置。
   * 放在视图外面（模块级），这样「切到别处看一篇文献再回来」还能直接复现，
   * 既不用重敲条件，也不用白跑一次本地/网络请求。
   */
  let lastSession = null
  let scrollSaveTimer = null
  /** 已经为哪个会话做过后台校验（视图可能因为多重渲染被创建两次，别重复发请求） */
  let revalidatedKey = ''
  /** 当前页面正在用的会话指纹（滚动监听是模块级只装一次，靠它判断该往哪份快照里写） */
  let currentSessionKey = ''
  /** 结果区所在的滚动容器（可能是 window，也可能是某个 overflow:auto 的祖先） */
  let currentScroller = null

  /**
   * 找真正的滚动容器：从结果区往上找第一个「能滚的」祖先。
   * 不能假定是 window——这个布局里侧栏自己 overflow:auto，有些页面内容是内层滚动，
   * 那样 window.scrollY 恒为 0，记/还原滚动位置就完全无效（踩过）。
   */
  function findScroller (startEl) {
    let node = startEl && startEl.parentElement
    while (node && node !== document.body) {
      const style = window.getComputedStyle(node)
      if (/(auto|scroll)/.test(style.overflowY) && node.scrollHeight > node.clientHeight + 20) return node
      node = node.parentElement
    }
    return document.scrollingElement || document.documentElement
  }

  function readScroll () {
    if (currentScroller) return currentScroller.scrollTop || 0
    return window.scrollY || (document.scrollingElement ? document.scrollingElement.scrollTop : 0) || 0
  }

  // 用捕获阶段监听：元素的 scroll 事件不冒泡，但捕获阶段能收到
  window.addEventListener('scroll', () => {
    if (scrollSaveTimer) return
    scrollSaveTimer = setTimeout(() => {
      scrollSaveTimer = null
      // 只有「搜索页还在屏幕上」时才记：
      // 离开页面时滚动容器会被重置为 0，那一刻也会触发 scroll 事件，
      // 不设防就会把刚记好的位置覆盖成 0（还原永远是顶部，踩过一次）。
      if (!document.getElementById('search-page-input')) return
      if (lastSession && lastSession.key === currentSessionKey) lastSession.scrollY = readScroll()
    }, 200)
  }, { passive: true, capture: true })

  /** 把 URL 参数读进一个普通对象 */
  function readParams (params) {
    return {
      q: trim(params.get('q')),
      author: trim(params.get('author')),
      venue: trim(params.get('venue')),
      yearFrom: trim(params.get('yearFrom')),
      yearTo: trim(params.get('yearTo')),
      type: params.get('type') || 'all',
      starred: params.get('starred') === 'true',
      keyword: trim(params.get('keyword')),
      tags: (params.get('tags') || '').split(',').map(s => trim(s)).filter(Boolean),
      hasPdf: params.get('hasPdf') === 'true',
      scope: params.get('scope') || 'metadata',
      sort: params.get('sort') || '',
      page: Number(params.get('page')) || 1,
      // 看的是本地库还是网络检索。放进 URL 是为了「切走再回来」还能停在同一页
      tab: params.get('tab') === 'web' ? 'web' : 'local',
      // 右侧 facet 的多选值（同类 OR、跨类 AND）
      facets: {
        years: splitList(params.get('fy')),
        authors: splitList(params.get('fa')),
        venues: splitList(params.get('fv')),
        types: splitList(params.get('ftype')),
        tags: splitList(params.get('ftag'))
      },
      // 网络检索页的 facet（前端在已取回的结果里聚合）
      webFacets: {
        years: splitList(params.get('wy')),
        venues: splitList(params.get('wv')),
        authors: splitList(params.get('wa')),
        onlyOA: params.get('woa') === 'true'
      },
      // 网络结果的排序：只重排已取回的那批，不进 URL（切走再回来重新检索时按相关度重来）
      webSort: 'relevance'
    }
  }

  function splitList (v) {
    return String(v == null ? '' : v).split(',').map(s => s.trim()).filter(Boolean)
  }

  /** 把查询对象写回 URL 参数 */
  function toParams (state) {
    const f = state.facets || {}
    const w = state.webFacets || {}
    return {
      q: state.q || '',
      author: state.author || '',
      venue: state.venue || '',
      yearFrom: state.yearFrom || '',
      yearTo: state.yearTo || '',
      type: state.type !== 'all' ? state.type : '',
      starred: state.starred ? 'true' : '',
      keyword: state.keyword || '',
      tags: state.tags.length ? state.tags.join(',') : '',
      hasPdf: state.hasPdf ? 'true' : '',
      scope: state.scope && state.scope !== 'metadata' ? state.scope : '',
      sort: state.sort || '',
      page: state.page > 1 ? state.page : '',
      tab: state.tab === 'web' ? 'web' : '',
      fy: (f.years || []).join(','),
      fa: (f.authors || []).join(','),
      fv: (f.venues || []).join(','),
      ftype: (f.types || []).join(','),
      ftag: (f.tags || []).join(','),
      wy: (w.years || []).join(','),
      wv: (w.venues || []).join(','),
      wa: (w.authors || []).join(','),
      woa: w.onlyOA ? 'true' : ''
    }
  }

  /** 有没有任何有效的搜索条件（没有就不发请求、显示引导页） */
  function hasAnyCondition (state) {
    const f = state.facets || {}
    const facetOn = ['years', 'authors', 'venues', 'types', 'tags'].some(k => (f[k] || []).length)
    return !!(state.q || state.author || state.venue || state.keyword ||
      state.yearFrom || state.yearTo || (state.tags || []).length ||
      state.hasPdf || facetOn ||
      (state.type && state.type !== 'all'))
  }

  async function searchHandler (ctx) {
    const { outlet, params, isStale } = ctx
    const state = readParams(params)
    // 会话指纹：条件完全一致才复用缓存
    const sessionKey = JSON.stringify(toParams(state))
    const restore = { ok: !!(lastSession && lastSession.key === sessionKey && lastSession.result) }
    currentSessionKey = sessionKey
    // 记下真正的滚动容器（结果区可能不是靠 window 滚的）
    setTimeout(() => { try { currentScroller = findScroller(results) } catch (err) {} }, 0)
    // 侧栏「搜索」按这个回到上次条件（见 app.js 的 navItem）
    patch({ lastSearchParams: toParams(state) })

    /** 结果可能过期的提示（点了就用新数据重查一次） */
    const staleHint = h('button.btn.sm.ghost', {
      type: 'button', hidden: true,
      style: { marginLeft: '8px' },
      title: '后台核对时发现库里的结果变了',
      onclick: () => { lastSession = null; runSearch(true) }
    }, '结果可能已过期，点此刷新')


    /* ---------- 右侧 facet 筛选栏 ---------- */
    // 计数由服务端在「当前结果集」上算（见 library.facets）：同类 OR、跨类 AND，
    // 且每类计数都先剥掉本类条件，否则勾了一项后同类别里其它选项就全变 0、再也加不了。

    const FACET_GROUPS = [
      { key: 'years', label: '年份' },
      { key: 'authors', label: '作者' },
      { key: 'venues', label: '期刊 / 会议' },
      { key: 'types', label: '文献类型' },
      { key: 'tags', label: '标签' }
    ]
    const TYPE_TEXT = { journal: '期刊论文', conference: '会议论文', book: '图书', thesis: '学位论文', preprint: '预印本', report: '报告', other: '其它' }
    let lastFacets = null
    let facetExpanded = {}
    let facetOpen = true
    const facetBody = h('div', { id: 'facet-body', style: { display: 'grid', gap: '12px' } })
    const facetPanel = h('aside#facet-panel', { style: { width: '248px', flex: '0 0 248px' } },
      h('section.panel',
        h('div.panel-head', '筛选',
          h('div.right', h('button.btn.sm.ghost', {
            type: 'button',
            title: '收起 / 展开筛选栏',
            onclick: () => { facetOpen = !facetOpen; renderFacets() }
          }, '收起 / 展开'))
        ),
        h('div.panel-body', facetBody)
      )
    )

    function toggleFacet (key, value) {
      const list = state.facets[key] || []
      const has = list.some(v => String(v) === String(value))
      state.facets[key] = has ? list.filter(v => String(v) !== String(value)) : [...list, String(value)]
      state.page = 1
      apply()
    }

    function renderFacetGroup (group, data) {
      const selected = state.facets[group.key] || []
      const items = (data && data.items) || []
      const distinct = (data && data.distinct) || items.length
      const expanded = !!facetExpanded[group.key]
      const shown = expanded ? items : items.slice(0, 5)
      const rows = shown.map(it => {
        const on = selected.some(v => String(v) === String(it.value))
        const label = group.key === 'types' ? (TYPE_TEXT[it.value] || it.value) : String(it.value)
        return h('label', {
          style: {
            display: 'flex', gap: '7px', alignItems: 'baseline', cursor: 'pointer',
            padding: '2px 0', fontSize: '12.8px', color: on ? 'var(--accent-text)' : 'var(--text)'
          }
        },
          h('input.check', { type: 'checkbox', checked: on, onchange: () => toggleFacet(group.key, it.value) }),
          h('span', { style: { flex: '1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: label }, label),
          h('span', { style: { color: 'var(--muted)', fontVariantNumeric: 'tabular-nums' } }, String(it.count))
        )
      })
      return h('div', null,
        h('div', { style: { fontWeight: '600', fontSize: '12.8px', marginBottom: '4px', color: 'var(--muted)' } }, group.label),
        items.length ? h('div', { style: { display: 'grid' } }, ...rows) : h('div.hint', { style: { fontSize: '12px' } }, '（无）'),
        distinct > shown.length || expanded
          ? h('button.btn.sm.ghost', {
            type: 'button',
            style: { marginTop: '3px', padding: '1px 6px', fontSize: '12px' },
            onclick: () => { facetExpanded[group.key] = !expanded; renderFacets() }
          }, expanded ? '收起' : '查看更多（' + distinct + '）')
          : null
      )
    }

    function renderFacets () {
      if (!facetOpen) { facetBody.hidden = true; return }
      facetBody.hidden = false
      const anySelected = FACET_GROUPS.some(g => (state.facets[g.key] || []).length)
      if (!lastFacets) {
        facetBody.replaceChildren(h('div.hint', { style: { fontSize: '12.5px' } },
          '搜索之后，这里会按年份、作者、期刊、类型、标签列出当前结果集里的分布，可以多选细化。'))
        return
      }
      /* 末尾「清除全部筛选」在没有选中项时是 null：
         Element.replaceChildren(null) 会把 null 转成文本 "null" 插进面板，
         于是筛选栏最下面一直挂着一行 "null"。先过滤掉再塞。 */
      facetBody.replaceChildren(...[
        h('div', { style: { fontSize: '12.4px', color: 'var(--muted)' } },
          '当前结果集 ' + lastFacets.total + ' 条' + (anySelected ? '（已筛）' : '')),
        ...FACET_GROUPS.map(g => renderFacetGroup(g, lastFacets[g.key])),
        anySelected
          ? h('button.btn.sm.ghost', {
            type: 'button',
            onclick: () => {
              state.facets = { years: [], authors: [], venues: [], types: [], tags: [] }
              state.page = 1
              apply()
            }
          }, '清除全部筛选')
          : null
      ].filter(Boolean))
    }

    async function refreshFacets (query, seq) {
      try {
        const res = await api.queryFacets(Object.assign({}, query, { page: 1, pageSize: 1 }))
        // 已经不是最新一次检索了：这份计数作废，别覆盖后发的结果
        if (seq != null && seq !== searchSeq) return
        lastFacets = res && res.query ? res.query : null
      } catch (err) {
        if (seq != null && seq !== searchSeq) return
        lastFacets = null
      }
      if (lastSession && lastSession.key === sessionKey) {
        lastSession.facets = lastFacets
        lastSession.result = lastResult
      }
      renderFacets()
    }

    /* ---------- 会话保留：切走再回来不重查 ---------- */

    /** 结果可能过期时的提示（后台静默校验发现问题才出现，不自动把用户的结果换掉） */
    function showStaleHint () {
      if (staleHint.parentNode) { staleHint.hidden = false; return }
      resultCount.appendChild(staleHint)
      staleHint.hidden = false
    }

    async function revalidate () {
      if (!lastResult) return
      // 同一个会话只校验一次：搜索页有时会被渲染两次（hashchange 与主动重绘撞车），
      // 不设防就会发两次一模一样的后台请求。
      if (revalidatedKey === sessionKey) return
      revalidatedKey = sessionKey
      const query = buildQueryParams()
      let res = null
      try { res = await api.listPapers(query) } catch (err) { return }
      if (!res) return
      const changed = res.total !== lastResult.total ||
        (res.items && lastResult.items && res.items.length && res.items[0] &&
          res.items[0].id !== lastResult.items[0].id)
      if (changed) showStaleHint()
    }

    function restoreScroll () {
      const y = lastSession && lastSession.scrollY
      if (!y) return
      // 放到下一帧再滚：此刻 DOM 刚填好，浏览器可能还没算完布局，直接滚会被截到当前最大高度
      setTimeout(() => {
        try {
          const target = currentScroller || findScroller(results)
          if (target && target !== document.scrollingElement && target !== document.documentElement) {
            target.scrollTop = y
          } else {
            window.scrollTo(0, y)
          }
        } catch (err) {}
      }, 0)
    }

    /* ---------- 页面骨架 ---------- */
    const resultCount = h('div', { style: { color: 'var(--muted)', fontSize: '13.5px' } }, '')
    const results = h('div')
    const advancedPanel = h('section.panel', { hidden: true })
    const pager = h('div')
    const advancedToggle = h('button.btn.sm', { type: 'button' }, '高级搜索 ▾')
    const tagRow = h('div', { style: { display: 'flex', gap: '5px', flexWrap: 'wrap', alignItems: 'center' } })
    let lastResult = null

    /* ---------- 搜索框 ---------- */
    const input = h('input.input', {
      type: 'search',
      id: 'search-page-input',
      placeholder: '搜索标题、作者、摘要、正文…（输入即搜，支持前缀 / 子串；AND / OR / NOT 与括号；#标签 可筛标签）',
      value: state.q,
      autocomplete: 'off',
      // 输入即搜：防抖后只在结果区就地刷新，不重绘整页（重绘会把输入框连焦点一起换掉）
      oninput: ev => scheduleLive(ev.target.value),
      onkeydown: ev => {
        if (ev.key === 'Enter') { ev.preventDefault(); flushLive(ev.target.value) }
        if (ev.key === 'Escape') { ev.preventDefault(); ev.target.value = ''; flushLive('') }
      }
    })

    // 排序：默认按相关度（文本相似度）
    const sortSelect = h('select.select', {
      title: '结果排序方式',
      onchange: ev => { state.sort = ev.target.value; state.page = 1; apply() }
    },
      h('option', { value: '', selected: !state.sort }, '相关度优先（按文本相似度）'),
      h('option', { value: 'updatedAt', selected: state.sort === 'updatedAt' }, '最近更新'),
      h('option', { value: 'addedAt', selected: state.sort === 'addedAt' }, '最近添加'),
      h('option', { value: 'yearDesc', selected: state.sort === 'yearDesc' }, '年份 ↓'),
      h('option', { value: 'yearAsc', selected: state.sort === 'yearAsc' }, '年份 ↑'),
      h('option', { value: 'title', selected: state.sort === 'title' }, '标题 A→Z'),
      h('option', { value: 'rating', selected: state.sort === 'rating' }, '评分 ↓')
    )

    // 搜索页自己的「卡片 / 紧凑」切换（顶栏那两个只在书库首页出现）
    const searchViewToggle = h('div.btn-group', { title: '切换卡片 / 紧凑视图' },
      h('button.btn.sm' + (getState().prefs.view === 'cards' ? '.primary' : ''), {
        type: 'button', 'aria-label': '卡片视图', 'data-view': 'cards',
        onclick: () => { setView('cards') }
      }, '▦'),
      h('button.btn.sm' + (getState().prefs.view === 'rows' ? '.primary' : ''), {
        type: 'button', 'aria-label': '紧凑列表', 'data-view': 'rows',
        onclick: () => { setView('rows') }
      }, '☰')
    )

    function apply (options) {
      const opts = options || {}
      const next = toParams(state)
      if (opts.replace) {
        // 只改 URL 不新增历史记录，避免每敲一个字都塞一条历史
        const qs = new URLSearchParams()
        for (const [k, v] of Object.entries(next)) if (v !== '' && v != null) qs.set(k, String(v))
        const hash = '#/search' + (qs.toString() ? '?' + qs.toString() : '')
        if (location.hash !== hash) {
          try { history.replaceState(null, '', hash) } catch { location.hash = hash }
        }
        scheduleRender()
        return
      }
      go('search', next)
    }

    /* ---------- 输入即搜（动态即时刷新） ----------
       不经过 apply()/go()：那会改 hash → 触发整页重绘 → 输入框被换成新节点，打字直接断。
       这里只改 state、静默同步地址栏、就地重画结果区，输入焦点全程保留。 */
    const LIVE_DELAY = 160
    let liveTimer = null

    /** 地址栏跟着条件走，但用 replaceState（不产生历史、也不触发 hashchange） */
    function syncUrlSilent () {
      const next = toParams(state)
      const qs = new URLSearchParams()
      for (const [k, v] of Object.entries(next)) if (v !== '' && v != null) qs.set(k, String(v))
      const hash = '#/search' + (qs.toString() ? '?' + qs.toString() : '')
      if (location.hash === hash) return
      try { history.replaceState(null, '', hash) } catch (err) { /* 隐私模式等：忽略 */ }
    }

    function scheduleLive (value) {
      if (liveTimer) clearTimeout(liveTimer)
      liveTimer = setTimeout(() => { liveTimer = null; runLive(value) }, LIVE_DELAY)
    }

    function flushLive (value) {
      if (liveTimer) { clearTimeout(liveTimer); liveTimer = null }
      runLive(value)
    }

    function runLive (value) {
      const q = trim(value)
      if (q === state.q) return
      state.q = q
      state.page = 1
      syncUrlSilent()
      patch({ lastSearchParams: toParams(state) })
      renderChips()
      // keepResults：输入过程中不要先把列表清成「正在检索…」，那样每敲一个字都会闪一下
      runSearch(false, { keepResults: true })
    }

    /* ---------- 高级搜索面板 ---------- */
    const fAuthor = h('input.input', {
      type: 'text', placeholder: '如 Vaswani 或 王小明', value: state.author,
      onkeydown: ev => { if (ev.key === 'Enter') { state.author = trim(ev.target.value); state.page = 1; apply() } }
    })
    const fVenue = h('input.input', {
      type: 'text', placeholder: '如 NeurIPS / Nature / 计算机学报', value: state.venue,
      onkeydown: ev => { if (ev.key === 'Enter') { state.venue = trim(ev.target.value); state.page = 1; apply() } }
    })
    const fKeyword = h('input.input', {
      type: 'text', placeholder: '文献原文里的关键词', value: state.keyword,
      onkeydown: ev => { if (ev.key === 'Enter') { state.keyword = trim(ev.target.value); state.page = 1; apply() } }
    })
    const fYearFrom = h('input.input', { type: 'text', inputmode: 'numeric', placeholder: '起，如 2018', value: state.yearFrom })
    const fYearTo = h('input.input', { type: 'text', inputmode: 'numeric', placeholder: '止，如 2024', value: state.yearTo })
    const fType = h('select.select', { style: { width: '100%' } },
      ...TYPE_OPTIONS.map(([v, label]) => h('option', { value: v, selected: state.type === v }, label)))
    const fStarred = h('input.check', { type: 'checkbox', checked: state.starred })
    const fHasPdf = h('input.check', { type: 'checkbox', checked: state.hasPdf })
    // 检索范围：元数据 / 全文 / 两者
    const fScope = h('select.select', { style: { width: '100%' } },
      h('option', { value: 'metadata', selected: state.scope === 'metadata' }, '题录与摘要（标题、作者、摘要）'),
      h('option', { value: 'fulltext', selected: state.scope === 'fulltext' }, 'PDF 全文正文'),
      h('option', { value: 'all', selected: state.scope === 'all' }, '两者都搜（推荐）')
    )
    const tagSuggestBox = h('div', { style: { display: 'flex', gap: '5px', flexWrap: 'wrap', marginTop: '4px' } })

    function renderTagSuggest () {
      const known = (getState().tags || []).slice(0, 24)
      if (!known.length) {
        tagSuggestBox.replaceChildren(h('span.hint', '还没有标签'))
        return
      }
      tagSuggestBox.replaceChildren(...known.map(t =>
        h('button.tag-chip' + (state.tags.includes(t.name) ? '.active' : ''), {
          type: 'button',
          onclick: () => {
            state.tags = state.tags.includes(t.name)
              ? state.tags.filter(x => x !== t.name)
              : [...state.tags, t.name]
            state.page = 1
            apply()
          }
        }, t.name, h('span.n', String(t.count)))
      ))
    }

    function collectAdvanced () {
      state.author = trim(fAuthor.value)
      state.venue = trim(fVenue.value)
      state.keyword = trim(fKeyword.value)
      state.yearFrom = trim(fYearFrom.value)
      state.yearTo = trim(fYearTo.value)
      state.type = fType.value
      state.hasPdf = !!fHasPdf.checked
      state.scope = fScope.value
      state.page = 1
      apply()
    }

    advancedPanel.replaceChildren(
      h('div.panel-head', '高级搜索',
        h('div.right',
          h('button.btn.sm.primary', { type: 'button', onclick: collectAdvanced }, '应用条件'),
          h('button.btn.sm.ghost', {
            type: 'button',
            onclick: () => {
              fAuthor.value = ''
              fVenue.value = ''
              fKeyword.value = ''
              fYearFrom.value = ''
              fYearTo.value = ''
              fType.value = 'all'
              fStarred.checked = false
              fHasPdf.checked = false
              collectAdvanced()
            }
          }, '清空')
        )
      ),
      h('div.panel-body', h('div', { style: { display: 'grid', gap: '13px' } },
        h('div.form-grid',
          h('div.field', h('label', '作者'), fAuthor),
          h('div.field', h('label', '期刊 / 会议'), fVenue)
        ),
        h('div.form-grid',
          h('div.field', h('label', '年份范围'), h('div', { style: { display: 'flex', gap: '6px', alignItems: 'center' } }, fYearFrom, h('span', '—'), fYearTo)),
          h('div.field', h('label', '类型'), fType)
        ),
        h('div.field', h('label', '原文关键词（作者给的关键词）'), fKeyword),
        h('div.field', h('label', '检索范围'), fScope,
          h('div.hint', '「PDF 全文正文」需要在文献详情页先点过「提取全文」，或在上传 PDF 后自动提取。')),
        h('div.field', h('label', '我的标签'), tagSuggestBox),
        h('div', { style: { display: 'flex', gap: '16px', flexWrap: 'wrap' } },
          h('label', { style: { display: 'flex', gap: '6px', alignItems: 'center', fontSize: '13.5px' } }, fStarred, '只看收藏的'),
          h('label', { style: { display: 'flex', gap: '6px', alignItems: 'center', fontSize: '13.5px' } }, fHasPdf, '只看有 PDF 的')
        )
      ))
    )
    renderTagSuggest()

    advancedToggle.addEventListener('click', () => {
      advancedPanel.hidden = !advancedPanel.hidden
      advancedToggle.textContent = advancedPanel.hidden ? '高级搜索 ▾' : '收起高级搜索 ▴'
    })

    /* ---------- 结果渲染 ---------- */
    function renderResults () {
      const showCount = hasAnyCondition(state)
      resultCount.replaceChildren(...(showCount && lastResult
        ? [h('span.count-num', `${lastResult.total} 条结果`),
          lastResult.pageCount > 1 ? h('span.count-page', `第 ${lastResult.page} / ${lastResult.pageCount} 页`) : null,
          lastResult.boolean
            ? h('span', {
                title: '查询里含 AND / OR / NOT（也可用 && || ! 、与/或/非），已按布尔逻辑筛选，「非」的词只在本地复核',
                style: { color: 'var(--accent, #4a9eff)' }
              }, '· 布尔逻辑已生效')
            : null].filter(Boolean)
        : []))
      // 结果区左侧的返回入口
      tagRow.replaceChildren(
        h('button.btn.sm.ghost', { type: 'button', onclick: () => go('#/') }, '← 返回全部文献')
      )

      if (!showCount) {
        results.replaceChildren(h('div.empty',
          h('div.big', '开始搜索'),
          h('p', { text: '在上面输入关键词，或展开「高级搜索」按年份、作者、期刊、标签筛选。' }),
          h('p', { text: '想要更精确：用 AND / OR / NOT 组合关键词，例如 「cucurbituril AND capsule」「(cage OR capsule) NOT porphyrin」；中文可写「与 / 或 / 非」，引号里的短语会整体匹配。' }),
          h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'center' } },
            ...(getState().tags || []).slice(0, 8).map(t => h('button.tag-chip', {
              type: 'button',
              onclick: () => { state.tags = [t.name]; state.page = 1; apply() }
            }, t.name, h('span.n', String(t.count))))
          )
        ))
        pager.replaceChildren()
        return
      }

      if (loading) {
        results.replaceChildren(h('div.empty', h('div.big', '正在检索…')))
        return
      }
      if (!lastResult || !lastResult.items.length) {
        results.replaceChildren(h('div.empty',
          h('div.big', '本地文献库里没有匹配结果'),
          h('p', { text: '可以试试减少条件，或换成更通用的关键词。' }),
          h('button.btn', { type: 'button', onclick: () => go('#/') }, '返回全部文献')
        ))
        return
      }
      const common = {
        onTag: tag => { state.tags = [tag]; state.page = 1; apply() },
        // 搜索结果里也能直接改分类（列表页有入口，这里没有的话用户会以为功能消失了）
        onFolder: paper => changeFolder(paper)
      }
      if (getState().prefs.view === 'rows') {
        // 以前这里连 common 都没传：紧凑视图里点标签不会筛选，也没有改分类的入口
        results.replaceChildren(h('div.rows', lastResult.items.map(item => paperRow(item, common))))
      } else {
        results.replaceChildren(h('div.cards', lastResult.items.map(item => paperCard(item, common))))
      }
    }

    /** 改分类：搜索结果里直接移动，成功后就地重新检索（目录变了，结果集可能也要跟着变） */
    function changeFolder (paper) {
      foldersUi.openFolderPicker({
        current: paper.folder || '',
        onPick: async path => {
          if ((paper.folder || '') === path) {
            toast('分类没有变化')
            return
          }
          try {
            await api.movePapers([paper.id], path)
            foldersUi.notifyChanged()
            toast('已移到「' + (path ? path.split('/').pop() : foldersUi.ROOT_LABEL) + '」')
            apply()
          } catch (err) {
            toast('移动失败：' + (err && err.message ? err.message : err))
          }
        }
      })
    }

    function renderPager () {
      if (!lastResult || lastResult.pageCount <= 1) {
        pager.replaceChildren()
        return
      }
      const go = page => {
        state.page = Math.min(Math.max(1, page), lastResult.pageCount)
        apply()
      }
      pager.replaceChildren(h('div.pager',
        h('button.btn.sm', { type: 'button', disabled: lastResult.page <= 1, onclick: () => go(lastResult.page - 1) }, '← 上一页'),
        h('span', `第 ${lastResult.page} / ${lastResult.pageCount} 页 · 共 ${lastResult.total} 条`),
        h('button.btn.sm', { type: 'button', disabled: lastResult.page >= lastResult.pageCount, onclick: () => go(lastResult.page + 1) }, '下一页 →')
      ))
    }

    /* ---------- 查询 ---------- */
    let loading = false
    /** 组装 /api/papers（与 /api/facets）要用的查询参数 */
    function buildQueryParams () {
      const query = {
        q: state.q,
        author: state.author,
        venue: state.venue,
        keyword: state.keyword,
        yearFrom: state.yearFrom,
        yearTo: state.yearTo,
        type: state.type,
        starred: state.starred ? 'true' : '',
        tags: state.tags,
        hasPdf: state.hasPdf,
        scope: state.scope || 'metadata',
        sort: state.sort,
        page: state.page,
        pageSize: getState().prefs.pageSize,
        // 右侧 facet 的多选条件（同类 OR）
        years: state.facets.years,
        authors: state.facets.authors,
        venues: state.facets.venues
      }
      if (state.facets.types.length) query.type = state.facets.types.join(',')
      if (state.facets.tags.length) query.tags = [...new Set([...state.tags, ...state.facets.tags])]
      return query
    }

    /** 每次检索递增；响应回来时若已经不是最新一次，直接丢弃（打字快时防旧结果盖新结果） */
    let searchSeq = 0

    async function runSearch (force, options) {
      const opts = options || {}
      const seq = ++searchSeq
      if (!hasAnyCondition(state)) {
        lastResult = null
        renderResults()
        return
      }
      // 上次搜过同样条件？直接用缓存渲染，**不再打一次请求**
      // （用户抱怨过：切去看一篇文献再回来，搜索被清空、还得重查一遍）
      if (!force && restore.ok && lastSession) {
        lastResult = lastSession.result
        lastFacets = lastSession.facets || null
        loading = false
        renderResults()
        renderPager()
        renderFacets()
        revalidate()
        restoreScroll()
        return
      }
      loading = true
      staleHint.hidden = true
      // 输入即搜（keepResults）时不先把列表刷成「正在检索…」，否则每敲一个字都闪一下；
      // 保留上一批结果，等新结果到了直接替换
      if (!opts.keepResults) renderResults()
      const query = buildQueryParams()
      try {
        const res = await api.listPapers(query)
        if (isStale() || seq !== searchSeq) return
        lastResult = res
        rememberTitles(res.items || [])
      } catch (err) {
        if (seq !== searchSeq) return
        lastResult = { total: 0, items: [], page: 1, pageCount: 1 }
        results.replaceChildren(h('div.empty',
          h('div.big', '搜索失败'),
          h('p', { text: err.message }),
          h('button.btn', { type: 'button', onclick: runSearch }, '重试')))
        loading = false
        return
      }
      loading = false
      renderResults()
      renderPager()
      // 记下这次会话：条件 + 结果（切走再回来直接复用，不再重查）
      lastSession = {
        key: sessionKey,
        result: lastResult,
        facets: null,
        at: Date.now(),
        scrollY: 0,
        external: (lastSession && lastSession.key === sessionKey && lastSession.external) || null
      }
      // facet 计数必须和主列表同源：用同一套参数再问一次服务端
      await refreshFacets(query, seq)
    }

    /* ---------- 外部检索结果区 ---------- */

    let externalItems = []
    let externalLoading = false
    let externalPage = 1
    let externalHasMore = false
    const seenKeys = new Set()   // 跨页去重：key = doi 或归一化标题

    const EXT_SOURCES = [
      { id: 'openalex', label: 'OpenAlex', note: '最稳，2.5 亿+ 作品' },
      { id: 'crossref', label: 'Crossref', note: '出版商题录，覆盖广' },
      { id: 'europepmc', label: 'Europe PMC', note: '生命科学/医学最全' },
      { id: 'arxiv', label: 'arXiv', note: '预印本，免费（官方接口偶发限流）' },
      { id: 'semanticscholar', label: 'Semantic Scholar', note: '免费额度紧，常限流' }
    ]
    // 记住上次选的来源
    const savedSources = Array.isArray(getState().prefs.externalSources) && getState().prefs.externalSources.length
      ? getState().prefs.externalSources
      : ['openalex', 'crossref', 'europepmc']
    const chosenSources = new Set(savedSources)

    const externalBody = h('div', { id: 'external-results' })
    const externalStatus = h('div.hint', '')
    /**
     * 每个来源一个状态角标：`[✓] OpenAlex` / `[⚠] arXiv`。
     * 详情走 title（鼠标悬浮）**并且**点击可展开——触屏没有 hover，不能只靠 tooltip。
     * 这样限流/额度这类信息不再堆成一大段红字压在主界面上。
     */
    const externalBadges = h('div', { id: 'ext-source-badges', style: { display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' } })
    const externalDetail = h('div.hint', { hidden: true, style: { color: 'var(--muted)' } })

    function renderSourceBadges (statusMap, chosen, boolQuery) {
      const map = statusMap || {}
      const ids = [...(chosen || [])]
      if (!ids.length) { externalBadges.replaceChildren(); externalDetail.hidden = true; return }
      const details = []
      externalBadges.replaceChildren(...ids.map(id => {
        const st = map[id] || {}
        const ok = st.ok !== false
        const label = sourceLabel(id)
        const why = st.hint || st.error || ''
        const cap = ['openalex', 'europepmc', 'arxiv'].includes(id)
        const capNote = boolQuery
          ? (cap ? '原生支持 AND/OR/NOT' : '只能按相关度检索，已用本地布尔复核剔除不符的')
          : ''
        const detail = [ok ? '正常' : '不可用', why, capNote].filter(Boolean).join(' · ')
        if (!ok && why) details.push(label + '：' + why + (capNote ? '（' + capNote + '）' : ''))
        return h('span.badge', {
          title: detail,
          style: {
            cursor: 'pointer',
            fontSize: '12px',
            padding: '2px 7px',
            borderRadius: '999px',
            border: '1px solid ' + (ok ? 'var(--border)' : 'var(--danger)'),
            color: ok ? 'var(--muted)' : 'var(--danger)',
            background: ok ? 'transparent' : 'var(--surface-3)'
          },
          onclick: () => {
            if (!details.length) return
            externalDetail.hidden = !externalDetail.hidden
            externalDetail.textContent = externalDetail.hidden ? '' : details.join('；')
          }
        }, (ok ? '✓ ' : '⚠ ') + label)
      }))
      externalDetail.hidden = !details.length
      externalDetail.textContent = details.join('；')
    }

    const sourceBoxes = {}
    const sourceRow = h('div', { style: { display: 'flex', gap: '14px', flexWrap: 'wrap', alignItems: 'center' } },
      ...EXT_SOURCES.map(s => {
        const box = h('input.check', {
          type: 'checkbox',
          checked: chosenSources.has(s.id),
          onchange: () => {
            if (box.checked) chosenSources.add(s.id)
            else chosenSources.delete(s.id)
            if (!chosenSources.size) {
              // 至少留一个
              chosenSources.add(s.id)
              box.checked = true
              toast('至少保留一个来源', 'warn')
              return
            }
            savePrefs({ externalSources: [...chosenSources] }, { silent: true })
            externalItems = []
            seenKeys.clear()
            runExternal(true)
          }
        })
        sourceBoxes[s.id] = box
        return h('label', {
          style: { display: 'flex', gap: '5px', alignItems: 'center', fontSize: '13px' },
          title: s.note
        }, box, s.label)
      })
    )

    // 外部结果筛选
    const efYearFrom = h('input.input', { type: 'text', inputmode: 'numeric', placeholder: '起', style: 'width:80px' })
    const efYearTo = h('input.input', { type: 'text', inputmode: 'numeric', placeholder: '止', style: 'width:80px' })
    const efVenue = h('input.input', { type: 'text', placeholder: '期刊 / 会议名', style: 'width:170px' })
    const efAuthor = h('input.input', { type: 'text', placeholder: '作者', style: 'width:120px' })
    const efMinCit = h('input.input', { type: 'text', inputmode: 'numeric', placeholder: '如 50', style: 'width:90px', title: '最低被引数' })
    const efOnlyOA = h('input.check', { type: 'checkbox' })

    function externalFilterValues () {
      return {
        yearFrom: trim(efYearFrom.value),
        yearTo: trim(efYearTo.value),
        venue: trim(efVenue.value),
        author: trim(efAuthor.value),
        minCitations: trim(efMinCit.value),
        onlyOA: !!efOnlyOA.checked
      }
    }

    const resetExternal = () => { externalItems = []; seenKeys.clear(); expandedTitleGroups.clear() }
    const applyExtFilters = () => { resetExternal(); runExternal(true) }
    ;[efYearFrom, efYearTo, efVenue, efAuthor, efMinCit].forEach(inp => {
      inp.addEventListener('keydown', ev => { if (ev.key === 'Enter') applyExtFilters() })
    })
    efOnlyOA.addEventListener('change', applyExtFilters)

    const extFilters = h('div', {
      style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginTop: '8px' }
    },
      h('span', { style: { fontSize: '12.5px', color: 'var(--faint)' } }, '来源：'),
      sourceRow
    )
    const extFilters2 = h('div', {
      style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginTop: '8px' }
    },
      h('span', { style: { fontSize: '12.5px', color: 'var(--faint)' } }, '筛选：'),
      efYearFrom, h('span', '—'), efYearTo,
      efVenue, efAuthor,
      h('span', { style: { fontSize: '12.5px', color: 'var(--faint)' } }, '被引 ≥'), efMinCit,
      h('label', { style: { display: 'flex', gap: '5px', alignItems: 'center', fontSize: '13px' } }, efOnlyOA, '只看开放获取'),
      h('button.btn.sm', { type: 'button', onclick: applyExtFilters }, '应用筛选'),
      h('button.btn.sm.ghost', {
        type: 'button',
        onclick: () => {
          efYearFrom.value = ''
          efYearTo.value = ''
          efVenue.value = ''
          efAuthor.value = ''
          efMinCit.value = ''
          efOnlyOA.checked = false
          applyExtFilters()
        }
      }, '清空')
    )

    const extFilterPanel = h('div', { hidden: true }, extFilters)
    const extFilterToggle = h('button.btn.sm', { type: 'button' }, '筛选 ▾')
    extFilterToggle.addEventListener('click', () => {
      extFilterPanel.hidden = !extFilterPanel.hidden
      extFilterToggle.textContent = extFilterPanel.hidden ? '筛选 ▾' : '收起筛选 ▴'
    })

    /* ---------- 网络结果的排序（在已取回的这批里排，不再发请求）----------
     * 外部接口不保证给出「按被引」这类排序，但我们手里已经有 citations / year 字段，
     * 所以排序纯在本地做：**不额外消耗额度**，界面上的数字与顺序也立刻对得上。
     * 范围要说清楚：只重排「已取回的那批」，不是全网重查（与右侧 facet 的口径一致）。 */
    const webSortSelect = h('select.select', {
      title: '只重排「已取回的这一批」，不会重新联网检索',
      style: { width: 'auto' },
      onchange: ev => { state.webSort = ev.target.value; renderExternalList() }
    },
      h('option', { value: 'relevance' }, '按相关度'),
      h('option', { value: 'timeDesc' }, '按时间（新→旧）'),
      h('option', { value: 'timeAsc' }, '按时间（旧→新）'),
      h('option', { value: 'citations' }, '按被引量（多→少）')
    )
    webSortSelect.value = state.webSort || 'relevance'

    /** 按当前选择重排一份副本（不改动原数组，避免「加载更多」时顺序错乱） */
    function sortExternalItems (list) {
      const mode = state.webSort || 'relevance'
      const arr = (list || []).slice()
      if (mode === 'timeDesc') {
        return arr.sort((a, b) => (Number(b.year) || 0) - (Number(a.year) || 0) || (Number(b.citations) || 0) - (Number(a.citations) || 0))
      }
      if (mode === 'timeAsc') {
        return arr.sort((a, b) => (Number(a.year) || 9999) - (Number(b.year) || 9999) || (Number(b.citations) || 0) - (Number(a.citations) || 0))
      }
      if (mode === 'citations') {
        return arr.sort((a, b) => (Number(b.citations) || 0) - (Number(a.citations) || 0) || (Number(b.year) || 0) - (Number(a.year) || 0))
      }
      return arr   // relevance：保持接口返回的相关度顺序
    }

    /* ---------- 网络检索页的 facet（只在「已取回的结果」上聚合）----------
     * 外部 API 不提供聚合计数，所以这里的数字**只代表已经取回来的那几十条**，
     * 界面上会写明，不假装它是全库分布。选好之后按「应用到网络检索」，
     * 才会变成 yearFrom/yearTo/venue/author/onlyOA 真参数重新检索一次。 */

    const webFacetBody = h('div', { id: 'web-facet-body', style: { display: 'grid', gap: '11px' } })
    const webFacetPanel = h('aside#web-facet-panel', { style: { width: '248px', flex: '0 0 248px' } },
      h('section.panel',
        h('div.panel-head', '筛选（网络结果）'),
        h('div.panel-body', webFacetBody)
      )
    )

    function computeWebFacets () {
      const years = new Map()
      const venues = new Map()
      const authors = new Map()
      let oaYes = 0
      for (const it of externalItems) {
        if (it.year) years.set(String(it.year), (years.get(String(it.year)) || 0) + 1)
        if (it.venue) venues.set(String(it.venue), (venues.get(String(it.venue)) || 0) + 1)
        for (const a of (it.authors || [])) {
          const name = String(a || '').trim()
          if (name) authors.set(name, (authors.get(name) || 0) + 1)
        }
        if (it.isOpenAccess) oaYes += 1
      }
      const toGroup = (map, cmp) => {
        const items = [...map.entries()].map(([value, count]) => ({ value, count }))
        items.sort(cmp || ((a, b) => b.count - a.count || String(a.value).localeCompare(String(b.value))))
        return { items, distinct: items.length }
      }
      return {
        total: externalItems.length,
        years: toGroup(years, (a, b) => Number(b.value) - Number(a.value)),
        venues: toGroup(venues),
        authors: toGroup(authors),
        oa: { items: [{ value: 'oa', count: oaYes }], distinct: 1 }
      }
    }

    /** 把选中的 facet 变成外部检索的**真参数**，重新检索一次 */
    function applyWebFacets () {
      const w = state.webFacets
      const ys = (w.years || []).map(Number).filter(Number.isFinite)
      efYearFrom.value = ys.length ? String(Math.min.apply(null, ys)) : ''
      efYearTo.value = ys.length ? String(Math.max.apply(null, ys)) : ''
      // 外部检索的 venue 只能给一个字符串，多项时取第一个（按钮上会说明）
      efVenue.value = (w.venues || []).length ? String(w.venues[0]) : ''
      efAuthor.value = (w.authors || []).length ? String(w.authors[0]) : ''
      efOnlyOA.checked = !!w.onlyOA
      applyExtFilters()
    }

    function renderWebFacets () {
      const f = computeWebFacets()
      const w = state.webFacets
      if (!f.total) {
        webFacetBody.replaceChildren(h('div.hint', { style: { fontSize: '12.5px' } },
          rich('先在上面输入关键词并检索，这里会列出**已取回结果**里的年份 / 期刊 / 作者分布。')))
        return
      }
      const groups = [
        { key: 'years', label: '年份', data: f.years },
        { key: 'venues', label: '期刊 / 会议', data: f.venues },
        { key: 'authors', label: '作者', data: f.authors }
      ]
      const anySelected = groups.some(g => (w[g.key] || []).length) || w.onlyOA
      webFacetBody.replaceChildren(
        h('div', { style: { fontSize: '12.4px', color: 'var(--muted)' } },
          '已取回 ' + f.total + ' 条（计数只统计这批，不是全网分布）'),
        ...groups.map(g => h('div', null,
          h('div', { style: { fontWeight: '600', fontSize: '12.8px', marginBottom: '4px', color: 'var(--muted)' } }, g.label),
          g.data.items.length
            ? h('div', { style: { display: 'grid' } }, ...g.data.items.slice(0, 5).map(it => {
              const on = (w[g.key] || []).some(v => String(v) === String(it.value))
              return h('label', {
                style: {
                  display: 'flex', gap: '7px', alignItems: 'baseline', cursor: 'pointer',
                  padding: '2px 0', fontSize: '12.8px', color: on ? 'var(--accent-text)' : 'var(--text)'
                }
              },
                h('input.check', {
                  type: 'checkbox', checked: on,
                  onchange: () => {
                    const list = w[g.key] || []
                    w[g.key] = on ? list.filter(v => String(v) !== String(it.value)) : [...list, String(it.value)]
                    renderWebFacets()
                  }
                }),
                h('span', { style: { flex: '1', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }, title: String(it.value) }, String(it.value)),
                h('span', { style: { color: 'var(--muted)' } }, String(it.count))
              )
            }))
            : h('div.hint', { style: { fontSize: '12px' } }, '（无）')
        )),
        h('label', { style: { display: 'flex', gap: '7px', alignItems: 'center', fontSize: '12.8px' } },
          h('input.check', {
            type: 'checkbox', checked: !!w.onlyOA,
            onchange: () => { w.onlyOA = !w.onlyOA; renderWebFacets() }
          }),
          '只看开放获取（' + f.oa.items[0].count + '）'),
        h('div', { style: { display: 'grid', gap: '6px', marginTop: '2px' } },
          h('button.btn.sm.primary', {
            type: 'button',
            title: '按选中的条件重新检索一次网络来源（年份→区间、期刊/作者取第一个、开放获取）',
            onclick: applyWebFacets
          }, '应用到网络检索'),
          anySelected
            ? h('button.btn.sm.ghost', {
              type: 'button',
              onclick: () => {
                state.webFacets = { years: [], venues: [], authors: [], onlyOA: false }
                renderWebFacets()
              }
            }, '清除选择')
            : null,
          h('div.hint', { style: { fontSize: '11.8px' } },
            '年份会变成区间（最早—最晚）；期刊与作者外部接口只收一个，多项时取第一个。')
        )
      )
    }

    const externalMore = h('div', { style: { marginTop: '10px' } })

    /* ---------- 网络资料（Exa）：与论文题录分开显示 ----------
       Exa 返回的是网页（博客 / 新闻 / 预印本页面 / 项目主页），
       元数据和「论文题录」不是一回事，混在一列里会让人误以为是论文，所以单独一块。 */
    const webBody = h('div', { id: 'web-results' })
    const webStatus = h('span.hint', '')
    const webZone = h('section.external-zone', { id: 'web-zone', hidden: true },
      h('div.external-head',
        h('div', { style: { display: 'grid', gap: '2px' } },
          h('div', { style: { fontWeight: '650', fontSize: '15px' } }, '网络资料（Exa）'),
          h('div.hint', '网页、博客、新闻、预印本页面等，不是论文题录；每条都带正文片段，方便判断相关性。需要先在「设置 → API服务 → 网络检索（Exa）」里填 Exa API Key。')
        ),
        h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', marginLeft: 'auto', flexWrap: 'wrap' } },
          webStatus,
          h('button.btn.sm', { type: 'button', onclick: () => runWebSearch(true) }, '搜索网络资料')
        )
      ),
      webBody
    )

    let webLoading = false
    let webReady = false      // 设置里是否配了 Exa：配了才显示「网络资料」区
    async function runWebSearch (force) {
      const q = String(state.q || '').trim()
      if (!q || webLoading) return
      webLoading = true
      webZone.hidden = false
      webStatus.textContent = '正在检索…'
      webStatus.style.color = ''
      webBody.replaceChildren(h('div.hint', { style: { padding: '8px 0' } }, '正在向 Exa 查询…'))
      try {
        const res = await api.webSearch(q, { num: 6 })
        const items = res.items || []
        if (!items.length) {
          webBody.replaceChildren(h('div.hint', { style: { padding: '8px 0' } }, '没有找到相关网页。'))
        } else {
          webBody.replaceChildren(...items.map(it => h('div.external-item',
            h('div', { style: { display: 'flex', gap: '8px', alignItems: 'baseline', flexWrap: 'wrap' } },
              h('a.external-title', { href: it.url, target: '_blank', rel: 'noopener', style: { color: 'inherit' } }, it.title),
              h('span.badge', it.domain + (it.published ? ' · ' + it.published : ''))
            ),
            (it.highlights || []).length || it.summary
              ? h('div.external-meta', { style: { display: 'grid', gap: '4px' } },
                (it.highlights || []).slice(0, 2).map(txt => h('div', { text: txt })),
                it.summary ? h('div', { style: { color: 'var(--faint)' } }, '摘要：' + it.summary) : null)
              : null,
            h('div.external-actions',
              h('a.btn.sm', { href: it.url, target: '_blank', rel: 'noopener' }, '打开网页 ↗'),
              h('button.btn.sm', {
                type: 'button',
                title: '把这条网页的标题与链接记成一篇文献（正文需要你自己补）',
                onclick: async () => {
                  try {
                    await api.createPaper({
                      title: it.title,
                      url: it.url,
                      year: it.published ? Number(String(it.published).slice(0, 4)) || null : null,
                      type: 'web',
                      summary: [it.summary].concat(it.highlights || []).filter(Boolean).join('\n').slice(0, 1200)
                    })
                    toast('已加入我的库')
                    refreshMeta()
                  } catch (err) { toast('加入失败：' + err.message, 'error') }
                }
              }, '加入我的库'))
          )))
        }
        webStatus.textContent = res.cached ? '缓存结果' : ('共 ' + items.length + ' 条')
        webStatus.style.color = 'var(--accent-text)'
      } catch (err) {
        webStatus.textContent = '检索失败'
        webStatus.style.color = 'var(--danger)'
        webBody.replaceChildren(h('div.hint', { style: { padding: '8px 0', color: 'var(--danger)' } },
          err.message, h('br'), '需要先在「设置 → API服务 → 网络检索（Exa）」里填好 API Key。'))
      } finally {
        webLoading = false
      }
    }

    /** 网络结果卡片。duplicate = 同名文献里被折叠起来的那几条 */
    function externalCard (item, options) {
      const opts = options || {}
      const actions = []
      if (item.inLibrary) {
        actions.push(h('span.badge.owned', '已在库中'))
        if (item.localId) {
          actions.push(h('button.btn.sm', {
            type: 'button', onclick: () => go('#/paper/' + item.localId)
          }, '打开'))
        }
      } else {
        actions.push(h('button.btn.sm.primary', {
          type: 'button',
          onclick: async ev => {
            const btn = ev.target
            btn.setAttribute('disabled', '')
            btn.textContent = item.doi ? '导入中…' : '创建中…'
            try {
              let paper
              if (item.doi) {
                // 复用 DOI 抓取：题录更完整（卷期页码等）
                try {
                  const res = await api.fetchDoi(item.doi)
                  paper = res.paper
                } catch {
                  paper = null // 联网抓取失败就退回用搜索结果的字段
                }
              }
              const payload = paper || {
                title: item.title,
                authors: item.authors || [],
                year: item.year,
                venue: item.venue,
                doi: item.doi,
                url: item.url,
                type: item.type === 'journal-article' ? 'journal' : 'other',
                summary: item.abstract ? String(item.abstract).slice(0, 1200) : ''
              }
              const created = await api.createPaper(payload)
              toast('已导入到你的文库')
              item.inLibrary = true
              item.localId = created.paper.id
              renderExternalList()
              refreshMeta()
            } catch (err) {
              toast('导入失败：' + err.message, 'error')
              btn.removeAttribute('disabled')
              btn.textContent = '导入到我的库'
            }
          }
        }, '导入到我的库'))
      }
      if (item.oaUrl) {
        actions.push(h('a.btn.sm', { href: item.oaUrl, target: '_blank', rel: 'noopener' }, '开放获取全文 ↗'))
      }
      if (item.url) {
        actions.push(h('a.btn.sm', { href: item.url, target: '_blank', rel: 'noopener' }, '去看原文 ↗'))
      }

      /* 三段式：标题 → 元信息（年份 / 期刊 / 被引 / 相似度 / 来源）→ 动作。
         元信息全部走 flex 的 gap 排版，不再手工拼 ' · ' 字符串，
         窄屏换行时也不会出现「· 停在一行开头」这种问题。 */
      const sources = item.sources || []
      return h('div.external-item' + (opts.duplicate ? '.dup' : ''),
        // 显示前再兜一次清洗：服务端已经清过，这里主要防**旧缓存**里的 `&lt;i&gt;`
        h('div.external-title', toPlainText(item.title)),
        h('div.external-meta',
          item.year ? h('span.ext-year', String(item.year)) : null,
          item.venue ? h('span.ext-venue', { title: String(item.venue) }, String(item.venue)) : null,
          item.citations != null ? h('span.ext-cite', '被引 ' + item.citations) : null,
          // 显示与查询的相似度，让排序依据一目了然
          item.relevance != null
            ? h('span.badge', { title: '与查询词的文本相似度（排序依据）' }, '相似度 ' + item.relevance)
            : null,
          // 折叠提示：同一篇被多个来源命中时显示「N 个来源」
          sources.length > 1
            ? h('span.badge', { title: '这些来源都收录了这篇：' + sources.map(sourceLabel).join('、') },
              sources.length + ' 个来源')
            : null,
          sources.length
            ? h('span.ext-src', { title: sources.map(sourceLabel).join(' + ') },
              '（' + sources.map(sourceLabel).join(' + ') + '）')
            : null
        ),
        h('div.external-actions', actions)
      )
    }

    function sourceLabel (id) {
      const found = EXT_SOURCES.find(s => s.id === id)
      return found ? found.label : id
    }
    /** 去重键：优先 DOI，其次归一化标题 */
    function dedupeKey (item) {
      if (item.doi) return 'doi:' + String(item.doi).toLowerCase()
      return 'title:' + String(item.title || '').toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ').trim()
    }

    /* ---------- 同名文献折叠（反馈 #7） ----------
       同一个标题可能被不同来源/出版商各收录一份（预印本 + 正式版）。
       分组规则放在 searchSyntax.groupByTitle 里（纯函数，方便单测）：
       只把**被引数最高**的那条摆在明面上，其余的收进它左边的小三角，点开才展开。 */

    const expandedTitleGroups = new Set()

    /** 按「同名折叠」规则重画外部结果列表 */
    function renderExternalList () {
      // 先按用户选的顺序排一遍（决定「哪条当代表」），再折叠同名。
      // 折完之后必须**再排一次**：groupByTitle 把每组放在「该组首次出现」的位置，
      // 但展示出来的是组内**被引最高**的那条，两者可能不是同一条——
      // 于是按年份排序时会出现「1992 排在 1999 前面」这种肉眼可见的乱序（实测踩到）。
      // 最后这次排序是对「真正的展示条目」排的，顺序才和选项一致。
      const grouped = searchSyntax.groupByTitle(sortExternalItems(externalItems))
      const groups = sortExternalItems(grouped.map(g => g.main)).map(main => {
        const found = grouped.find(g => g.main === main)
        return found || { key: '', main, duplicates: [] }
      })
      const nodes = []
      for (const group of groups) {
        if (!group.duplicates.length) {
          nodes.push(externalCard(group.main))
          continue
        }
        const open = expandedTitleGroups.has(group.key)
        const toggle = h('button.ext-dup-toggle', {
          type: 'button',
          'aria-expanded': open ? 'true' : 'false',
          title: open
            ? '收起这几篇同名文献'
            : '还有 ' + group.duplicates.length + ' 篇同名文献（被引更低），点开查看'
        }, (open ? '▾' : '▸') + group.duplicates.length)
        toggle.addEventListener('click', ev => {
          ev.stopPropagation()
          if (expandedTitleGroups.has(group.key)) expandedTitleGroups.delete(group.key)
          else expandedTitleGroups.add(group.key)
          renderExternalList()
        })
        nodes.push(h('div.external-group',
          toggle,
          externalCard(group.main),
          open
            ? h('div.ext-dup-list', ...group.duplicates.map(d => externalCard(d, { duplicate: true })))
            : null
        ))
      }
      externalBody.replaceChildren(...nodes)
    }

    /**
     * 查询外部来源。
     * append=true 表示「加载更多」：追加到现有列表，并跳过已经显示过的条目。
     */
    async function runExternal (resetOrAppend) {
      const append = resetOrAppend === 'append'
      const q = trim(state.q)
      const filters = externalFilterValues()
      const hasFilter = Object.values(filters).some(v => v !== '' && v !== false)
      if ((!q && !hasFilter) || externalLoading) {
        // 没有关键词也没有任何条件：整个网络区先收起来（Tab 显隐由 applyTab 负责）
        if (!q && !hasFilter && webGroup) webGroup.hidden = true
        return
      }
      externalLoading = true
      if (webGroup) webGroup.hidden = false
      // 配了 Exa 才把「网络资料」区露出来（它有独立的手动搜索按钮，不会自动花钱）
      if (webReady) webZone.hidden = false
      externalStatus.textContent = append ? '正在加载更多…' : '正在检索…'
      externalStatus.style.color = ''
      if (!append) {
        externalPage = 1
        resetExternal()
        externalBody.replaceChildren(h('div.hint', { style: { padding: '10px 0' } }, '正在并发查询所选来源…'))
      }
      try {
        const res = await api.externalSearch(q, {
          limit: 8,
          page: externalPage,
          sources: [...chosenSources].join(','),
          ...filters
        })
        const incoming = res.items || []
        const fresh = incoming.filter(item => {
          const key = dedupeKey(item)
          if (seenKeys.has(key)) return false
          seenKeys.add(key)
          return true
        })
        externalItems = append ? externalItems.concat(fresh) : fresh
        externalHasMore = !!res.hasMore && fresh.length > 0

        const failed = Object.entries(res.sources || {}).filter(([, v]) => !v.ok)
        // OpenAlex 额度用尽时给一句可操作的提示（而不是让你猜为什么它没结果）
        const oaState = (res.sources || {}).openalex || {}
        if (oaState.quota && oaState.quota.exhausted && !oaState.ok) {
          failed.push(['openalex', {
            hint: 'OpenAlex 今日额度已用尽' +
              (oaState.quota.resetAt ? '（' + new Date(oaState.quota.resetAt).toLocaleString('zh-CN', { hour12: false }) + ' 重置）' : '') +
              (oaState.keyless ? '；在「设置 → API服务 → 学术检索来源」填一个免费 key 可提升约 10 倍额度' : '')
          }])
        }
        if (!externalItems.length) {
          externalBody.replaceChildren(h('div.hint', { style: { padding: '10px 0' } },
            '所选来源都没有找到相关文献。可以放宽筛选条件，或勾选更多来源。',
            failed.length
              ? h('div', { style: { marginTop: '6px' } },
                failed.map(([k, v]) => h('div', sourceLabel(k) + '：' + (v.hint || v.error || '不可用'))))
              : null))
        } else {
          renderExternalList()
        }
        // 布尔查询：说明各源的真实能力（哪些原生支持、哪些只能按相关度检索后由本地复核）
        const boolNote = res.boolean
          ? '；布尔查询：' + (() => {
            const ok = [...chosenSources].filter(id => ['openalex', 'europepmc', 'arxiv'].includes(id))
            const weak = [...chosenSources].filter(id => !['openalex', 'europepmc', 'arxiv'].includes(id))
            const bits = []
            if (ok.length) bits.push(ok.map(sourceLabel).join('/') + ' 原生支持 AND/OR/NOT')
            if (weak.length) bits.push(weak.map(sourceLabel).join('/') + ' 只能按相关度检索（已用本地布尔复核剔除不符的）')
            return bits.join('；')
          })()
          : ''
        if (failed.length) {
          const hints = failed.map(([k, v]) => sourceLabel(k) + '：' + (v.hint || v.error || '不可用'))
          externalStatus.textContent = (res.cached ? '缓存结果 · ' : '') +
            (externalItems.length ? `已显示 ${externalItems.length} 条` : '所选来源暂时没有结果')
          externalStatus.style.color = ''
          externalDetail.hidden = false
          externalDetail.textContent = hints.join('；')
        } else {
          externalStatus.textContent = (res.cached ? '缓存结果 · ' : '') +
            `已显示 ${externalItems.length} 条` + (externalHasMore ? '，还有更多' : '') + boolNote
          externalStatus.style.color = 'var(--accent-text)'
        }
        // 角标反映每个来源的真实状态（取代原来那一大段红字）
        renderSourceBadges(res.sources, chosenSources, !!res.boolean)
        // 网络 facet 只能在「已取回的这批结果」上聚合（外部 API 不给聚合）
        renderWebFacets()
        // 记进会话：切走再回来直接复用这批结果，不重新请求（省额度）
        saveExternalSession(res.sources, res.boolean)
        renderExternalMore()
      } catch (err) {
        externalStatus.textContent = '外部检索失败'
        externalStatus.style.color = 'var(--danger)'
        if (!append) {
          externalBody.replaceChildren(h('div.hint', { style: { padding: '10px 0', color: 'var(--danger)' } },
            err.message, h('br'), '外部检索需要联网；不影响上面的本地搜索结果。'))
        }
      } finally {
        externalLoading = false
      }
    }

    /** 「加载更多」按钮 */
    function renderExternalMore () {
      if (!externalItems.length) {
        externalMore.replaceChildren()
        return
      }
      externalMore.replaceChildren(h('div', { style: { display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' } },
        externalHasMore
          ? h('button.btn', {
            type: 'button',
            onclick: async ev => {
              const btn = ev.target
              btn.setAttribute('disabled', '')
              btn.textContent = '加载中…'
              externalPage += 1
              await runExternal('append')
              btn.removeAttribute('disabled')
              btn.textContent = '加载更多文献'
            }
          }, '加载更多文献')
          : h('span.hint', '没有更多了（可以勾选更多来源或放宽筛选）'),
        h('span.hint', `已显示 ${externalItems.length} 条，来自 ${[...chosenSources].map(sourceLabel).join('、')}`)
      ))
    }

    /**
     * 切到「网络检索」时按需检索。
     * 以前是「本地结果少就偷偷去网上找」，现在有明确的 Tab，行为要可预期：
     * 切到网络页就按当前关键词搜一次；同一个词已经搜过就直接复用，不重复发请求。
     */
    let externalQueried = ''
    /** 先把这次网络结果记进会话，切走再回来就不必重新请求（外部请求还吃额度） */
    function saveExternalSession (statusMap, isBoolean) {
      if (!lastSession || lastSession.key !== sessionKey) return
      lastSession.external = {
        queried: externalQueried,
        items: externalItems.slice(),
        hasMore: externalHasMore,
        page: externalPage,
        statusMap: statusMap || {},
        boolean: !!isBoolean,
        statusText: externalStatus.textContent || '',
        badgeIds: [...chosenSources]
      }
    }

    /** 从会话里恢复网络结果（同一个词、同一批来源才算命中） */
    function restoreExternalSession (q) {
      const ext = lastSession && lastSession.key === sessionKey ? lastSession.external : null
      if (!ext || ext.queried !== q || !ext.items || !ext.items.length) return false
      externalItems = ext.items.slice()
      externalHasMore = !!ext.hasMore
      externalPage = Number(ext.page) || 1
      externalQueried = q
      seenKeys.clear()
      for (const it of externalItems) seenKeys.add(dedupeKey(it))
      renderExternalList()
      renderExternalMore()
      renderSourceBadges(ext.statusMap, ext.badgeIds || chosenSources, ext.boolean)
      if (ext.statusText) {
        externalStatus.textContent = ext.statusText
        externalStatus.style.color = 'var(--accent-text)'
      }
      renderWebFacets()
      return true
    }

    function ensureExternal (force) {
      const q = trim(state.q)
      if (!q) {
        externalStatus.textContent = ''
        externalBody.replaceChildren(h('div.hint', { style: { padding: '8px 0' } },
          '先在上面输入关键词（标题、作者、DOI 都行），这里就会去公开学术来源里找。'))
        externalMore.replaceChildren()
        renderSourceBadges({}, chosenSources, false)
        return
      }
      if (!force && externalQueried === q && externalItems.length) {
        renderExternalList()
        renderWebFacets()
        renderExternalMore()
        return
      }
      // 切走再回来：会话里有这个词的网络结果就直接复用（外部请求吃额度，能省则省）
      if (!force && restoreExternalSession(q)) return
      externalQueried = q
      resetExternal()
      runExternal(true)
    }
    const summaryChips = h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } })
    function renderChips () {
      const chips = []
      if (state.q) chips.push(['关键词：' + state.q, () => { state.q = ''; input.value = ''; state.page = 1; apply() }])
      if (state.author) chips.push(['作者：' + state.author, () => { state.author = ''; fAuthor.value = ''; state.page = 1; apply() }])
      if (state.venue) chips.push(['期刊：' + state.venue, () => { state.venue = ''; fVenue.value = ''; state.page = 1; apply() }])
      if (state.keyword) chips.push(['关键词(原文)：' + state.keyword, () => { state.keyword = ''; fKeyword.value = ''; state.page = 1; apply() }])
      if (state.yearFrom || state.yearTo) chips.push([`年份：${state.yearFrom || '不限'} — ${state.yearTo || '不限'}`, () => { state.yearFrom = ''; state.yearTo = ''; fYearFrom.value = ''; fYearTo.value = ''; state.page = 1; apply() }])
      if (state.type !== 'all') chips.push(['类型：' + typeLabel(state.type), () => { state.type = 'all'; fType.value = 'all'; state.page = 1; apply() }])
      if (state.starred) chips.push(['★ 只看收藏', () => { state.starred = false; fStarred.checked = false; state.page = 1; apply() }])
      for (const tag of state.tags) chips.push(['#' + tag, () => { state.tags = state.tags.filter(t => t !== tag); state.page = 1; apply() }])
      if (state.hasPdf) chips.push(['有 PDF', () => { state.hasPdf = false; fHasPdf.checked = false; state.page = 1; apply() }])
      if (state.scope && state.scope !== 'metadata') {
        chips.push([state.scope === 'fulltext' ? '搜的是 PDF 全文' : '搜全文 + 题录',
          () => { state.scope = 'metadata'; fScope.value = 'metadata'; state.page = 1; apply() }])
      }
      // 右侧 facet 的已选项也做成可点掉的标签（同类 OR、跨类 AND 的效果一眼可见）
      for (const g of FACET_GROUPS) {
        for (const v of (state.facets[g.key] || [])) {
          const label = g.key === 'types' ? (TYPE_TEXT[v] || v) : v
          chips.push([g.label + '：' + label, () => toggleFacet(g.key, v)])
        }
      }

      summaryChips.replaceChildren(...(chips.length
        ? [h('span', { style: { fontSize: '12.5px', color: 'var(--faint)', alignSelf: 'center' } }, '当前条件：'),
          ...chips.map(([label, remove]) => h('button.tag-chip.active', {
            type: 'button', title: '点击移除该条件', onclick: remove
          }, label, h('span.n', '×'))),
          h('button.btn.sm.ghost', { type: 'button', onclick: () => go('#/search') }, '清空全部')]
        : []))
    }

    /* ---------- 顶部两个 Tab：本地库 / 网络检索 ---------- */

    const tabLocal = h('button.search-tab', { type: 'button' }, '🏠 本地库')
    const tabWeb = h('button.search-tab', { type: 'button' }, '🌐 网络检索')
    const tabBar = h('div', { id: 'search-tabs', style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } }, tabLocal, tabWeb)
    /* 结果工具条：左边「← 返回全部文献」，右边条数 + 排序 + 视图切换。
       跟下面的结果列表用同一条细线分界，扫读时层级是「工具条 → 列表」，不再和卡片混在一起。 */
    const localToolbar = h('div.search-toolbar',
      tagRow,
      h('span.grow'),
      resultCount,
      sortSelect,
      searchViewToggle
    )
    const localZone = h('div', { id: 'local-zone' },
      advancedPanel,
      localToolbar,
      // 结果在左、筛选栏在右（窄窗口靠 CSS 折成一列，见 .search-columns 的媒体查询）
      h('div.search-columns',
        h('div.search-main', results, pager),
        facetPanel
      )
    )
    /* 整块网络区域：头部通栏 + 左右双栏。
       头部（标题 / 排序 / 状态 / 来源角标 / 可展开的筛选）**故意留在双栏之外**：
       以前这一块在左栏内部，把左侧第一张卡片顶下去 182px，
       于是右侧筛选栏比卡片高出整整一截、看起来没对齐。
       现在头是通栏、双栏从同一条水平线开始（列容器上加 align-items: flex-start）。
       外层仍保留 id="external-zone"（下面这些都在它里面，外部脚本按这个 id 找结果区）。 */
    const webGroup = h('div', { id: 'web-group' },
      h('div', { id: 'external-zone' },
        /* 头部两段式：第一行标题 + 说明（动作「重新搜索」贴右），
           第二行工具条放来源角标 + 排序 / 筛选 / 状态。
           原来这些都挤在同一行，窄屏时换行成两三截、右边缘和结果列对不上。 */
        h('div.ext-head',
          h('div.ext-head-text',
            h('div.ext-head-title', '网络上的相关文献'),
            h('div.hint', '来自多个公开学术来源，不在你的库里；同一篇被多个来源命中时会自动折叠成一条。')
          ),
          h('button.btn.sm', { type: 'button', onclick: () => { resetExternal(); runExternal(true) } }, '重新搜索')
        ),
        h('div.search-toolbar.ext-toolbar',
          externalBadges,
          h('span.grow'),
          externalStatus,
          webSortSelect,
          extFilterToggle
        ),
        externalDetail,
        extFilterPanel,
        // 结果在左、网络 facet 在右（窄窗口靠 CSS 折成一列）
        h('div#web-columns.search-columns', { style: { marginTop: '10px' } },
          h('div.search-main', externalBody, externalMore, webZone),
          webFacetPanel
        )
      )
    )

    /** 切 Tab：只改显示，不重绘页面（重绘会重跑一遍本地检索，白费一次请求） */
    function applyTab (opts) {
      const web = state.tab === 'web'
      localZone.hidden = web
      webGroup.hidden = !web
      tabLocal.classList.toggle('active', !web)
      tabWeb.classList.toggle('active', web)
      advancedToggle.hidden = web
      if (web && opts && opts.run) ensureExternal(false)
    }

    function switchTab (tab) {
      if (state.tab === tab) return
      state.tab = tab
      // 切 Tab 保留关键词与其它条件；URL 只做 replace，不新增历史
      const next = toParams(state)
      const qs = new URLSearchParams()
      for (const [k, v] of Object.entries(next)) if (v !== '' && v != null) qs.set(k, String(v))
      const hash = '#/search' + (qs.toString() ? '?' + qs.toString() : '')
      if (location.hash !== hash) {
        try { history.replaceState(null, '', hash) } catch { location.hash = hash }
      }
      applyTab({ run: true })
    }
    tabLocal.onclick = () => switchTab('local')
    tabWeb.onclick = () => switchTab('web')

    outlet.replaceChildren(h('div.content',
      /* 头部：素材 hero（Google 多色字标）居中；原「搜索」标题与副标题由字标 + tagline 取代，
         「← 返回」保留，绝对定位在左上角，不参与居中计算 */
      h('div.hero-head',
        h('button.btn.sm.ghost.hero-back', { type: 'button', onclick: () => go('#/'), title: '返回全部文献' }, '← 返回'),
        components.hero({
          letters: HERO_PAPER_LIBRARY,
          tagline: '极速语义检索 · 智能穿透标题、摘要、作者与正文相关性排序'
        })
      ),
      h('section.panel.hero-search',
        h('div.panel-body', h('div', { style: { display: 'grid', gap: '9px' } },
          h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
            h('div', { style: { flex: '1 1 320px', minWidth: '200px' } }, input),
            h('button.btn.primary', {
              type: 'button',
              onclick: () => flushLive(input.value)
            }, '搜索'),
            advancedToggle
          ),
          tabBar,
          summaryChips
        ))
      ),
      localZone,
      webGroup
    ))

    // 先看设置里有没有 Exa key（本地接口，很快），决定要不要露出「网络资料」区
    try {
      const ws = await api.webSearchSettings()
      webReady = !!(ws && ws.settings && ws.settings.hasKey)
    } catch { webReady = false }

    renderChips()
    renderFacets()
    applyTab()
    await runSearch()
    // 停在「网络检索」页时（例如从 URL 直接进来）才去搜网络，不白跑请求
    if (state.tab === 'web') ensureExternal(false)
  }

  export { searchHandler }
