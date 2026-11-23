/* 图表画廊视图 #/figures（合并后新增的浏览器视图；Node 参考实现与其前端都没有这一页，属新写）
   移植自 PaperLibrary app/figures_arxiv.py（图表画廊 + 图注匹配）与 app/thumbs.py（缩略图），
   数据面换成 Nexus 的 HTTP 端点。契约：docs/合并新增端点契约-20261107.md
        §1 GET /api/figures（q / paperId / type / page / pageSize，响应 items/total/page/pageSize/pageCount）
        §2 GET|HEAD /api/figures/:id/image（图片流，可直接当 <img src>）
        §3 GET /api/papers/:id/figures
        §4 POST /api/papers/:id/figures/extract（force 覆盖已有记录）
        §11 DELETE /api/figures/:id（删除画廊里的一条图表记录，不碰文献 / PDF）
   依赖：util.mjs / api.mjs / shell/app-api.mjs —— 不 import app.mjs，保持 0 环 import。 */
import * as util from '../util.mjs'
import * as components from '../components.mjs'
import { api } from '../api.mjs'
import { go, setHash } from '../shell/app-api.mjs'

const { h, toast, replace, truncate, confirmDialog } = util

/** 契约默认值：pageSize 1-100，默认 48；画廊按 48 张一页排（缩略图网格） */
const PAGE_SIZE = 48

/* hero 字标的逐字母配色，与素材 components/hero-google.html 的图检索页版本逐字一致 */
const HERO_FIGURE_LENS = [
  ['F', 'blue'], ['i', 'red'], ['g', 'yellow'], ['u', 'blue'], ['r', 'green'],
  ['e', 'red'], ['L', 'blue'], ['e', 'yellow'], ['n', 'green'], ['s', 'blue']
]

const TYPE_OPTIONS = [
  ['', '全部类型'],
  ['figure', '插图（figure）'],
  ['table', '表格（table）'],
  ['scheme', '路线（scheme）'],
  ['chart', '图表（chart）']
]

/** 地址即状态：筛选条件全部写在 hash 查询串里（刷新/后退都能还原） */
function readState (params) {
  return {
    q: params.get('q') || '',
    paperId: params.get('paperId') || '',
    type: params.get('type') || '',
    page: Math.max(1, Number(params.get('page')) || 1)
  }
}

