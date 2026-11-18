/**
 * 应用服务层（shell/app-api）—— 视图与外壳之间唯一的依赖面。
 *
 * 为什么单独一个模块：P4 那版是「6 个 views + components 都 import app.mjs，而 app.mjs 又 import 这些 views」，
 * 依赖图成环，导致视图没法单独理解/单独测试（G1 可拆分单元不成立）。这里把「视图需要的应用能力」
 * （导航、查询状态、渲染调度、偏好保存、侧栏元信息）收进本模块：
 *
 *   views / components  →  shell/app-api.mjs
 *   app.mjs（外壳）     →  shell/app-api.mjs   +   views/*（注册路由处理器）
 *
 * app-api **不 import 任何 view**，渲染时要碰外壳的两件事（切页高亮/顶栏重画、离开列表面板时清批量勾选）
 * 通过 setShellHooks() 由外壳注入 —— 依赖方向单向，import 图无环。
 *
 * 行为与 P4 版完全一致：这里只是把 app.mjs 里的服务函数原样搬过来（保留原缩进便于逐行对照），
 * 没有改任何 UI / 交互 / 文案。
 */
import * as util from '../util.mjs'
import * as searchSyntax from '../search.mjs'
import { api } from '../api.mjs'
import { getState, patch, persistPrefs } from '../state/store.mjs'

const { h } = util

const state = getState()

/* 外壳注入的两个钩子（见文件头说明）：默认什么都不做，boot 时由 app.mjs 装上 */
let syncChromeHook = null
let onPanelLeaveHook = null

/**
 * 由外壳在 boot 时注入：
 *   syncChrome(path)   —— 切页时同步侧栏高亮/顶栏（属于 DOM 外壳，不在本层）
 *   onPanelLeave()     —— 真的离开列表面板时清批量勾选（需要 views/list.mjs 的 resetListBatch）
 */
