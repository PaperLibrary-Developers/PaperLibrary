/* 可复用界面组件：文献卡片、列表行、标签输入、星级评分、引用工具条
   （ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from './util.mjs'
import { api } from './api.mjs'
import { filterByTag, refreshMeta } from './shell/app-api.mjs'
import { go } from './shell/app-api.mjs'

const { h, typeLabel, authorsText, starsHtml, toast, copyText, escapeHtml } = util

  function tagNodes (tags, options) {
    const opts = options || {}
    const nodes = (tags || []).slice(0, opts.max || 6).map(tag =>
      h('button.tag' + (opts.clickable ? '.clickable' : ''), {
        type: 'button',
        title: '按标签筛选',
        onclick: ev => {
          ev.stopPropagation()
          // 标签筛选统一走搜索栏（#标签），不再有单独的标签看板页面
          if (opts.onTag) opts.onTag(tag)
          else filterByTag(tag)
        }
      }, tag)
    )
    if ((tags || []).length > (opts.max || 6)) {
      nodes.push(h('span.tag', '+' + ((tags || []).length - (opts.max || 6))))
    }
    return nodes
  }

  function starsNode (rating, options) {
    const opts = options || {}
    if (!opts.interactive) {
      // 只读星级：0 分时显示 5 颗空心星（不是实心），一眼能看出「还没评分」
      const value = Math.max(0, Math.min(5, Number(rating) || 0))
      const title = opts.title || (value ? '我的评分：' + value + ' / 5（在详情页打分）' : '还没评分（在详情页打分）')
      return h('span.stars', { title: title, html: starsHtml(value) })
    }
    const wrap = h('span.stars.interactive', { title: '点击打分（再点一次清除）' })
    for (let i = 1; i <= 5; i++) {
      wrap.appendChild(h('span', {
        onclick: ev => {
          ev.stopPropagation()
          const next = (Number(rating) || 0) === i ? 0 : i
          opts.onChange(next)
        }
      }, i <= (Number(rating) || 0) ? '★' : '☆'))
    }
    return wrap
  }

  /**
   * 收藏星标：一键切换。
   * 注意「收藏」和 1-5 星的「评分」是两回事——收藏 = 这篇对我有用，评分 = 这篇质量如何。
   * 两个字形（空星/实星）都在 DOM 里，靠 .on 这个 class 决定显示哪个：
   * 这样「点亮」完全由 class 驱动，不依赖改文字，任何主题下都不会出现「已收藏还是空心」。
   */
  function starButton (paper, options) {
    const opts = options || {}
    const btn = h('button.star-btn' + (paper.starred ? '.on' : ''), {
      type: 'button',
      title: paper.starred ? '取消收藏' : '加入收藏',
      'aria-label': paper.starred ? '取消收藏' : '加入收藏',
      'aria-pressed': paper.starred ? 'true' : 'false'
    }, h('span.star-glyph-off', '☆'), h('span.star-glyph-on', '★'))
    // 批量模式下星标退化成纯装饰：不响应点击（CSS .static { pointer-events:none }），
    // 也不再是可聚焦控件，点击会穿透到卡片/行上变成「勾选」
    if (opts.inert) {
      btn.classList.add('static')
      btn.setAttribute('tabindex', '-1')
      btn.setAttribute('aria-hidden', 'true')
      return btn
    }
    btn.addEventListener('click', async ev => {
      ev.stopPropagation()
      ev.preventDefault()
      if (btn.disabled) return
      btn.disabled = true
      const next = !paper.starred
      try {
        const res = await api.updatePaper(paper.id, { starred: next })
        const saved = res && res.paper ? res.paper : { starred: next }
        paper.starred = !!saved.starred
        paper.starredAt = saved.starredAt || ''
        btn.classList.toggle('on', paper.starred)
        btn.title = paper.starred ? '取消收藏' : '加入收藏'
        btn.setAttribute('aria-pressed', paper.starred ? 'true' : 'false')
        if (opts.onChange) opts.onChange(paper.starred)
        refreshMeta()
      } catch (err) {
        util.toast(err.message || '操作失败', 'error')
      } finally {
        btn.disabled = false
      }
    })
    return btn
  }

  /**
   * 批量模式下的选中逻辑，卡片和紧凑行共用。
   * 关键点：批量模式下整张卡片/整行都是勾选热区——以前只有右上角那个小勾选框算，
   * 点到卡片其它地方会直接跳进详情页，很容易误点（用户的反馈）。
   */
  function selectHelpers (paper, opts) {
    const selectable = !!opts.selectable
    const selected = !!opts.selected
    const toggle = () => { if (selectable && opts.onSelect) opts.onSelect(paper.id, !selected) }
    const goDetail = () => { go('#/paper/' + encodeURIComponent(paper.id)) }
    return {
      selectable,
      selected,
      toggle,
      /** 整块区域的点击：命中按钮/链接/输入框/标签时让它们自己处理，其余一律勾选 */
      onClick: ev => {
        const target = ev.target
        if (target && typeof target.closest === 'function' && target.closest('button, a, input, label')) return
        if (selectable) toggle()
        else goDetail()
      },
      onKeydown: ev => {
        if (ev.key !== 'Enter' && !(selectable && ev.key === ' ')) return
        if (ev.preventDefault) ev.preventDefault()
        if (selectable) toggle()
        else goDetail()
      }
    }
  }

  /** 卡片视图里的单条文献 */
  function paperCard (paper, options) {
    const opts = options || {}
    const s = selectHelpers(paper, opts)
    const selected = s.selected
    const card = h('article.card' + (s.selectable ? '.selectable' : '') + (selected ? '.selected' : ''), {
      tabindex: '0',
      role: s.selectable ? 'checkbox' : 'link',
      'aria-checked': s.selectable ? (selected ? 'true' : 'false') : null,
      onclick: s.onClick,
      onkeydown: s.onKeydown
    })

    if (s.selectable) {
      card.appendChild(h('label.card-check', { title: '选中以批量操作' },
        h('input.check', {
          type: 'checkbox',
          checked: selected,
          'aria-label': '选中《' + (paper.title || '') + '》',
          onclick: ev => {
            ev.stopPropagation()
            s.toggle()
          }
        })
      ))
    }

    card.appendChild(h('h3.card-title', { text: paper.title }))
    card.appendChild(h('div.card-meta',
      h('span', { text: authorsText(paper.authors, 3) }),
      paper.year ? h('span', '·') : null,
      paper.year ? h('span', { text: paper.year }) : null,
      paper.venue ? h('span', '·') : null,
      paper.venue ? h('span', { text: paper.venue, title: paper.venue }) : null
    ))

    if (paper.summary) card.appendChild(h('p.card-summary', { text: paper.summary }))
    if ((paper.tags || []).length) card.appendChild(h('div.card-tags', tagNodes(paper.tags, { onTag: opts.onTag })))

    card.appendChild(h('div.card-foot',
      // 批量模式下星标只做展示（.static 让它不响应点击），整张卡片都是勾选热区，
      // 免得在勾选时误点到星标把收藏状态改了
      starButton(paper, { inert: s.selectable }),
      h('span.badge.type', { text: typeLabel(paper.type) }),
      paper.hasPdf ? h('span.badge.pdf', { text: 'PDF' }) : null,
      h('span.right',
        starsNode(paper.rating),
        h('span', { text: util.relTime(paper.updatedAt) })
      )
    ))
    return card
  }

  /** 表格/紧凑视图里的一行 */
  function paperRow (paper, options) {
    const opts = options || {}
    const s = selectHelpers(paper, opts)
    return h('div.row-item' + (s.selectable ? '.selectable' : '') + (s.selected ? '.selected' : ''), {
      role: s.selectable ? 'checkbox' : 'link',
      tabindex: '0',
      'aria-checked': s.selectable ? (s.selected ? 'true' : 'false') : null,
      onclick: s.onClick,
      onkeydown: s.onKeydown
    },
      // 批量模式下第一格换成勾选框（紧凑视图以前整个没有勾选框，根本没得选）
      s.selectable
        ? h('div.row-star.row-check', h('input.check', {
          type: 'checkbox',
          checked: s.selected,
          title: '选中以批量操作',
          'aria-label': '选中《' + (paper.title || '') + '》',
          onclick: ev => { ev.stopPropagation(); s.toggle() }
        }))
        : h('div.row-star', starButton(paper)),
      h('div', { style: { minWidth: 0 } },
        h('div.row-title', { text: paper.title }),
        // 副标题：作者 · 期刊，末尾补一个 PDF 标记。
        // 反馈 #15：紧凑视图原来没有「是否关联 PDF」这一条（卡片视图有），这里补上；
        // 放在这一行而不是新起一行，行高不变。
        h('div.row-sub', { style: { display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 } },
          h('span', {
            text: authorsText(paper.authors, 3) + (paper.venue ? ' · ' + paper.venue : ''),
            style: { minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }
          }),
          paper.hasPdf
            ? h('span.badge.pdf', { title: '已关联 PDF 原文', style: { flex: '0 0 auto' } }, 'PDF')
            : null
        )
      ),
      h('div.row-hide', h('span.badge.type', { text: typeLabel(paper.type) })),
      h('div.row-hide', { style: { display: 'flex', gap: '5px', flexWrap: 'wrap' } },
        (paper.tags || []).slice(0, 2).map(t => h('span.tag', { text: t }))),
      h('div', { style: { textAlign: 'right' } },
        h('div', { text: paper.year || '—' }),
        // 星级在这里只做展示（紧凑列表不做打分），所以套上 .stars 容器，
        // 让「空心星」的样式生效——以前漏了这个类，5 颗永远是实心，看着像装饰
        starsNode(paper.rating)
      )
    )
  }

  /** 标签输入：回车/逗号添加（支持一次粘贴多个），可点建议 */
  function tagInput (initial, options) {
    const opts = options || {}
    let tags = (initial || []).slice()
    const box = h('div.chips-input')
    const input = h('input', {
      type: 'text',
      placeholder: opts.placeholder || '输入标签后按回车；粘贴「a, b, c」会一次加三个',
      onkeydown: ev => {
        if (ev.key === 'Enter') {
          ev.preventDefault()
          // 一次可以贴多个：按逗号/顿号/分号切开
          util.splitTagInput(input.value).forEach(add)
          input.value = ''
        } else if (ev.key === 'Backspace' && !input.value && tags.length) {
          tags.pop()
          renderChips()
          if (opts.onChange) opts.onChange(tags.slice())
        }
      },
      onblur: () => {
        if (input.value.trim()) {
          util.splitTagInput(input.value).forEach(add)
          input.value = ''
        }
      }
    })
    const chipWrap = h('div', { style: { display: 'contents' } })

    function add (raw) {
      const value = util.normalizeTagInput(raw)
      if (!value) return
      if (tags.some(t => t.toLowerCase() === value.toLowerCase())) return
      tags.push(value)
      renderChips()
      if (opts.onChange) opts.onChange(tags.slice())
    }
    function remove (tag) {
      tags = tags.filter(t => t !== tag)
      renderChips()
      if (opts.onChange) opts.onChange(tags.slice())
    }
    function renderChips () {
      chipWrap.replaceChildren(...tags.map(tag =>
        h('span.chip', tag, h('button', { type: 'button', title: '移除', onclick: () => remove(tag) }, '×'))
      ))
      box.replaceChildren(chipWrap, input)
    }
    renderChips()

    const wrap = h('div', box)
    if (opts.suggestions && opts.suggestions.length) {
      const suggest = h('div.suggest', opts.suggestions.slice(0, 14).map(s =>
        h('button.tag-chip', { type: 'button', onclick: () => add(s.name || s) }, (s.name || s) + (s.count ? ' ' + s.count : ''))
      ))
      wrap.appendChild(suggest)
    }
    wrap.getTags = () => tags.slice()
    return wrap
  }

  /** 引用格式工具条：切换格式 + 一键复制 */
  const CITE_LABELS = { gb: 'GB/T 7714', apa: 'APA 7', mla: 'MLA 9', bibtex: 'BibTeX', markdown: 'Markdown' }
  function citePanel (paperId, options) {
    const opts = options || {}
    const textEl = h('div.cite-text', { text: '加载中…' })
    let current = opts.initial || 'gb'
    const buttons = {}
    const row = h('div.cite-format-row')
    for (const [key, label] of Object.entries(CITE_LABELS)) {
      const btn = h('button.btn.sm', {
        type: 'button',
        onclick: () => { current = key; sync(); load() }
      }, label)
      buttons[key] = btn
      row.appendChild(btn)
    }
    function sync () {
      for (const [key, btn] of Object.entries(buttons)) btn.classList.toggle('primary', key === current)
    }
    sync()

    async function load () {
      textEl.textContent = '加载中…'
      try {
        const res = await api.cite(paperId, current)
        textEl.textContent = res.text
      } catch (err) {
        textEl.textContent = '生成失败：' + err.message
      }
    }
    load()

    const panel = h('section.panel',
      h('div.panel-head', '引用格式',
        h('div.right',
          h('button.btn.sm', {
            type: 'button',
            onclick: async () => {
              const ok = await copyText(textEl.textContent)
              toast(ok ? '已复制到剪贴板' : '复制失败，请手动选择文本', ok ? '' : 'error')
            }
          }, '复制')
        )
      ),
      h('div.panel-body', h('div.cite-box', row, textEl))
    )
    return panel
  }

  export { paperCard, paperRow, tagNodes, starsNode, starButton, tagInput, citePanel, CITE_LABELS }
  export default { paperCard, paperRow, tagNodes, starsNode, starButton, tagInput, citePanel, CITE_LABELS }