function figuresHandler (ctx) {
  const { outlet, params, isStale } = ctx
  const st = readState(params)

  // 详情页把「Esc 返回首页」写在 document.onkeydown 上，切到画廊后可能残留：
  // 那样灯箱 / 删除确认框按 Esc 会被顺带导航走。与 timeline.mjs 一样进视图时清掉。
  if (document.onkeydown) document.onkeydown = null

  const bar = h('div', { id: 'figures-bar', style: { marginBottom: '14px', display: 'grid', gap: '10px' } })
  const grid = h('div', { id: 'figures-grid' })
  const pager = h('div', { id: 'figures-pager' })
  /* 顶部 hero（素材里的图检索页版本：FigureLens + 翠绿胶囊副标）；只建一次，
     不参与 renderBar 的重绘，筛选/翻页时不会闪烁或丢焦点 */
  const heroHead = components.hero({
    letters: HERO_FIGURE_LENS,
    sub: 'Images & Graphs',
    subTone: 'emerald',
    tagline: '自然语言跨语言检索每一张论文图表 · 精确到 Figure 级空间图注对齐'
  })
  outlet.replaceChildren(h('div.content', heroHead, bar, grid, pager))

  let items = []
  let info = null
  let loading = false
  /** 单篇筛选时用来显示标题（paperId 本身对用户没有意义） */
  const paperTitles = new Map()

  /** 改筛选条件 → 写地址（外壳会重绘本视图）。page 归 1 除非显式指定 */
  function apply (patch) {
    const next = Object.assign({}, st, patch)
    setHash('figures', {
      q: next.q,
      paperId: next.paperId,
      type: next.type,
      page: next.page > 1 ? next.page : ''
    })
  }

  function hasFilter () {
    return !!(st.q || st.paperId || st.type)
  }

  async function ensurePaperTitle () {
    if (!st.paperId || paperTitles.has(st.paperId)) return
    try {
      const res = await api.getPaper(st.paperId)
      const paper = (res && res.paper) || null
      paperTitles.set(st.paperId, (paper && paper.title) || st.paperId)
    } catch {
      paperTitles.set(st.paperId, st.paperId)
    }
  }

  /* ---------------- 顶部筛选条 ---------------- */

  function renderBar () {
    const search = h('input.input', {
      type: 'search',
      value: st.q,
      placeholder: '搜索图注 / 图表描述（回车搜索）',
      autocomplete: 'off',
      onkeydown: ev => {
        if (ev.key === 'Enter') {
          ev.preventDefault()
          apply({ q: String(ev.target.value || '').trim(), page: 1 })
        }
        if (ev.key === 'Escape') {
          ev.preventDefault()
          ev.target.value = ''
          apply({ q: '', page: 1 })
        }
      }
    })

    const typeSelect = h('select.select', {
      title: '按图类型筛选',
      onchange: ev => apply({ type: ev.target.value, page: 1 })
    }, ...TYPE_OPTIONS.map(([value, label]) =>
      h('option', { value, selected: st.type === value }, label)))

    const paperChip = st.paperId
      ? h('span.fig-paper-chip',
        h('span', { text: '只看《' + truncate(paperTitles.get(st.paperId) || st.paperId, 34) + '》' }),
        h('button', { type: 'button', title: '取消单篇筛选', onclick: () => apply({ paperId: '', page: 1 }) }, '×')
      )
      : null

    const extractBtn = st.paperId
      ? h('button.btn.sm', {
        type: 'button',
        title: '用 PyMuPDF 重新渲染这篇的 PDF 并按图注 bbox 匹配图表（会覆盖已有记录）',
        onclick: ev => extractForPaper(st.paperId, ev.currentTarget)
      }, '从原文重提图表')
      : null

    /* 概要紧在「有结果」时才说：空库/无匹配时不再显示「没有图表」这类占位文案
       （空状态本身已经有一块说明，两处叠在一起只剩噪音）。 */
    const summaryBits = []
    if (info) {
      if (info.total) summaryBits.push('共 ' + info.total + ' 张图表')
      if (info.total && info.pageCount > 1) summaryBits.push('第 ' + info.page + ' / ' + info.pageCount + ' 页')
    } else if (loading) {
      summaryBits.push('正在读取…')
    }
    if (st.q) summaryBits.push('关键词「' + st.q + '」')

    const clearBtn = hasFilter()
      ? h('button.btn.sm.ghost', {
        type: 'button',
        onclick: () => apply({ q: '', paperId: '', type: '', page: 1 })
      }, '清空筛选')
      : null
    replace(bar,
      // 概要与操作：没有可说的内容、也没有按钮时整行都不占位
      summaryBits.length || extractBtn || clearBtn
        ? h('div.list-bar.fig-summary-row',
          h('span.list-summary', { text: summaryBits.join(' · ') }),
          h('div.list-controls', extractBtn, clearBtn))
        : null,
      /* 搜索行：类型下拉在左、搜索框在右，整组居中且限宽 720px（搜索框不再拉满整行） */
      h('div.fig-search-row',
        h('div.fig-search-group',
          typeSelect,
          h('div.search-wrap.fig-search', h('span.ico', '⌕'), search)
        )
      ),
      paperChip ? h('div.fig-chip-row', paperChip) : null
    )
  }

  /* ---------------- 缩略图网格 ---------------- */

  function thumbFor (item) {
    const thumb = h('div.fig-thumb')
    if (!item.hasImage) {
      thumb.replaceChildren(h('div.fig-missing', '无图片'),
        h('div.fig-missing-sub', '这条记录没有可显示的图片文件'))
      return thumb
    }
    const img = h('img', {
      alt: item.caption || ('图 ' + item.figIndex),
      loading: 'lazy',
      draggable: 'false',
      // 监听器写在 src 前面：图片加载失败时 img 还是这个节点，能就地换成占位提示
      onerror: () => {
        thumb.replaceChildren(h('div.fig-missing', '图片文件缺失'),
          h('div.fig-missing-sub', '可对这篇重新提取图表'))
      },
      src: api.figureImageUrl(item.id)
    })
    thumb.appendChild(img)
    return thumb
  }

  function figureCard (item) {
    const caption = String(item.caption || '').trim() || ('图 ' + (item.figIndex || '?'))
    const metaBits = []
    if (item.figIndex != null) metaBits.push('图 ' + item.figIndex)
    if (item.figureType) metaBits.push(item.figureType)
    const card = h('article.fig-card', {
      tabindex: '0',
      role: 'button',
      title: caption,
      onclick: () => openLightbox(item),
      onkeydown: ev => {
        if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); openLightbox(item) }
      }
    },
      thumbFor(item),
      h('div.fig-body',
        h('div.fig-cap', { text: truncate(caption, 110) }),
        h('div.fig-meta',
          h('span.fig-paper', { text: truncate(item.paperTitle || item.paperId || '（文献已删除）', 26) }),
          metaBits.length ? h('span.fig-badge', { text: metaBits.join(' · ') }) : null
        )
      )
    )
    return card
  }

  function renderGrid () {
    if (loading) {
      grid.replaceChildren(h('div.empty', h('div.big', '正在读取图表…')))
      return
    }
    if (!items.length) {
      grid.replaceChildren(h('div.empty',
        h('div.big', hasFilter() ? '没有匹配的图表' : '图表画廊还是空的'),
        h('p', {
          text: hasFilter()
            ? '换个关键词或类型试试；也可以清空筛选看全部。'
            : '导入原文 PDF 时会自动提取有图注的图表，也可以为单篇文献从原文重提。'
        }),
        h('div', { style: { display: 'flex', gap: '8px' } },
          h('button.btn.primary', { type: 'button', onclick: () => go('') }, '去文献库'),
          hasFilter()
            ? h('button.btn', {
              type: 'button',
              onclick: () => apply({ q: '', paperId: '', type: '', page: 1 })
            }, '清空筛选')
            : null
        )
      ))
      return
    }
    grid.replaceChildren(h('div.fig-grid', items.map(figureCard)))
  }

  /* ---------------- 大图（灯箱） ---------------- */

  function closeLightbox (overlay, onKey) {
    overlay.remove()
    document.removeEventListener('keydown', onKey)
  }

  function openLightbox (item) {
    const caption = String(item.caption || '').trim() || ('图 ' + (item.figIndex || '?'))
    const img = item.hasImage
      ? h('img.fig-large-img', { alt: caption, src: api.figureImageUrl(item.id) })
      : h('div.fig-missing', '这条记录没有可显示的图片文件')

    const bodyBits = []
    if (item.contextText) {
      bodyBits.push(h('details.fig-details', { open: true },
        h('summary', '正文上下文'),
        h('div.fig-text', { text: item.contextText })))
    }
    if (item.aiDescription) {
      bodyBits.push(h('div.fig-block', h('div.fig-block-k', 'AI 描述'), h('div.fig-text', { text: item.aiDescription })))
    }
    if (item.aiConclusion) {
      bodyBits.push(h('div.fig-block', h('div.fig-block-k', 'AI 结论'), h('div.fig-text', { text: item.aiConclusion })))
    }
    if (!bodyBits.length) {
      bodyBits.push(h('div.hint', '这条图表还没有图注上下文与 AI 描述（可在单篇文献里重新提取，或让模型读图后补充）。'))
    }

    /* 删除入口只放在大图详情里（画廊专用），沿用现成的 confirmDialog / toast / .btn.danger。 */
    let confirming = false  // 确认框开着时 Esc 只归确认框，灯箱不跟着关
    let removing = false    // 防重复提交
    const deleteBtn = h('button.btn.danger.fig-del', {
      type: 'button',
      title: '只从图表画廊移除这一张，不影响文献、PDF 与其它图表',
      onclick: () => removeCurrent()
    }, '删除这张图表')

    async function removeCurrent () {
      if (removing) return
      confirming = true
      const ok = await confirmDialog({
        title: '删除这张图表？',
        body: '将从图表画廊移除「' + truncate(caption, 60) + '」。文献、PDF 与其它图表不受影响，此操作不可撤销。',
        okText: '删除',
        cancelText: '取消',
        danger: true
      })
      confirming = false
      if (!ok) return  // 取消：不发请求、不改列表、不关灯箱

      removing = true
      const label = deleteBtn.textContent
      deleteBtn.disabled = true
      deleteBtn.textContent = '正在删除…'
      const lastOnPage = st.page > 1 && items.length === 1
      try {
        await api.deleteFigure(item.id)
        closeLightbox(overlay, onKey)  // 详情展示：这张已不存在，收起灯箱
        toast('已删除「' + truncate(caption, 30) + '」')
        if (isStale()) return
        if (lastOnPage) apply({ page: st.page - 1 })  // 本页删空了就退回上一页
        else await load()                              // 同步刷新画廊列表 / 概要 / 分页
      } catch (err) {
        toast('删除失败：' + (err.message || '未知错误'), 'error')
        // 失败：灯箱保持打开、列表原样，方便用户重试
      } finally {
        removing = false
        if (deleteBtn.isConnected) {
          deleteBtn.disabled = false
          deleteBtn.textContent = label
        }
      }
    }

    const overlay = h('div.modal-back', {
      onclick: ev => { if (ev.target === overlay) closeLightbox(overlay, onKey) }
    },
      h('div.modal.fig-modal',
        h('h3', { text: caption }),
        h('div.fig-modal-meta',
          h('span', { text: truncate(item.paperTitle || item.paperId || '（文献已删除）', 70) }),
          item.figureType ? h('span.fig-badge', { text: item.figureType }) : null,
          item.figIndex != null ? h('span.fig-badge', { text: '图 ' + item.figIndex }) : null
        ),
        h('div.fig-modal-img', img),
        ...bodyBits,
        h('div.actions',
          deleteBtn,
          item.paperId
            ? h('button.btn', {
              type: 'button',
              onclick: () => {
                closeLightbox(overlay, onKey)
                go('paper/' + encodeURIComponent(item.paperId), { figure: item.id })
              }
            }, '打开文献详情')
            : null,
          item.paperId
            ? h('button.btn', {
              type: 'button',
              title: '重新从这篇的 PDF 提取图表（覆盖已有记录）',
              onclick: ev => { closeLightbox(overlay, onKey); extractForPaper(item.paperId, ev.currentTarget) }
            }, '重提这篇的图表')
            : null,
          h('button.btn.primary', { type: 'button', onclick: () => closeLightbox(overlay, onKey) }, '关闭')
        )
      )
    )
    const onKey = ev => { if (ev.key === 'Escape' && !confirming) closeLightbox(overlay, onKey) }
    document.addEventListener('keydown', onKey)
    document.body.appendChild(overlay)
  }

  /* ---------------- 分页 ---------------- */

  function renderPager () {
    if (!info || info.pageCount <= 1) {
      pager.replaceChildren()
      return
    }
    const goto = page => apply({ page: Math.min(Math.max(1, page), info.pageCount) })
    pager.replaceChildren(h('div.pager',
      h('button.btn.sm', { type: 'button', disabled: info.page <= 1, onclick: () => goto(info.page - 1) }, '← 上一页'),
      h('span', '第 ' + info.page + ' / ' + info.pageCount + ' 页 · 共 ' + info.total + ' 张'),
      h('button.btn.sm', { type: 'button', disabled: info.page >= info.pageCount, onclick: () => goto(info.page + 1) }, '下一页 →')
    ))
  }

  /* ---------------- 提取（§4） ---------------- */

  async function extractForPaper (paperId, btn) {
    if (!paperId) {
      toast('先在上面按一篇文献筛选，再重提图表', 'warn')
      return
    }
    const label = btn ? btn.textContent : ''
    if (btn) {
      btn.setAttribute('disabled', '')
      btn.textContent = '正在提取…'
    }
    try {
      const res = await api.extractFigures(paperId, { force: true })
      toast('已从 PDF 提取 ' + (res.extracted || 0) + ' 张图表')
      await load()
    } catch (err) {
      toast('提取失败：' + err.message, 'error')
    } finally {
      if (btn && btn.parentNode) {
        btn.removeAttribute('disabled')
        btn.textContent = label
      }
    }
  }

  /* ---------------- 数据加载 ---------------- */

  async function load () {
    loading = true
    renderGrid()
    try {
      const res = await api.listFigures({
        q: st.q,
        paperId: st.paperId,
        type: st.type,
        page: st.page,
        pageSize: PAGE_SIZE
      })
      if (isStale()) return
      items = res.items || []
      info = res
      loading = false
      await ensurePaperTitle()
      if (isStale()) return
      renderBar()
      renderGrid()
      renderPager()
    } catch (err) {
      if (isStale()) return
      loading = false
      items = []
      info = null
      renderBar()
      grid.replaceChildren(h('div.empty',
        h('div.big', '读取图表失败'),
        h('p', { text: err.message }),
        h('button.btn', { type: 'button', onclick: () => load() }, '重试')))
      pager.replaceChildren()
    }
  }

  renderBar()
  load().catch(err => {
    console.error('[figures] 读取失败：', err)
    loading = false
    if (!isStale()) renderGrid()
  })
}

export { figuresHandler }
export default figuresHandler
