/* 详情页子模块：中文/原文摘要切换、参考文献列表、AI 总结流式生成、正文提取/OCR 与打印导出 PDF */
import * as util from '../../util.mjs'
import * as md from '../../markdown.mjs'
import { api } from '../../api.mjs'
import { lookupTitle } from '../../shell/app-api.mjs'

const { h, authorsText } = util

export function buildSummaryAndAi ({
  paper, ftInfo, signal, isStale, toast, confirm, later, cancelLater,
  selectPdfTab, getActivePdfViewer, isReadingOriginal, setReadingOriginal, refreshChat
}) {
  /* ---------- 导出 PDF：AI 总结 + 与这篇文献的对话 ---------- */
  async function exportPdf () {
    try { await refreshChat() } catch {}
    if (isStale()) return
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

    const oldTitle = document.title
    document.title = String(paper.title || 'PaperLibrary 导出').replace(/[\\/:*?"<>|]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 80)
    document.body.appendChild(doc)
    document.body.classList.add('exporting')
    let cleaned = false
    let printTimer = null
    const cleanup = () => {
      if (cleaned) return
      cleaned = true
      document.body.classList.remove('exporting')
      if (doc.parentNode) doc.parentNode.removeChild(doc)
      document.title = oldTitle
      window.removeEventListener('afterprint', cleanup)
      signal.removeEventListener('abort', cleanup)
      cancelLater(printTimer)
    }
    window.addEventListener('afterprint', cleanup)
    signal.addEventListener('abort', cleanup, { once: true })
    printTimer = later(cleanup, 60000)
    toast('已生成导出视图：在打印窗口把「目标打印机」选成「另存为 PDF」')
    try {
      window.print()
    } catch (err) {
      cleanup()
      toast('这台浏览器不支持直接打印：' + err.message, 'error')
    }
  }

  /* ---------- 摘要面板（中文 / 原文） ---------- */
  const summaryText = String(paper.summary || '').trim()
  const summaryZhView = h('div.md')
  const summaryRawView = h('div.md', { html: md.render(summaryText, { byTitle: lookupTitle }) })
  const summaryZhMeta = h('div.hint', '')
  let summaryMode = paper.summaryZh ? 'zh' : (summaryText ? 'raw' : 'zh')

  const summaryTabZh = h('button.read-tab', { type: 'button' }, '中文')
  const summaryTabRaw = h('button.read-tab', { type: 'button' }, '原文')
  const summaryBtn = h('button.btn.sm.primary', { type: 'button' }, '生成中文总结')

  function summaryButtonLabel () {
    if (paper.summaryZh) return '重新生成'
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
    summaryTabRaw.hidden = !summaryText
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
      if (isStale()) return
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

  /* ---------- AI 总结（放在 PDF 右边） ---------- */
  const aiBody = h('div.md')
  const aiMeta = h('div.hint', '')
  const aiStatus = h('span.hint', '')
  let ftReady = !!(ftInfo && ftInfo.has)

  function renderAiSummary (text) {
    if (!text) {
      aiBody.replaceChildren(h('div', { style: { color: 'var(--muted)' } },
        h('p', '还没有生成总结。'),
        h('p', { text: paper.pdfFile
          ? '点下面的按钮，模型会完整读取这篇的正文与全部补充材料（SI），生成结构化中文总结并保存在本地。'
          : '先在右侧上传 PDF 原文，总结质量会好很多；也可以只用题录与摘要生成。' })))
      return
    }
    aiBody.innerHTML = md.render(text, { byTitle: lookupTitle })
  }
  renderAiSummary(paper.aiSummary)

  if (paper.aiSummaryAt) {
    aiMeta.textContent = `生成于 ${util.formatDate(paper.aiSummaryAt)}${paper.aiSummaryModel ? ' · ' + paper.aiSummaryModel : ''}`
  }

  /* ---------- 头部动作：主按钮「生成总结」，正文相关的两个动作收进它的 ▾ 菜单 ----------
     以前三个按钮平铺在标题栏里，右侧栏一窄就把「AI 总结 / 与文献对话」挤成竖排。
     现在只留「模型设置 + 生成总结(▾)」，正文动作按需展开，逻辑与原按钮完全一致。 */
  const aiButton = h('button.btn.sm.primary.split-main', { type: 'button' }, paper.aiSummary ? '重新生成' : '生成总结')
  const extractItem = h('button.more-item', {
    type: 'button', role: 'menuitem',
    title: '从 PDF 里抽出文字，供搜索与总结使用'
  }, '⟳ 提取全文')
  const ocrItem = h('button.more-item', {
    type: 'button', role: 'menuitem',
    title: '这是图片版 PDF（没有文字层）时用：逐页发给模型识别成文字，之后就能全文检索与总结'
  }, '🔍 识图识别全文')
  const ftNote = h('div.more-note', '')
  const ftMenu = h('div.more-menu.split-menu', {
    id: 'summary-ft-menu', role: 'menu', 'aria-label': '正文操作', hidden: true
  }, ftNote, h('div.more-sep'), extractItem, ocrItem)
  const ftCaret = h('button.split-caret', {
    type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', 'aria-controls': 'summary-ft-menu',
    title: '正文操作：重新提取全文 / 识图识别全文', 'aria-label': '正文操作',
    onclick: ev => { ev.stopPropagation(); setFtMenu(ftMenu.hidden) },
    onkeydown: ev => {
      if (ev.key === 'ArrowDown') { ev.preventDefault(); setFtMenu(true) }
    }
  }, '▾')
  const ftSplit = h('div.split', aiButton, ftCaret, ftMenu)
  let ftBusy = false
  let ftOutside = null

  function setFtMenu (open) {
    const next = !!open
    ftMenu.hidden = !next
    ftCaret.setAttribute('aria-expanded', next ? 'true' : 'false')
    ftSplit.classList.toggle('is-open', next)
    if (ftOutside) {
      document.removeEventListener('click', ftOutside)
      document.removeEventListener('keydown', onFtEsc, true)
      ftOutside = null
    }
    if (!next) return
    ftOutside = ev => {
      if (!ftSplit.isConnected) { setFtMenu(false); return }
      if (!ftSplit.contains(ev.target)) setFtMenu(false)
    }
    document.addEventListener('click', ftOutside, { signal })
    document.addEventListener('keydown', onFtEsc, { capture: true, signal })
    const first = [extractItem, ocrItem].find(el => !el.hidden && !el.disabled)
    if (first) {
      try { first.focus({ preventScroll: true }) } catch (err) { /* 老浏览器忽略 */ }
    }
  }

  function onFtEsc (ev) {
    if (ev.key !== 'Escape') return
    ev.stopImmediatePropagation()
    setFtMenu(false)
    try { ftCaret.focus({ preventScroll: true }) } catch (err) { /* 同上 */ }
  }

  function paintFtCaret () {
    ftCaret.disabled = ftBusy || !paper.pdfFile
    ftCaret.replaceChildren(ftBusy ? h('span.spin') : '▾')
  }

  // 提取 / 识图进行中：菜单收起、两项禁用、▾ 位置显示转圈，进度统一写在 ftStatus
  function setFtBusy (busy) {
    ftBusy = !!busy
    extractItem.disabled = ftBusy
    ocrItem.disabled = ftBusy
    paintFtCaret()
    if (ftBusy) setFtMenu(false)
  }

  const ftStatus = h('div.hint', '')

  function renderFtState () {
    const hasPdf = !!paper.pdfFile
    extractItem.hidden = !hasPdf
    ocrItem.hidden = !hasPdf
    extractItem.textContent = ftReady ? '⟳ 重新提取全文' : '⟳ 提取全文'
    ftNote.textContent = !hasPdf ? '还没有关联 PDF 原文' : (ftReady ? '正文已提取' : '正文尚未提取')
    paintFtCaret()
    if (!hasPdf) {
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

  function markFulltextReady () {
    ftReady = true
    renderFtState()
  }

  // 扫描件识图：逐页渲染 → 模型抄录 → 最后存成这篇的正文
  ocrItem.addEventListener('click', async () => {
    if (ftBusy) return
    setFtMenu(false)
    const viewer = await selectPdfTab('')
    if (!viewer || typeof viewer.capturePage !== 'function') {
      toast('内置阅读器还没就绪（或没装 pdfjs-dist），暂时无法逐页识图', 'warn')
      return
    }
    const total = typeof viewer.getPageCount === 'function' ? viewer.getPageCount() : 0
    if (!total) { toast('读不到页数，稍后再试', 'warn'); return }
    const ok = await confirm({
      title: '用识图模型识别全文？',
      body: '会把每一页渲染成图片发给模型抄录，共 ' + total + ' 页（也就是 ' + total +
        ' 次模型调用，扫描件大约几角钱）。识别完成后会存成这篇文献的正文，之后能全文检索、生成总结与问答。',
      okText: '开始识别',
      danger: false
    })
    if (!ok || isStale() || viewer !== getActivePdfViewer() || isReadingOriginal()) return
    setReadingOriginal(true)
    setFtBusy(true)
    const parts = []
    try {
      for (let n = 1; n <= total; n++) {
        if (isStale()) return
        ftStatus.textContent = '正在识别第 ' + n + ' / ' + total + ' 页（识图很慢，可以去做别的）…'
        ftStatus.style.color = ''
        const dataUrl = await viewer.capturePage(n, { scale: 2, format: 'image/jpeg', quality: 0.85 })
        if (isStale()) return
        if (!dataUrl) throw new Error('第 ' + n + ' 页渲染失败')
        const res = await api.transcribeImage(paper.id, dataUrl, n)
        if (isStale()) return
        const text = String((res && res.text) || '').trim()
        parts.push('===== 第 ' + n + ' 页 =====\n' + (text || '（本页无文字）'))
      }
      const merged = parts.join('\n\n')
      const saved = await api.saveFulltextText(paper.id, merged)
      if (isStale()) return
      ftReady = true
      renderFtState()
      ftStatus.textContent = '✓ 识图完成，已存为正文 ' + saved.chars + ' 字（可全文检索，也会用于 AI 总结）'
      ftStatus.style.color = 'var(--accent-text)'
      toast('识图识别完成：' + saved.chars + ' 字')
    } catch (err) {
      if (isStale()) return
      ftStatus.textContent = '识图失败：' + err.message
      ftStatus.style.color = 'var(--danger)'
      toast('识图失败：' + err.message, 'error')
    } finally {
      setReadingOriginal(false)
      setFtBusy(false)
    }
  })

  extractItem.addEventListener('click', async () => {
    if (ftBusy) return
    setFtMenu(false)
    setFtBusy(true)
    ftStatus.textContent = '正在从 PDF 抽文字…'
    ftStatus.style.color = ''
    try {
      const res = await api.extractFulltext(paper.id)
      if (isStale()) return
      ftReady = true
      toast(`已提取 ${res.pages} 页 / ${res.chars} 字`)
      ftStatus.textContent = '✓ 已提取 ' + res.chars + ' 字（可被全文检索，也会用于 AI 总结）'
      ftStatus.style.color = 'var(--accent-text)'
    } catch (err) {
      if (isStale()) return
      ftStatus.textContent = '提取失败：' + err.message
      ftStatus.style.color = 'var(--danger)'
      toast('提取失败：' + err.message, 'error')
    } finally {
      setFtBusy(false)
    }
  })
  renderFtState()

  aiButton.addEventListener('click', async () => {
    aiButton.setAttribute('disabled', '')
    aiButton.textContent = '生成中…'
    aiStatus.textContent = '正在请求模型…'
    aiStatus.style.color = ''
    let acc = ''
    let coverage = null
    aiBody.replaceChildren(h('div', { style: { color: 'var(--muted)' } }, '…'))
    try {
      await api.aiSummarizeStream(paper.id, {
        save: true,
        onStart: info => {
          if (isStale()) return
          coverage = info.fullRead || null
          if (info.autoExtract && info.autoExtract.ok) {
            ftReady = true
            toast('已自动提取 PDF 正文 ' + info.autoExtract.chars + ' 字')
            renderFtState()
          } else if (info.autoExtract && info.autoExtract.ok === false) {
            toast('自动提取正文失败：' + info.autoExtract.error, 'warn')
          }
          const fr = coverage
          if (fr && fr.totalChars) {
            const miss = fr.missing && fr.missing.length ? `；⚠ ${fr.missing.length} 个 SI 未提取到文字` : ''
            aiStatus.textContent = `全文资料：正文 ${fr.bodyChars} 字 + SI ${fr.siChars} 字（${fr.attachCount} 个文件）${miss}；模型 ${info.model} 正在生成…`
          } else if (info.usedFulltext) {
            aiStatus.textContent = `已读入 ${info.chars} 字正文，模型 ${info.model} 正在生成…`
          } else {
            aiStatus.textContent = `未找到 PDF 正文（${(info.autoExtract && info.autoExtract.error) || '没有关联 PDF'}），仅依据题录与摘要；模型 ${info.model} 正在生成…`
          }
        },
        onDelta: chunk => {
          if (isStale()) return
          acc += chunk
          aiBody.innerHTML = md.render(acc, { byTitle: lookupTitle })
        }
      })
      if (isStale()) return
      aiStatus.textContent = '已生成并保存 ✓'
      aiStatus.style.color = 'var(--accent-text)'
      const miss = coverage && coverage.missing && coverage.missing.length
        ? `；⚠ ${coverage.missing.length} 个 SI 未提取到文字` : ''
      aiMeta.textContent = coverage && coverage.totalChars
        ? `已读入正文 ${coverage.bodyChars} 字 + SI ${coverage.siChars} 字（${coverage.attachCount} 个文件）${miss} · 刚刚生成`
        : '刚刚生成'
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

  const aiPanelActions = h('div.right', { id: 'read-actions-summary' },
    h('a.btn.sm.ghost', { href: '#/settings', title: '模型与 API 设置' }, '模型设置'),
    ftSplit
  )

  const readViewSummary = h('div.read-view', { id: 'read-view-summary' },
    ftStatus,
    aiStatus,
    h('div.ai-scroll', aiBody),
    aiMeta
  )

  return { summaryPanel, refsPanel, aiPanelActions, readViewSummary, markFulltextReady, exportPdf }
}