export function setShellHooks (hooks) {
  const opts = hooks || {}
  if (typeof opts.syncChrome === 'function') syncChromeHook = opts.syncChrome
  if (typeof opts.onPanelLeave === 'function') onPanelLeaveHook = opts.onPanelLeave
}

  /** 保存偏好：写本地 → 异步同步服务端 → 让界面立刻反映出来 */
  function savePrefs (changes, options) {
    patch({ prefs: changes || {} })
    persistPrefs()
    api.savePrefs(state.prefs).catch(() => {})
    if (options && options.silent) return
    // 不重绘的话，切换视图/每页条数看起来就像「点了没反应」
    updateList()
  }

  async function refreshMeta () {
    try {
      const [tags, stats] = await Promise.all([api.tags(), api.stats()])
      // 侧栏的数字要跟着刷新：侧栏只在启动时渲染过一次，
      // 不让订阅者更新的话「全部文献 / 我的收藏」的条数会一直停在打开页面那一刻（反馈 #9）
      patch({ tags: tags.tags || [], stats: stats.stats || null })
    } catch (err) {
      console.warn('统计信息加载失败：', err.message)
    }
  }

  function rememberTitles (items) {
    for (const item of items || []) state.titleMap.set(String(item.title || '').toLowerCase(), item)
  }

  function lookupTitle (title) {
    const key = String(title || '').toLowerCase().trim()
    if (state.titleMap.has(key)) return state.titleMap.get(key)
    for (const [k, v] of state.titleMap) {
      if (k.includes(key) || key.includes(k)) return v
    }
    return null
  }

  /* ---------------- 路由 ---------------- */

  const routes = []
  function route (pattern, handler) {
    const keys = []
    const regex = new RegExp('^' + pattern.replace(/:([A-Za-z]+)/g, (m, key) => {
      keys.push(key)
      return '([^/?]+)'
    }) + '$')
    routes.push({ regex, keys, handler })
  }

  function parseHash () {
    const raw = location.hash.replace(/^#\/?/, '')
    const [pathPart, queryPart] = raw.split('?')
    const path = '/' + decodeURIComponent(pathPart || '').replace(/\/+$/, '')
    const params = new URLSearchParams(queryPart || '')
    return { path: path === '/' ? '/' : path, params }
  }

  function setHash (path, params) {
    const sp = new URLSearchParams()
    for (const [key, value] of Object.entries(params || {})) {
      if (value == null || value === '' || value === false) continue
      if (Array.isArray(value)) { if (value.length) sp.set(key, value.join(',')) } else sp.set(key, String(value))
    }
    const qs = sp.toString()
    const next = '#/' + String(path).replace(/^\/+/, '') + (qs ? '?' + qs : '')
    if (location.hash === next) return false
    location.hash = next
    // 不依赖 hashchange 事件：桩环境与某些浏览器分支下它不一定触发
    scheduleRender()
    return true
  }

  /** 列表页筛选条件写进 URL，便于收藏与前进后退 */
  function applyQueryFromUrl (params) {
    const rawQ = params.get('q') || ''
    const parsed = searchSyntax.parse(rawQ)
    // 从 URL 重建时保留用户原始输入（含正在输入的 #）
    state.query.qRaw = rawQ
    state.query.q = parsed.text
    state.query.tags = parsed.tags
    state.query.extraTags = []
    state.query.type = params.get('type') || 'all'
    state.query.starred = params.get('starred') === 'true'
    state.query.sort = params.get('sort') || ''
    state.query.page = Number(params.get('page')) || 1
    // 文件夹：URL 里用 "/" 表示根目录（未分类）——空串会被 setHash 过滤掉，没法表达「只看根」
    if (params.has('folder')) {
      const raw = params.get('folder') || '/'
      state.query.folder = raw === '/' ? '' : raw
    } else {
      state.query.folder = null
    }
  }

  let currentRenderToken = 0
  let activeRender = null

  /**
   * 渲染调度：所有「需要重绘」的地方都走这里，避免 hashchange 与主动重绘撞车。
   *
   * 之前同时存在「hashchange 触发重绘」和「setHash 里手动重绘」两条路径，
   * 用户点一个标签会渲染两次，后一次把前一次的结果判成过期丢掉，列表就空了。
   */
  let renderTimer = null
  let forceNextRender = false

  /** 定时器触发的重绘：可以按指纹去重（同一状态下不必重画） */
  function scheduleRender (options) {
    if (options && options.force) forceNextRender = true
    if (renderTimer) return
    renderTimer = setTimeout(() => {
      renderTimer = null
      const force = forceNextRender
      forceNextRender = false
      render({ force, scheduled: true }).catch(err => console.error('[render] 失败：', err))
    }, 0)
  }

  /** 页面内导航统一入口 */
  function navigate (hash) {
    if (location.hash === hash) return scheduleRender()
    location.hash = hash
    scheduleRender()
  }

  /**
   * 渲染指纹：路由 + 查询条件 + 视图模式，用来判断是否真的需要重画。
   * 注意：只有 scheduleRender 会用它去重；直接调用 render() 一律真的重画——
   * 否则「上传完 PDF 后重绘」这类操作会因为指纹没变而被跳过，页面看起来没反应。
   */
  function renderFingerprint (path, params) {
    const q = state.query
    return [path, params.toString(), state.prefs.view, state.prefs.pageSize, q.sort || '', q.page || 1].join('|')
  }

  let lastFingerprint = null
  let lastPanel = null

  /**
   * 「面板」是按用户感知分的，不是按地址分的：
   * #/ 与 #/starred 都是同一个书库列表（点一下「☆ 收藏」就从前者跳到后者），
   * 在它们之间走一趟不该算「离开书库」。别的路由各自算一个面板。
   */
  function panelOf (path) {
    return path === '/starred' ? '/' : path
  }

  async function render (options) {
    const { path, params } = parseHash()
    const fingerprint = renderFingerprint(path, params)
    const isScheduled = !!(options && options.scheduled)
    if (isScheduled && !options.force && fingerprint === lastFingerprint) return
    lastFingerprint = fingerprint
    if (activeRender) activeRender.abort()
    const scope = new AbortController()
    activeRender = scope
    // 真的离开了书库面板（不是切视图/改筛选/切收藏那些原地重绘）才清批量勾选，
    // 否则用户勾好的文献会被一起带出去
    const panel = panelOf(path)
    if (lastPanel !== null && lastPanel !== panel && typeof onPanelLeaveHook === 'function') onPanelLeaveHook()
    lastPanel = panel
    const token = ++currentRenderToken
    for (const r of routes) {
      const match = r.regex.exec(path)
      if (!match) continue
      const routeParams = {}
      r.keys.forEach((key, idx) => { routeParams[key] = match[idx + 1] })
      const outlet = document.getElementById('outlet')
      if (!outlet) return
      try {
        await r.handler({ outlet, params, routeParams, token, path,
          signal: scope.signal, isStale: () => scope.signal.aborted })
      } catch (err) {
        if (scope.signal.aborted) return
        console.error(err)
        outlet.replaceChildren(h('div.content', h('div.panel', h('div.panel-body',
          h('h3', '页面出错了'),
          h('p', { text: err.message || String(err) }),
          h('button.btn', { onclick: () => render() }, '重试')
        ))))
      }
      if (!scope.signal.aborted && typeof syncChromeHook === 'function') syncChromeHook(path)
      return
    }
    setHash('')
  }

  /**
   * 把当前查询状态写进 URL。
   * 注意：q 必须带上搜索框里的原始文本（含 #标签），否则刷新/后退时
   * 列表页会从 URL 重建状态，把 #标签 丢掉（这是真实踩过的坑）。
   */
  function queryToParams () {
    const q = state.query
    const text = q.qRaw != null && q.qRaw !== '' ? q.qRaw : (q.q || '')
    const extra = (q.extraTags || []).filter(Boolean)
    const combined = [text.trim(), ...extra.map(t => '#' + t)].filter(Boolean).join(' ').trim()
    return {
      q: combined,
      type: q.type !== 'all' ? q.type : '',
      // 收藏筛选由「路径」表达（#/starred），不重复放进查询串
      folder: q.folder == null ? '' : (q.folder === '' ? '/' : q.folder),
      sort: q.sort || '',
      page: q.page > 1 ? q.page : ''
    }
  }

  /**
   * 执行搜索。搜索串里可以写 "#标签"，这里把它解析出来：
   *   - 关键词部分 → q
   *   - #标签 部分 → tags
   * 原始输入保留在 qRaw，用于回填搜索框。
   */
  function runSearch (value) {
    const raw = String(value || '')
    const parsed = searchSyntax.parse(raw)
    state.query.qRaw = raw
    state.query.q = parsed.text
    state.query.tags = parsed.tags
    state.query.page = 1
    const input = document.getElementById('search-input')
    state.prefs.focusSearch = !!(input && document.activeElement === input)
    setHash('', queryToParams())
  }

  /** 只改标签筛选、不动关键词（详情页点标签时用） */
  function filterByTag (tag, options) {
    const opts = options || {}
    state.query.q = ''
    state.query.qRaw = '#' + tag + ' '
    state.query.tags = [tag]
    state.query.extraTags = []
    state.query.page = 1
    if (opts.resetType) state.query.type = 'all'
    if (opts.resetStarred) state.query.starred = false
    if (!setHash('', queryToParams())) scheduleRender()
  }

  /** 切换卡片 / 紧凑视图：只改偏好并重绘，绝不动地址（否则会清掉当前页面的状态） */
  function setView (view) {
    if (state.prefs.view === view) return
    state.prefs.view = view
    savePrefs({ view })
    scheduleRender({ force: true })
  }

  /**
   * 算出真正要发给服务端的查询条件。
   * 标签有两个来源：搜索框里的 #标签，以及从别处点进来的标签筛选，
   * 这里合并去重后一起传。
   */
  function effectiveQuery () {
    const q = state.query
    const tags = []
    const seen = new Set()
    for (const tag of [...(q.tags || []), ...(q.extraTags || [])]) {
      const key = String(tag).toLowerCase()
      if (!key || seen.has(key)) continue
      seen.add(key)
      tags.push(tag)
    }
    return {
      q: q.q || '',
      tags,
      // folder 只在「指定了目录」时才发；'' 会被 qs() 丢掉，所以根目录用 '/' 兜一下
      folder: q.folder == null ? '' : (q.folder === '' ? '/' : q.folder),
      type: q.type !== 'all' ? q.type : '',
      starred: q.starred ? 'true' : '',
      sort: q.sort || (q.q ? '' : 'updatedAt'),
      page: q.page,
      pageSize: state.prefs.pageSize
    }
  }

  /**
   * 让列表页重新按当前筛选条件渲染。
   * setHash 在地址没变化时返回 false，此时要手动重绘——否则「切换视图」这类
   * 只改偏好、不改地址的操作看起来就像点了没反应。
   */
  function updateList () {
    const raw = location.hash.replace(/^#\/?/, '')
    const path = raw.split('?')[0]
    // 详情/编辑/搜索这些页面各自管理自己的状态，不要用首页的查询参数去覆盖地址
    if (path.startsWith('paper/') || path.startsWith('edit/') || path === 'new' || path === 'search') return
    if (!setHash(path, queryToParams())) scheduleRender()
  }

  /** 应用内导航统一入口：path 形如 '#/timeline' 或空字符串（回书库） */
  function go (path, params) {
    if (typeof path === 'string' && path.includes('#')) {
      navigate(path)
      return
    }
    if (!setHash(path || '', params)) scheduleRender()
  }

/* ------------------------------------------------------------------ */
/* 对外导出的应用能力（视图只依赖这些）                                 */
/* ------------------------------------------------------------------ */

export {
  render, refreshMeta, rememberTitles, lookupTitle, parseHash,
  setHash, queryToParams, savePrefs, route, applyQueryFromUrl, runSearch, updateList, setView,
  filterByTag, effectiveQuery, scheduleRender, navigate, go
}
export { getState, subscribe } from '../state/store.mjs'
