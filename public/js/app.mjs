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
// 使用文档（新手手册）：纯静态页，放在侧栏最下方
import { guideHandler } from './views/guide.mjs'
// 全库智能问答助手（Copilot）：入口在侧栏「使用文档」上方，点开是左下角对话框
import { copilotSideEntry, mountCopilotDock } from './copilot.mjs'
import { copilotHandler } from './views/copilot.mjs'
// 文件夹分类：侧栏树、导入选目录、管理器共用的组件
import * as foldersUi from './folders.mjs'
import { autoCheckEnabled, autoDownloadEnabled, createAutoChecker } from './update-auto.mjs'

const { h, toast, debounce } = util

  /* 前端版本号：唯一来源是包根 version.json（服务端 /api/version 转出），
     品牌区、页面 <title>、使用文档里的版本号全部取自同一份，前端不再硬编码。
     启动时若取不到（服务异常），不显示版本号而不是显示一个可能过期的写死值。 */
  let UI_VERSION = ''

  function applyUiVersion (label) {
    UI_VERSION = label || ''
    if (!UI_VERSION) return
    // 排查「页面是不是旧代码」时，控制台一句 document.documentElement.dataset.uiVersion 就够了
    document.documentElement.dataset.uiVersion = UI_VERSION
    const meta = document.querySelector('meta[name="ui-version"]')
    if (meta) meta.setAttribute('content', UI_VERSION)
    document.title = 'PaperLibrary ' + UI_VERSION + ' Nexus · 我的文献资料库'
  }

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

  /* ---------------- 侧栏抽屉（展开 / 收起 + 遮罩 + 焦点管理） ----------------
     两种形态共用一套判断：
       · 宽屏（>1024）：停靠式。收起 = 侧栏滑出左边界、主区把 250px 让回去（推挤）。
       · 窄屏（≤1024）：浮层抽屉。默认收起，打开时压一层半透明遮罩，点遮罩/导航项就收起。
     看不见的那一侧设 inert：键盘 Tab 与读屏都不会撞进抽屉后面的内容里。 */

  /** 断点与 CSS 的 @media (max-width: 1024px) 保持一致 */
  const sideMQ = typeof window.matchMedia === 'function'
    ? window.matchMedia('(max-width: 1024px)')
    : { matches: false }

  const SIDE_KEY = 'lv:side-collapsed'
  function readSidePref () {
    try { return localStorage.getItem(SIDE_KEY) === '1' } catch (err) { return false }
  }
  function writeSidePref (collapsed) {
    try { localStorage.setItem(SIDE_KEY, collapsed ? '1' : '0') } catch (err) {}
  }
  /** 宽屏下用户上次是不是收起了侧栏（重开页面沿用，不在加载后闪一下再滑走） */
  let sideCollapsed = readSidePref()
  /** 打开抽屉前的焦点：关闭后还回去，键盘用户不会"丢失位置" */
  let sideReturnFocus = null
  /** 因「点导航项」收起时，焦点等新页面渲染完再还（顶栏会被整块重建） */
  let sideFocusPending = false

  const inOverlayMode = () => !!sideMQ.matches
  const drawerOpen = () => document.body.classList.contains('nav-open')

  function sideParts () {
    return {
      sidebar: document.getElementById('sidebar'),
      scrim: document.getElementById('side-scrim'),
      main: document.querySelector('.main'),
      toggle: document.getElementById('side-toggle')
    }
  }

  /** 把当前状态同步到 DOM（可在任何时候重复调用，不会累积副作用） */
  function syncSidebar () {
    const overlay = inOverlayMode()
    const open = overlay ? drawerOpen() : !sideCollapsed
    const { sidebar, scrim, main, toggle } = sideParts()
    document.body.classList.toggle('side-collapsed', !overlay && sideCollapsed)
    if (scrim) scrim.classList.toggle('is-on', overlay && open)
    if (sidebar) {
      const away = overlay ? !open : sideCollapsed
      sidebar.toggleAttribute('inert', away)
      if (away) sidebar.setAttribute('aria-hidden', 'true')
      else sidebar.removeAttribute('aria-hidden')
    }
    // 抽屉打开时主区 inert ≈ 焦点陷阱：Tab 不会跑到遮罩背后的内容上
    if (main) main.toggleAttribute('inert', overlay && open)
    if (toggle) {
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false')
      const label = open ? '收起导航栏' : '展开导航栏'
      toggle.setAttribute('aria-label', label)
      toggle.title = label + '（Ctrl+B）'
      toggle.classList.toggle('on', open)
    }
  }

  function openDrawer () {
    if (!inOverlayMode() || drawerOpen()) return
    // 记住「是谁打开的」：一般是顶栏那个开关；body 说明当前没有落点，就用开关当锚点
    const active = document.activeElement
    sideReturnFocus = active && active !== document.body ? active : document.getElementById('side-toggle')
    document.body.classList.add('nav-open')
    syncSidebar()
    const { sidebar } = sideParts()
    const first = sidebar && sidebar.querySelector('.side-close, .nav-item, .side-doc')
    if (first) { try { first.focus({ preventScroll: true }) } catch (err) {} }
  }

  function closeDrawer (options) {
    if (!drawerOpen()) return
    const opts = options || {}
    document.body.classList.remove('nav-open')
    syncSidebar()
    if (!opts.keepFocus) {
      // 点导航项会连带切页、顶栏整块重建：现在这个节点下一帧就作废了，
      // 交给 syncChrome() 在新 DOM 上把焦点还给新的开关
      if (opts.deferFocus) {
        sideFocusPending = true
        return
      }
      let target = opts.returnTo || sideReturnFocus
      if (!target || !document.contains(target)) target = document.getElementById('side-toggle')
      if (target && typeof target.focus === 'function') {
        try { target.focus({ preventScroll: true }) } catch (err) {}
      }
    }
    sideReturnFocus = null
  }

  /** 把焦点还给顶栏开关（切页后顶栏是新的，必须按 id 现取） */
  function restoreSideFocus () {
    const target = document.getElementById('side-toggle')
    if (target && typeof target.focus === 'function') {
      try { target.focus({ preventScroll: true }) } catch (err) {}
    }
  }

  function toggleSidebar () {
    if (inOverlayMode()) {
      if (drawerOpen()) closeDrawer()
      else openDrawer()
      return
    }
    sideCollapsed = !sideCollapsed
    writeSidePref(sideCollapsed)
    syncSidebar()
    // 收起后焦点留在开关上，Tab 顺序接得住
    const { toggle } = sideParts()
    if (toggle) { try { toggle.focus({ preventScroll: true }) } catch (err) {} }
  }

  // 跨断点时清掉浮层态：否则在宽屏上残留 nav-open 会把主区压成 inert
  if (typeof sideMQ.addEventListener === 'function') {
    sideMQ.addEventListener('change', () => {
      document.body.classList.remove('nav-open')
      sideReturnFocus = null
      syncSidebar()
    })
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
      // aria-current 也一起同步：侧栏只切 class 不重建，光靠渲染时写的那次会一直是旧的
      if (active) item.setAttribute('aria-current', 'page')
      else item.removeAttribute('aria-current')
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
    // 顶栏可能刚重建过（开关按钮是新的），把抽屉状态重新贴上去
    syncSidebar()
    if (sideFocusPending) {
      sideFocusPending = false
      restoreSideFocus()
    }
  }

  /* ---------------- 侧栏 + 顶栏 ---------------- */

  /* 「分类文件夹」表头右侧两个小按钮的图标。
     原来是＋（全角字符）和 ⚙（emoji）：都靠字体度量排版，在 24px 按钮里并不居中——
     实测 ⚙ 的墨迹比按钮中心偏右 4.75px、＋ 偏右 1.75px，各系统还不一样。
     换成内联 SVG 后按 viewBox 居中，位置与大小都确定，也不再受 emoji 字体影响。 */
  const ICON_PLUS =
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">' +
    '<path d="M12 4.4v15.2M4.4 12h15.2" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"/>' +
    '</svg>'
  const ICON_GEAR =
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">' +
    '<circle cx="12" cy="12" r="5.6" fill="none" stroke="currentColor" stroke-width="2.2"/>' +
    '<circle cx="12" cy="12" r="2.2" fill="none" stroke="currentColor" stroke-width="1.7"/>' +
    '<path d="M17.7 12h2.3M16.03 7.97 17.66 6.34M12 6.3V4M7.97 7.97 6.34 6.34M6.3 12H4M7.97 16.03 6.34 17.66M12 17.7V20M16.03 16.03 17.66 17.66"' +
    ' fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>' +
    '</svg>'
  /* 折叠三角：▾ 这个字形只有 0.36em 宽，11px 时墨迹才 4px——靠字号放大要 30px 才够看，
     线框也会被撑开。改用 SVG 三角，尺寸和旋转都自己说了算（收起时 CSS 转 -90°）。 */
  const ICON_CARET =
    '<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">' +
    '<path d="M4.5 9h15l-7.5 9.2z" fill="currentColor"/>' +
    '</svg>'

  /* 导航图标混着两类字形：彩色 emoji（🖼 🔬 📖）和文字符号（▤ ★ ⌕ ⇄ ⚙）。
     符号是细线框，同样字号下墨迹比 emoji 小一截，所以打上 is-sym 让 CSS 单独提一档字号，
     两类图标看起来才一样重——不这么做，Windows 上「搜索 ⌕」会明显比「图表 🖼」小一号。 */
  const SYMBOL_ICONS = '▤★☆⌕⇄'
  function icoSpan (ch) {
    return h('span.nav-ico' + (SYMBOL_ICONS.includes(ch) ? '.is-sym' : ''), ch)
  }

  function navItem (label, hash, icon, count, active, countKey, onNavigate) {
    // 计数元素存进 registry：refreshMeta 之后可以就地更新，不用整块重画侧栏
    const countEl = h('span.count', count == null ? '' : String(count))
    if (count == null) countEl.hidden = true
    if (countKey) sidebarCountEls[countKey] = countEl
    return h('button.nav-item' + (active ? '.active' : ''), {
      type: 'button',
      // data-nav 存「路径」而不是 hash，方便切换页面时只改 class、不重建 DOM
      'data-nav': hash.replace(/^#/, '') || '/',
      // 读屏靠这个知道"当前在第几页"
      'aria-current': active ? 'page' : null,
      onclick: () => {
        if (onNavigate) onNavigate()
        else go(hash)
        // 浮层抽屉里点导航项 = 选完就走：收起抽屉，焦点等新页面渲染完再还给开关
        // （宽屏停靠时没有任何动作）
        closeDrawer({ deferFocus: true })
      }
    },
      icoSpan(icon),
      h('span', label),
      countEl
    )
  }

  /** 新建文件夹：默认建在「当前浏览的目录」下面（不在目录里就建在根） */
  async function createFolderPrompt (tree) {
    const parent = getState().query.folder || ''
    const name = await foldersUi.askName({
      title: parent ? '在「' + foldersUi.labelOf(parent) + '」下新建文件夹' : '新建文件夹',
      label: '文件夹名',
      placeholder: '例如：电催化'
    })
    if (!name) return
    const created = await foldersUi.createFolder(parent, name)
    if (created && tree) await tree.reload()
  }

  // 只保留一份「当前侧栏树」的引用：变更事件统一刷新它，避免每次渲染都挂监听造成泄漏
  let activeFolderTree = null

  // 「分类文件夹」是否收起：侧栏每次切页都会重绘，状态必须放模块级并持久化，
  // 否则点一下导航就又展开了（用户的分类多起来时很烦）
  let foldersCollapsed = (() => {
    try { return localStorage.getItem('lv.foldersCollapsed') === '1' } catch (err) { return false }
  })()

  function setFoldersCollapsed (next) {
    foldersCollapsed = !!next
    try { localStorage.setItem('lv.foldersCollapsed', foldersCollapsed ? '1' : '0') } catch (err) { /* 隐私模式下忽略 */ }
  }

  /** 折叠/展开：只切 class，不重画侧栏（重画会丢树的展开状态与滚动位置） */
  function toggleFolders (btn) {
    setFoldersCollapsed(!foldersCollapsed)
    const box = btn.closest('.side-folders')
    if (box) box.classList.toggle('is-collapsed', foldersCollapsed)
    btn.setAttribute('aria-expanded', foldersCollapsed ? 'false' : 'true')
    btn.title = foldersCollapsed ? '展开分类文件夹' : '收起分类文件夹'
  }

  function renderSidebar (activePath) {
    const folderTree = foldersUi.folderTree({ mode: 'side', active: getState().query.folder })
    activeFolderTree = folderTree
    // 先用缓存画（可能为空），再异步拉一次真实目录树
    foldersUi.load().then(() => { if (activeFolderTree === folderTree) folderTree.reload() }).catch(() => {})
    const totalEl = navItem('全部文献', '#/', '▤', state.stats ? state.stats.total : null, activePath === '/', 'total')
    const starredEl = navItem('我的收藏', '#/starred', '★',
      state.stats && state.stats.starred ? state.stats.starred : null, activePath === '/starred', 'starred')
    const footStats = h('span', state.stats ? `共 ${state.stats.total} 条 · ${state.stats.tags} 个标签` : '读取中…')
    sidebarCountEls.foot = footStats
    const footMissing = h('span', state.stats && state.stats.pdfMissing ? `⚠ ${state.stats.pdfMissing} 个 PDF 丢失` : '')
    sidebarCountEls.missing = footMissing
    return h('aside.sidebar', { id: 'sidebar', 'aria-label': '侧栏' },
      // 浮层抽屉里给一个显式关闭按钮（宽屏隐藏）：触屏用户不必去够顶栏那个开关
      h('button.side-close', {
        type: 'button', 'aria-label': '关闭导航栏', title: '关闭导航栏（Esc）',
        onclick: () => closeDrawer()
      }, '✕'),
      // 品牌区：标记 + （字标 + NEXUS 胶囊）+ 版本号。
      // 版本号仍在字标下方这一行（跟改造前同一个位置），只是收成 v<major>.<minor>——
      // 产品线名已经由胶囊表达，再写一遍是重复；整块高度仍由 logo 小块（36px）决定，导航位置不变。
      h('div.brand', {
        title: UI_VERSION ? 'PaperLibrary ' + UI_VERSION + ' Nexus' : 'PaperLibrary Nexus',
        'aria-label': UI_VERSION ? 'PaperLibrary ' + UI_VERSION + ' Nexus' : 'PaperLibrary Nexus'
      },
        // logo 是图片（自带浅灰底），放在同底色的小圆角块里，图与块之间不露接缝
        h('div.brand-mark', h('img', { src: '/paperlibrary-mark.png', alt: 'PaperLibrary', draggable: 'false' })),
        // 名称按 logo 的配色：Paper 深蓝、Library 青色（颜色在 CSS 变量里，夜间主题会换成亮一点的同色系）
        h('div.brand-name',
          h('div.brand-row',
            h('div.brand-word',
              h('span.brand-navy', 'Paper'),
              h('span.brand-teal', 'Library')
            ),
            // 产品线标记：跟字标同一行、字号小一档，不抢字标的视觉重心
            h('span.brand-badge', 'NEXUS')
          ),
          h('span.brand-ver', UI_VERSION)
        )
      ),
      h('nav.nav', { 'aria-label': '主导航' }, totalEl, starredEl,
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
      // 分类文件夹：和导航同处左侧栏，但它是「树」，单独一块更好读。
      // 新建 / 管理两个入口放在这一块的表头，符合「整合到分类与导航区域」的要求。
      h('div.side-folders' + (foldersCollapsed ? '.is-collapsed' : ''),
        h('div.side-folders-head',
          // 标题本身就是展开/收起开关（带 ▾），跟导航项一样整行可点
          h('button.side-folders-toggle', {
            type: 'button',
            'aria-expanded': foldersCollapsed ? 'false' : 'true',
            'aria-controls': 'side-folders-body',
            title: foldersCollapsed ? '展开分类文件夹' : '收起分类文件夹',
            onclick: ev => toggleFolders(ev.currentTarget)
          },
            h('span.side-folders-caret', { html: ICON_CARET }),
            h('span.side-folders-title', '分类文件夹')
          ),
          h('button.side-folders-btn', {
            type: 'button', title: '新建文件夹', 'aria-label': '新建文件夹',
            html: ICON_PLUS,
            onclick: () => createFolderPrompt(folderTree)
          }),
          h('button.side-folders-btn', {
            type: 'button', title: '管理文件夹（重命名 / 删除）', 'aria-label': '管理文件夹',
            html: ICON_GEAR,
            onclick: () => foldersUi.openFolderManager()
          })
        ),
        h('div.side-folders-body', { id: 'side-folders-body' }, folderTree.el)
      ),
      // 底部区块：文档入口 + 统计/提示。用 margin-top:auto 整体贴底，
      // 这样入口正好落在「共 N 条 · N 个标签」上方那一块，而不是挤在功能导航里
      h('div.side-bottom',
        // 智能问答助手：唯一的入口就是这一行（紧贴「使用文档」上方），点开在左下角弹对话框。
        // 对话不放在侧栏里：侧栏只有 222px，放不下 Markdown 回答的正文宽度。
        // 窄屏点它是「开抽屉再开面板」，所以顺手把抽屉收起来，别让遮罩挡住对话框。
        copilotSideEntry({ onOpen: () => closeDrawer({ deferFocus: true }) }),
        h('button.side-doc' + (activePath === '/guide' ? '.active' : ''), {
          type: 'button',
          'data-nav': '/guide',
          'aria-current': activePath === '/guide' ? 'page' : null,
          onclick: () => {
            go('#/guide')
            closeDrawer({ deferFocus: true })
          }
        },
          icoSpan('📖'),
          h('span', '使用文档')
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
      // 侧栏抽屉开关：任何尺寸都显示（窄屏开浮层抽屉，宽屏收起/展开停靠侧栏）。
      // aria-expanded / aria-label / title 统一由 syncSidebar() 维护。
      h('button.btn.icon.side-toggle', {
        type: 'button', id: 'side-toggle', 'aria-controls': 'sidebar',
        onclick: () => toggleSidebar()
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
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 'b') {
        if (typing) return
        ev.preventDefault()
        toggleSidebar()
        return
      }
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === 's') {
        const form = document.querySelector('form[data-paper-form]')
        if (form) { ev.preventDefault(); form.requestSubmit() }
        return
      }
      // Esc 收起浮层抽屉（焦点回到开关）；输入框里按 Esc 仍旧是"清空输入"的语义
      if (ev.key === 'Escape' && !typing && inOverlayMode() && drawerOpen()) {
        ev.preventDefault()
        closeDrawer()
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
    // 智能问答助手：每次进入都会拉一次概览（就绪状态 + 库藏统计）
    add('/copilot', copilotHandler)
    // 使用文档：静态页，不依赖任何接口，所以放在最后注册（不会被 :id 之类吃掉）
    add('/guide', guideHandler)
  }

  /* 在线心跳：每个页面挂一条 SSE（/api/presence）。
     服务端据此知道「还有页面开着」——全部关掉 30 秒后自动停机，不用再手动点「停止服务」。
     EventSource 自带断线重连，所以刷新、切走再回来、短断网都不会被误判成「页面关了」；
     从 bfcache 前进/后退回来时（pagehide 已经关掉了连接）要重新挂上。 */
  function watchPresence () {
    if (typeof EventSource !== 'function') return
    let source = null
    const open = () => {
      if (source) return
      try {
        source = new EventSource('/api/presence')
        // 服务端已退出时这里会连续报错；交给 EventSource 自己退避重试，不打扰用户
        source.onerror = () => {}
      } catch {
        source = null
      }
    }
    const close = () => {
      if (!source) return
      try { source.close() } catch {}
      source = null
    }
    window.addEventListener('pagehide', close)
    window.addEventListener('pageshow', open)
    open()
  }

  async function boot () {
    applyTheme(state.prefs.theme)
    // 版本先取再画：品牌区/标题/文档页要用同一个值，取不到就显示无版本号
    try {
      const info = await api.getVersion()
      applyUiVersion(info && info.ui ? info.ui : '')
    } catch {}
    watchPresence()
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
        // 遮罩放在侧栏与主区之间：z-index 44 压住顶栏、45 的侧栏压在它上面
        h('div.side-scrim', {
          id: 'side-scrim', 'aria-hidden': 'true',
          onclick: () => closeDrawer()
        }),
        h('main.main',
          renderTopbar('/'),
          h('div', { id: 'outlet' })
        )
      )
    )
    app.hidden = false
    // 首屏就把抽屉状态贴上去：宽屏沿用上次的收起/展开，窄屏默认收起
    syncSidebar()
    // 左下角的问答对话框：挂在 body 上而不是 #app 里，路由重建外壳时不会跟着被换掉
    mountCopilotDock()
    const bootEl = document.getElementById('boot')
    if (bootEl) bootEl.remove()
    patch({ booted: true })

    // 自动检查更新（阶段 1：只通知，不下载）：默认开（产品已确认），设置页可关；失败静默、24 小时节流。
    // 定时器只活在页面里：关掉页面/应用后不再检查，也不起任何后台进程。
    const autoChecker = createAutoChecker({
      check: () => api.checkUpdate(),
      notify: latest => toast('发现新版本 ' + (latest && latest.tag ? latest.tag : '') +
        '，可在「设置 → 关于」查看'),
      autoDownload: () => autoDownloadEnabled(getState().prefs),
      download: () => api.startUpdateDownload(),
      onAutoDownload: latest => toast('已开始自动下载 ' + (latest && latest.tag ? latest.tag : '') +
        '，完成后请在设置页点击「立即安装」（不会静默安装）'),
      enabled: () => autoCheckEnabled(getState().prefs),
      getLastCheckAt: () => Number(getState().prefs.updateLastCheckAt) || 0,
      saveLastCheckAt: ts => {
        patch({ prefs: { updateLastCheckAt: ts } })
        api.savePrefs({ updateLastCheckAt: ts }).catch(() => {})
      },
      getSkipped: () => getState().prefs.updateSkippedVersion || '',
      onError: () => {}
    })
    // 暴露给验收脚本/排错（不参与业务逻辑）
    window.__paperUpdateAuto = autoChecker
    autoChecker.start()
    document.addEventListener('update:auto-changed', ev => {
      autoChecker.setEnabled(!!(ev && ev.detail && ev.detail.enabled))
    })

    // 只监听 hashchange（用户手改地址、前进后退）；应用内导航统一走 go() → scheduleRender
    window.addEventListener('hashchange', () => scheduleRender())
    bindShortcuts()
    // 文件夹增删改 → 刷新侧栏那棵树（保留展开状态与当前选中）
    document.addEventListener('folders:changed', () => {
      if (activeFolderTree) activeFolderTree.reload()
    })
    if (!location.hash) {
      location.hash = '#/'
      scheduleRender()
      return
    }
    await render()
  }

  export { boot }
