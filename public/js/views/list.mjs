/* 书库列表页（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from '../util.mjs'
import * as components from '../components.mjs'
import { api } from '../api.mjs'
import { getState } from '../state/store.mjs'
import { applyQueryFromUrl, effectiveQuery, filterByTag, go, queryToParams, refreshMeta, rememberTitles, setHash } from '../shell/app-api.mjs'
import * as foldersUi from '../folders.mjs'

  const { h, toast, confirmDialog, typeLabel } = util
  const { paperCard, paperRow } = components
  
  /**
   * 批量选择状态（在批量模式里选了哪几条）。
   *
   * 必须放在模块级、而不是 listHandler 的闭包里：切换卡片/紧凑视图、排序、
   * 类型筛选、「只看收藏」都会触发整页重绘（渲染指纹里带着 prefs.view，
   * 改筛选还会换一个地址、直接重渲染），闭包里的 Set 每次重绘都会被新建一个空的，
   * 于是「勾了 5 条 → 动一下上面那几个控件 → 全没了，批量模式也退出了」。
   *
   * 只在**真的离开列表面板**时才清（app.js 的页面切换会调 resetListBatch）：
   * 翻页、改筛选、排序这些仍然在「挑文献」的过程里，中途清空等于白勾。
   * 为了让「看不见的勾选」不至于变成一个陷阱，批量条上常驻一个「清空选择」。
   */
  let batch = { selectMode: false, selected: new Set() }

  /** 目录视图里「含子文件夹」开关（默认开：点父目录就该看到全部后代） */
  let folderDeep = true

  /**
   * 「空库」的起步板：三条真实可用的入库路径，点整张卡片就能去对应页面。
   *
   * 为什么不只是一个「新增文献」按钮：新库打开时整页空白，用户既不知道能做什么，
   * 也不知道有几条路可走（拖 PDF / 扫描已有目录 / 粘 DOI）。这里把三条路摆出来，
   * 每条都写明「要动哪里」，空页面就不再是一个死胡同。
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
          '手上已有一堆 PDF：放进 data/article/papers/，到「设置 → 资产与备份 → 原文与补充材料」点「扫描」，自动识别成文献。',
          '打开扫描', () => go('#/settings')),
        way('🔗', '方式三', '粘贴 DOI 抓题录',
          '还没下载原文也行：粘一个 DOI 回车，标题、作者、期刊、摘要自动补齐。',
          '去粘贴 DOI', () => go('#/new'))
      ),
      // 能力一览：库建起来之后能做什么。它收尾整块面板，一行人扫完就知道这软件的价值。
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

  function listHandler (ctx) {
    const { outlet, params, isStale } = ctx
    applyQueryFromUrl(params)
    const state = getState()
    // 「我的收藏」是一个独立地址（#/starred），进来自动只看收藏
    if (ctx.path === '/starred') state.query.starred = true

    /** 判断当前有没有任何筛选条件（关键词、标签、类型、收藏） */
    function hasAnyFilter (q) {
      const query = q || state.query
      return !!(query.q || (query.tags || []).length || (query.extraTags || []).length ||
        (query.type && query.type !== 'all') || query.starred || query.folder != null)
    }

    const bar = h('div', { id: 'list-bar', style: { marginBottom: '14px', display: 'grid', gap: '10px' } })
    // 目录面包屑：只在「按文件夹筛选」时出现，用来看清自己在哪一级、并一键回到上层
    const folderBar = h('div', { id: 'list-folder', hidden: true })
    const results = h('div', { id: 'list-results' })
    const pager = h('div', { id: 'list-pager' })
    // 批量条挂在这一页的容器里（不是 document.body）：这样切到别的页面时
    // 它会跟着列表一起消失，不会留在屏幕上（以前从批量模式点侧栏去别的页，条子还飘在那儿）
    const content = h('div.content', folderBar, bar, results, pager)
    outlet.replaceChildren(content)

    let items = []
    let loading = false
    // 批量状态挂在模块级：切视图、改筛选、翻页都不会清（见文件顶部说明）
    const batchState = batch
    const selected = batchState.selected
    const isSelectMode = () => batchState.selectMode

    /* ---------- 顶部筛选条 ---------- */

    /** 面包屑：全部文献 / A / B  + 「含子文件夹」开关 + 退出目录 */
    function renderFolderBar () {
      const current = state.query.folder
      if (current == null) {
        folderBar.hidden = true
        folderBar.replaceChildren()
        return
      }
      folderBar.hidden = false
      const segs = current ? current.split('/') : []
      const crumbs = [
        h('button.fld-crumb', { type: 'button', title: '回到全部文献', onclick: () => go('') }, '全部文献')
      ]
      segs.forEach((seg, idx) => {
        const prefix = segs.slice(0, idx + 1).join('/')
        crumbs.push(h('span.fld-crumb-sep', '/'))
        crumbs.push(h('button.fld-crumb' + (idx === segs.length - 1 ? '.current' : ''), {
          type: 'button',
          onclick: () => foldersUi.navTo(prefix)
        }, seg))
      })
      if (!segs.length) {
        crumbs.length = 1
        crumbs.push(h('span.fld-crumb-sep', '/'))
        crumbs.push(h('span.fld-crumb.current', foldersUi.ROOT_LABEL))
      }

      const deepToggle = h('label.fld-deep',
        h('input', {
          type: 'checkbox',
          checked: folderDeep,
          onchange: ev => { folderDeep = !!ev.target.checked; load() }
        }),
        h('span', '含子文件夹'))

      folderBar.replaceChildren(h('div.fld-crumbs',
        h('span.fld-crumbs-ico', '📂'),
        ...crumbs,
        h('span', { style: { flex: '1 1 auto' } }),
        deepToggle,
        h('button.btn.sm.ghost', {
          type: 'button', title: '退出目录视图',
          onclick: () => go('')
        }, '× 退出目录')))
    }

    function renderBar (info) {
      const q = state.query
      const typeSelect = h('select.select', {
        title: '按类型筛选',
        onchange: ev => { state.query.type = ev.target.value; state.query.page = 1; setHash('', queryToParams()) }
      },
        h('option', { value: 'all', selected: q.type === 'all' }, '全部类型'),
        ...Object.entries(util.TYPE_LABELS).map(([key, label]) =>
          h('option', { value: key, selected: q.type === key }, label))
      )
      // 收藏筛选：一键「只看收藏」（替代原来的阅读状态下拉）
      const starToggle = h('button.btn.sm' + (q.starred ? '.primary' : ''), {
        type: 'button',
        title: q.starred ? '显示全部文献' : '只看我收藏的',
        onclick: () => {
          state.query.starred = !state.query.starred
          state.query.page = 1
          setHash(state.query.starred ? 'starred' : '', queryToParams())
        }
      }, (q.starred ? '★' : '☆') + ' 收藏')

      const tagChips = q.tags.map(tag => h('button.tag-chip.active', {
        type: 'button',
        title: '移除该筛选',
        onclick: () => {
          state.query.tags = state.query.tags.filter(t => t !== tag)
          state.query.page = 1
          setHash('', queryToParams())
        }
      }, tag, h('span.n', '×')))

      const summary = []
      const showCount = hasAnyFilter(q) || !!q.q
      if (!info) {
        summary.push('正在读取…')
      } else if (showCount) {
        summary.push(`${info.total} 条结果`)
        if (q.q) summary.push(`关键词「${q.q}」`)
        if (info.pageCount > 1) summary.push(`第 ${info.page} / ${info.pageCount} 页`)
      }

      const resetBtn = hasAnyFilter(q)
        ? h('button.btn.sm.ghost', {
          type: 'button',
          onclick: () => {
            state.query = { q: '', qRaw: '', tags: [], extraTags: [], type: 'all', starred: false, sort: state.query.sort, page: 1, folder: null }
            setHash('', queryToParams())
          }
        }, '清空筛选')
        : null

      // 注意用 util.replace 而不是 replaceChildren：浏览器会把 null 当成字符串插进去，
      // 页面上就多出一个写着 "null" 的文本节点（反馈 #7 的截图就是这个）
      // 空库时这一条筛选没有意义（0 条还筛什么），直接收起，把注意力留给起步板。
      // 注意 #list-bar 的内联样式里带着 display:grid，hidden 属性压不住它，只能改 style。
      if (info && info.total === 0 && !hasAnyFilter(q) && !q.q) {
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
        const hasFilter = hasAnyFilter(q)
        if (!hasFilter) {
          // 真正的空库：给起步板（三条入库路径），而不是一句「文献库还是空的」
          results.replaceChildren(emptyStart())
          return
        }
        results.replaceChildren(h('div.empty',
          h('div.big', '没有匹配的文献'),
          h('p', { text: '试试换个关键词，或清空筛选条件。' }),
          h('div', { style: { display: 'flex', gap: '8px' } },
            h('button.btn.primary', { type: 'button', onclick: () => { go('#/new' ); } }, '＋ 新增文献'),
            h('button.btn', {
              type: 'button',
              onclick: () => {
                state.query = { q: '', qRaw: '', tags: [], extraTags: [], type: 'all', starred: false, sort: state.query.sort, page: 1, folder: null }
                setHash('', queryToParams())
              }
            }, '清空筛选')
          )
        ))
        return
      }
      const common = {
        selectable: isSelectMode(),
        selected: false,
        onTag: tag => {
          // 点卡片上的标签 → 填进搜索栏（同时清掉类型/状态筛选，免得筛出空结果）
          if (filterByTag) filterByTag(tag, { resetType: true, resetStarred: true })
        },
        // 直接把这一篇挪到别的分类文件夹（弹窗里也能顺手新建）
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
        // 紧凑列表也要能批量：以前这里没把 selectable/onSelect 传下去，
        // 于是切到紧凑视图后「批量」按钮形同虚设（用户反馈）
        results.replaceChildren(h('div.rows', items.map(item =>
          paperRow(item, Object.assign({}, common, { selected: selected.has(item.id) })))))
      } else {
        results.replaceChildren(h('div.cards', items.map(item =>
          paperCard(item, Object.assign({}, common, { selected: selected.has(item.id) })))))
      }
      renderBulkBar()
    }

    /**
     * 改单篇文献的分类：弹目录树 → 移动 → 刷新列表。
     * 正在目录视图里时，这一篇会被移出当前目录，所以必须重新 load 而不是只改本地字段。
     */
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

    /**
     * 批量移动：选中多篇 → 选目录 → 一次请求 → 刷新。
     * 与单篇的 changeFolder 共用同一个目录选择器（里面也能新建目录），
     * 区别只在接口拿到的是一批 id。
     */
    function moveSelectedToFolder () {
      const ids = [...selected]
      if (!ids.length) return
      // 批量条自己的 refresh 在 renderBulkBar 里，这里拿不到；用列表页这一层的重绘
      const refresh = () => { renderBar(lastInfo); renderResults() }
      foldersUi.openFolderPicker({
        current: getState().query.folder || '',
        onPick: async path => {
          const where = path ? path.split('/').pop() : foldersUi.ROOT_LABEL
          // 全都已经在这个目录里就不用发请求了（后端会把它们再算一遍 moved）
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
            // 侧栏计数立刻变；列表按当前筛选重新拉（在目录视图里被移走的会消失）
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
      // 批量模式下常驻显示（哪怕一条都没选）：以前只在选中 1 条以上时才出现，
      // 「全选」按钮就没地方放了；现在没选时动作按钮变灰，选一条就亮
      if (!isSelectMode()) return
      const pageIds = items.map(i => i.id)
      const allSelected = pageIds.length > 0 && pageIds.every(id => selected.has(id))
      // 有一部分勾选不在本页（改过筛选 / 翻过页）时提示一下：否则用户看不见它们
      const hiddenCount = pageIds.filter(id => selected.has(id)).length
      const offPage = Math.max(0, selected.size - hiddenCount)
      const none = selected.size === 0
      // 选择变化后要同时刷新：顶部「批量中（N）」、卡片/行的勾选态、这条浮动条本身
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
        // 选择会跨视图/跨筛选保留，所以必须给一个明确的「反悔」出口
        h('button.btn.sm.ghost', {
          type: 'button',
          title: '清空已勾选的文献（不影响文献本身）',
          disabled: none,
          onclick: () => {
            selected.clear()
            refresh()
          }
        }, '清空选择'),
        // 「全选 / 全不选」：外观是一整个按钮，中间一条竖线分成两半（.seg-btn）
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
              // 只要有一条没收藏，就整体设为「收藏」；全都收藏了则整体取消
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
      const go = page => {
        state.query.page = Math.min(Math.max(1, page), info.pageCount)
        setHash('', queryToParams())
      }
      pager.replaceChildren(h('div.pager',
        h('button.btn.sm', { type: 'button', disabled: info.page <= 1, onclick: () => go(info.page - 1) }, '← 上一页'),
        h('span', `第 ${info.page} / ${info.pageCount} 页 · 共 ${info.total} 条`),
        h('button.btn.sm', { type: 'button', disabled: info.page >= info.pageCount, onclick: () => go(info.page + 1) }, '下一页 →')
      ))
    }

    /* ---------- 数据加载 ---------- */

    let lastInfo = null
    async function load () {
      loading = true
      renderResults()
      // 关键词与标签合并后交给服务端：标签可能来自搜索栏的 #标签，也可能来自点标签筛选
      const params = effectiveQuery
        ? effectiveQuery()
        : { q: state.query.q, tags: state.query.tags, page: state.query.page, pageSize: state.prefs.pageSize }
      // 目录视图：默认连子文件夹一起看，用户可关掉
      if (state.query.folder != null) params.includeSubfolders = folderDeep ? 'true' : 'false'
      try {
        const res = await api.listPapers(params)
        if (isStale()) return
        items = res.items || []
        lastInfo = res
        rememberTitles(items)
      } catch (err) {
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
      renderFolderBar()
      renderBar(lastInfo)
      renderResults()
      renderPager(lastInfo)
    }

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
