/* 应用外壳：主题、侧栏、顶栏、快捷键、路由注册、启动
   —— 视图需要的应用能力（导航/查询状态/渲染调度/偏好保存）在 shell/app-api.mjs。
      依赖方向是单向的：views/components → shell/app-api；app.mjs → shell/app-api + views（注册路由），
      所以不存在循环依赖。state 本体在 state/store.mjs。 */
import * as util from './util.mjs'
import * as searchSyntax from './search.mjs'
import { api } from './api.mjs'
import { getState, patch, subscribe } from './state/store.mjs'
import {
  go, render, refreshMeta, parseHash, setHash, queryToParams, savePrefs, route,
  runSearch, setView, scheduleRender, setShellHooks
} from './shell/app-api.mjs'
import { listHandler, resetListBatch } from './views/list.mjs'
import { detailHandler } from './views/detail.mjs'
import { editHandler } from './views/edit.mjs'
import { searchHandler } from './views/search.mjs'
import { settingsHandler } from './views/settings.mjs'
import { timeline as timelineHandler } from './views/timeline.mjs'
// 合并后新增的 3 个视图（图表画廊 / 横向对比 / 深度分析）
import { figuresHandler } from './views/figures.mjs'
import { compareHandler } from './views/compare.mjs'
import { deepHandler } from './views/deep.mjs'

