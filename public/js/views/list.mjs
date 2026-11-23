/* 书库主页（文件夹卡片网格）与文献列表页（浏览器式子文件夹标签页 / 全部文献 / 我的收藏） */
import * as util from '../util.mjs'
import * as components from '../components.mjs'
import { api } from '../api.mjs'
import { getState } from '../state/store.mjs'
import { applyQueryFromUrl, effectiveQuery, filterByTag, go, queryToParams, refreshMeta, rememberTitles, setHash } from '../shell/app-api.mjs'
import * as foldersUi from '../folders.mjs'

  const { h, toast, confirmDialog } = util
  const { paperCard, paperRow } = components

  /**
   * 批量选择状态（在批量模式里选了哪几条）。
   * 放在模块级：切视图、改筛选、翻页都不会清，只在真的离开书库面板时由 resetListBatch 清空。
   */
  let batch = { selectMode: false, selected: new Set() }

  /** 大号文件夹 / 未分类 SVG 图标（用于文件夹主页卡片） */
  function bigFolderSvg (kind) {
    const body = kind === 'stack'
      ? '<rect x="4.5" y="4" width="15" height="4" rx="1.4"/>' +
        '<rect x="3.5" y="10" width="17" height="4" rx="1.4"/>' +
        '<rect x="2.5" y="16" width="19" height="4.5" rx="1.4"/>'
      : kind === 'plus'
        ? '<path d="M12 5v14M5 12h14"/>'
        : '<path d="M3 6.5A2.2 2.2 0 0 1 5.2 4.3h4.2l2.1 2.5h7.3A2.2 2.2 0 0 1 21 9v8.8a2.2 2.2 0 0 1-2.2 2.2H5.2A2.2 2.2 0 0 1 3 17.8z"/>'
    return h('span.fld-home-ico-svg', {
      html: '<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" ' +
        'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>'
    })
  }

  function findNodeByPath (nodes, targetPath) {
    for (const node of nodes || []) {
      if (node.path === targetPath) return node
      if (node.children && node.children.length) {
        const found = findNodeByPath(node.children, targetPath)
        if (found) return found
      }
    }
    return null
  }

  /**
   * 「空库」的起步板：三条真实可用的入库路径，点整张卡片就能去对应页面。
   */
  function emptyStart () {
    const way = (icon, no, title, desc, goText, onclick) => h('button.start-way', {
      type: 'button',
      onclick
    },
      h('div.start-way-ico', icon),
      h('div.start-way-h', h('span.n', no + ' '), title),
      h('div.start-way-d', desc),
      h('div.start-go', goText + ' →')
    )

    const cap = (icon, title, desc) => h('div.start-cap',
      h('span.start-cap-ico', icon),
      h('span.start-cap-t', title),
      h('span.start-cap-d', desc)
    )

    return h('div.start',
      h('div.start-mark', h('img', { src: '/paperlibrary-mark.png', alt: '', draggable: 'false' })),
      h('h2.start-title', '欢迎使用 PaperLibrary'),
      h('p.start-sub', '当前文献库还没有文献。挑一种方式开始，半分钟就能建好第一条：'),
      h('div.start-ways',
        way('📥', '方式一', '直接拖入 PDF',
          '把下载好的 PDF 拖进新建页的虚线框，题录与正文自动解析，一次可以拖多篇。',
          '去新建页', () => go('#/new')),
        way('🗂', '方式二', '放进文件夹后扫描',
          '把已分类的 PDF 放进 data/article/papers/ 下的子文件夹，重新扫描后按目录入库。直接放在根目录的 PDF 归入未分类。',
          '打开扫描', () => go('#/settings?panel=assets-pdf')),
        way('🔗', '方式三', '粘贴 DOI 抓题录',
          '还没下载原文也行：粘一个 DOI 回车，标题、作者、期刊、摘要自动补齐。',
          '去粘贴 DOI', () => go('#/new'))
      ),
      h('div.start-caps',
        cap('🔍', '题录 + 全文检索', '中英混搜、#标签、字段与布尔'),
        cap('🤖', 'AI 总结与对话', '配了模型即可用'),
        cap('🖼', '图表画廊', '按图注跨文献找图'),
        cap('📊', '横向对比', '2–8 篇按模板出表')
      )
    )
  }

  /** 离开列表页时清掉（app.js 里的页面切换会调），免得勾选跟着跑到别的页面 */
  function resetListBatch () {
    batch = { selectMode: false, selected: new Set() }
  }

  /** 新建文件夹弹窗（可指定父目录） */
  async function promptCreateFolder (parent) {
    const name = await foldersUi.askName({
      title: parent ? '在「' + foldersUi.labelOf(parent) + '」下新建子文件夹' : '新建文件夹',
      label: '文件夹名',
      placeholder: parent ? '例如：自旋与磁场' : '例如：电催化'
    })
    if (!name) return null
    return foldersUi.createFolder(parent || '', name)
  }

  /* ==================== 文件夹主页（#/ 默认视图） ==================== */

  function renderFolderHomeView (ctx) {
    const { outlet, isStale, signal } = ctx
    const host = h('div.content')
    outlet.replaceChildren(host)

    async function paint () {
      let data
      try {
        data = await foldersUi.load()
      } catch (err) {
        if (isStale()) return
        host.replaceChildren(h('div.empty',
          h('div.big', '读取文件夹失败'),
          h('p', { text: err.message }),
          h('button.btn', { type: 'button', onclick: paint }, '重试')))
        return
      }
      if (isStale()) return

      const nodes = foldersUi.buildTree(data)
      const total = data.total || 0
      const rootCount = data.rootCount || 0

      // 真正的空库（0 篇文献且 0 个文件夹）：仍展示新手起步板
      if (total === 0 && nodes.length === 0) {
        host.replaceChildren(emptyStart())
        return
      }

      const subText = nodes.length
        ? `选择一个分类文件夹进入查看文献 · 共 ${nodes.length} 个一级分类，${total} 篇文献`
        : `还没有建立分类文件夹 · 共 ${total} 篇文献在未分类中，可先新建分类或点下方卡片整理`

      const head = h('div.fld-home-head',
        h('div.fld-home-heading',
          h('h1.fld-home-title', '主页'),
          h('p.fld-home-sub', subText)
        ),
        h('div.fld-home-actions',
          h('button.btn.sm', {
            type: 'button',
            onclick: async () => { await promptCreateFolder('') }
          }, '＋ 新建文件夹'),
          h('button.btn.sm', {
            type: 'button',
            onclick: () => foldersUi.openFolderManager()
          }, '⚙ 管理文件夹')
        )
      )

      const cards = nodes.map(node => {
        const kids = node.children || []
        const previewKids = kids.slice(0, 4).map(k => h('span.fld-home-kid', k.name))
        if (kids.length > 4) previewKids.push(h('span.fld-home-kid.is-more', `+${kids.length - 4}`))
        return h('button.fld-home-card', {
          type: 'button',
          title: `进入「${node.name}」`,
          onclick: () => foldersUi.navTo(node.path)
        },
          h('div.fld-home-card-top',
            h('div.fld-home-ico', bigFolderSvg('folder')),
            h('span.fld-home-count', `${node.countDeep} 篇`)
          ),
          h('div.fld-home-name', node.name),
          h('div.fld-home-meta',
            h('span', kids.length ? `含 ${kids.length} 个子文件夹` : '直接存放文献'),
            h('span.fld-home-enter', '进入 →')
          ),
          kids.length ? h('div.fld-home-kids', previewKids) : null
        )
      })

      // 只要有未分类文献就默认展示独立虚线样式的「未分类」卡片
      if (rootCount > 0) {
        cards.push(h('button.fld-home-card.fld-home-card--uncat', {
          type: 'button',
          title: '查看尚未归入文件夹的文献，可批量移动整理',
          onclick: () => foldersUi.navTo('')
        },
          h('div.fld-home-card-top',
            h('div.fld-home-ico.is-muted', bigFolderSvg('stack')),
            h('span.fld-home-count.is-muted', `${rootCount} 篇`)
          ),
          h('div.fld-home-name', foldersUi.ROOT_LABEL),
          h('div.fld-home-meta',
            h('span', '尚未归入文件夹，点进可批量归类'),
            h('span.fld-home-enter', '整理 →')
          )
        ))
      }

      // 还没有任何分类文件夹时，附带一张温和的新建入口卡，避免页面只孤零零留一张未分类
      if (!nodes.length) {
        cards.push(h('button.fld-home-card.fld-home-card--new', {
          type: 'button',
          onclick: async () => { await promptCreateFolder('') }
        },
          h('div.fld-home-card-top',
            h('div.fld-home-ico', bigFolderSvg('plus'))
          ),
          h('div.fld-home-name', '新建第一个分类文件夹'),
          h('div.fld-home-meta',
            h('span', '按课题、方向或项目建立目录，支持多级子文件夹'),
            h('span.fld-home-enter', '新建 →')
          )
        ))
      }

      host.replaceChildren(head, h('div.fld-home-grid', cards))
    }

    const onChanged = () => { if (!isStale()) paint() }
    document.addEventListener('folders:changed', onChanged, { signal })
    paint()
  }

  /* ==================== 主路由处理器 ==================== */

  function listHandler (ctx) {
    const { outlet, params, isStale, signal } = ctx
    applyQueryFromUrl(params)
    const state = getState()
    const listRoute = ctx.path === '/all' ? 'all' : (ctx.path === '/starred' ? 'starred' : '')
    // 「我的收藏」是一个独立地址（#/starred），进来自动只看收藏
    if (ctx.path === '/starred') state.query.starred = true
    if (ctx.path === '/all') {
      state.query.folder = null
      state.query.deep = false
    }

    /** 判断当前有没有任何筛选条件（关键词、标签、类型、收藏、目录） */
    function hasAnyFilter (q) {
      const query = q || state.query
      return !!(query.q || (query.tags || []).length || (query.extraTags || []).length ||
        (query.type && query.type !== 'all') || query.starred || query.folder != null)
    }

    /** 除文件夹本身之外的额外筛选条件 */
    function hasExtraFilter (q) {
      const query = q || state.query
      return !!(query.q || (query.tags || []).length || (query.extraTags || []).length ||
        (query.type && query.type !== 'all') || query.starred)
    }

    // 主页（#/）且无任何筛选/目录参数 → 展示「文件夹主页」卡片网格
    if (ctx.path === '/' && !hasAnyFilter(state.query)) {
      renderFolderHomeView(ctx)
      return
    }

    const folderBar = h('div', { id: 'list-folder', hidden: true })
    const folderTabs = h('div', { id: 'list-folder-tabs', hidden: true })
    const subFolderBar = h('div', { id: 'list-subfolders', hidden: true })
    const bar = h('div', { id: 'list-bar', style: { marginBottom: '14px', display: 'grid', gap: '10px' } })
    const results = h('div', { id: 'list-results' })
    const pager = h('div', { id: 'list-pager' })
    const tabPanel = h('div.fld-tab-panel', subFolderBar, bar, results, pager)
    const tabShell = h('div.fld-tab-shell', folderTabs, tabPanel)
    const content = h('div.content', folderBar, tabShell)
    outlet.replaceChildren(content)

    let items = []
    let loading = false
    let folderTreeNodes = []
    const batchState = batch
    const selected = batchState.selected
    const isSelectMode = () => batchState.selectMode

    /* ---------- 目录面包屑 + 浏览器式子文件夹标签页 ---------- */

    function renderFolderChrome () {
      const current = state.query.folder
      if (current == null) {
        folderBar.hidden = true
        folderBar.replaceChildren()
        folderTabs.hidden = true
        folderTabs.replaceChildren()
        subFolderBar.hidden = true
        subFolderBar.replaceChildren()
        tabShell.classList.remove('has-tabs')
        return
      }

      // 1) 顶部面包屑栏
      folderBar.hidden = false
      const segs = current ? current.split('/') : []
      const crumbs = [
        h('button.fld-crumb', { type: 'button', title: '回到主页', onclick: () => go('') }, '主页')
      ]
      if (!segs.length) {
        crumbs.push(h('span.fld-crumb-sep', '/'))
        crumbs.push(h('span.fld-crumb.current', foldersUi.ROOT_LABEL))
      } else {
        segs.forEach((seg, idx) => {
          const prefix = segs.slice(0, idx + 1).join('/')
          crumbs.push(h('span.fld-crumb-sep', '/'))
          if (idx === segs.length - 1) {
            crumbs.push(h('span.fld-crumb.current', seg))
          } else {
            crumbs.push(h('button.fld-crumb', {
              type: 'button',
              onclick: () => foldersUi.navTo(prefix)
            }, seg))
          }
        })
      }

      const rightAction = current !== ''
        ? h('button.btn.sm.ghost', {
          type: 'button',
          title: '在当前目录下新建子文件夹',
          onclick: async () => { await promptCreateFolder(current) }
        }, '＋ 新建子文件夹')
        : h('span.fld-crumbs-tip', '提示：点下方「批量」可将未分类文献移入文件夹')

      folderBar.replaceChildren(h('div.fld-crumbs',
        h('button.btn.sm.ghost.fld-back-btn', {
          type: 'button',
          title: '返回主页',
          onclick: () => go('')
        }, '← 主页'),
        h('span.fld-crumbs-div', '|'),
        h('span.fld-crumbs-ico', '📂'),
        ...crumbs,
        h('span', { style: { flex: '1 1 auto' } }),
        rightAction
      ))

      // 2) 浏览器式子文件夹标签页（样式一：顶层同级标签常驻，原地切换；默认「仅当前目录直属」）
      const rootPath = segs[0] || ''
      const rootNode = rootPath ? findNodeByPath(folderTreeNodes, rootPath) : null
      if (!rootNode || !rootNode.children || !rootNode.children.length) {
        folderTabs.hidden = true
        folderTabs.replaceChildren()
        subFolderBar.hidden = true
        subFolderBar.replaceChildren()
        tabShell.classList.remove('has-tabs')
        return
      }

      tabShell.classList.add('has-tabs')
      folderTabs.hidden = false
      const isRootExact = current === rootNode.path
      const isDeep = !!state.query.deep

      const tabButtons = [
        // 默认选中项：仅当前目录直属
        h('button.fld-btab' + (isRootExact && !isDeep ? '.active' : ''), {
          type: 'button',
          role: 'tab',
          'aria-selected': isRootExact && !isDeep ? 'true' : 'false',
          title: `仅查看直接放在「${rootNode.name}」下的文献`,
          onclick: () => foldersUi.navTo(rootNode.path, { deep: false })
        },
          h('span', `仅${rootNode.name}直属`),
          h('span.fld-btab-count', String(rootNode.count))
        ),
        // 含子目录全部
        h('button.fld-btab' + (isRootExact && isDeep ? '.active' : ''), {
          type: 'button',
          role: 'tab',
          'aria-selected': isRootExact && isDeep ? 'true' : 'false',
          title: `查看「${rootNode.name}」及所有子文件夹的全部文献`,
          onclick: () => foldersUi.navTo(rootNode.path, { deep: true })
        },
          h('span', '全部（含子目录）'),
          h('span.fld-btab-count', String(rootNode.countDeep))
        ),
        // 二级子文件夹标签页
        ...rootNode.children.map(child => {
          const isActive = current === child.path || current.startsWith(child.path + '/')
          return h('button.fld-btab' + (isActive ? '.active' : ''), {
            type: 'button',
            role: 'tab',
            'aria-selected': isActive ? 'true' : 'false',
            title: child.children.length
              ? `${child.name}（直属 ${child.count} 篇，含子目录共 ${child.countDeep} 篇）`
              : `${child.name}（${child.count} 篇）`,
            onclick: () => foldersUi.navTo(child.path, { deep: false })
          },
            foldersUi.folderIco(isActive ? 'open' : 'closed'),
            h('span', child.name),
            h('span.fld-btab-count', String(child.countDeep))
          )
        })
      ]
      folderTabs.replaceChildren(h('div.fld-btabs', { role: 'tablist', 'aria-label': '子文件夹标签页' }, tabButtons))

      // 3) 更深层级（三级及以上）子文件夹胶囊条：保留上排浏览器标签不动，在面板顶部按层级自然展开
      const subRows = []
      for (let i = 1; i < segs.length; i++) {
        const ancestorPath = segs.slice(0, i + 1).join('/')
        const node = findNodeByPath(folderTreeNodes, ancestorPath)
        if (!node || !node.children || !node.children.length) continue
        const isExact = current === node.path
        const pills = [
          h('span.fld-subbar-label', `「${node.name}」子目录：`),
          h('button.fld-subpill' + (isExact && !isDeep ? '.active' : ''), {
            type: 'button',
            onclick: () => foldersUi.navTo(node.path, { deep: false })
          }, `仅直属 ${node.count}`),
          h('button.fld-subpill' + (isExact && isDeep ? '.active' : ''), {
            type: 'button',
            onclick: () => foldersUi.navTo(node.path, { deep: true })
          }, `全部 ${node.countDeep}`),
          ...node.children.map(kid => {
            const kidActive = current === kid.path || current.startsWith(kid.path + '/')
            return h('button.fld-subpill' + (kidActive ? '.active' : ''), {
              type: 'button',
              onclick: () => foldersUi.navTo(kid.path, { deep: false })
            },
              foldersUi.folderIco(kidActive ? 'open' : 'closed'),
              h('span', `${kid.name} ${kid.countDeep}`)
            )
          })
        ]
        subRows.push(h('div.fld-subbar', pills))
      }

      if (subRows.length) {
        subFolderBar.hidden = false
        subFolderBar.replaceChildren(...subRows)
      } else {
        subFolderBar.hidden = true
        subFolderBar.replaceChildren()
      }
    }

    /* ---------- 顶部筛选条 ---------- */

    function renderBar (info) {
      const q = state.query
      const typeSelect = h('select.select', {
        title: '按类型筛选',
        onchange: ev => { state.query.type = ev.target.value; state.query.page = 1; setHash(listRoute, queryToParams()) }
      },
        h('option', { value: 'all', selected: q.type === 'all' }, '全部类型'),
        ...Object.entries(util.TYPE_LABELS).map(([key, label]) =>
          h('option', { value: key, selected: q.type === key }, label))
      )
      const starToggle = h('button.btn.sm' + (q.starred ? '.primary' : ''), {
        type: 'button',
        title: q.starred ? '取消只看收藏' : '只看我收藏的',
        onclick: () => {
          state.query.starred = !state.query.starred
          state.query.page = 1
          const nextRoute = state.query.starred ? 'starred' : (state.query.folder != null ? '' : 'all')
          setHash(nextRoute, queryToParams())
        }
      }, (q.starred ? '★' : '☆') + ' 收藏')

      const tagChips = q.tags.map(tag => h('button.tag-chip.active', {
        type: 'button',
        title: '移除该筛选',
        onclick: () => {
          state.query.tags = state.query.tags.filter(t => t !== tag)
          state.query.page = 1
          setHash(listRoute, queryToParams())
        }
      }, tag, h('span.n', '×')))

      const summary = []
      const showCount = hasAnyFilter(q) || ctx.path === '/all'
      if (!info) {
        summary.push('正在读取…')
      } else if (showCount) {
        if (ctx.path === '/all' && !hasExtraFilter(q)) summary.push(`全部文献 · 共 ${info.total} 条`)
        else summary.push(`${info.total} 条结果`)
        if (q.q) summary.push(`关键词「${q.q}」`)
        if (info.pageCount > 1) summary.push(`第 ${info.page} / ${info.pageCount} 页`)
      }

      const showReset = state.query.folder != null ? hasExtraFilter(q) : hasAnyFilter(q)
      const resetBtn = showReset
        ? h('button.btn.sm.ghost', {
          type: 'button',
          onclick: () => {
            const keepFolder = state.query.folder
            const keepDeep = state.query.deep
            state.query = {
              q: '', qRaw: '', tags: [], extraTags: [], type: 'all', starred: false,
              sort: state.query.sort, page: 1,
              folder: keepFolder, deep: keepDeep
            }
            const nextRoute = keepFolder != null ? '' : (ctx.path === '/all' || ctx.path === '/starred' ? 'all' : '')
            setHash(nextRoute, queryToParams())
          }
        }, '清空筛选')
        : null

      if (info && info.total === 0 && !hasAnyFilter(q) && !q.q && ctx.path !== '/all') {
        util.replace(bar)
        bar.style.display = 'none'
        return
      }
      bar.style.display = ''
      util.replace(bar,
        h('div.list-bar',
          h('span.list-summary', { text: summary.join(' · ') }),
          h('span', { style: { flex: '1 1 auto' } }),
          h('div.list-controls', typeSelect, starToggle,
            h('button.btn.sm' + (isSelectMode() ? '.primary' : ''), {
              type: 'button',
              title: '进入批量操作模式',
              onclick: () => {
                batchState.selectMode = !batchState.selectMode
                if (!batchState.selectMode) selected.clear()
                renderBar(info)
                renderResults()
              }
            }, isSelectMode() ? `批量中（${selected.size}）` : '批量'),
            resetBtn
          )
        ),
        tagChips.length ? h('div', { style: { display: 'flex', gap: '5px', flexWrap: 'wrap', alignItems: 'center' } },
          h('span', { style: { fontSize: '12.5px', color: 'var(--faint)' } }, '标签筛选：'), tagChips) : null
      )
    }

    /* ---------- 结果区 ---------- */

    function renderResults () {
      if (loading) {
        results.replaceChildren(h('div.empty', h('div.big', '正在检索…')))
        return
      }
      if (!items.length) {
        const q = state.query
        if (!hasAnyFilter(q)) {
          results.replaceChildren(emptyStart())
          return
        }
        // 目录「仅当前目录直属」下无直属文献，但子文件夹里有文献 → 给出友好引导
        if (q.folder && !q.deep && !hasExtraFilter(q)) {
          const curNode = findNodeByPath(folderTreeNodes, q.folder)
          if (curNode && curNode.countDeep > 0) {
            results.replaceChildren(h('div.empty',
              h('div.big', `「${curNode.name}」当前目录暂无直属文献`),
              h('p', { text: `它的子文件夹中共有 ${curNode.countDeep} 篇文献，可点上方子文件夹标签页查看，或一键展开全部。` }),
              h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'center' } },
                h('button.btn.primary', {
                  type: 'button',
                  onclick: () => foldersUi.navTo(q.folder, { deep: true })
                }, `查看全部（含子目录 ${curNode.countDeep} 篇）`),
                h('button.btn', { type: 'button', onclick: () => go('#/new') }, '＋ 新增文献')
              )
            ))
            return
          }
        }
        results.replaceChildren(h('div.empty',
          h('div.big', q.folder != null && !hasExtraFilter(q) ? '这个文件夹里还没有文献' : '没有匹配的文献'),
          h('p', { text: q.folder != null && !hasExtraFilter(q) ? '可以新增文献导入到这里，或把其他文献移动到此文件夹。' : '试试换个关键词，或清空筛选条件。' }),
          h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', justifyContent: 'center' } },
            h('button.btn.primary', { type: 'button', onclick: () => { go('#/new') } }, '＋ 新增文献'),
            hasExtraFilter(q) ? h('button.btn', {
              type: 'button',
              onclick: () => {
                const keepFolder = state.query.folder
                const keepDeep = state.query.deep
                state.query = {
                  q: '', qRaw: '', tags: [], extraTags: [], type: 'all', starred: false,
                  sort: state.query.sort, page: 1, folder: keepFolder, deep: keepDeep
                }
                setHash(keepFolder != null ? '' : (ctx.path === '/all' || ctx.path === '/starred' ? 'all' : ''), queryToParams())
              }
            }, '清空筛选') : null
          )
        ))
        return
      }
      const common = {
        selectable: isSelectMode(),
        selected: false,
        onTag: tag => {
          if (filterByTag) filterByTag(tag, { resetType: true, resetStarred: true })
        },
        onFolder: paper => changeFolder(paper),
        onSelect: (id, checked) => {
          if (checked) selected.add(id)
          else selected.delete(id)
          renderBar(lastInfo)
          renderBulkBar()
          renderResults()
        }
      }
      if (state.prefs.view === 'rows') {
        results.replaceChildren(h('div.rows', items.map(item =>
          paperRow(item, Object.assign({}, common, { selected: selected.has(item.id) })))))
      } else {
        results.replaceChildren(h('div.cards', items.map(item =>
          paperCard(item, Object.assign({}, common, { selected: selected.has(item.id) })))))
      }
      renderBulkBar()
    }

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
            load()
          } catch (err) {
            toast('移动失败：' + (err && err.message ? err.message : err))
          }
        }
      })
    }

    function moveSelectedToFolder () {
      const ids = [...selected]
      if (!ids.length) return
      const refresh = () => { renderBar(lastInfo); renderResults() }
      foldersUi.openFolderPicker({
        current: getState().query.folder || '',
        onPick: async path => {
          const where = path ? path.split('/').pop() : foldersUi.ROOT_LABEL
          const known = items.filter(i => selected.has(i.id))
          if (known.length === ids.length && known.every(i => (i.folder || '') === path)) {
            toast('这些文献已经在「' + where + '」里，没有移动', 'warn')
            selected.clear()
            refresh()
            return
          }
          try {
            const res = await api.movePapers(ids, path)
            const moved = res && typeof res.moved === 'number' ? res.moved : ids.length
            if (!moved) {
              toast('这些文献已经在「' + where + '」里，没有移动', 'warn')
              selected.clear()
              refresh()
              return
            }
            toast('已把 ' + moved + ' 篇移到「' + where + '」')
            selected.clear()
            foldersUi.notifyChanged()
            refreshMeta().then(() => load()).catch(() => load())
          } catch (err) {
            toast('移动失败：' + (err && err.message ? err.message : err), 'error')
          }
        }
      })
    }

    let bulkBar = null
    function renderBulkBar () {
      if (bulkBar) { bulkBar.remove(); bulkBar = null }
      if (!isSelectMode()) return
      const pageIds = items.map(i => i.id)
      const allSelected = pageIds.length > 0 && pageIds.every(id => selected.has(id))
      const hiddenCount = pageIds.filter(id => selected.has(id)).length
      const offPage = Math.max(0, selected.size - hiddenCount)
      const none = selected.size === 0
      const refresh = () => { renderBar(lastInfo); renderResults() }
      const action = (label, opts) => h('button.btn.sm' + (opts.danger ? '.danger' : ''), {
        type: 'button',
        title: opts.title || label,
        disabled: none,
        onclick: opts.run
      }, label)

      bulkBar = h('div.bulkbar',
        h('span', none ? (items.length ? '勾选文献或点「全选」' : '本页没有文献')
          : `已选 ${selected.size} 条` + (offPage ? `（其中 ${offPage} 条不在本页）` : '')),
        h('button.btn.sm.ghost', {
          type: 'button',
          title: '清空已勾选的文献（不影响文献本身）',
          disabled: none,
          onclick: () => {
            selected.clear()
            refresh()
          }
        }, '清空选择'),
        h('div.seg-btn', { role: 'group', 'aria-label': '全选或全不选' },
          h('button.seg-part', {
            type: 'button',
            title: items.length ? `全选本页 ${items.length} 条` : '本页没有文献',
            disabled: allSelected || !items.length,
            onclick: () => {
              pageIds.forEach(id => selected.add(id))
              refresh()
            }
          }, '全选'),
          h('button.seg-part', {
            type: 'button',
            title: '取消本页全部选择',
            disabled: none,
            onclick: () => {
              selected.clear()
              refresh()
            }
          }, '全不选')
        ),
        action('加标签', {
          title: '给选中的文献批量添加标签',
          run: async () => {
            const tag = window.prompt('给选中的文献添加标签（多个用逗号分隔）：')
            if (!tag) return
            try {
              await api.bulk([...selected], { tags: tag })
              toast('已添加标签')
              selected.clear()
              refreshMeta().then(() => load())
            } catch (err) { toast(err.message, 'error') }
          }
        }),
        action('★ 收藏', {
          title: '把选中的文献加入收藏（再点一次可取消）',
          run: async () => {
            try {
              const allStarred = items.filter(i => selected.has(i.id)).every(i => i.starred)
              await api.bulk([...selected], { starred: !allStarred })
              toast(allStarred ? '已取消收藏' : '已加入收藏')
              selected.clear()
              load()
              refreshMeta()
            } catch (err) { toast(err.message, 'error') }
          }
        }),
        action('移动到目录', {
          title: '把选中的文献一起移到某个分类文件夹',
          run: () => moveSelectedToFolder()
        }),
        action('复制引用', {
          title: '复制选中第一条的 GB/T 7714 引用',
          run: async () => {
            try {
              const text = await api.cite([...selected][0], 'gb')
              const ok = await util.copyText(text.text)
              toast(ok ? '已复制第一条的 GB/T 7714 引用' : '复制失败', ok ? '' : 'error')
            } catch (err) { toast(err.message, 'error') }
          }
        }),
        action('删除', {
          danger: true,
          title: '删除选中的文献',
          run: async () => {
            const ok = await confirmDialog({
              title: '删除选中的文献？',
              body: `将删除 ${selected.size} 条文献记录，其中关联的 PDF 文件也会一起删除。此操作不可撤销。`,
              okText: '确认删除',
              danger: true
            })
            if (!ok) return
            try {
              for (const id of [...selected]) await api.deletePaper(id)
              toast('已删除 ' + selected.size + ' 条')
              selected.clear()
              batchState.selectMode = false
              foldersUi.notifyChanged()
              load()
              refreshMeta()
            } catch (err) { toast(err.message, 'error') }
          }
        })
      )
      content.appendChild(bulkBar)
    }

    /* ---------- 分页 ---------- */

    function renderPager (info) {
      if (!info || info.pageCount <= 1) {
        pager.replaceChildren()
        return
      }
      const goPage = page => {
        state.query.page = Math.min(Math.max(1, page), info.pageCount)
        setHash(listRoute, queryToParams())
      }
      pager.replaceChildren(h('div.pager',
        h('button.btn.sm', { type: 'button', disabled: info.page <= 1, onclick: () => goPage(info.page - 1) }, '← 上一页'),
        h('span', `第 ${info.page} / ${info.pageCount} 页 · 共 ${info.total} 条`),
        h('button.btn.sm', { type: 'button', disabled: info.page >= info.pageCount, onclick: () => goPage(info.page + 1) }, '下一页 →')
      ))
    }

    /* ---------- 数据加载 ---------- */

    let lastInfo = null
    async function load () {
      loading = true
      renderResults()
      const queryParams = effectiveQuery
        ? effectiveQuery()
        : { q: state.query.q, tags: state.query.tags, page: state.query.page, pageSize: state.prefs.pageSize }
      if (state.query.folder != null) {
        queryParams.includeSubfolders = state.query.deep ? 'true' : 'false'
      }
      try {
        const [res, folderData] = await Promise.all([
          api.listPapers(queryParams),
          state.query.folder != null ? foldersUi.load().catch(() => foldersUi.getCache()) : Promise.resolve(null)
        ])
        if (isStale()) return
        if (folderData) folderTreeNodes = foldersUi.buildTree(folderData)
        items = res.items || []
        lastInfo = res
        rememberTitles(items)
      } catch (err) {
        if (isStale()) return
        items = []
        results.replaceChildren(h('div.empty',
          h('div.big', '读取失败'),
          h('p', { text: err.message }),
          h('button.btn', { type: 'button', onclick: load }, '重试')))
        bar.replaceChildren()
        pager.replaceChildren()
        loading = false
        return
      }
      loading = false
      renderFolderChrome()
      renderBar(lastInfo)
      renderResults()
      renderPager(lastInfo)
    }

    // 文件夹增删改（如在当前目录新建子文件夹、侧栏改名等）时同步刷新标签页与列表
    const onFoldersChanged = () => {
      if (isStale() || state.query.folder == null) return
      load().catch(() => {})
    }
    document.addEventListener('folders:changed', onFoldersChanged, { signal })

    load().catch(err => {
      console.error('[list] 读取失败：', err)
      loading = false
      if (!isStale()) {
        results.replaceChildren(h('div.empty',
          h('div.big', '读取失败'),
          h('p', { text: err.message }),
          h('button.btn', { type: 'button', onclick: () => load() }, '重试')))
      }
    })
  }

  export { listHandler, resetListBatch }
