/* 详情页子模块：顶部操作栏、引用弹窗、三点菜单、图表重拉按钮、元信息卡片与就地标签编辑 */
import * as util from '../../util.mjs'
import * as components from '../../components.mjs'
import { api } from '../../api.mjs'
import { getState } from '../../state/store.mjs'
import { filterByTag, go, refreshMeta, render } from '../../shell/app-api.mjs'
import * as foldersUi from '../../folders.mjs'

const { h, typeLabel, authorsText, relTime, formatDate } = util
const { starsNode, folderGlyph } = components

const CITE_FORMATS = [
  ['gb', 'GB/T 7714', '中文学位论文 / 期刊常用'],
  ['apa', 'APA 7', '心理学、社科常用'],
  ['mla', 'MLA 9', '人文学科常用'],
  ['bibtex', 'BibTeX', 'LaTeX / Zotero 用'],
  ['markdown', 'Markdown', '笔记软件用']
]

function metaCell (key, value, options) {
  const opts = options || {}
  if (!value) return null
  const content = opts.html
    ? h('div.v', { html: opts.html })
    : h('div.v', opts.title ? { text: value, title: opts.title } : { text: value })
  return h('div.meta-cell', h('div.k', key), content)
}

export function buildDetailHeaderAndMeta ({
  paper, pdfOk, signal, isStale, toast, confirm, later, cancelLater, showOverlay, onExportPdf
}) {
  /* ---------- 顶部 ---------- */
  const links = []
  if (paper.doi) links.push(h('a.link', { href: 'https://doi.org/' + paper.doi, target: '_blank', rel: 'noopener' }, 'DOI: ' + paper.doi))
  if (paper.url && paper.url !== 'https://doi.org/' + paper.doi) {
    links.push(h('a.link', { href: paper.url, target: '_blank', rel: 'noopener' }, '原文链接 ↗'))
  }

  /* ---------- 引用弹窗（仿 Google Scholar 的引用栏） ---------- */
  function openCiteDialog () {
    const textBox = h('div.cite-text', { text: '加载中…' })
    let current = 'gb'
    let query = null
    const buttons = {}
    const formatRow = h('div.cite-format-row')
    for (const [key, label, hint] of CITE_FORMATS) {
      const btn = h('button.btn.sm', {
        type: 'button', title: hint,
        onclick: () => { current = key; syncButtons(); load() }
      }, label)
      buttons[key] = btn
      formatRow.appendChild(btn)
    }
    function syncButtons () {
      for (const [key, btn] of Object.entries(buttons)) btn.classList.toggle('primary', key === current)
    }
    syncButtons()
    async function load () {
      if (query) query.abort()
      const request = new AbortController()
      query = request
      textBox.textContent = '加载中…'
      try {
        const res = await api.cite(paper.id, current, request.signal)
        if (!request.signal.aborted) textBox.textContent = res.text
      } catch (err) {
        if (!request.signal.aborted) textBox.textContent = '生成失败：' + err.message
      }
    }
    load()

    const back = h('div.modal-back', {
      onclick: ev => { if (ev.target === back) close() }
    },
      h('div.modal', { style: { maxWidth: '620px' } },
        h('h3', '引用这篇文献'),
        h('div', { style: { color: 'var(--muted)', fontSize: '13px' } },
          '选择格式后点「复制」即可粘贴到论文或笔记里。'),
        formatRow,
        textBox,
        h('div.actions',
          h('button.btn', { type: 'button', onclick: () => close() }, '关闭'),
          h('button.btn.primary', {
            type: 'button',
            onclick: async () => {
              const ok = await util.copyText(textBox.textContent)
              if (!isStale()) toast(ok ? '已复制到剪贴板' : '复制失败，请手动选择文本', ok ? '' : 'error')
            }
          }, '复制')
        )
      )
    )
    const close = showOverlay(back, () => {
      if (query) query.abort()
    })
  }

  /* ---------- 三点菜单（把编辑/删除等收进二级，避免误点） ---------- */
  function buildMoreMenu () {
    const panel = h('div.more-menu', { hidden: true },
      h('a.more-item', { href: '#/edit/' + encodeURIComponent(paper.id) }, '✎ 编辑文献'),
      h('button.more-item', {
        type: 'button',
        title: '把这篇的 AI 总结与「与文献对话」的问答整理成一页，用浏览器打印成 PDF',
        onclick: () => { panel.hidden = true; onExportPdf() }
      }, '⤓ 导出 AI 总结与对话（PDF）'),
      paper.doi ? h('a.more-item', {
        href: 'https://doi.org/' + paper.doi, target: '_blank', rel: 'noopener'
      }, '↗ 打开 DOI 页面') : null,
      h('div.more-sep'),
      h('button.more-item.danger', {
        type: 'button',
        onclick: async () => {
          panel.hidden = true
          const ok = await confirm({
            title: '删除这条文献？',
            body: '记录会从文献库中移除' + (paper.pdfFile ? '，关联的 PDF 文件也会被删除' : '') + '，此操作不可撤销。',
            okText: '删除',
            danger: true
          })
          if (!ok) return
          try {
            await api.deletePaper(paper.id)
            if (isStale()) return
            toast('已删除')
            refreshMeta()
            go('#/')
          } catch (err) { if (!isStale()) toast(err.message, 'error') }
        }
      }, '🗑 删除文献')
    )
    const toggle = h('button.btn.sm.icon', {
      type: 'button', title: '更多操作', 'aria-label': '更多操作',
      onclick: ev => {
        ev.stopPropagation()
        panel.hidden = !panel.hidden
      }
    }, '⋯')
    const wrap = h('div.more-wrap', toggle, panel)
    // 点别处自动收起
    document.addEventListener('click', ev => {
      if (!wrap.contains(ev.target)) panel.hidden = true
    }, { signal })
    return wrap
  }

  /* 图表按钮：左侧跳图表画廊，右侧 ▾ 菜单支持重拉图表 */
  function buildFigureButton () {
    const status = h('button.fig-status', {
      type: 'button', hidden: true, 'aria-live': 'polite',
      title: '看刷新后的图表',
      onclick: () => go('figures', { paperId: paper.id })
    })
    let statusTimer = 0
    let busy = false
    let outside = null

    const menuItem = h('button.more-item', {
      type: 'button',
      role: 'menuitem',
      onclick: () => reextract()
    }, '⟳ 重拉图表')

    const menu = h('div.more-menu.split-menu', {
      id: 'fig-menu',
      role: 'menu',
      'aria-label': '图表操作',
      hidden: true
    }, menuItem)

    const caret = h('button.split-caret', {
      type: 'button',
      'aria-haspopup': 'menu',
      'aria-expanded': 'false',
      'aria-controls': 'fig-menu',
      title: '更多图表操作：从 PDF 重拉图表',
      'aria-label': '更多图表操作',
      onclick: ev => { ev.stopPropagation(); setMenu(menu.hidden) },
      onkeydown: ev => {
        if (ev.key === 'ArrowDown') { ev.preventDefault(); setMenu(true) }
      }
    }, '▾')

    const label = h('span.split-label', '图表')
    const main = h('button.btn.sm.split-main', {
      type: 'button',
      title: '看这篇的图表；也可以从图库进入跨文献的图表画廊',
      onclick: () => go('figures', { paperId: paper.id })
    }, h('span', '🖼'), label)

    const wrap = h('div.split', main, caret, menu)
    const unit = h('span.split-unit', wrap, status)

    menuItem.title = paper.pdfFile
      ? '用 PyMuPDF 重新渲染这篇的 PDF，按图注重新匹配图表（会覆盖已有记录）'
      : '这篇还没有关联 PDF，先关联 PDF 再重拉'

    function setMenu (open) {
      const next = !!open && !busy
      menu.hidden = !next
      caret.setAttribute('aria-expanded', next ? 'true' : 'false')
      wrap.classList.toggle('is-open', next)
      if (outside) {
        document.removeEventListener('click', outside)
        document.removeEventListener('keydown', onEsc, true)
        outside = null
      }
      if (!next) return
      outside = ev => {
        if (!unit.isConnected) { setMenu(false); return }
        if (!wrap.contains(ev.target)) setMenu(false)
      }
      document.addEventListener('click', outside, { signal })
      document.addEventListener('keydown', onEsc, { capture: true, signal })
      try { menuItem.focus({ preventScroll: true }) } catch (err) { /* 老浏览器忽略 */ }
    }

    function onEsc (ev) {
      if (ev.key !== 'Escape') return
      ev.stopImmediatePropagation()
      setMenu(false)
      try { caret.focus({ preventScroll: true }) } catch (err) { /* 同上 */ }
    }

    function showStatus (text, warn) {
      status.textContent = text
      status.classList.toggle('is-warn', !!warn)
      status.hidden = false
      cancelLater(statusTimer)
      statusTimer = later(() => { status.hidden = true }, 8000)
    }

    async function reextract () {
      setMenu(false)
      if (busy) return
      if (!paper.pdfFile) {
        toast('这篇还没有关联 PDF，先关联 PDF 再重拉图表', 'warn')
        return
      }
      busy = true
      wrap.classList.add('is-busy')
      wrap.setAttribute('aria-busy', 'true')
      main.disabled = true
      caret.disabled = true
      caret.replaceChildren(h('span.spin'))
      label.textContent = '重拉中…'
      status.hidden = true
      try {
        const res = await api.extractFigures(paper.id, { force: true })
        if (isStale()) return
        let n = res && typeof res.extracted === 'number'
          ? res.extracted
          : (res && Array.isArray(res.figures) ? res.figures.length : 0)
        try {
          const fresh = await api.paperFigures(paper.id, signal)
          if (fresh && Array.isArray(fresh.figures)) n = fresh.figures.length
        } catch (err) { /* 回读失败就退回提取接口给的 extracted */ }
        if (isStale()) return
        if (n) {
          toast('已从 PDF 重新提取 ' + n + ' 张图表')
          showStatus('已更新 ' + n + ' 张图表 · 查看 →')
        } else {
          toast('没识别到图表，可以检查这篇的 PDF 是否带图注', 'warn')
          showStatus('没识别到图表', true)
        }
      } catch (err) {
        toast(err.message || '重拉图表失败', 'error')
        showStatus('重拉失败 · 查看 →', true)
      } finally {
        busy = false
        wrap.classList.remove('is-busy')
        wrap.removeAttribute('aria-busy')
        main.disabled = false
        caret.disabled = false
        caret.textContent = '▾'
        label.textContent = '图表'
      }
    }

    return unit
  }

  const header = h('section.detail-head',
    h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap' } },
      h('button.btn.sm.ghost', { type: 'button', onclick: () => history.length > 1 ? history.back() : go('#/') }, '← 返回'),
      h('span.badge.type', typeLabel(paper.type)),
      pdfOk ? h('span.badge.pdf', 'PDF 已关联') : (paper.pdfFile ? h('span.badge', '⚠ PDF 文件丢失') : null),
      h('span.right', { style: { marginLeft: 'auto', display: 'flex', gap: '8px', alignItems: 'center' } },
        // 收藏（一键星标）＋ 评分（1-5 星），两者独立
        h('span.star-wrap', { title: '收藏：把这篇放进「我的收藏」' },
          components.starButton(paper, {
            onChange: on => {
              if (isStale()) return
              toast(on ? '已加入收藏' : '已取消收藏')
              if (getState().query.starred && !on) render()
            }
          })
        ),
        starsNode(paper.rating, {
          interactive: true,
          onChange: async value => {
            try {
              await api.updatePaper(paper.id, { rating: value })
              if (isStale()) return
              paper.rating = value
              toast(value ? '已打 ' + value + ' 分' : '已清除评分')
              render()
            } catch (err) { toast(err.message, 'error') }
          }
        }),
        buildMoreMenu()
      )
    ),
    h('h1.detail-title', paper.title),
    h('div.detail-authors', { text: authorsText(paper.authors, 20) }),
    links.length ? h('div', { style: { display: 'flex', gap: '14px', flexWrap: 'wrap', fontSize: '13.5px' } }, links) : null,
    h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '2px', alignItems: 'center' } },
      h('button.btn.sm.primary', { type: 'button', onclick: openCiteDialog, title: '查看并复制各种引用格式' }, '" 引用'),
      paper.pdfFile ? h('button.btn.sm', {
        type: 'button',
        onclick: () => {
          const panel = document.getElementById('pdf-panel')
          if (panel) panel.scrollIntoView({ behavior: 'smooth', block: 'start' })
        }
      }, '📄 查看 PDF') : null,
      buildFigureButton(),
      h('button.btn.sm', {
        type: 'button',
        title: '把这篇带进横向对比，再挑其它文献一起比',
        onclick: () => go('compare', { ids: [paper.id] })
      }, '⇄ 对比'),
      h('button.btn.sm', {
        type: 'button',
        title: '22 字段深度分析 + 五维评分（结果存本地）',
        onclick: () => go('deep/' + encodeURIComponent(paper.id))
      }, '🔬 深度分析')
    )
  )

  /* ---------- 元信息 ---------- */
  const volIssuePages = [
    paper.volume && '卷 ' + paper.volume,
    paper.issue && '期 ' + paper.issue,
    paper.pages && '页 ' + paper.pages
  ].filter(Boolean).join(' · ')
  const pdfDisplayName = paper.pdfFile ? String(paper.pdfFile).replace(/^[a-f0-9]{8,}-/i, '') : ''

  const metaRow = (label, value, opts) => h('div.meta-row',
    h('div.meta-k', label),
    h('div.meta-v', (opts && opts.title) ? { title: opts.title } : null, value)
  )

  /** 详情页里直接改分类：不用先去列表再找那颗文件夹按钮 */
  function changeFolderFromDetail () {
    foldersUi.openFolderPicker({
      current: paper.folder || '',
      signal,
      onPick: async path => {
        if ((paper.folder || '') === path) {
          toast('分类没有变化')
          return
        }
        try {
          await api.movePapers([paper.id], path)
          foldersUi.notifyChanged()
          if (isStale()) return
          toast('已移到「' + (path ? path.split('/').pop() : foldersUi.ROOT_LABEL) + '」')
          render()
        } catch (err) {
          toast('移动失败：' + (err && err.message ? err.message : err))
        }
      }
    })
  }

  const folderChip = h('button.card-folder.meta-folder' + (paper.folder ? '.has' : ''), {
    type: 'button',
    title: '当前分类：' + (paper.folder || '未分类') + '（点击更改）',
    onclick: () => changeFolderFromDetail()
  },
    folderGlyph('card-folder-ico'),
    h('span.meta-folder-t', paper.folder ? paper.folder.split('/').join(' / ') : '未分类'),
    h('span.meta-folder-edit', '更改')
  )

  const meta = h('section.panel',
    h('div.panel-head', '文献信息'),
    h('div.panel-body', h('div.meta-block',
      h('div.meta-grid',
        metaCell('年份', paper.year ? String(paper.year) : '—'),
        metaCell('类型', typeLabel(paper.type)),
        metaCell('期刊 / 会议', paper.venue || '—'),
        metaCell('卷期页码', volIssuePages || '—'),
        metaCell('出版社', paper.publisher || '—'),
        metaCell('收藏', paper.starred ? '★ 已收藏' : '—')
      ),
      h('div.meta-rows',
        metaRow('作者', authorsText(paper.authors, 40) || '—'),
        metaRow('关键词', (paper.keywords || []).join('、') || '—'),
        metaRow('分类', folderChip),
        metaRow('PDF 文件', pdfDisplayName || '未关联', { title: paper.pdfFile || '' })
      ),
      h('details.meta-more',
        h('summary', '更多信息（BibTeX 键 / 时间）'),
        h('div.meta-rows',
          metaRow('BibTeX 键', paper.bibtexKey || '—'),
          metaRow('加入时间', formatDate(paper.addedAt)),
          metaRow('最近修改', formatDate(paper.updatedAt) + (relTime(paper.updatedAt) ? '（' + relTime(paper.updatedAt) + '）' : ''))
        )
      )
    ))
  )

  /* ---------- 标签（可就地编辑） ---------- */
  const tagRow = h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' } })

  function renderTagRow () {
    const current = Array.isArray(paper.tags) ? paper.tags : []
    const nodes = [
      h('span', { style: { fontSize: '12.5px', color: 'var(--faint)' } }, '标签：')
    ]
    if (!current.length) {
      nodes.push(h('span', { style: { fontSize: '12.5px', color: 'var(--faint)' } }, '（还没有）'))
    }
    for (const tag of current) {
      nodes.push(h('span.tag-chip', { style: { display: 'inline-flex', gap: '4px', alignItems: 'center' } },
        h('button', {
          type: 'button',
          title: '按这个标签筛选',
          style: { border: '0', background: 'transparent', color: 'inherit', padding: '0', cursor: 'pointer' },
          onclick: () => { if (filterByTag) filterByTag(tag, { resetType: true, resetStatus: true }) }
        }, tag),
        h('button', {
          type: 'button',
          title: '移除这个标签',
          style: { border: '0', background: 'transparent', color: 'var(--faint)', padding: '0 1px', cursor: 'pointer' },
          onclick: async () => {
            try {
              const next = current.filter(t => t !== tag)
              await api.updatePaper(paper.id, { tags: next })
              paper.tags = next
              toast('已移除标签「' + tag + '」')
              renderTagRow()
              refreshMeta()
            } catch (err) { toast(err.message, 'error') }
          }
        }, '×')
      ))
    }
    nodes.push(h('button.btn.sm.ghost', {
      type: 'button',
      title: '添加标签',
      onclick: () => startAddTag()
    }, '＋ 添加标签'))
    tagRow.replaceChildren(...nodes)
  }

  function startAddTag () {
    const input = components.tagInput(paper.tags || [], {
      placeholder: '输入标签后回车；粘贴「a, b, c」会一次加三个',
      suggestions: (getState().tags || []).slice(0, 12)
    })
    const save = h('button.btn.sm.primary', { type: 'button' }, '保存标签')
    const cancel = h('button.btn.sm.ghost', { type: 'button', onclick: () => renderTagRow() }, '取消')
    save.addEventListener('click', async () => {
      const next = input.getTags()
      try {
        await api.updatePaper(paper.id, { tags: next })
        paper.tags = next
        toast('标签已保存')
        renderTagRow()
        refreshMeta()
      } catch (err) { toast(err.message, 'error') }
    })
    tagRow.replaceChildren(
      h('span', { style: { fontSize: '12.5px', color: 'var(--faint)' } }, '标签：'),
      h('div', { style: { flex: '1 1 320px', minWidth: '240px' } }, input),
      save,
      cancel
    )
  }

  renderTagRow()

  return { header, tagRow, meta }
}