const { h, toast, debounce } = util

  /* ---------------- 全局状态（P4.4：本体在 state/store.mjs） ---------------- */
  /* 这里保留一个指向同一对象的本地别名给渲染代码用；写入统一走 patch()，
     视图/组件通过 getState() 读、通过 patch() 写，不再碰全局对象上的 state。 */

  const state = getState()

  /* ---------------- 主题 ---------------- */

  function applyTheme (theme) {
    const t = theme === 'night' ? 'night' : 'paper'
    document.documentElement.setAttribute('data-theme', t)
    patch({ prefs: { theme: t } })
  }

  function toggleTheme () {
    const next = state.prefs.theme === 'night' ? 'paper' : 'night'
    applyTheme(next)
    savePrefs({ theme: next })
    const btn = document.getElementById('theme-toggle')
    if (btn) btn.textContent = next === 'night' ? '☾' : '☀'
    toast(next === 'night' ? '已切换到夜间模式' : '已切换到纸感浅色模式')
  }

  /* ---------------- 数据 ---------------- */

  /** 侧栏里几个会变的数字：导航项右侧的计数 + 底部那行汇总 */
  const sidebarCountEls = {}

  function updateSidebarCounts () {
    const total = state.stats ? state.stats.total : null
    const starred = state.stats ? state.stats.starred : null
    const tagCount = state.stats ? state.stats.tags : null
    const set = (key, value) => {
      const el = sidebarCountEls[key]
      if (!el) return
      el.textContent = value == null ? '' : String(value)
      el.hidden = value == null
    }
    set('total', total)
    set('starred', starred)
    const foot = sidebarCountEls.foot
    if (foot) {
      foot.textContent = state.stats
        ? `共 ${total} 条 · ${tagCount} 个标签`
        : '读取中…'
    }
    const missing = sidebarCountEls.missing
    if (missing) {
      const n = state.stats ? state.stats.pdfMissing : 0
      missing.textContent = n ? `⚠ ${n} 个 PDF 丢失` : ''
      missing.hidden = !n
    }
  }

  /**
   * 切换页面时更新外壳：
   *   - 侧栏导航项的 active 高亮（只改 class，不动 DOM 结构）
   *   - **顶栏按当前路由重画**——因为「排序」「卡片/紧凑」这些控件只在书库首页才该出现。
   *     之前顶栏只在启动时按首页渲染过一次、之后再没更新，导致搜索页上也显示着首页的控件，
   *     点一下就执行了首页的逻辑（把地址改回 #/），搜索词和结果被清空。
   */
  function syncChrome (activePath) {
    const navItems = document.querySelectorAll('[data-nav]')
    for (const item of navItems) {
      const target = item.getAttribute('data-nav')
      const active = activePath === target || (target !== '/' && activePath.startsWith(target + '/'))
      item.classList.toggle('active', active)
    }
    // 顶栏重建：注意只替换 header 本身，出口容器（#outlet）在 main 下面，不受影响
    const oldTopbar = document.getElementById('topbar')
    const mode = activePath === '/' ? 'list' : 'plain'
    if (oldTopbar && oldTopbar.__lvTopbarMode === mode && typeof oldTopbar.__lvTopbarSync === 'function') {
      // 还是同一类页面（书库 ↔ 书库、其它 ↔ 其它）：结构不变就别整块重画。
      // 整块重画会连带把用户正在输入的搜索框焦点、以及 #标签 联想下拉一起丢掉。
      oldTopbar.__lvTopbarSync()
    } else if (oldTopbar) {
      const next = renderTopbar(activePath)
      if (typeof oldTopbar.replaceWith === 'function') oldTopbar.replaceWith(next)
      else if (oldTopbar.parentNode) oldTopbar.parentNode.replaceChild(next, oldTopbar)
    }
  }

  /* ---------------- 侧栏 + 顶栏 ---------------- */

  function navItem (label, hash, icon, count, active, countKey, onNavigate) {
    // 计数元素存进 registry：refreshMeta 之后可以就地更新，不用整块重画侧栏
    const countEl = h('span.count', count == null ? '' : String(count))
    if (count == null) countEl.hidden = true
    if (countKey) sidebarCountEls[countKey] = countEl
    return h('button.nav-item' + (active ? '.active' : ''), {
      type: 'button',
      // data-nav 存「路径」而不是 hash，方便切换页面时只改 class、不重建 DOM
      'data-nav': hash.replace(/^#/, '') || '/',
      onclick: () => {
        if (onNavigate) onNavigate()
        else go(hash)
        document.body.classList.remove('nav-open')
      }
    },
      h('span.nav-ico', icon),
      h('span', label),
      countEl
    )
  }

  function renderSidebar (activePath) {
    const totalEl = navItem('全部文献', '#/', '▤', state.stats ? state.stats.total : null, activePath === '/', 'total')
    const starredEl = navItem('我的收藏', '#/starred', '★',
      state.stats && state.stats.starred ? state.stats.starred : null, activePath === '/starred', 'starred')
    const footStats = h('span', state.stats ? `共 ${state.stats.total} 条 · ${state.stats.tags} 个标签` : '读取中…')
    sidebarCountEls.foot = footStats
    const footMissing = h('span', state.stats && state.stats.pdfMissing ? `⚠ ${state.stats.pdfMissing} 个 PDF 丢失` : '')
    sidebarCountEls.missing = footMissing
    return h('aside.sidebar', { id: 'sidebar' },
      h('div.brand',
        // logo 是图片（白底），所以放在一个白色小圆角块里，深浅主题下都立得住
        h('div.brand-mark', h('img', { src: '/sci-lib-mark.png', alt: 'PaperLibrary', draggable: 'false' })),
        // 名称按 logo 的配色：Sci 深蓝、-lib 青色（颜色在 CSS 变量里，夜间主题会换成亮一点的同色系）
        h('div.brand-name',
          h('span.brand-navy', 'Sci'),
          h('span.brand-teal', '-lib')
        )
      ),
      h('nav.nav', totalEl, starredEl,
        navItem('搜索', '#/search', '⌕', null, activePath === '/search', null, () => {
          // 回到上次的搜索条件与结果（用户抱怨过：切去看一篇文献再回来，搜索就被清空了）
          const last = state.lastSearchParams
          go('search', last && Object.keys(last).length ? last : undefined)
        }),
        navItem('年份时间线', '#/timeline', '⏱', null, activePath === '/timeline'),
        // 合并后新增：PaperLibrary 的三项能力（图表画廊 / 横向对比 / 深度分析）
        navItem('图表画廊', '#/figures', '🖼', null, activePath === '/figures'),
        navItem('横向对比', '#/compare', '⇄', null, activePath === '/compare'),
        navItem('深度分析', '#/deep', '🔬', null, activePath === '/deep'),
        navItem('设置与备份', '#/settings', '⚙', null, activePath === '/settings')
      ),
      h('div.side-foot',
        footStats,
        footMissing,
        h('span', { style: { marginTop: '6px', lineHeight: '1.7' } },
          '搜标签：在搜索框输入 # 会有联想，',
          h('br'),
          '或直接写 ', h('span.kbd', '#Transformer')
        ),
        h('span', h('span.kbd', 'Ctrl'), ' + ', h('span.kbd', 'K'), ' 快速搜索')
      )
    )
  }

  function renderTopbar (activePath) {
    const onList = activePath === '/'
    // 标签联想下拉：输入 # 之后出现
    const suggest = h('div.tag-suggest', { id: 'tag-suggest', hidden: true })
    let suggestItems = []
    let suggestIndex = -1

    function hideSuggest () {
      suggest.hidden = true
      suggest.replaceChildren()
      suggestItems = []
      suggestIndex = -1
    }
    function renderSuggest () {
      const built = suggestItems.map((item, idx) =>
        h('button.tag-suggest-item' + (idx === suggestIndex ? '.active' : ''), {
          type: 'button',
          onmousedown: ev => { ev.preventDefault(); choose(item.name) }
        },
          h('span', '#' + item.name),
          h('span.n', String(item.count) + ' 条')
        )
      )
      // 顶栏可能刚被重画过，这里按 id 取当前 DOM 里的那个节点，
      // 避免写进已经脱离文档的旧节点（写进去用户也看不见）。
      const live = (typeof document.getElementById === 'function' && document.getElementById('tag-suggest')) || suggest
      live.replaceChildren(...built)
      live.hidden = suggestItems.length === 0
      if (live !== suggest) {
        suggest.replaceChildren()
        suggest.hidden = true
      }
    }
    function choose (tag) {
      // 取消排队中的防抖搜索：否则「输入 #」触发的旧搜索会晚到，把刚选好的标签覆盖掉
      searchDebounced.cancel()
      // 必须实时按 id 取输入框：重绘之后，闭包里那个 search 已经脱离 DOM 了
      const live = document.getElementById('search-input')
      if (!live) return
      live.value = searchSyntax.appendTag(live.value, tag)
      hideSuggest()
      runSearch(live.value)
      live.focus()
    }
    function updateSuggest (value) {
      const pending = searchSyntax.pendingTag(value)
      if (pending === null) return hideSuggest()
      const used = new Set(searchSyntax.parse(value).tags.map(t => t.toLowerCase()))
      const needle = pending.toLowerCase()
      suggestItems = (state.tags || [])
        .filter(t => !used.has(t.name.toLowerCase()))
        .filter(t => !needle || t.name.toLowerCase().includes(needle))
        .slice(0, 8)
      suggestIndex = suggestItems.length ? 0 : -1
      renderSuggest()
    }
    const search = h('input.input', {
      type: 'search',
      id: 'search-input',
      // 首页的搜索框只做「快速筛选」；想按年份/作者/期刊等组合条件，按回车去搜索页
      placeholder: '快速筛选标题、作者、摘要…  支持 AND/OR/NOT；回车进入搜索页，#标签 可筛标签',
      value: state.query.qRaw || state.query.q,
      autocomplete: 'off',
      role: 'combobox',
      'aria-expanded': 'false',
      onkeydown: ev => {
        if (!suggest.hidden && suggestItems.length) {
          if (ev.key === 'ArrowDown') { ev.preventDefault(); suggestIndex = (suggestIndex + 1) % suggestItems.length; renderSuggest(); return }
          if (ev.key === 'ArrowUp') { ev.preventDefault(); suggestIndex = (suggestIndex - 1 + suggestItems.length) % suggestItems.length; renderSuggest(); return }
          if (ev.key === 'Enter' || ev.key === 'Tab') { ev.preventDefault(); choose(suggestItems[suggestIndex].name); return }
          if (ev.key === 'Escape') { ev.preventDefault(); hideSuggest(); return }
        }
        if (ev.key === 'Enter') {
          // 回车 → 去独立搜索页，把当前输入带过去
          ev.preventDefault()
          searchDebounced.cancel()
          hideSuggest()
          go('search', { q: String(ev.target.value || '').trim() })
          ev.target.blur()
          return
        }
        if (ev.key === 'Escape') { searchDebounced.cancel(); ev.target.value = ''; runSearch('') }
      },
      oninput: ev => {
        // 在搜索页时不要再做「首页筛选」，否则会和搜索页自己的状态打架
        if (location.hash.replace(/^#\/?/, '').split('?')[0] === 'search') return
        updateSuggest(ev.target.value)
        searchDebounced(ev.target.value)
      },
      onfocus: ev => updateSuggest(ev.target.value),
      onblur: () => setTimeout(hideSuggest, 120)
    })
    // 输入防抖：太频繁地重绘和请求都会卡；注意要能取消（见 choose）
    const searchDebounced = util.debounce(value => runSearch(value), 260)
    if (state.prefs.focusSearch) {
      state.prefs.focusSearch = false
      const caret = search.value.length
      setTimeout(() => {
        search.focus()
        try { search.setSelectionRange(caret, caret) } catch {}
      }, 20)
    }

    // 有查询词时，把「相关度优先」放最前面作为默认项（本地搜索按文本相似度排序）
    const buildSortOptions = () => {
      const hasQuery = !!state.query.q
      const options = []
      if (hasQuery) {
        options.push(h('option', { value: '', selected: !state.query.sort }, '相关度优先（按文本相似度）'))
      }
      options.push(
        h('option', { value: 'updatedAt', selected: state.query.sort === 'updatedAt' || (!hasQuery && !state.query.sort) }, '最近更新'),
        h('option', { value: 'addedAt', selected: state.query.sort === 'addedAt' }, '最近添加'),
        h('option', { value: 'yearDesc', selected: state.query.sort === 'yearDesc' }, '年份 ↓'),
        h('option', { value: 'yearAsc', selected: state.query.sort === 'yearAsc' }, '年份 ↑'),
        h('option', { value: 'title', selected: state.query.sort === 'title' }, '标题 A→Z'),
        h('option', { value: 'rating', selected: state.query.sort === 'rating' }, '评分 ↓')
      )
      return options
    }
    /** 重画排序下拉（查询词有/无时选项不一样），供顶栏复用时的增量同步使用 */
    const fillSort = select => {
      const hasQuery = !!state.query.q
      select.title = hasQuery ? '排序方式（默认为相关度）' : '排序方式'
      select.replaceChildren(...buildSortOptions())
      select.value = state.query.sort || (hasQuery ? '' : 'updatedAt')
    }

    const sort = h('select.select', {
      title: state.query.q ? '排序方式（默认为相关度）' : '排序方式',
      onchange: ev => {
        state.query.sort = ev.target.value
        state.query.page = 1
        setHash('', queryToParams())
      }
    }, ...buildSortOptions())

    const viewCards = h('button.btn.sm' + (state.prefs.view === 'cards' ? '.primary' : ''), {
      type: 'button', 'aria-label': '卡片视图', 'data-view': 'cards', onclick: () => setView('cards')
    }, '▦')
    const viewRows = h('button.btn.sm' + (state.prefs.view === 'rows' ? '.primary' : ''), {
      type: 'button', 'aria-label': '紧凑列表', 'data-view': 'rows', onclick: () => setView('rows')
    }, '☰')
    const viewToggle = h('div.btn-group', { title: '切换卡片 / 紧凑视图' }, viewCards, viewRows)

    const themeBtn = h('button.btn.icon', { type: 'button', id: 'theme-toggle', title: '切换主题', onclick: toggleTheme },
      state.prefs.theme === 'night' ? '☾' : '☀')

    // 顶栏这个搜索框只服务「库列表页的快速筛选」。
    // 搜索页自己有一个更大的搜索框，同页出现两个输入框纯属干扰，所以在搜索页把它藏起来。
    // 这些页面自己带搜索框（搜索页、图表画廊），顶栏那个「快速筛选书库」的输入框就别再占位置了
    const hideTopSearch = path => path === '/search' || path === '/figures'
    const searchWrap = h('div.search-wrap',
      h('span.ico', '⌕'),
      search,
      suggest,
      h('span.search-hint', 'Ctrl K')
    )
    searchWrap.hidden = hideTopSearch(activePath)

    const bar = h('header.topbar', { id: 'topbar' },
      h('button.btn.icon.mobile-only', {
        type: 'button', title: '菜单', onclick: () => document.body.classList.toggle('nav-open')
      }, '☰'),
      searchWrap,
      h('div.toolbar',
        onList ? sort : null,
        onList ? viewToggle : null,
        // 「导入 PDF」按钮已去掉：拖 PDF 与 DOI 抓取现在都在 #/new 的万能导入区里
        h('button.btn.primary', {
          type: 'button', onclick: () => { go('#/new') }
        }, '＋ 新增文献'),
        themeBtn
      )
    )

    // 顶栏「同一类页面内不重画」：只有从书库切到别的页面（或反过来）才换掉整块 DOM。
    // 否则每次重绘都会新建搜索框，用户打字打到一半焦点就没了、#标签 联想也会被抹掉。
    // 结构不变时改为就地同步下面这几处与状态相关的显示。
    bar.__lvTopbarMode = onList ? 'list' : 'plain'
    bar.__lvTopbarSync = () => {
      // 同一类页面之间切换时（例如「文献详情 → 搜索」都算 plain），这里也要跟着改，
      // 否则从详情页进搜索页时顶栏搜索框还杵在那儿。
      const nowPath = parseHash().path
      searchWrap.hidden = hideTopSearch(nowPath)
      // 用户正在搜索框里打字时不要回写，否则光标会跳到末尾
      if (document.activeElement !== search) {
        const want = state.query.qRaw != null && state.query.qRaw !== '' ? state.query.qRaw : (state.query.q || '')
        if (search.value !== want) search.value = want
      }
      if (onList) fillSort(sort)
      viewCards.classList.toggle('primary', state.prefs.view === 'cards')
      viewRows.classList.toggle('primary', state.prefs.view === 'rows')
      themeBtn.textContent = state.prefs.theme === 'night' ? '☾' : '☀'
    }
    return bar
  }

  /* ---------------- 快捷键 ---------------- */

  function bindShortcuts () {
    document.addEventListener('keydown', ev => {
      const tag = (ev.target.tagName || '').toLowerCase()
      const typing = tag === 'input' || tag === 'textarea' || tag === 'select' || ev.target.isContentEditable
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'k') {
        ev.preventDefault()
        const input = document.getElementById('search-input')
        if (input) { input.focus(); input.select() } else { state.prefs.focusSearch = true; setHash('') }
        return
      }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') {
        const form = document.querySelector('form[data-paper-form]')
        if (form) { ev.preventDefault(); form.requestSubmit() }
        return
      }
      if (typing) return
      if (ev.key === 'n') { go('#/new') }
      if (ev.key === 't') { toggleTheme() }
      if (ev.key === '/') {
        ev.preventDefault()
        const input = document.getElementById('search-input')
        if (input) input.focus()
      }
    })
  }

  /* ---------------- 启动 ---------------- */

  function registerRoutes () {
    const add = (pattern, handler) => { if (handler) route(pattern, handler) }
    add('/', listHandler)
    // 「我的收藏」复用书库列表，只是强制只看收藏（单独的地址，侧栏高亮才分得清）
    add('/starred', listHandler)
    add('/paper/:id', detailHandler)
    add('/new', editHandler)
    add('/edit/:id', editHandler)
    // 「导入 PDF」已经并进 #/new（统一建录页）。这里保留一个转发，
    // 免得旧的书签/链接失效，也让老的验证脚本仍然能落到同一个页面上。
    add('/import-pdf', ctx => {
      go('#/new')
      return Promise.resolve()
    })
    add('/timeline', timelineHandler)
    add('/search', searchHandler)
    add('/settings', settingsHandler)
    // 合并后新增的 3 个视图：图表画廊、横向对比、单篇深度分析（/deep/:id 也走同一个处理器）
    add('/figures', figuresHandler)
    add('/compare', compareHandler)
    add('/deep', deepHandler)
    add('/deep/:id', deepHandler)
  }

  async function boot () {
    applyTheme(state.prefs.theme)
    // 把外壳的两件事注入服务层：切页时同步侧栏高亮/顶栏、真的离开列表面板时清批量勾选
    // （这两件事都依赖 DOM/views，不属于 app-api，所以用钩子注入而不是反向 import）
    setShellHooks({ syncChrome, onPanelLeave: resetListBatch })
    // state 变化 → 同步侧栏数字（refreshMeta 里不再手动调用）
    subscribe(() => updateSidebarCounts())
    registerRoutes()
    // 服务端偏好（跨浏览器保留主题等）
    api.getPrefs().then(res => {
      const server = res.prefs || {}
      if (server.theme && server.theme !== state.prefs.theme) {
        applyTheme(server.theme)
        patch({ prefs: { theme: server.theme } })
        const btn = document.getElementById('theme-toggle')
        if (btn) btn.textContent = server.theme === 'night' ? '☾' : '☀'
      }
    }).catch(() => {})

    await refreshMeta()
    const app = document.getElementById('app')
    app.replaceChildren(
      h('div.shell',
        renderSidebar('/'),
        h('main.main',
          renderTopbar('/'),
          h('div', { id: 'outlet' })
        )
      )
    )
    app.hidden = false
    const bootEl = document.getElementById('boot')
    if (bootEl) bootEl.remove()
    patch({ booted: true })

    // 只监听 hashchange（用户手改地址、前进后退）；应用内导航统一走 go() → scheduleRender
    window.addEventListener('hashchange', () => scheduleRender())
    bindShortcuts()
    if (!location.hash) {
      location.hash = '#/'
      scheduleRender()
      return
    }
    await render()
  }

  export { boot }
