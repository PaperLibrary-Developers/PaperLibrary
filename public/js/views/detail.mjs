/* 文献详情页（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from '../util.mjs'
import * as components from '../components.mjs'
import * as md from '../markdown.mjs'
import { api } from '../api.mjs'
import * as pdfviewer from '../pdfviewer.mjs'
import { getState } from '../state/store.mjs'
import { filterByTag, go, lookupTitle, refreshMeta, render, savePrefs } from '../shell/app-api.mjs'
import * as foldersUi from '../folders.mjs'

  const { h, toast, confirmDialog, typeLabel, authorsText, relTime, formatDate, starsHtml } = util
  const { starsNode, folderGlyph } = components
  
  function metaCell (key, value, options) {
    const opts = options || {}
    if (!value) return null
    const content = opts.html
      ? h('div.v', { html: opts.html })
      : h('div.v', opts.title ? { text: value, title: opts.title } : { text: value })
    return h('div.meta-cell', h('div.k', key), content)
  }

  async function detailHandler (ctx) {
    const { outlet, routeParams, isStale } = ctx
    const id = routeParams.id
    // 换一篇文献时先把上一个阅读器释放掉（worker 和 canvas 都不便宜）
    disposePdfViewer()
    resetAnnotationBridge()
    outlet.replaceChildren(h('div.content', h('div.empty', h('div.big', '正在打开文献…'))))

    let data
    try {
      data = await api.getPaper(id)
    } catch (err) {
      outlet.replaceChildren(h('div.content', h('div.empty',
        h('div.big', '找不到这条文献'),
        h('p', { text: err.message }),
        h('button.btn', { type: 'button', onclick: () => { go('#/' ); } }, '← 回到书库')
      )))
      return
    }
    if (isStale()) return
    const paper = data.paper
    const pdfOk = data.pdfExists

    /* ---------- PDF 批注（荧光笔 / 便签） ---------- */
    // 阅读器只管画和改，数据从这里读写：打开时先取一次，改完由 savePdfAnnotations 落库。
    //
    // 这里有一层「存不下也不丢」的兜底（踩过一次：后台进程还是旧代码，PUT 回
    // 404「接口不存在：/api/papers/<id>/annotations」，标了一屏，一刷新全没了）：
    //   ① 读失败不拦页面，当作「这篇还没有批注」；
    //   ② 写失败先把这一份整表快照 + 期间删掉的 id 记进 localStorage（按文献 id 分键）；
    //   ③ 下次打开这篇：服务端那份与本地草稿合并 → 静默补写一次 → 成功后清草稿；
    //   ④ 同样的失败只提示一次，不会每标一笔弹一个红框。
    let pdfAnnotations = []
    let annotLoaded = false              // 是否真的读到了服务端那一份（读失败时不能拿空表去覆盖）
    if (pdfOk) {
      try {
        const res = await api.getAnnotations(paper.id)
        pdfAnnotations = Array.isArray(res && res.items) ? res.items : []
        annotLoaded = true
      } catch (err) {
        pdfAnnotations = []
      }
      if (isStale()) return
    }

    const annotDraft = readAnnotDraft(paper.id)
    const hasAnnotDraft = annotDraft.items.length > 0 || annotDraft.removed.length > 0
    if (hasAnnotDraft) {
      pdfAnnotations = mergeAnnotations(pdfAnnotations, annotDraft.items)
        .filter(item => annotDraft.removed.indexOf(item.id) < 0)
    }

    // 串行写：连着标两处时，两个 PUT 按顺序落地，慢的那个不会把新的覆盖掉
    let annotSaveChain = Promise.resolve()
    let annotKnownIds = pdfAnnotations.map(item => item.id)   // 上一次「服务端确实存住了」的 id 集合
    let annotWarned = false                                   // 同类错误只提醒一次

    /**
     * 保存批注（整表覆盖）。失败先落本地草稿，再给一次「照着做就能修好」的提示。
     * @param {Array} items
     * @param {{silent?:boolean}} [options] silent = 补写草稿时不打扰用户
     */
    function savePdfAnnotations (items, options) {
      const opts = options || {}
      const list = Array.isArray(items) ? items : []
      const ids = list.map(item => item && item.id).filter(Boolean)
      const removed = annotKnownIds.filter(id => ids.indexOf(id) < 0)
      annotSaveChain = annotSaveChain
        .then(() => api.saveAnnotations(paper.id, list))
        .then(() => {
          annotKnownIds = ids
          annotWarned = false
          clearAnnotDraft(paper.id)          // 服务端已经存住了，本地草稿可以扔
        })
        .catch(err => {
          writeAnnotDraft(paper.id, list, removed)   // 兜底：先存本机，下次打开自动补
          if (opts.silent || annotWarned) return
          annotWarned = true
          if (err && err.status === 404) {
            // 404 = 这台服务还没有批注接口：几乎都是「后台进程还是旧代码」（路由表在启动时
            // 就 import 进内存了，改了 routes.py 也不会热更）。提示要能照着做，别只报错。
            toast('批注暂存在本机：文献库后台还没这个接口（多半是旧进程）。重启一次文献库，重开这篇会自动补存。', 'warn')
          } else {
            toast('批注保存失败：' + err.message + '（已暂存在本机，下次打开会自动重试）', 'error')
          }
        })
    }
    // 上次留下的草稿：静默补写一次。服务端已支持就当场同步掉；还是旧进程就继续留草稿，不再打扰。
    if (hasAnnotDraft && annotLoaded) savePdfAnnotations(pdfAnnotations, { silent: true })

    /* ---------- 顶部 ---------- */

    const links = []
    if (paper.doi) links.push(h('a.link', { href: 'https://doi.org/' + paper.doi, target: '_blank', rel: 'noopener' }, 'DOI: ' + paper.doi))
    if (paper.url && paper.url !== 'https://doi.org/' + paper.doi) {
      links.push(h('a.link', { href: paper.url, target: '_blank', rel: 'noopener' }, '原文链接 ↗'))
    }

    /* ---------- 引用弹窗（仿 Google Scholar 的引用栏） ---------- */
    const CITE_FORMATS = [
      ['gb', 'GB/T 7714', '中文学位论文 / 期刊常用'],
      ['apa', 'APA 7', '心理学、社科常用'],
      ['mla', 'MLA 9', '人文学科常用'],
      ['bibtex', 'BibTeX', 'LaTeX / Zotero 用'],
      ['markdown', 'Markdown', '笔记软件用']
    ]
    function openCiteDialog () {
      const textBox = h('div.cite-text', { text: '加载中…' })
      let current = 'gb'
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
        textBox.textContent = '加载中…'
        try {
          const res = await api.cite(paper.id, current)
          textBox.textContent = res.text
        } catch (err) {
          textBox.textContent = '生成失败：' + err.message
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
            h('button.btn', { type: 'button', onclick: close }, '关闭'),
            h('button.btn.primary', {
              type: 'button',
              onclick: async () => {
                const ok = await util.copyText(textBox.textContent)
                toast(ok ? '已复制到剪贴板' : '复制失败，请手动选择文本', ok ? '' : 'error')
              }
            }, '复制')
          )
        )
      )
      function close () { back.remove() }
      document.body.appendChild(back)
      document.addEventListener('keydown', function onKey (ev) {
        if (ev.key === 'Escape') { close(); document.removeEventListener('keydown', onKey) }
      })
    }

    /* ---------- 三点菜单（把编辑/删除等收进二级，避免误点） ---------- */
    function buildMoreMenu () {
      const panel = h('div.more-menu', { hidden: true },
        h('a.more-item', { href: '#/edit/' + encodeURIComponent(paper.id) }, '✎ 编辑文献'),
        h('button.more-item', {
          type: 'button',
          title: '把这篇的 AI 总结与「与文献对话」的问答整理成一页，用浏览器打印成 PDF',
          onclick: () => { panel.hidden = true; exportPdf() }
        }, '⤓ 导出 AI 总结与对话（PDF）'),
        paper.doi ? h('a.more-item', {
          href: 'https://doi.org/' + paper.doi, target: '_blank', rel: 'noopener'
        }, '↗ 打开 DOI 页面') : null,
        h('div.more-sep'),
        h('button.more-item.danger', {
          type: 'button',
          onclick: async () => {
            panel.hidden = true
            const ok = await confirmDialog({
              title: '删除这条文献？',
              body: '记录会从文献库中移除' + (paper.pdfFile ? '，关联的 PDF 文件也会被删除' : '') + '，此操作不可撤销。',
              okText: '删除',
              danger: true
            })
            if (!ok) return
            try {
              await api.deletePaper(paper.id)
              toast('已删除')
              refreshMeta()
              go('#/')
            } catch (err) { toast(err.message, 'error') }
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
      })
      return wrap
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
        // 合并后新增的三项能力入口：图表画廊（单篇筛选）/ 横向对比（带上这篇）/ 深度分析
        h('button.btn.sm', {
          type: 'button',
          title: '看这篇的图表；也可以从图库进入跨文献的图表画廊',
          onclick: () => go('figures', { paperId: paper.id })
        }, '🖼 图表'),
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
    // 反馈：原来等宽网格里长短字段混排，「作者」被挤成三行、「PDF 文件」孤零零掉到第二行，
    // 右边还留一大片空白。现在分两级：
    //   短字段 → 等宽小格（一行排满）
    //   长文本 → 各自独占整行，标签列宽固定、值自动换行
    //   低频字段 → 收进「更多」折叠，正文版面保持清爽

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
        onPick: async path => {
          if ((paper.folder || '') === path) {
            toast('分类没有变化')
            return
          }
          try {
            await api.movePapers([paper.id], path)
            foldersUi.notifyChanged()
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
        // 点标签 = 按它筛选；右边的 × 用来从这里移除
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

    /* ---------- 导出 PDF：AI 总结 + 与这篇文献的对话 ---------- */

    /**
     * 生成一份「只含 AI 总结与对话」的打印视图，然后调起浏览器打印。
     * 选「另存为 PDF」就得到一份干净的文件——不需要任何第三方库，中文也不会乱码。
     */
    async function exportPdf () {
      try { await refreshChat() } catch {}
      const chat = Array.isArray(paper.chat) ? paper.chat : []
      const pairs = []
      chat.forEach(msg => {
        if (msg.role === 'user') pairs.push({ q: msg.content || '', at: msg.at, images: msg.images || 0, answers: [] })
        else if (pairs.length) pairs[pairs.length - 1].answers.push(msg)
      })
      const metaBits = [
        authorsText(paper.authors, 20),
        paper.year ? String(paper.year) : '',
        paper.venue || '',
        paper.doi ? 'DOI: ' + paper.doi : ''
      ].filter(Boolean)

      const doc = h('article', { id: 'export-doc' },
        h('h1.export-title', paper.title || '（未命名文献）'),
        h('div.export-meta', metaBits.join(' · ')),
        h('h2', 'AI 总结'),
        paper.aiSummary
          ? h('div.md', { html: md.render(paper.aiSummary, { byTitle: lookupTitle }) })
          : h('p.export-empty', '（这篇还没有生成 AI 总结）'),
        paper.summaryZh ? h('h3', '一句话总结') : null,
        paper.summaryZh ? h('div.md', { html: md.render(paper.summaryZh, { byTitle: lookupTitle }) }) : null,
        h('h2', '与这篇文献的对话' + (pairs.length ? '（' + pairs.length + ' 轮）' : '')),
        pairs.length
          ? h('div.export-chat', ...pairs.map((pair, idx) => h('section.export-pair',
            h('div.export-q',
              h('span.export-num', 'Q' + (idx + 1)),
              ' ' + pair.q,
              pair.images ? h('span.export-note', '（含 ' + pair.images + ' 张图）') : null
            ),
            pair.answers.length
              ? pair.answers.map(ans => h('div.export-a',
                h('div.export-a-meta', [
                  'AI' + (ans.model ? ' · ' + ans.model : ''),
                  ans.at ? util.formatDate(ans.at) : '',
                  ans.sources && ans.sources.length ? '来源：' + ans.sources.join('、') : ''
                ].filter(Boolean).join(' · ')),
                h('div.md', { html: md.render(ans.content || '', { byTitle: lookupTitle }) })
              ))
              : h('div.export-empty', '（这条提问还没有回答）')
          )))
          : h('p.export-empty', '（还没有对话记录）'),
        h('div.export-foot', '由 PaperLibrary 导出 · ' + util.formatDate(new Date().toISOString()))
      )

      // 文件名：浏览器默认用页面标题作为「另存为 PDF」的文件名
      const oldTitle = document.title
      document.title = String(paper.title || 'PaperLibrary 导出').replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80)
      document.body.appendChild(doc)
      document.body.classList.add('exporting')
      let cleaned = false
      const cleanup = () => {
        if (cleaned) return
        cleaned = true
        document.body.classList.remove('exporting')
        if (doc.parentNode) doc.parentNode.removeChild(doc)
        document.title = oldTitle
        window.removeEventListener('afterprint', cleanup)
      }
      window.addEventListener('afterprint', cleanup)
      toast('已生成导出视图：在打印窗口把「目标打印机」选成「另存为 PDF」')
      try {
        window.print()
      } catch (err) {
        cleanup()
        toast('这台浏览器不支持直接打印：' + err.message, 'error')
      }
      setTimeout(cleanup, 60000)   // 有的浏览器不触发 afterprint，兜底清理
    }


    const summaryText = String(paper.summary || '').trim()
    const summaryZhView = h('div.md')
    const summaryRawView = h('div.md', { html: md.render(summaryText, { byTitle: lookupTitle }) })
    const summaryZhMeta = h('div.hint', '')
    // 有中文就用中文；否则原文优先（有原文摘要时看原文，没有就直接看中文那页的说明）
    let summaryMode = paper.summaryZh ? 'zh' : (summaryText ? 'raw' : 'zh')

    const summaryTabZh = h('button.read-tab', { type: 'button' }, '中文')
    const summaryTabRaw = h('button.read-tab', { type: 'button' }, '原文')
    const summaryBtn = h('button.btn.sm.primary', { type: 'button' }, '生成中文总结')

    function summaryButtonLabel () {
      if (paper.summaryZh) return '重新生成'
      // 有英文摘要 → 翻译；没有摘要 → 依据题录/AI 总结/正文现写一段
      return summaryText ? '译成中文' : '生成中文总结'
    }

    function renderSummaryZh () {
      if (paper.summaryZh) {
        summaryZhView.innerHTML = md.render(paper.summaryZh, { byTitle: lookupTitle })
        summaryZhMeta.textContent = paper.summaryZhAt
          ? '生成于 ' + util.formatDate(paper.summaryZhAt) + (paper.summaryZhModel ? ' · ' + paper.summaryZhModel : '')
          : ''
      } else {
        summaryZhView.replaceChildren(h('div', { style: { color: 'var(--muted)' } },
          h('p', summaryText
            ? '还没有中文版本。点右上角「译成中文」，模型会把这篇文章的摘要翻译成中文并保存下来。'
            : '这篇还没有摘要。点右上角「生成中文总结」，模型会依据题录、AI 总结与 PDF 正文写一段中文总结。'),
          h('p', { style: { fontSize: '13px' } }, '只生成这一次，之后在「中文 / 原文」之间切换不会再调用模型。')))
        summaryZhMeta.textContent = ''
      }
    }

    function setSummaryMode (mode) {
      summaryMode = mode === 'raw' && summaryText ? 'raw' : 'zh'
      summaryZhView.hidden = summaryMode !== 'zh'
      summaryRawView.hidden = summaryMode === 'zh'
      summaryTabZh.classList.toggle('active', summaryMode === 'zh')
      summaryTabRaw.classList.toggle('active', summaryMode === 'raw')
      summaryTabRaw.hidden = !summaryText        // 没有原文摘要时不显示「原文」
      summaryBtn.textContent = summaryButtonLabel()
    }
    summaryTabZh.addEventListener('click', () => setSummaryMode('zh'))
    summaryTabRaw.addEventListener('click', () => setSummaryMode('raw'))

    async function generateSummaryZh (ev) {
      const btn = ev && ev.currentTarget ? ev.currentTarget : summaryBtn
      const isTranslate = !paper.summaryZh && !!summaryText
      btn.setAttribute('disabled', '')
      btn.textContent = isTranslate ? '翻译中…' : '生成中…'
      summaryZhMeta.textContent = '正在请求模型…'
      summaryZhMeta.style.color = ''
      try {
        const res = await api.translateSummary(paper.id)
        paper.summaryZh = res.paper ? res.paper.summaryZh : ''
        paper.summaryZhAt = res.paper ? res.paper.summaryZhAt : ''
        paper.summaryZhModel = res.paper ? res.paper.summaryZhModel : ''
        renderSummaryZh()
        setSummaryMode('zh')
        toast(res.translated === false ? '原文已经是中文，直接用原文' : '已生成并保存')
        summaryZhMeta.style.color = 'var(--accent-text)'
      } catch (err) {
        summaryZhMeta.textContent = '失败：' + err.message
        summaryZhMeta.style.color = 'var(--danger)'
        toast('失败：' + err.message, 'error')
      } finally {
        btn.removeAttribute('disabled')
        setSummaryMode(summaryMode)
      }
    }
    summaryBtn.addEventListener('click', generateSummaryZh)

    renderSummaryZh()
    setSummaryMode(summaryMode)
    // 面板永远显示：没有摘要的文献也能一键生成中文总结（反馈 #9）
    const summaryPanel = h('section.panel', { id: 'summary-panel' },
      h('div.panel-head',
        h('div.read-tabs', summaryTabZh, summaryTabRaw),
        h('div.right', summaryBtn)
      ),
      h('div.panel-body',
        h('div.summary-views', summaryZhView, summaryRawView),
        summaryZhMeta
      )
    )

    /* ---------- 参考文献列表 ---------- */

    const refs = Array.isArray(paper.refs) ? paper.refs : []
    const refsPanel = refs.length
      ? h('section.panel',
        h('div.panel-head', '参考文献',
          h('div.right', h('a.btn.sm.ghost', { href: '#/edit/' + encodeURIComponent(paper.id) }, '编辑'))
        ),
        h('div.panel-body', h('div', { style: { display: 'grid', gap: '8px' } },
          refs.map(ref => h('div', {
            style: { display: 'grid', gridTemplateColumns: '46px minmax(0,1fr)', gap: '10px', alignItems: 'start' }
          },
            h('div', { html: md.refChip(ref.num, '') }),
            h('div',
              h('div', { style: { fontSize: '14px' } }, ref.text || '（未填写）'),
              ref.url ? h('a.link', { href: ref.url, target: '_blank', rel: 'noopener', style: { fontSize: '12.5px' } }, ref.url) : null
            )
          ))
        ))
      )
      : null

    /* ---------- PDF ---------- */

    const uploadInput = h('input', {
      type: 'file',
      accept: 'application/pdf,.pdf',
      style: { display: 'none' },
      onchange: async ev => {
        const file = ev.target.files && ev.target.files[0]
        if (!file) return
        const hint = toast('正在上传 ' + file.name + ' …')
        try {
          const res = await api.uploadPdf(file, paper.id, ratio => { /* 进度可用于调试 */ })
          await api.updatePaper(paper.id, { pdfFile: res.pdfFile })
          // 上传时服务端会顺手提取正文，这里把结果告诉用户
          if (res.extract && res.extract.ok) {
            toast('PDF 已关联，并已提取正文 ' + res.extract.chars + ' 字')
          } else if (res.extract && res.extract.ok === false) {
            toast('PDF 已关联，但正文提取失败：' + res.extract.error, 'warn')
          } else {
            toast('PDF 已关联')
          }
          // 刷新统计并重绘当前页，避免还显示「还没有关联 PDF」
          await refreshMeta()
          render()
        } catch (err) {
          toast(err.message, 'error')
        }
      }
    })

    // 内置 PDF 阅读器的挂载点。真正的渲染在页面组装之后异步进行：
    // 成功用自绘阅读器（风格跟网站一致），失败/没装可选依赖就回退到浏览器自带查看器。
    const pdfHost = h('div.pdf-viewer-host', { id: 'pdf-viewer-host' })

    const pdfPanel = h('section.panel', { id: 'pdf-panel' },
      h('div.panel-head', 'PDF 原文',
        h('div.right',
          pdfOk ? h('a.btn.sm', { href: api.pdfUrl(paper.id), target: '_blank', rel: 'noopener' }, '新标签页打开') : null,
          h('button.btn.sm', { type: 'button', onclick: () => uploadInput.click() }, paper.pdfFile ? '更换文件' : '上传 PDF'),
          paper.pdfFile ? h('button.btn.sm.danger', {
            type: 'button',
            onclick: async () => {
              const ok = await confirmDialog({
                title: '取消关联这个 PDF？',
                body: '只会取消关联并删除副本，不会影响你磁盘上其他位置的原始文件。',
                okText: '取消关联',
                danger: true
              })
              if (!ok) return
              try {
                await api.updatePaper(paper.id, { pdfFile: '' })
                toast('已取消关联')
                render()
              } catch (err) { toast(err.message, 'error') }
            }
          }, '取消关联') : null
        )
      ),
      uploadInput,
      pdfOk
        ? pdfHost
        : h('div.pdf-empty',
          h('div', { text: paper.pdfFile ? '关联的 PDF 文件不见了：' + paper.pdfFile : '这条文献还没有关联 PDF。' }),
          h('div', { style: { fontSize: '13px', color: 'var(--faint)', maxWidth: '560px' } },
            '上传后文件会复制到数据目录的 papers/ 文件夹，之后无论原文件挪到哪里都能继续打开。也可以在编辑页填写「PDF 文件名」来关联 papers/ 目录里已有的文件。'),
          h('button.btn.primary', { type: 'button', onclick: () => uploadInput.click() }, '选择 PDF 文件上传')
        )
    )

    /* ---------- AI 总结（放在 PDF 右边） ---------- */

    const aiBody = h('div.md')
    const aiMeta = h('div.hint', '')
    const aiStatus = h('span.hint', '')
    const ftInfo = (data.fulltext || {})
    let ftReady = !!ftInfo.has

    function renderAiSummary (text) {
      if (!text) {
        aiBody.replaceChildren(h('div', { style: { color: 'var(--muted)' } },
          h('p', '还没有生成总结。'),
          h('p', { text: paper.pdfFile
            ? '点下面的按钮，模型会读这篇的题录与正文节选，生成结构化中文总结并保存在本地。'
            : '先在右侧上传 PDF 原文，总结质量会好很多；也可以只用题录与摘要生成。' })))
        return
      }
      aiBody.innerHTML = md.render(text, { byTitle: lookupTitle })
    }
    renderAiSummary(paper.aiSummary)

    if (paper.aiSummaryAt) {
      aiMeta.textContent = `生成于 ${util.formatDate(paper.aiSummaryAt)}${paper.aiSummaryModel ? ' · ' + paper.aiSummaryModel : ''}`
    }

    const aiButton = h('button.btn.sm.primary', { type: 'button' }, paper.aiSummary ? '重新生成' : '生成总结')

    /** 提取全文（有 PDF 但还没提取时用） */
    const extractButton = h('button.btn.sm', { type: 'button', title: '从 PDF 里抽出文字，供搜索与总结使用' }, '提取全文')
    /** 扫描件（图片版 PDF）走识图：浏览器渲染每一页 → 模型抄成文字 → 存为正文 */
    const ocrButton = h('button.btn.sm', {
      type: 'button',
      title: '这是图片版 PDF（没有文字层）时用：逐页发给模型识别成文字，之后就能全文检索与总结',
      hidden: true
    }, '识图识别全文')
    const ftStatus = h('div.hint', '')

    function renderFtState () {
      extractButton.hidden = !paper.pdfFile || ftReady
      ocrButton.hidden = !paper.pdfFile
      if (!paper.pdfFile) {
        ftStatus.textContent = '还没有 PDF 原文；上传后会自动提取正文。'
        ftStatus.style.color = 'var(--muted)'
      } else if (ftReady) {
        ftStatus.textContent = '✓ 已提取 PDF 正文（可被全文检索，也会用于 AI 总结）'
        ftStatus.style.color = 'var(--accent-text)'
      } else {
        ftStatus.textContent = '还没有提取 PDF 正文。如果是扫描件（图片版），用「识图识别全文」。'
        ftStatus.style.color = 'var(--muted)'
      }
    }

    // 扫描件识图：逐页渲染 → 模型抄录 → 最后存成这篇的正文
    ocrButton.addEventListener('click', async () => {
      const viewer = activePdfViewer
      if (!viewer || typeof viewer.capturePage !== 'function') {
        toast('内置阅读器还没就绪（或没装 pdfjs-dist），暂时无法逐页识图', 'warn')
        return
      }
      const total = typeof viewer.getPageCount === 'function' ? viewer.getPageCount() : 0
      if (!total) { toast('读不到页数，稍后再试', 'warn'); return }
      const ok = await confirmDialog({
        title: '用识图模型识别全文？',
        body: '会把每一页渲染成图片发给模型抄录，共 ' + total + ' 页（也就是 ' + total +
          ' 次模型调用，扫描件大约几角钱）。识别完成后会存成这篇文献的正文，之后能全文检索、生成总结与问答。',
        okText: '开始识别',
        danger: false
      })
      if (!ok) return
      ocrButton.setAttribute('disabled', '')
      const parts = []
      try {
        for (let n = 1; n <= total; n++) {
          ocrButton.textContent = '识别中 ' + n + '/' + total + '…'
          ftStatus.textContent = '正在识别第 ' + n + ' / ' + total + ' 页…'
          ftStatus.style.color = ''
          const dataUrl = await viewer.capturePage(n, { scale: 2, format: 'image/jpeg', quality: 0.85 })
          if (!dataUrl) { parts.push('（第 ' + n + ' 页渲染失败）'); continue }
          const res = await api.transcribeImage(paper.id, dataUrl, n)
          const text = String((res && res.text) || '').trim()
          parts.push('===== 第 ' + n + ' 页 =====\n' + (text || '（本页无文字）'))
        }
        const merged = parts.join('\n\n')
        const saved = await api.saveFulltextText(paper.id, merged)
        ftReady = true
        renderFtState()
        ftStatus.textContent = '✓ 识图完成，已存为正文 ' + saved.chars + ' 字（可全文检索，也会用于 AI 总结）'
        ftStatus.style.color = 'var(--accent-text)'
        toast('识图识别完成：' + saved.chars + ' 字')
      } catch (err) {
        ftStatus.textContent = '识图失败：' + err.message
        ftStatus.style.color = 'var(--danger)'
        toast('识图失败：' + err.message, 'error')
      } finally {
        ocrButton.removeAttribute('disabled')
        ocrButton.textContent = '识图识别全文'
      }
    })

    extractButton.addEventListener('click', async () => {
      extractButton.setAttribute('disabled', '')
      extractButton.textContent = '提取中…'
      ftStatus.textContent = '正在从 PDF 抽文字…'
      ftStatus.style.color = ''
      try {
        const res = await api.extractFulltext(paper.id)
        ftReady = true
        toast(`已提取 ${res.pages} 页 / ${res.chars} 字`)
        ftStatus.textContent = '✓ 已提取 ' + res.chars + ' 字（可被全文检索，也会用于 AI 总结）'
        ftStatus.style.color = 'var(--accent-text)'
      } catch (err) {
        ftStatus.textContent = '提取失败：' + err.message
        ftStatus.style.color = 'var(--danger)'
        toast('提取失败：' + err.message, 'error')
      } finally {
        extractButton.removeAttribute('disabled')
        extractButton.textContent = '提取全文'
        renderFtState()
      }
    })
    renderFtState()

    aiButton.addEventListener('click', async () => {
      aiButton.setAttribute('disabled', '')
      aiButton.textContent = '生成中…'
      aiStatus.textContent = '正在请求模型…'
      aiStatus.style.color = ''
      let acc = ''
      aiBody.replaceChildren(h('div', { style: { color: 'var(--muted)' } }, '…'))
      try {
        await api.aiSummarizeStream(paper.id, {
          save: true,
          onStart: info => {
            if (info.autoExtract && info.autoExtract.ok) {
              ftReady = true
              toast('已自动提取 PDF 正文 ' + info.autoExtract.chars + ' 字')
              renderFtState()
            } else if (info.autoExtract && info.autoExtract.ok === false) {
              toast('自动提取正文失败：' + info.autoExtract.error, 'warn')
            }
            aiStatus.textContent = info.usedFulltext
              ? `已读入 ${info.chars} 字正文节选，模型 ${info.model} 正在生成…`
              : `未找到 PDF 正文（${(info.autoExtract && info.autoExtract.error) || '没有关联 PDF'}），仅依据题录与摘要；模型 ${info.model} 正在生成…`
          },
          onDelta: chunk => {
            acc += chunk
            aiBody.innerHTML = md.render(acc, { byTitle: lookupTitle })
          }
        })
        aiStatus.textContent = '已生成并保存 ✓'
        aiStatus.style.color = 'var(--accent-text)'
        aiMeta.textContent = '刚刚生成 · ' + (paper.aiSummaryModel || '')
        toast('AI 总结已保存')
        paper.aiSummary = acc
        renderFtState()
      } catch (err) {
        aiStatus.textContent = '失败：' + err.message
        aiStatus.style.color = 'var(--danger)'
        if (!acc) renderAiSummary('')
        toast('生成失败：' + err.message, 'error')
      } finally {
        aiButton.removeAttribute('disabled')
        aiButton.textContent = '重新生成'
      }
    })

    // 「AI 总结」与「与这篇文献对话」共用一块面板，靠标题栏左上角切换
    // （反馈：两块并排高度对不齐，合并后整列高度就能和左边 PDF 框一样长）
    const aiPanelActions = h('div.right', { id: 'read-actions-summary' },
      extractButton,
      ocrButton,
      h('a.btn.sm.ghost', { href: '#/settings' }, '模型设置'),
      aiButton
    )

    /* ---------- 与这篇文献对话（替代原来的「我的笔记」） ---------- */

    const chatScroll = h('div.chat-scroll', { id: 'chat-body' })
    // 提问历史（二级菜单）：左边一列，点哪条就跳到那一问一答
    const chatQList = h('div.chat-q-list')
    const chatQCount = h('span.chat-q-count', '')
    const chatQsPane = h('aside.chat-qs',
      h('div.chat-qs-head', h('span', '提问历史'), chatQCount),
      chatQList
    )
    const chatBody = chatScroll
    const chatInput = h('textarea.textarea', {
      rows: 2,
      id: 'chat-input',
      placeholder: '就这篇文献提问，例如：这篇用了什么方法？结论对什么体系成立？（Enter 发送，Shift + Enter 换行）',
      onkeydown: ev => {
        // 回车发送，Shift + 回车换行；中文输入法组词过程中不能当回车用
        if (ev.key === 'Enter' && !ev.shiftKey && !ev.ctrlKey && !ev.metaKey && !ev.isComposing) {
          ev.preventDefault()
          sendQuestion()
          return
        }
        if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); sendQuestion() }
      }
    })
    const chatStatus = h('span.hint', '')

    /* ---------- 贴图提问：待发送的图片（不落库，只随请求发给模型） ---------- */
    let pendingImages = []          // [{ dataUrl, name }]
    const chatThumbs = h('div.chat-thumbs', { hidden: true })

    function renderChatThumbs () {
      chatThumbs.hidden = pendingImages.length === 0
      chatThumbs.replaceChildren(...pendingImages.map((img, idx) => h('div.chat-thumb',
        h('img', { src: img.dataUrl, alt: img.name || '待发送的图片' }),
        h('button.chat-thumb-del', {
          type: 'button',
          title: '移除这张图',
          onclick: () => { pendingImages.splice(idx, 1); renderChatThumbs() }
        }, '×')
      )))
    }

    /** 把 File/Blob 读成 dataURL 后进待发送列表 */
    function addImageFile (file) {
      if (!file || !/^image\//.test(file.type || '')) return false
      if (pendingImages.length >= 4) { toast('一次最多带 4 张图', 'warn'); return false }
      const reader = new FileReader()
      reader.onload = () => {
        pendingImages.push({ dataUrl: String(reader.result || ''), name: file.name || '图片' })
        renderChatThumbs()
      }
      reader.onerror = () => toast('读取图片失败', 'error')
      reader.readAsDataURL(file)
      return true
    }

    const chatFileInput = h('input', {
      type: 'file', accept: 'image/*', multiple: true, style: { display: 'none' },
      onchange: ev => {
        for (const file of Array.from((ev.target && ev.target.files) || [])) addImageFile(file)
        if (ev.target) ev.target.value = ''
      }
    })
    const attachBtn = h('button.btn.sm.ghost', {
      type: 'button',
      title: '贴图提问：选图片，或直接 Ctrl+V 粘贴、把图片拖进来',
      onclick: () => chatFileInput.click()
    }, '🖼 图片')
    /**
     * 「联网搜索（Exa）」勾选框：
     *   勾上 → 这一次提问会调用 Exa（约 $0.011，走最便宜的基础检索档）；
     *   不勾 → 和以前一样（提问里出现「相关研究 / 最新进展」时用免费的学术检索）。
     * 状态记在偏好里，但默认关闭，避免悄悄花钱。
     */
    const webToggle = h('input.check', {
      type: 'checkbox',
      id: 'chat-web',
      checked: getState().prefs.chatUseWeb === true
    })
    const webLabel = h('label', {
      style: { display: 'flex', gap: '5px', alignItems: 'center', fontSize: '12.5px', color: 'var(--muted)' },
      title: '勾上后这次提问会用 Exa 联网检索（约 $0.011/次，走最便宜的基础档）；不勾就用免费的学术检索。先在「设置 → 网络检索」里配好 Exa Key。'
    }, webToggle, '联网搜索')
    webToggle.addEventListener('change', () => {
      savePrefs({ chatUseWeb: !!webToggle.checked }, { silent: true })
    })
    // 注：Exa 的「深度检索 / Answer」按 /1k 请求起计费，比基础检索贵，个人免费额度下不划算，\n    // 所以界面上不提供入口（服务端保留能力，默认关闭）。\n

    // 粘贴图片（截图后 Ctrl+V 直接进）与拖拽
    chatInput.addEventListener('paste', ev => {
      const items = (ev.clipboardData && ev.clipboardData.items) || []
      let handled = false
      for (const item of items) {
        if (item.kind === 'file' && /^image\//.test(item.type || '')) {
          const file = item.getAsFile()
          if (file && addImageFile(file)) handled = true
        }
      }
      if (handled) ev.preventDefault()
    })
    chatInput.addEventListener('dragover', ev => { ev.preventDefault() })
    chatInput.addEventListener('drop', ev => {
      const files = (ev.dataTransfer && ev.dataTransfer.files) || []
      if (!files.length) return
      let handled = false
      for (const file of Array.from(files)) if (addImageFile(file)) handled = true
      if (handled) ev.preventDefault()
    })

    /** 一条消息气泡；userIndex 是它所属提问在聊天数组里的下标（用于定位与高亮） */
    function chatBubble (msg, msgIndex, pairIndex) {
      const isUser = msg.role === 'user'
      return h('div.chat-msg' + (isUser ? '.user' : '.ai'), {
        'data-msg-index': String(msgIndex),
        'data-pair-index': String(pairIndex)
      },
        h('div.chat-meta',
          isUser ? '我' : ('AI' + (msg.model ? ' · ' + msg.model : '')),
          msg.at ? h('span', { style: { marginLeft: '8px', color: 'var(--faint)' } }, util.relTime(msg.at)) : null,
          isUser && msg.images
            ? h('span', { style: { marginLeft: '8px', color: 'var(--faint)' } }, '（含 ' + msg.images + ' 张图）')
            : null,
          !isUser && msg.sources && msg.sources.length
            ? h('span', { style: { marginLeft: '8px', color: 'var(--faint)' } },
              '来源：' + msg.sources.map(s => String(s).slice(0, 18)).join('、'))
            : null
        ),
        isUser
          ? h('div.chat-text', { text: msg.content })
          : h('div.chat-text.md', { html: md.render(msg.content, { byTitle: lookupTitle }) })
      )
    }

    /** 提问历史（二级菜单）＋ 对话内容一起重画 */
    function renderChat (messages) {
      const list = messages || []
      // 每条提问在数组里的下标（回答跟随它，用于「点问题跳到回答」）
      const pairs = []
      list.forEach((msg, idx) => {
        if (msg.role === 'user') pairs.push({ index: idx, question: msg.content || '', at: msg.at })
      })
      chatQCount.textContent = pairs.length ? String(pairs.length) : ''

      if (!list.length) {
        chatBody.replaceChildren(h('div.hint', { style: { padding: '10px 0' } },
          '还没有对话。可以问：这篇解决了什么问题？方法的关键假设是什么？结论对哪些体系成立？'))
      } else {
        chatBody.replaceChildren(...list.map((msg, idx) => {
          const owner = pairs.filter(p => p.index <= idx).pop()
          return chatBubble(msg, idx, owner ? owner.index : -1)
        }))
      }

      if (!pairs.length) {
        chatQList.replaceChildren(h('div.chat-q-empty', '还没有提问'))
      } else {
        chatQList.replaceChildren(...pairs.map(pair => h('div.chat-q-item', {
          'data-pair-index': String(pair.index)
        },
          h('button.chat-q-text', {
            type: 'button',
            title: pair.question,
            onclick: () => scrollToPair(pair.index)
          },
            h('span.chat-q-num', 'Q' + (pairs.indexOf(pair) + 1)),
            h('span', { text: pair.question.length > 40 ? pair.question.slice(0, 40) + '…' : pair.question }),
            pair.at ? h('span.chat-q-time', util.relTime(pair.at)) : null
          ),
          h('button.chat-q-del', {
            type: 'button',
            title: '删除这一问一答',
            onclick: async ev => {
              ev.stopPropagation()
              const ok = await confirmDialog({
                title: '删除这条提问？',
                body: '「' + (pair.question.length > 30 ? pair.question.slice(0, 30) + '…' : pair.question) + '」以及它的回答会一起删掉。',
                okText: '删除',
                danger: true
              })
              if (!ok) return
              try {
                const res = await api.deleteChatPair(paper.id, pair.index)
                paper.chat = res.messages || []
                renderChat(paper.chat)
                chatStatus.textContent = '已删除这条提问'
                toast('已删除')
              } catch (err) { toast(err.message, 'error') }
            }
          }, '×')
        )))
      }

      // 自动定位到最新的一问一答（反馈：以前问完还要自己拖滚动条）
      if (list.length) {
        const raf = (typeof window !== 'undefined' && window.requestAnimationFrame) || (fn => setTimeout(fn, 0))
        raf(() => { chatBody.scrollTop = chatBody.scrollHeight })
      }
    }

    /** 点提问历史：把对应的问答滚到视野里并高亮一下 */
    function scrollToPair (pairIndex) {
      const want = String(pairIndex)
      // 注意：测试用的 DOM 桩对属性选择器支持有限，这里直接遍历子节点比较，
      // 不依赖 querySelector('[data-pair-index="…"]')。
      let target = null
      for (const node of chatBody.childNodes || []) {
        if (node.getAttribute && node.getAttribute('data-pair-index') === want) { target = node; break }
      }
      for (const node of chatBody.childNodes || []) {
        if (node.classList) node.classList.remove('hl')
      }
      if (target && target.classList) {
        target.classList.add('hl')
        // 只滚动对话区自己：scrollIntoView 会把整个页面一起往下带（反馈 #7），
        // 这里按两者的相对位置算偏移，只改 chatBody.scrollTop。
        const box = typeof chatBody.getBoundingClientRect === 'function' ? chatBody.getBoundingClientRect() : null
        const rect = typeof target.getBoundingClientRect === 'function' ? target.getBoundingClientRect() : null
        if (box && rect) {
          chatBody.scrollTop = Math.max(0, chatBody.scrollTop + (rect.top - box.top) - 6)
        } else if (typeof target.offsetTop === 'number') {
          chatBody.scrollTop = Math.max(0, target.offsetTop - 6)
        }
      }
      for (const item of chatQList.childNodes || []) {
        if (item.classList) item.classList.toggle('active', item.getAttribute('data-pair-index') === want)
      }
      if (target && target.classList) setTimeout(() => target.classList.remove('hl'), 1600)
    }
    renderChat(paper.chat)

    let streaming = false

    /**
     * 统一的提问入口：
     *  - 只有文字 → 走原来的「对话」接口（正文 + 补充材料 + 本库/联网检索）
     *  - 带图片   → 走「识图」接口（图 + 题录/摘要/正文节选），同样存进对话历史
     * regionInfo 用于「框选提问」：把「第 N 页框选」记在这条提问上。
     */
    async function askPaper (question, images, options) {
      const opts = options || {}
      const q = String(question || '').trim() || '解释这张图/表说明了什么。'
      if (streaming) return
      const pics = (images || []).slice(0, 4)
      const label = String(opts.label || (pics.length ? '含 ' + pics.length + ' 张图' : ''))
      const readinessNote = h('div.chat-msg.ai', { id: 'chat-stream-wrap' }, h('div.chat-meta', 'AI'),
        h('div.chat-text', { id: 'chat-stream' }, '…'))
      streaming = true
      sendButton.setAttribute('disabled', '')
      chatStatus.textContent = pics.length ? '正在把图片发给模型…' : '正在准备资料…'
      chatStatus.style.color = ''
      const optimistic = (paper.chat || []).concat([{
        role: 'user', content: q, at: new Date().toISOString(), images: pics.length || undefined
      }])
      chatBody.replaceChildren(...optimistic.map((msg, idx) => chatBubble(msg, idx, idx)), readinessNote)
      chatBody.scrollTop = chatBody.scrollHeight
      let acc = ''
      try {
        const hooks = {
          onStart: info => {
            chatStatus.textContent = pics.length
              ? ('已发送 ' + pics.length + ' 张图，模型 ' + (info.model || '') + ' 正在回答…')
              : ('资料来源：' + ((info.sources || []).join('、') || '题录'))
            if (info.autoExtract && info.autoExtract.ok) {
              ftReady = true
              renderFtState()
            }
          },
          onDelta: chunk => {
            acc += chunk
            const el = document.getElementById('chat-stream')
            if (el) el.innerHTML = md.render(acc, { byTitle: lookupTitle })
            chatBody.scrollTop = chatBody.scrollHeight
          }
        }
        if (pics.length) {
          await api.paperVisionStream(paper.id, {
            question: q,
            images: pics.map(p => (typeof p === 'string' ? p : p.dataUrl)),
            label
          }, hooks)
        } else {
          // 不带图时走对话接口：勾了「联网搜索」才用 Exa（花钱），否则和以前一样用免费的学术检索
          await api.paperChatStream(paper.id, q, Object.assign({ web: !!(webToggle && webToggle.checked) }, hooks))
        }
        chatStatus.textContent = '已保存到这篇文献的对话历史'
        chatStatus.style.color = 'var(--accent-text)'
        await refreshChat()
      } catch (err) {
        chatStatus.textContent = '失败：' + err.message
        chatStatus.style.color = 'var(--danger)'
        toast('提问失败：' + err.message, 'error')
        await refreshChat()
      } finally {
        streaming = false
        sendButton.removeAttribute('disabled')
      }
    }

    async function sendQuestion () {
      const question = String(chatInput.value || '').trim()
      if (!question && !pendingImages.length) return
      if (streaming) return
      const pics = pendingImages.slice()
      pendingImages = []
      renderChatThumbs()
      chatInput.value = ''
      await askPaper(question, pics, {})
    }

    async function refreshChat () {
      try {
        const res = await api.getChat(paper.id)
        paper.chat = res.messages || []
        renderChat(paper.chat)
      } catch {}
    }

    const sendButton = h('button.btn.primary', { type: 'button', onclick: sendQuestion }, '发送')
    // 「清空全部」按钮按反馈去掉了：现在用左边提问历史里每条的小 × 单独删除即可

    /* ---------- 合并成一个模块：AI 总结 / 与文献对话 ---------- */
    // 反馈：右边两块高度跟左边 PDF 对不齐。现在合成一块，标题栏左上角切换，
    // 整列（合并模块 + 下面的补充材料）高度 = 左边 PDF 框高度。

    /**
     * 「框选提问」：PDF 阅读器里框一块 → 这里弹一个小窗，默认问「解释这张图/表」，
     * 你也可以改成自己的问题；答完自动进对话历史。
     */
    function openRegionAsk (dataUrl, info) {
      const pageLabel = '第 ' + ((info && info.page) || '?') + ' 页框选'
      const preview = h('img', { src: dataUrl, alt: '框选区域预览', style: { maxWidth: '100%', maxHeight: '180px', border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)' } })
      const input = h('textarea.textarea', { rows: 3 },
        '解释这张图/表说明了什么。如果是表格，请说明各列含义与主要数据；如果是电镜/形貌图，请描述形貌特征（形状、大小量级、是否聚集）。')
      const answerBox = h('div.md', { style: { maxHeight: '260px', overflow: 'auto' } })
      const status = h('div.hint', '')
      const sendBtn = h('button.btn.primary', { type: 'button' }, '发送')
      const closeBtn = h('button.btn', { type: 'button' }, '关闭')

      const overlay = h('div.modal-back', { onclick: ev => { if (ev.target === overlay) close() } },
        h('div.modal', { role: 'dialog', 'aria-modal': 'true', style: { maxWidth: '720px' } },
          h('h3', '识图提问（' + pageLabel + '）'),
          h('div', { style: { display: 'grid', gap: '10px', maxHeight: '60vh', overflow: 'auto' } },
            preview,
            input,
            status,
            answerBox
          ),
          h('div.actions', sendBtn, closeBtn)
        )
      )
      const close = () => { overlay.remove(); document.removeEventListener('keydown', onKey) }
      const onKey = ev => { if (ev.key === 'Escape') close() }
      document.addEventListener('keydown', onKey)
      closeBtn.addEventListener('click', close)
      overlay.addEventListener('click', ev => { if (ev.target === overlay) close() })

      sendBtn.addEventListener('click', async () => {
        const question = String(input.value || '').trim() || '解释这张图/表说明了什么。'
        sendBtn.setAttribute('disabled', '')
        status.textContent = '正在把图片发给模型…'
        status.style.color = ''
        answerBox.replaceChildren(h('div', { style: { color: 'var(--muted)' } }, '…'))
        try {
          let acc = ''
          await api.paperVisionStream(paper.id, {
            question: question,
            images: [dataUrl],
            label: pageLabel
          }, {
            onDelta: chunk => {
              acc += chunk
              answerBox.innerHTML = md.render(acc, { byTitle: lookupTitle })
            }
          })
          status.textContent = '已保存到这篇文献的对话历史'
          status.style.color = 'var(--accent-text)'
          await refreshChat()
          setReadMode('chat')
        } catch (err) {
          status.textContent = '失败：' + err.message
          status.style.color = 'var(--danger)'
        } finally {
          sendBtn.removeAttribute('disabled')
        }
      })
      document.body.appendChild(overlay)
      if (typeof input.focus === 'function') input.focus()
    }

    const readViewSummary = h('div.read-view', { id: 'read-view-summary' },
      ftStatus,
      aiStatus,
      h('div.ai-scroll', aiBody),
      aiMeta
    )
    const readViewChat = h('div.read-view.chat-view', { id: 'read-view-chat', hidden: true },
      h('div.chat-wrap',
        chatQsPane,
        h('div.chat-main',
          chatBody,
          h('div.chat-compose',
            chatThumbs,
            chatInput,
            h('div.chat-compose-foot',
              sendButton,
              attachBtn,
              chatFileInput,
              webLabel,
              chatStatus,
              h('span', { style: { marginLeft: 'auto', fontSize: '12.5px', color: 'var(--faint)' } },
                '可贴图提问 · 资料来自：正文 + 补充材料 + 本库检索 + 联网检索')
            )
          )
        )
      )
    )

    const tabSummary = h('button.read-tab.active', { type: 'button' }, 'AI 总结')
    const tabChat = h('button.read-tab', { type: 'button' }, '与文献对话')

    function setReadMode (mode) {
      const chat = mode === 'chat'
      readViewSummary.hidden = chat
      readViewChat.hidden = !chat
      // 标题栏右侧那组按钮（提取全文 / 模型设置 / 重新生成）只属于 AI 总结，
      // 切到对话时必须真的藏起来（对应 CSS 里 .panel-head .right[hidden] 那条规则）
      aiPanelActions.hidden = chat
      tabSummary.classList.toggle('active', !chat)
      tabChat.classList.toggle('active', chat)
      if (chat) {
        const raf = (typeof window !== 'undefined' && window.requestAnimationFrame) || (fn => setTimeout(fn, 0))
        raf(() => { chatBody.scrollTop = chatBody.scrollHeight })
        // 注意要 preventScroll：直接 focus() 会让浏览器把输入框滚进视野，
        // 于是「点一下与文献对话，整页就跳到最底部」（反馈 #12）。
        try { chatInput.focus({ preventScroll: true }) } catch { chatInput.focus() }
      }
    }
    tabSummary.addEventListener('click', () => setReadMode('summary'))
    tabChat.addEventListener('click', () => setReadMode('chat'))

    const mergedPanel = h('section.panel', { id: 'read-panel' },
      h('div.panel-head',
        h('div.read-tabs', tabSummary, tabChat),
        aiPanelActions
      ),
      h('div.panel-body.read-panel-body', readViewSummary, readViewChat)
    )

    /* ---------- 补充材料（尽量紧凑：只显示文件名） ---------- */

    const attList = h('div', { style: { display: 'grid', gap: '6px' } })
    const attStatus = h('div.hint', '')
    const attInput = h('input', {
      type: 'file',
      multiple: true,
      style: { display: 'none' },
      onchange: async ev => {
        const files = Array.from(ev.target.files || [])
        if (!files.length) return
        attStatus.textContent = '正在上传 ' + files.length + ' 个文件…'
        for (const file of files) {
          try {
            const res = await api.uploadAttachment(paper.id, file)
            const ex = res.extract
            attStatus.textContent = ex && ex.ok
              ? `✓ ${file.name}：已抽取 ${ex.chars} 字，会用于 AI 总结与问答`
              : `✓ ${file.name}：已保存为附件（${ex ? ex.error : '未抽取文字'}）`
            attStatus.style.color = ex && ex.ok ? 'var(--accent-text)' : 'var(--muted)'
          } catch (err) {
            attStatus.textContent = '✗ ' + file.name + '：' + err.message
            attStatus.style.color = 'var(--danger)'
          }
        }
        ev.target.value = ''
        await loadAttachments()
        refreshMeta()
      }
    })

    // 附件列表：平时只列前 3 条，多出来的收进一个很不显眼的小翻页按钮里
    // （一篇文献的 SI 一般不超过 3 个，列表太长会把这一块撑高、挤掉上面的对话区）
    const ATT_VISIBLE = 3
    let attExpanded = false

    async function loadAttachments () {
      try {
        const res = await api.listAttachments(paper.id)
        const list = res.attachments || []
        paper.attachments = list
        if (!list.length) {
          attList.replaceChildren(h('div.hint.compact', '还没有补充材料（SI、附录、数据集说明等都可以放这里）'))
          return
        }
        const shown = attExpanded ? list : list.slice(0, ATT_VISIBLE)
        const restCount = list.length - shown.length
        const rows = shown.map(att => h('div.att-row',
          h('a.att-name', {
            href: api.attachmentUrl(paper.id, att.file),
            target: '_blank',
            rel: 'noopener',
            title: att.originalName + ' · ' + util.fileSize(att.size) +
              (att.hasText ? ' · 已抽取 ' + att.textChars + ' 字（用于总结与问答）' : ' · 未抽取文字（仅作附件）')
          }, att.originalName),
          att.hasText ? h('span.att-flag', { title: '已抽取文字，会用于 AI 总结与问答' }, '已读入') : null,
          h('button.att-del', {
            type: 'button',
            title: '删除这个补充材料',
            onclick: async () => {
              const ok = await confirmDialog({
                title: '删除这个补充材料？',
                body: '「' + att.originalName + '」会从这篇文献上移除。',
                okText: '删除',
                danger: true
              })
              if (!ok) return
              try {
                await api.deleteAttachment(paper.id, att.file)
                toast('已删除')
                await loadAttachments()
              } catch (err) { toast(err.message, 'error') }
            }
          }, '×')
        ))
        if (restCount > 0) {
          rows.push(h('button.att-more', {
            type: 'button',
            title: '展开剩下的 ' + restCount + ' 个补充材料',
            onclick: () => { attExpanded = true; loadAttachments() }
          }, '还有 ' + restCount + ' 个 ▾'))
        } else if (list.length > ATT_VISIBLE) {
          rows.push(h('button.att-more', {
            type: 'button',
            title: '收起',
            onclick: () => { attExpanded = false; loadAttachments() }
          }, '收起 ▴'))
        }
        attList.replaceChildren(...rows)
      } catch (err) {
        attList.replaceChildren(h('div.hint', { style: { color: 'var(--danger)' } }, '读取附件失败：' + err.message))
      }
    }

    // 反馈：这块只是上传补充材料的地方，越紧凑越好——只留一行文件名，其余信息放进 hover 提示
    const moreUploadBtn = h('button.btn.sm.ghost', {
      type: 'button',
      title: '上传 SI、附录、数据集说明等（支持 PDF / Word / 文本 / 图片）',
      onclick: () => attInput.click()
    }, '＋ 上传')
    const morePanel = h('section.panel.compact', { id: 'more-panel' },
      h('div.panel-head',
        h('span', '补充材料'),
        h('div.right', moreUploadBtn)
      ),
      h('div.panel-body.more-body',
        attInput,
        attList,
        attStatus
      )
    )
    loadAttachments()

    /* ---------- 可拖动的分隔条（自己调 PDF 与总结的宽度） ---------- */

    const readGrid = h('div.read-grid')
    const resizer = h('div.read-resizer', {
      title: '按住左右拖动，可以调整 PDF 与右侧面板的宽度比例',
      role: 'separator',
      'aria-orientation': 'vertical',
      tabindex: '0',
      'aria-label': '调整 PDF 与右侧面板的宽度'
    })
    // 恢复上次的宽度比例
    const savedRatio = Number(getState().prefs.readRatio)
    if (savedRatio >= 0.35 && savedRatio <= 0.78) {
      readGrid.style.setProperty('--read-ratio', String(savedRatio))
    }

    let dragging = false
    function onDragStart (ev) {
      dragging = true
      resizer.classList.add('dragging')
      document.body.style.userSelect = 'none'
      document.body.style.cursor = 'col-resize'
      ev.preventDefault()
    }
    function onDragMove (ev) {
      if (!dragging) return
      const rect = readGrid.getBoundingClientRect()
      if (!rect.width) return
      const ratio = Math.min(0.78, Math.max(0.35, (ev.clientX - rect.left) / rect.width))
      readGrid.style.setProperty('--read-ratio', String(Math.round(ratio * 1000) / 1000))
    }
    function onDragEnd () {
      if (!dragging) return
      dragging = false
      resizer.classList.remove('dragging')
      document.body.style.userSelect = ''
      document.body.style.cursor = ''
      const current = Number(readGrid.style.getPropertyValue('--read-ratio'))
      if (Number.isFinite(current) && current > 0) {
        savePrefs({ readRatio: current }, { silent: true })
      }
    }
    resizer.addEventListener('mousedown', onDragStart)
    document.addEventListener('mousemove', onDragMove)
    document.addEventListener('mouseup', onDragEnd)
    // 也支持键盘微调（无障碍）
    resizer.addEventListener('keydown', ev => {
      const current = Number(readGrid.style.getPropertyValue('--read-ratio')) || 0.68
      if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight') {
        ev.preventDefault()
        const next = Math.min(0.78, Math.max(0.35, current + (ev.key === 'ArrowLeft' ? -0.03 : 0.03)))
        readGrid.style.setProperty('--read-ratio', String(next))
        savePrefs({ readRatio: next }, { silent: true })
      }
    })

    /* ---------- 组装 ---------- */

    readGrid.replaceChildren(pdfPanel, resizer, h('div.read-side', mergedPanel, morePanel))

    outlet.replaceChildren(h('div.content.detail',
      header,
      tagRow,
      summaryPanel,
      // 左边读原文，右边看总结/对话：中间可拖动，两列等高
      readGrid,
      meta,
      refsPanel
    ))

    /* ---------- 内置 PDF 阅读器（失败自动回退到浏览器自带查看器） ---------- */

    currentPaperId = paper.id
    // 阅读器里「框选提问」的回调：框完把图交给这个函数弹窗提问
    currentRegionHandler = openRegionAsk
    // 批注：把这次读到的批注交给阅读器，改完走 savePdfAnnotations 落库
    currentAnnotations = pdfAnnotations
    currentAnnotationsHandler = savePdfAnnotations
    if (pdfOk) mountPdfViewer()

    // 右列高度对齐左边 PDF 框：CSS 里两边已经用了同一个 --read-h，
    // 这里再按实测像素补一次（阅读器加载完成后尺寸可能变），保证任何窗口尺寸下都齐平。
    bindReadColumnHeightSync()

    // 详情页快捷键：e 编辑，Esc 返回
    document.onkeydown = ev => {
      const tag = (ev.target.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || ev.target.isContentEditable) return
      if (ev.key === 'e') go('#/edit/' + encodeURIComponent(paper.id))
      if (ev.key === 'Escape') go('#/')
    }
  }

  /* ------------------------------------------------------------------ */
  /* 右列与 PDF 面板等高                                                 */
  /* ------------------------------------------------------------------ */

  let readHeightTimer = null
  let readHeightBound = false

  /**
   * 右列（总结/对话 + 补充材料）高度 = 左边 PDF **面板**（含标题栏）的高度。
   *
   * 为什么要用 JS 量：右列里是「总结正文」这种高度不定的内容，
   * 用 CSS 拉伸（align-items: stretch）会被内容撑高、反而比 PDF 面板长；
   * 所以这里按 PDF 面板的实测像素给一个**确定的高度**，多出来的内容在模块内部滚动。
   * （之前对齐的是里面的阅读区，正好矮掉 PDF 标题栏那一截，这是反馈 #7 第二次提到的。）
   */
  function syncReadColumnHeight () {
    if (typeof document === 'undefined') return
    const side = document.querySelector('.read-side')
    if (!side) return
    // 窄屏是上下排列，不锁高度
    if (typeof window !== 'undefined' && window.innerWidth && window.innerWidth <= 900) {
      side.style.height = ''
      return
    }
    const panel = document.getElementById('pdf-panel')
    if (!panel) return
    const rect = typeof panel.getBoundingClientRect === 'function' ? panel.getBoundingClientRect() : null
    const height = rect && rect.height ? rect.height : panel.offsetHeight
    if (!height || height < 160) return    // 还没布局好，等下一次
    side.style.height = Math.round(height) + 'px'
  }

  function scheduleReadColumnHeightSync () {
    if (readHeightTimer) clearTimeout(readHeightTimer)
    readHeightTimer = setTimeout(() => {
      readHeightTimer = null
      syncReadColumnHeight()
    }, 60)
  }

  function bindReadColumnHeightSync () {
    syncReadColumnHeight()
    // 图片/字体加载完、阅读器排版完成后尺寸会变，再补几次
    for (const delay of [120, 400, 1000, 2000]) setTimeout(syncReadColumnHeight, delay)
    if (readHeightBound || typeof window === 'undefined') return
    readHeightBound = true
    window.addEventListener('resize', scheduleReadColumnHeightSync)
  }

  /* ------------------------------------------------------------------ */
  /* PDF 阅读器：优先用自绘的 pdf.js 阅读器（风格统一），不行再用 iframe */
  /* ------------------------------------------------------------------ */

  let activePdfViewer = null
  let currentRegionHandler = null
  let currentAnnotations = []
  let currentAnnotationsHandler = null

  /** 离开详情页时把 worker / canvas 释放掉，避免来回切换页面越积越多 */
  function disposePdfViewer () {
    if (activePdfViewer && typeof activePdfViewer.destroy === 'function') {
      try { activePdfViewer.destroy() } catch (err) { console.warn('[pdf] 释放阅读器失败：', err) }
    }
    activePdfViewer = null
  }

  /** 断开批注回调（旧实例的回调不能把数据写到下一篇文献上） */
  function resetAnnotationBridge () {
    currentAnnotations = []
    currentAnnotationsHandler = null
  }

  /* ------------------------------------------------------------------ */
  /* 批注本地草稿：服务端存不下时的兜底（数据不丢，下次打开自动补）        */
  /* ------------------------------------------------------------------ */

  const ANNOT_DRAFT_PREFIX = 'lv:annot-draft:'
  const ANNOT_DRAFT_MAX = 1024 * 1024        // 单篇草稿上限 1MB，超了就不写（localStorage 只有 5MB）

  function annotDraftKey (paperId) {
    return ANNOT_DRAFT_PREFIX + String(paperId || '')
  }

  /**
   * 读本地草稿。
   * @returns {{items:Array, removed:Array<string>}} 没有/坏数据/隐私模式一律返回空草稿
   */
  function readAnnotDraft (paperId) {
    const empty = { items: [], removed: [] }
    let raw = null
    try {
      raw = window.localStorage.getItem(annotDraftKey(paperId))
    } catch (err) {
      return empty                          // 隐私模式 / 存储被禁
    }
    if (!raw) return empty
    try {
      const parsed = JSON.parse(raw)
      if (!parsed || parsed.v !== 1) return empty
      return {
        items: Array.isArray(parsed.items) ? parsed.items.filter(item => item && item.id) : [],
        removed: Array.isArray(parsed.removed) ? parsed.removed.map(String) : []
      }
    } catch (err) {
      return empty
    }
  }

  /** 记一份草稿（整表快照 + 这次删掉的 id）；空草稿等于删键 */
  function writeAnnotDraft (paperId, items, removed) {
    const list = Array.isArray(items) ? items : []
    const gone = Array.isArray(removed) ? removed : []
    try {
      if (!list.length && !gone.length) {
        window.localStorage.removeItem(annotDraftKey(paperId))
        return
      }
      const payload = JSON.stringify({ v: 1, at: new Date().toISOString(), items: list, removed: gone })
      if (payload.length > ANNOT_DRAFT_MAX) return
      window.localStorage.setItem(annotDraftKey(paperId), payload)
    } catch (err) {
      // 配额满 / 被禁：没有兜底也比把详情页弄崩强
    }
  }

  function clearAnnotDraft (paperId) {
    try {
      window.localStorage.removeItem(annotDraftKey(paperId))
    } catch (err) {}
  }

  /**
   * 合并两份批注：同一个 id 用 updatedAt 较新的那份（本地草稿通常是后改的，所以多数时候它胜出）。
   * 注意这是**并集**：服务端独有、草稿里没有的条目会保留（宁可在极少数情况下把离线删掉的
   * 一条又找回来，也不能把别人新加的批注弄丢）。真正删除靠草稿里的 removed 列表排除。
   */
  function mergeAnnotations (base, extra) {
    const out = []
    const index = new Map()
    const list = [].concat(Array.isArray(base) ? base : [], Array.isArray(extra) ? extra : [])
    for (const item of list) {
      if (!item || !item.id) continue
      const at = index.get(item.id)
      if (at === undefined) {
        index.set(item.id, out.length)
        out.push(item)
        continue
      }
      const prev = out[at]
      if (String(item.updatedAt || '') >= String(prev.updatedAt || '')) out[at] = item
    }
    return out
  }
  if (typeof window !== 'undefined' && !window.__lvPdfCleanupBound) {
    window.__lvPdfCleanupBound = true
    window.addEventListener('hashchange', () => {
      if (!/^#\/paper\//.test(window.location.hash || '')) disposePdfViewer()
    })
  }

  async function mountPdfViewer () {
    const host = document.getElementById('pdf-viewer-host')
    if (!host) return
    disposePdfViewer()
    const url = api.pdfUrl(currentPaperId)
    const showIframe = reason => {
      if (reason) console.warn('[pdf] 使用浏览器自带查看器：' + reason)
      host.classList.add('pdf-fallback')
      host.replaceChildren(h('iframe.pdf-frame', { src: url + '#view=FitH', title: 'PDF 预览' }))
    }
    const viewer = pdfviewer
    if (!viewer || typeof viewer.mount !== 'function') return showIframe('内置阅读器脚本未加载')
    try {
      const res = await viewer.mount(host, {
        url,
        title: 'PDF 原文',
        downloadUrl: url,
        // 批注：把这次读到的交给阅读器；它改完（新增/改内容/删除）回调这里落库
        annotations: currentAnnotations,
        onAnnotationsChange: items => {
          currentAnnotations = items
          if (typeof currentAnnotationsHandler === 'function') currentAnnotationsHandler(items)
        },
        // 框选提问：阅读器把框出来的高清图交回来，由详情页弹窗提问
        onRegionSelected: (dataUrl, info) => {
          if (typeof currentRegionHandler === 'function') currentRegionHandler(dataUrl, info || {})
        }
      })
      if (res && res.ok) activePdfViewer = res
      else showIframe(res && res.reason)
    } catch (err) {
      showIframe(err && err.message)
    }
  }

  // mountPdfViewer 要知道当前是哪篇文献（详情页可能被重绘多次）
  let currentPaperId = null

  export { detailHandler }
