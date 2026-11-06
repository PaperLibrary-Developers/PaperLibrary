/* 建录页（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from '../util.mjs'
import * as components from '../components.mjs'
import { api } from '../api.mjs'
import { getState } from '../state/store.mjs'
import { go, refreshMeta } from '../shell/app-api.mjs'

  const { h, toast, confirmDialog } = util
  
  /** 来源的中文名（显示「摘要来自哪里」用） */
  const SOURCE_LABEL = { crossref: 'Crossref', openalex: 'OpenAlex' }

  function newBatchId () {
    return 'b' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8)
  }

  function looksLikeDoi (value) {
    const s = String(value || '').trim()
    return /^(doi:\s*)?10\.\d{4,9}\/\S+$/i.test(s) || /^https?:\/\/(dx\.)?doi\.org\/10\.\d{4,9}\/\S+$/i.test(s)
  }

  function isPdfFile (file) {
    return /\.pdf$/i.test((file && file.name) || '') || (file && file.type === 'application/pdf')
  }

  function strOf (v) { return String(v == null ? '' : v).trim() }

  /** 统一把「卡片上的字段值」变成入库用的 payload */
  function formToPayload (form, extra) {
    return Object.assign({
      title: strOf(form.title),
      authors: Array.isArray(form.authors) ? form.authors : String(form.authors || '')
        .split(/[,，;；]/).map(s => s.trim()).filter(Boolean),
      year: strOf(form.year) ? Number(strOf(form.year)) : null,
      type: form.type || 'journal',
      venue: strOf(form.venue),
      volume: strOf(form.volume),
      issue: strOf(form.issue),
      pages: strOf(form.pages),
      publisher: strOf(form.publisher),
      doi: strOf(form.doi),
      url: strOf(form.url),
      keywords: Array.isArray(form.keywords) ? form.keywords : [],
      summary: String(form.summary || '')
    }, extra || {})
  }

  /** 一张可编辑的题录卡片（PDF 与 DOI 共用） */
  function renderMetaCard (options) {
    const opts = options || {}
    const form = opts.form || {}

    function textLine (label, key, placeholder, width) {
      const input = h('input.input', { type: 'text', value: form[key] == null ? '' : String(form[key]), placeholder: placeholder || '' })
      input.setAttribute('data-field', key)
      input.addEventListener('input', () => { form[key] = input.value })
      return h('div.field', { style: width ? { flex: width, minWidth: '90px' } : null },
        h('label', label), input)
    }

    const titleInput = h('input.input', { type: 'text', value: form.title || '', placeholder: '标题' })
    titleInput.setAttribute('data-field', 'title')
    titleInput.addEventListener('input', () => { form.title = titleInput.value })

    const authorsInput = h('input.input', {
      type: 'text',
      value: Array.isArray(form.authors) ? form.authors.join(', ') : String(form.authors || ''),
      placeholder: '多个作者用逗号分隔'
    })
    authorsInput.setAttribute('data-field', 'authors')
    authorsInput.addEventListener('input', () => {
      form.authors = authorsInput.value.split(/[,，;；]/).map(s => s.trim()).filter(Boolean)
    })

    const summaryInput = h('textarea.textarea', { rows: 3, placeholder: '摘要（抓取时自动填入，可改；Crossref 没有的刊会用 OpenAlex 补）' },
      form.summary || '')
    summaryInput.setAttribute('data-field', 'summary')
    summaryInput.addEventListener('input', () => { form.summary = summaryInput.value })

    const typeSelect = h('select.select', { style: { width: '100%' } },
      ...Object.entries(util.TYPE_LABELS).map(([key, label]) =>
        h('option', { value: key, selected: (form.type || 'journal') === key }, label)))
    typeSelect.addEventListener('change', () => { form.type = typeSelect.value })

    const headBits = [
      opts.subtitle || '',
      opts.duplicate ? '库里可能已有这篇：' + String(opts.duplicate.title || '').slice(0, 40) : ''
    ].filter(Boolean)

    const headExtra = h('div', { style: { display: 'flex', gap: '6px', alignItems: 'center', marginLeft: 'auto', flexWrap: 'wrap' } },
      opts.badge || null,
      ...(opts.actions || []))

    const card = h('div.import-card',
      h('div.import-card-head',
        h('div.import-card-title',
          h('div.import-card-name', { title: opts.name || '' }, opts.name || ''),
          headBits.length ? h('div.import-card-sub', headBits.join(' · ')) : null),
        headExtra),
      h('div.field', h('label', '标题'), titleInput),
      h('div.field', h('label', '作者'), authorsInput),
      h('div.import-row',
        textLine('年份', 'year', '2018', '80px'),
        h('div.field', { style: { flex: '110px', minWidth: '90px' } }, h('label', '类型'), typeSelect),
        textLine('卷', 'volume', '', '70px'),
        textLine('期', 'issue', '', '70px'),
        textLine('页', 'pages', '', '110px')
      ),
      h('div.import-row',
        textLine('期刊 / 会议', 'venue', '', '1 1 220px'),
        textLine('DOI', 'doi', '10.xxxx/xxxxx', '1 1 200px')
      ),
      h('div.field', h('label', '摘要'), summaryInput),
      opts.hint ? h('div.hint', opts.hint) : null
    )

    return { card, form, titleInput, focus: () => titleInput.focus() }
  }

  async function editHandler (ctx) {
    const { outlet, routeParams, isStale } = ctx
    const id = routeParams.id || null
    const isNew = !id

    /* ---------------- 编辑已有文献：维持原来的单一表单 ---------------- */
    if (!isNew) return renderEditExisting(ctx, id)

    /* ---------------- 新建：两块独立通道 ---------------- */
    outlet.replaceChildren(h('div.content', h('div.empty', h('div.big', '正在准备表单…'))))

    const batch = newBatchId()
    /** 待导入卡片：{ key, kind:'pdf'|'doi', name, tempId, form, parsed, duplicate, error } */
    let cards = []
    let busy = false

    /* ======== ① 智能导入区 ======== */

    const doiInput = h('input.input', {
      type: 'text',
      id: 'unified-import-input',
      placeholder: '拖入 PDF 文件，或在此粘贴 DOI 后按回车 (Enter) 自动抓取',
      autocomplete: 'off'
    })
    const browseBtn = h('button.btn', { type: 'button', title: '从磁盘选择 PDF 文件' }, '浏览')
    const browseInput = h('input', {
      type: 'file', accept: 'application/pdf,.pdf', multiple: true, style: { display: 'none' }
    })
    const progressBox = h('div.import-progress', { hidden: true })
    const importHint = h('div.hint', '')
    const cardListBox = h('div', { id: 'import-card-list' })
    const importFoot = h('div', { id: 'import-foot' })

    const importPanel = h('section.panel', { id: 'unified-import' },
      h('div.panel-head', '智能导入',
        h('div.right', h('span.hint', '拖 PDF 或粘贴 DOI 回车，每篇会单独生成一张可核对的表单'))),
      h('div.panel-body', h('div', { style: { display: 'grid', gap: '10px' } },
        h('div', { id: 'unified-import-row', style: { display: 'flex', gap: '8px', alignItems: 'stretch' } },
          doiInput,
          browseBtn,
          browseInput
        ),
        importHint,
        progressBox,
        cardListBox,
        importFoot
      ))
    )

    function setBusy (value) {
      busy = !!value
      if (busy) browseBtn.setAttribute('disabled', '')
      else browseBtn.removeAttribute('disabled')
      importPanel.classList.toggle('is-busy', busy)
      // 卡片上的【导入这篇】【去掉】是按 busy 决定禁用的，所以忙完必须**重画卡片**，
      // 只重画底部按钮是不够的（曾经因此让卡片按钮永远停在禁用状态、点了没反应）。
      renderCards()
    }

    browseBtn.addEventListener('click', () => { if (!busy) browseInput.click() })
    browseInput.addEventListener('change', ev => {
      const list = Array.from(ev.target.files || [])
      ev.target.value = ''
      if (list.length) handleFiles(list)
    })

    // 按你的要求：**只有回车**才联网抓 DOI，单纯粘贴不自动发请求
    doiInput.addEventListener('keydown', ev => {
      if (ev.key !== 'Enter') return
      ev.preventDefault()
      const value = doiInput.value.trim()
      if (!value) { toast('先粘贴一个 DOI，或直接把 PDF 拖进来', 'warn'); return }
      if (!looksLikeDoi(value)) {
        toast('这看起来不是 DOI。DOI 形如 10.1038/nature14539', 'warn')
        return
      }
      fetchDoiCard(value)
    })

    // 拖放：整个导入区都能接（拖到输入框上也走同一条路径）
    for (const type of ['dragenter', 'dragover']) {
      importPanel.addEventListener(type, ev => {
        ev.preventDefault()
        if (!busy) importPanel.classList.add('dragging')
      })
    }
    for (const type of ['dragleave', 'drop']) {
      importPanel.addEventListener(type, ev => {
        ev.preventDefault()
        importPanel.classList.remove('dragging')
      })
    }
    importPanel.addEventListener('drop', ev => {
      if (busy) return
      const list = Array.from((ev.dataTransfer && ev.dataTransfer.files) || []).filter(isPdfFile)
      if (!list.length) { toast('只支持 PDF 文件', 'warn'); return }
      handleFiles(list)
    })

    /* ---- PDF：预检 → 生成一张卡片 ---- */

    async function handleFiles (list) {
      const accepted = list.filter(isPdfFile)
      const skipped = list.length - accepted.length
      if (skipped) toast('已跳过 ' + skipped + ' 个非 PDF 文件', 'warn')
      if (!accepted.length) return

      setBusy(true)
      progressBox.hidden = false
      let done = 0
      for (const file of accepted) {
        progressBox.replaceChildren(
          h('div.import-progress-bar',
            h('div.import-progress-fill', { style: { width: Math.round(done / accepted.length * 100) + '%' } })),
          h('div.hint', '正在解析第 ' + (done + 1) + ' / ' + accepted.length + ' 个：' + file.name))
        const card = {
          key: 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          kind: 'pdf',
          name: file.name,
          tempId: '',
          parsed: null,
          duplicate: null,
          error: '',
          form: {
            title: '', authors: [], year: null, type: 'journal', venue: '', doi: '',
            volume: '', issue: '', pages: '', publisher: '', url: '', summary: '', keywords: []
          }
        }
        cards.push(card)
        renderCards()
        try {
          const res = await api.preflightPdf(file, batch)
          card.tempId = res.tempId
          card.name = res.name || file.name
          card.parsed = res.parsed || null
          card.duplicate = (res.draft && res.draft.duplicate) || null
          card.form = Object.assign(card.form, res.draft || {})
        } catch (err) {
          card.error = err.message
        }
        done++
        renderCards()
      }
      progressBox.hidden = true
      setBusy(false)
      const failed = cards.filter(c => c.error).length
      if (failed) toast(failed + ' 个文件解析失败，已在卡片上标出', 'warn')
      else toast('解析完成，请核对卡片后导入')
    }

    /* ---- DOI：抓取 → 生成一张卡片 ---- */

    async function fetchDoiCard (doi) {
      setBusy(true)
      importHint.textContent = '正在抓取题录（Crossref + OpenAlex 并行）…'
      importHint.style.color = ''
      try {
        const res = await api.fetchDoi(doi)
        if (isStale()) return
        const doc = res.paper || {}
        const src = res.sources || {}
        const errs = res.errors || {}
        cards.push({
          key: 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
          kind: 'doi',
          name: doc.title || ('DOI ' + doi),
          tempId: '',
          parsed: null,
          duplicate: null,
          error: '',
          sources: src,
          sourceErrors: errs,
          form: {
            title: doc.title || '',
            authors: doc.authors || [],
            year: doc.year || null,
            type: doc.type || 'journal',
            venue: doc.venue || '',
            volume: doc.volume || '',
            issue: doc.issue || '',
            pages: doc.pages || '',
            publisher: doc.publisher || '',
            doi: doc.doi || doi,
            url: doc.url || '',
            keywords: doc.keywords || [],
            summary: doc.summary || ''
          }
        })
        const bits = ['题录：' + (src.crossref === 'ok'
          ? 'Crossref ✓'
          : (src.openalex === 'ok' ? 'OpenAlex ✓（Crossref 没有这条）' : '未取到'))]
        if (String(doc.summary || '').trim()) {
          bits.push('摘要：' + (SOURCE_LABEL[doc.summarySource] || '已填入') + ' ✓（' + String(doc.summary).length + ' 字）')
        } else {
          bits.push('摘要：两个来源都没有')
        }
        if (errs.openalex) bits.push('OpenAlex ⚠ ' + errs.openalex)
        if (errs.crossref) bits.push('Crossref ⚠ ' + errs.crossref)
        importHint.textContent = '已生成一张待导入表单，请核对后导入。　' + bits.join('　')
        importHint.style.color = 'var(--accent-text)'
        doiInput.value = ''
        renderCards()
      } catch (err) {
        importHint.textContent = '抓取失败：' + err.message
        importHint.style.color = 'var(--danger)'
        toast('抓取失败：' + err.message, 'error')
      } finally {
        setBusy(false)
      }
    }

    /* ---- 卡片清单 ---- */

    function renderCards () {
      if (!cards.length) { cardListBox.replaceChildren(); renderImportFoot(); return }
      cardListBox.replaceChildren(h('div.import-cards', ...cards.map(renderOneCard)))
      renderImportFoot()
    }

    function renderOneCard (entry) {
      if (entry.error) {
        return h('div.import-card.failed',
          h('div.import-card-head',
            h('div.import-card-title',
              h('div.import-card-name', { title: entry.name }, entry.name),
              h('div.import-card-sub', '解析失败：' + entry.error)),
            h('div', { style: { marginLeft: 'auto' } },
              h('button.btn.sm.ghost', { type: 'button', onclick: () => removeCard(entry) }, '去掉'))),
          h('div.hint', '这张卡片不会被导入。可以去掉它，或修好文件后重新拖进来。'))
      }

      const badge = entry.kind === 'doi'
        ? h('span.badge.import-badge.ok', 'DOI 抓取')
        : (entry.parsed && entry.parsed.doi && entry.parsed.doiVerified
          ? h('span.badge.import-badge.ok', 'DOI ✓')
          : h('span.badge.import-badge.muted', '来自 PDF'))

      const hintBits = []
      if (entry.kind === 'pdf' && entry.parsed) {
        if (entry.parsed.pages) hintBits.push(entry.parsed.pages + ' 页')
        hintBits.push(entry.parsed.doi
          ? (entry.parsed.doiVerified ? 'DOI 已由 Crossref 核对' : 'DOI 来自 PDF，未能联网核对')
          : '没在 PDF 里找到 DOI，题录可手工补')
      }
      if (entry.kind === 'doi') {
        const src = entry.sources || {}
        hintBits.push('题录来源：' + (src.crossref === 'ok' ? 'Crossref' : 'OpenAlex'))
        if (entry.sourceErrors && entry.sourceErrors.openalex) hintBits.push('OpenAlex ⚠ ' + entry.sourceErrors.openalex)
      }

      const built = renderMetaCard({
        name: entry.name,
        subtitle: entry.kind === 'pdf' ? '来自 PDF' : '来自 DOI 抓取',
        form: entry.form,
        duplicate: entry.duplicate,
        badge,
        hint: hintBits.join('；'),
        actions: [
          h('button.btn.sm.primary', {
            type: 'button',
            disabled: busy,
            onclick: () => singleImport(entry)
          }, '导入这篇'),
          h('button.btn.sm.ghost', {
            type: 'button',
            disabled: busy,
            title: '从待导入列表里去掉',
            onclick: () => removeCard(entry)
          }, '去掉')
        ]
      })
      // 卡片里的输入直接写回 entry.form（renderMetaCard 已绑定到同一个对象）
      entry.form = built.form
      return built.card
    }

    function removeCard (entry) {
      cards = cards.filter(c => c.key !== entry.key)
      renderCards()
    }

    function renderImportFoot () {
      if (!cards.length) { importFoot.replaceChildren(); return }
      const ready = cards.filter(c => !c.error).length
      importFoot.replaceChildren(h('div.import-actions',
        h('span.hint', '共 ' + cards.length + ' 张待导入表单（可导入 ' + ready + ' 张）'),
        h('span', { style: { flex: '1 1 auto' } }),
        h('button.btn', {
          type: 'button',
          disabled: busy,
          onclick: cancelAllCards
        }, '取消'),
        h('button.btn.primary', {
          type: 'button',
          disabled: busy || !ready,
          onclick: importAll
        }, busy ? '正在导入…' : '全部导入')
      ))
    }

    async function cancelAllCards () {
      const ok = await confirmDialog({
        title: '取消这次导入？',
        body: '下面这些已经解析/抓取好的表单会被清空（服务端临时文件也会删除）。' +
          '手动录入区的内容不受影响。',
        okText: '取消导入',
        cancelText: '继续核对',
        danger: true
      })
      if (!ok) return
      try { await api.cancelPdfImport(batch) } catch (err) { /* 清不掉就交给服务端按时间清 */ }
      cards = []
      importHint.textContent = '已取消，待导入的表单都清掉了。'
      importHint.style.color = ''
      renderCards()
    }

    /** 导入一张卡片；成功返回 true */
    async function importOne (entry) {
      const payload = formToPayload(entry.form)
      if (!payload.title) {
        toast('这张卡片的标题还是空的，请先补上', 'warn')
        return false
      }
      if (entry.kind === 'pdf') {
        const res = await api.confirmPdfImport(batch, [{
          tempId: entry.tempId,
          name: entry.name,
          paper: Object.assign({}, payload, { pdfFile: '' })
        }])
        const imported = (res.imported || [])[0]
        const failed = (res.failed || [])[0]
        if (!imported) throw new Error((failed && failed.error) || '入库失败')
        cards = cards.filter(c => c.key !== entry.key)
        return true
      }
      await api.createPaper(payload)
      cards = cards.filter(c => c.key !== entry.key)
      return true
    }

    async function singleImport (entry) {
      setBusy(true)
      try {
        const ok = await importOne(entry)
        if (ok) {
          toast('已导入 1 篇')
          refreshMeta()
          renderCards()
        }
      } catch (err) {
        toast('导入失败：' + err.message, 'error')
      } finally {
        setBusy(false)
      }
    }

    async function importAll () {
      const list = cards.filter(c => !c.error).slice()
      if (!list.length) return
      setBusy(true)
      let ok = 0
      const failed = []
      for (const entry of list) {
        try {
          if (await importOne(entry)) ok++
        } catch (err) {
          failed.push(entry.name + '：' + err.message)
        }
      }
      setBusy(false)
      renderCards()
      refreshMeta()
      if (failed.length) {
        toast('导入 ' + ok + ' 篇，' + failed.length + ' 篇失败：' + failed[0], 'warn')
      } else {
        toast('已导入 ' + ok + ' 篇')
        importHint.textContent = '全部导入完成。'
        importHint.style.color = 'var(--accent-text)'
      }
    }

    /* ======== ② 手动录入（折叠，自带表单） ======== */

    const manualBody = h('div', { hidden: true })
    const manualArrow = h('span', '▸')
    const manualLabel = h('span', { style: { marginLeft: '6px' } }, '展开 手动录入选项')
    const manualToggle = h('button.btn', { type: 'button' }, manualArrow, manualLabel)
    manualToggle.addEventListener('click', () => {
      const open = manualBody.hidden
      manualBody.hidden = !open
      manualArrow.textContent = open ? '▾' : '▸'
      manualLabel.textContent = open ? '收起 手动录入选项' : '展开 手动录入选项'
      if (open) setTimeout(() => mf.title.focus(), 30)
    })

    const mf = {
      title: h('input.input', { type: 'text', id: 'manual-title', placeholder: '文献标题，例如 Attention Is All You Need' }),
      authors: h('input.input', { type: 'text', placeholder: '多个作者用逗号分隔，例如 Ashish Vaswani, Noam Shazeer' }),
      year: h('input.input', { type: 'text', inputmode: 'numeric', placeholder: '2017', style: { maxWidth: '130px' } }),
      venue: h('input.input', { type: 'text', placeholder: '期刊名或会议名，例如 NeurIPS / 计算机学报' }),
      volume: h('input.input', { type: 'text', placeholder: '如 33' }),
      issue: h('input.input', { type: 'text', placeholder: '如 2' }),
      pages: h('input.input', { type: 'text', placeholder: '如 436-444' }),
      doi: h('input.input', { type: 'text', placeholder: '10.1038/nature14539' }),
      url: h('input.input', { type: 'text', placeholder: 'https://…' }),
      keywords: h('input.input', { type: 'text', placeholder: '原文关键词，用逗号分隔' }),
      bibtexKey: h('input.input', { type: 'text', placeholder: '留空则自动生成，例如 Vaswani2017' }),
      summary: h('textarea.textarea', { rows: 3, placeholder: '原文摘要（可留空）' })
    }
    const mType = h('select.select', { style: { width: '100%' } },
      ...Object.entries(util.TYPE_LABELS).map(([key, label]) => h('option', { value: key }, label)))
    const mStarred = h('input.check', { type: 'checkbox' })
    let mRating = 0
    const mRatingSlot = h('div')
    function renderMRating () {
      mRatingSlot.replaceChildren(components.starsNode(mRating, {
        interactive: true,
        onChange: v => { mRating = v; renderMRating() }
      }))
    }
    renderMRating()
    const mTags = components.tagInput([], {
      placeholder: '输入标签后按回车',
      suggestions: (getState().tags || []).slice(0, 16)
    })

    function manualPayload () {
      return {
        title: strOf(mf.title.value),
        authors: strOf(mf.authors.value).split(/[,，;；]/).map(s => s.trim()).filter(Boolean),
        year: strOf(mf.year.value) ? Number(strOf(mf.year.value)) : null,
        type: mType.value || 'journal',
        venue: strOf(mf.venue.value),
        volume: strOf(mf.volume.value),
        issue: strOf(mf.issue.value),
        pages: strOf(mf.pages.value),
        doi: strOf(mf.doi.value),
        url: strOf(mf.url.value),
        tags: mTags.getTags(),
        keywords: strOf(mf.keywords.value).split(/[,，;；]/).map(s => s.trim()).filter(Boolean),
        starred: !!mStarred.checked,
        rating: mRating,
        summary: String(mf.summary.value || ''),
        notes: '',
        refs: [],
        pdfFile: '',
        bibtexKey: strOf(mf.bibtexKey.value)
      }
    }

    function clearManual () {
      for (const key of Object.keys(mf)) mf[key].value = ''
      mType.value = 'journal'
      mStarred.checked = false
      mRating = 0
      renderMRating()
      const box = mTags.querySelector('.chips-input')
      const fresh = components.tagInput([], { placeholder: '输入标签后按回车' })
      mTags.replaceChild(fresh.querySelector('.chips-input'), box)
      mTags.getTags = fresh.getTags
    }

    const manualSaveBtn = h('button.btn.primary', { type: 'submit' }, '保存')

    /** 保存手动录入的这一条 */
    async function saveManual () {
      const payload = manualPayload()
      if (!payload.title) { toast('标题不能为空', 'warn'); mf.title.focus(); return }
      manualSaveBtn.setAttribute('disabled', '')
      manualSaveBtn.textContent = '保存中…'
      try {
        const res = await api.createPaper(payload)
        toast('已新增文献')
        refreshMeta()
        go('#/paper/' + encodeURIComponent(res.paper.id))
      } catch (err) {
        toast('保存失败：' + err.message, 'error')
        manualSaveBtn.removeAttribute('disabled')
        manualSaveBtn.textContent = '保存'
      }
    }

    const manualCancelBtn = h('button.btn', {
      type: 'button',
      onclick: async () => {
        const payload = manualPayload()
        if (payload.title) {
          const ok = await confirmDialog({
            title: '清空手动录入的内容？',
            body: '这个表单里填的内容会被丢弃。上面智能导入区的卡片不受影响。',
            okText: '清空',
            danger: true
          })
          if (!ok) return
        }
        clearManual()
        toast('已清空手动录入表单')
      }
    }, '取消')

    // 手动录入是一个**真正独立的表单**（自带提交/取消），与上面的智能导入完全不共用
    const manualForm = h('form.form', {
      'data-manual-form': '',
      onsubmit: ev => { ev.preventDefault(); saveManual() }
    })

    util.replace(manualForm, ...[
      h('div.hint', '信息不全的非标文献（未发表的学位论文、内部报告等）在这里手工创建；这里的表单与上面的智能导入互不影响。'),
      h('div.field', h('label', '标题 *'), mf.title),
      h('div.field', h('label', '作者'), mf.authors),
      h('div.form-grid',
        h('div.field', h('label', '年份'), mf.year),
        h('div.field', h('label', '类型'), mType),
        h('div.field', h('label', '收藏'),
          h('label', { style: { display: 'flex', gap: '7px', alignItems: 'center', fontSize: '13.5px' } },
            mStarred, '加入我的收藏')),
        h('div.field', h('label', '我的评分'), mRatingSlot)
      ),
      h('div.form-grid',
        h('div.field', h('label', '期刊 / 会议'), mf.venue),
        h('div.field', h('label', '卷'), mf.volume),
        h('div.field', h('label', '期'), mf.issue),
        h('div.field', h('label', '页码'), mf.pages)
      ),
      h('div.form-grid',
        h('div.field', h('label', 'DOI'), mf.doi),
        h('div.field', h('label', '链接'), mf.url),
        h('div.field', h('label', 'BibTeX 键'), mf.bibtexKey)
      ),
      h('div.field', h('label', '标签'), mTags, h('div.hint', '标签决定你在侧栏和标签看板里怎么找到它')),
      h('div.field', h('label', '原文关键词'), mf.keywords),
      h('div.field', h('label', '摘要'), mf.summary),
      h('div.form-actions', { style: { position: 'static', borderTop: '0', paddingBottom: '0' } },
        manualCancelBtn,
        h('span.hint', '快捷键：Ctrl + S 保存'),
        h('div.right', manualSaveBtn))
    ])
    manualForm.style.padding = '12px 2px 2px'
    manualForm.style.gap = '13px'
    manualBody.replaceChildren(manualForm)

    const manualZone = h('section.panel', { id: 'manual-entry-zone' },
      h('div.panel-head', { style: { padding: '0', border: '0' } }, manualToggle),
      manualBody
    )

    /* ---------------- 组装 ---------------- */

    const pageHead = h('div.page-head',
      h('div',
        h('div.page-title', '新增文献'),
        h('div.page-sub', '拖 PDF 或粘贴 DOI 抓取题录；信息不全的文献用下面的手动录入。')))

    outlet.replaceChildren(h('div.content', h('div.detail', pageHead, importPanel, manualZone)))
    renderCards()
    setTimeout(() => doiInput.focus(), 60)
    document.onkeydown = null

    // 离开页面时把还没导入的临时文件清掉（已导入的早就转移走了）
    window.addEventListener('hashchange', function onLeave () {
      if (/^#\/new/.test(window.location.hash || '')) return
      window.removeEventListener('hashchange', onLeave)
      if (cards.some(c => c.kind === 'pdf')) api.cancelPdfImport(batch).catch(() => {})
    })
  }

  /* ---------------- 编辑已有文献：单一表单 ---------------- */

  async function renderEditExisting (ctx, id) {
    const { outlet, isStale } = ctx
    outlet.replaceChildren(h('div.content', h('div.empty', h('div.big', '正在准备表单…'))))

    let paper
    try {
      const res = await api.getPaper(id)
      paper = res.paper
    } catch (err) {
      outlet.replaceChildren(h('div.content', h('div.empty',
        h('div.big', '找不到这条文献'),
        h('p', { text: err.message }),
        h('button.btn', { type: 'button', onclick: () => { go('#/') } }, '← 回到书库'))))
      return
    }
    if (isStale()) return

    const f = {
      title: h('input.input', { type: 'text', value: paper.title || '', placeholder: '文献标题' }),
      year: h('input.input', { type: 'text', value: paper.year || '', placeholder: '2017', style: { maxWidth: '130px' } }),
      venue: h('input.input', { type: 'text', value: paper.venue || '', placeholder: '期刊名或会议名' }),
      volume: h('input.input', { type: 'text', value: paper.volume || '', placeholder: '如 33' }),
      issue: h('input.input', { type: 'text', value: paper.issue || '', placeholder: '如 2' }),
      pages: h('input.input', { type: 'text', value: paper.pages || '', placeholder: '如 436-444' }),
      publisher: h('input.input', { type: 'text', value: paper.publisher || '', placeholder: '出版社（图书类填写）' }),
      doi: h('input.input', { type: 'text', value: paper.doi || '', placeholder: '10.1038/nature14539' }),
      url: h('input.input', { type: 'text', value: paper.url || '', placeholder: 'https://…' }),
      keywords: h('input.input', { type: 'text', value: (paper.keywords || []).join(', '), placeholder: '原文关键词，用逗号分隔' }),
      bibtexKey: h('input.input', { type: 'text', value: paper.bibtexKey || '', placeholder: '留空则自动生成' }),
      summary: h('textarea.textarea', { rows: 4, placeholder: '原文摘要' }, paper.summary || '')
    }
    const authors = components.tagInput(paper.authors, { placeholder: '输入作者姓名后按回车' })
    const tags = components.tagInput(paper.tags, {
      placeholder: '输入标签后按回车',
      suggestions: (getState().tags || []).slice(0, 16)
    })
    const typeSelect = h('select.select', { style: { width: '100%' } },
      ...Object.entries(util.TYPE_LABELS).map(([key, label]) =>
        h('option', { value: key, selected: (paper.type || 'journal') === key }, label)))
    const starredInput = h('input.check', { type: 'checkbox', checked: !!paper.starred })
    let rating = Number(paper.rating) || 0
    const ratingSlot = h('div')
    function renderRating () {
      ratingSlot.replaceChildren(components.starsNode(rating, {
        interactive: true, onChange: v => { rating = v; renderRating() }
      }))
    }
    renderRating()

    const pdfInput = h('input', { type: 'file', accept: 'application/pdf,.pdf', style: { display: 'none' } })
    let pendingPdfFile = null
    const pdfNameText = h('span', { style: { flex: '1', minWidth: '0' } })
    const pdfPickBtn = h('button.btn.sm', { type: 'button' }, paper.pdfFile ? '更换 PDF 文件' : '选择 PDF 文件')
    const pdfClearBtn = h('button.btn.sm.ghost', { type: 'button', hidden: true }, '移除')
    function renderPdfRow () {
      if (pendingPdfFile) {
        pdfNameText.textContent = '待上传：' + pendingPdfFile.name + '，保存后一起上传并提取正文'
        pdfClearBtn.hidden = false
        return
      }
      pdfNameText.textContent = paper.pdfFile ? '当前关联：' + paper.pdfFile : '还没有关联 PDF'
      pdfClearBtn.hidden = !paper.pdfFile
    }
    pdfPickBtn.addEventListener('click', () => pdfInput.click())
    pdfInput.addEventListener('change', ev => {
      const file = ev.target.files && ev.target.files[0]
      ev.target.value = ''
      if (!file) return
      pendingPdfFile = file
      renderPdfRow()
    })
    pdfClearBtn.addEventListener('click', () => {
      pendingPdfFile = null
      paper.pdfFile = ''
      renderPdfRow()
    })

    const submitBtn = h('button.btn.primary', { type: 'submit' }, '保存修改')
    const form = h('form.form', {
      onsubmit: async ev => {
        ev.preventDefault()
        const payload = {
          title: strOf(f.title.value),
          authors: authors.getTags(),
          year: strOf(f.year.value) ? Number(strOf(f.year.value)) : null,
          type: typeSelect.value || 'journal',
          venue: strOf(f.venue.value),
          volume: strOf(f.volume.value),
          issue: strOf(f.issue.value),
          pages: strOf(f.pages.value),
          publisher: strOf(f.publisher.value),
          doi: strOf(f.doi.value),
          url: strOf(f.url.value),
          tags: tags.getTags(),
          keywords: strOf(f.keywords.value).split(/[,，;；]/).map(s => s.trim()).filter(Boolean),
          starred: !!starredInput.checked,
          rating,
          summary: String(f.summary.value || ''),
          // 这两个字段表单里没有编辑入口，但旧数据必须原样保留
          notes: String(paper.notes || ''),
          refs: Array.isArray(paper.refs) ? paper.refs : [],
          pdfFile: strOf(paper.pdfFile),
          bibtexKey: strOf(f.bibtexKey.value)
        }
        if (!payload.title) { toast('标题不能为空', 'warn'); f.title.focus(); return }
        submitBtn.setAttribute('disabled', '')
        submitBtn.textContent = '保存中…'
        try {
          const res = await api.updatePaper(id, payload)
          const saved = res.paper
          if (pendingPdfFile) {
            try {
              const up = await api.uploadPdf(pendingPdfFile, saved.id)
              await api.updatePaper(saved.id, { pdfFile: up.pdfFile })
              toast('已保存，PDF 已上传')
            } catch (err) {
              toast('文献已保存，但 PDF 上传失败：' + err.message, 'error')
            }
          } else {
            toast('已保存修改')
          }
          refreshMeta()
          go('#/paper/' + encodeURIComponent(saved.id))
        } catch (err) {
          toast('保存失败：' + err.message, 'error')
          submitBtn.removeAttribute('disabled')
          submitBtn.textContent = '保存修改'
        }
      }
    })

    util.replace(form, ...[
      h('div.page-head', h('div',
        h('div.page-title', '编辑文献'),
        h('div.page-sub', paper.title))),
      h('div.panel',
        h('div.panel-head', '基本信息',
          h('div.right', h('span.hint', '带 * 的是必填'))),
        h('div.panel-body', h('div', { style: { display: 'grid', gap: '13px' } },
          h('div.field', h('label', '标题 *'), f.title),
          h('div.field', h('label', '作者'), authors),
          h('div.form-grid',
            h('div.field', h('label', '年份'), f.year),
            h('div.field', h('label', '类型'), typeSelect),
            h('div.field', h('label', '收藏'),
              h('label', { style: { display: 'flex', gap: '7px', alignItems: 'center', fontSize: '13.5px' } },
                starredInput, '加入我的收藏')),
            h('div.field', h('label', '我的评分'), ratingSlot)),
          h('div.form-grid',
            h('div.field', h('label', '期刊 / 会议'), f.venue),
            h('div.field', h('label', '卷'), f.volume),
            h('div.field', h('label', '期'), f.issue),
            h('div.field', h('label', '页码'), f.pages)),
          h('div.form-grid',
            h('div.field', h('label', 'DOI'), f.doi),
            h('div.field', h('label', '链接'), f.url),
            h('div.field', h('label', '出版社'), f.publisher),
            h('div.field', h('label', 'BibTeX 键'), f.bibtexKey)),
          h('div.field', h('label', '标签'), tags),
          h('div.field', h('label', '原文关键词'), f.keywords),
          h('div.field', h('label', '摘要'), f.summary),
          h('div', { id: 'pdf-row' },
            h('div.field', h('label', 'PDF 原文'),
              h('div', { id: 'pdf-row-line' }, pdfPickBtn, pdfNameText, pdfClearBtn, pdfInput),
              h('div.hint', '保存时会自动关联并提取正文')))
        ))),
      h('div.form-actions',
        h('button.btn', {
          type: 'button',
          onclick: () => { go('#/paper/' + encodeURIComponent(id)) }
        }, '取消'),
        h('span.hint', '快捷键：Ctrl + S 保存'),
        h('div.right',
          h('button.btn.danger', {
            type: 'button',
            onclick: async () => {
              const ok = await confirmDialog({ title: '删除这条文献？', body: '此操作不可撤销。', okText: '删除', danger: true })
              if (!ok) return
              try {
                await api.deletePaper(id)
                toast('已删除')
                refreshMeta()
                go('#/')
              } catch (err) { toast(err.message, 'error') }
            }
          }, '删除'),
          submitBtn))
    ])

    outlet.replaceChildren(h('div.content', h('div.detail', form)))
    renderPdfRow()
    document.onkeydown = null
  }

  export { editHandler }
