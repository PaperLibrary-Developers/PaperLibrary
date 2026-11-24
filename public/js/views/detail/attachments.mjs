/* 详情页子模块：PDF 阅读器多标签页（原文 + PDF 补充材料）、逐份批注读写与补充材料列表 */
import * as util from '../../util.mjs'
import { api } from '../../api.mjs'
import * as pdfviewer from '../../pdfviewer.mjs'
import { refreshMeta, render } from '../../shell/app-api.mjs'
import {
  buildAnnotKey,
  clearAnnotDraft,
  mergeAnnotations,
  pendingAnnotationSaves,
  readAnnotDraft,
  writeAnnotDraft
} from './annot-draft.mjs'

const { h } = util

async function mountPdfViewer (host, options) {
  const { signal, url } = options
  const showIframe = reason => {
    if (signal.aborted) return null
    if (reason) console.warn('[pdf] 使用浏览器自带查看器：' + reason)
    host.classList.add('pdf-fallback')
    const page = options.initialView?.page
    host.replaceChildren(h('iframe.pdf-frame', {
      src: url + (page > 0 ? '#page=' + page + '&view=FitH' : '#view=FitH'),
      title: 'PDF 预览'
    }))
    signal.addEventListener('abort', () => host.replaceChildren(), { once: true })
    return null
  }
  try {
    const res = await pdfviewer.mount(host, options)
    return res.ok ? res : showIframe(res.reason)
  } catch (err) {
    return showIframe(err && err.message)
  }
}

export function buildPdfAndAttachments ({
  paper, pdfOk, figureLocation, signal, isStale, toast, confirm, isReadingOriginal, syncHeight, onRegionSelected
}) {
  let activePdfViewer = null
  let pdfTabController = null

  /* ---------- PDF 批注（原文与每份 SI 分别写入） ---------- */
  async function loadPdfAnnotations (tab, viewSignal) {
    const { file } = tab
    const key = buildAnnotKey(paper.id, file, tab.addedAt)
    await pendingAnnotationSaves.get(key)
    if (viewSignal.aborted) return null
    let items = []
    let loaded = false
    try {
      const res = await api.getAnnotations(paper.id, viewSignal, file)
      items = Array.isArray(res && res.items) ? res.items : []
      loaded = true
    } catch (err) {
      // 草稿仍可读取。只有成功读到服务端列表后，才会静默补存草稿。
    }
    if (viewSignal.aborted) return null
    const draft = readAnnotDraft(key)
    const state = { file, key, knownIds: items.map(item => item.id), removed: new Set(draft.removed), warned: false }
    if (draft.items.length || draft.removed.length) {
      items = mergeAnnotations(items, draft.items).filter(item => !state.removed.has(item.id))
      if (loaded) savePdfAnnotations(state, items, true)
    }
    return { state, items }
  }

  function savePdfAnnotations (state, items, silent = false) {
    const { key, file } = state
    const list = Array.isArray(items) ? items : []
    const ids = list.map(item => item && item.id).filter(Boolean)
    const saving = (pendingAnnotationSaves.get(key) || Promise.resolve())
      .then(() => api.saveAnnotations(paper.id, list, file))
      .then(() => {
        state.knownIds = ids
        state.removed.clear()
        state.warned = false
        clearAnnotDraft(key)
      })
      .catch(err => {
        for (const id of state.knownIds) if (!ids.includes(id)) state.removed.add(id)
        for (const id of ids) state.removed.delete(id)
        writeAnnotDraft(key, list, [...state.removed])
        if (isStale() || silent || state.warned) return
        state.warned = true
        toast('批注保存失败：' + err.message + '（已暂存在本机，下次打开会自动重试）', 'error')
      })
      .finally(() => {
        if (pendingAnnotationSaves.get(key) === saving) pendingAnnotationSaves.delete(key)
      })
    pendingAnnotationSaves.set(key, saving)
  }

  /* ---------- PDF 面板与多标签页 ---------- */
  const uploadInput = h('input', {
    type: 'file',
    accept: 'application/pdf,.pdf',
    style: { display: 'none' },
    onchange: async ev => {
      const file = ev.target.files && ev.target.files[0]
      if (!file) return
      toast('正在上传 ' + file.name + ' …')
      try {
        const res = await api.uploadPdf(file, paper.id, () => {})
        await api.updatePaper(paper.id, { pdfFile: res.pdfFile })
        if (isStale()) return
        if (res.extract && res.extract.ok) {
          toast('PDF 已关联，并已提取正文 ' + res.extract.chars + ' 字')
        } else if (res.extract && res.extract.ok === false) {
          toast('PDF 已关联，但正文提取失败：' + res.extract.error, 'warn')
        } else {
          toast('PDF 已关联')
        }
        await refreshMeta()
        if (!isStale()) render()
      } catch (err) {
        toast(err.message, 'error')
      }
    }
  })

  const pdfTabs = new Map([['', { file: '', title: 'PDF', url: pdfOk ? api.pdfUrl(paper.id) : '' }]])
  let translationMode = 'orig'
  let translationInfo = { state: 'idle', exists: false, translating: false, progress: 0, total: 0, error: '' }
  let translationPoll = null
  let activePdfTab = ''
  let pdfMount = null
  const pdfTabBar = h('div.pdf-doc-tabs', { role: 'tablist', 'aria-label': 'PDF 文档' })
  const pdfBody = h('div', { id: 'pdf-document-panel', role: 'tabpanel' })
  const translateBtn = h('button.btn.sm', {
    type: 'button',
    title: '把原文就地翻译成中文并保留原版式，可随时切回原文',
    onclick: () => toggleTranslation()
  }, '翻译')
  const langSwitch = h('div.pdf-lang-switch', { role: 'group', 'aria-label': '原文 / 翻译', hidden: '' },
    h('button.pdf-lang-btn', { type: 'button', 'data-mode': 'orig', 'aria-pressed': 'true', onclick: () => switchLang('orig') }, '原文'),
    h('button.pdf-lang-btn', { type: 'button', 'data-mode': 'trans', 'aria-pressed': 'false', onclick: () => switchLang('trans') }, '翻译')
  )
  const originalPdfActions = h('div.right.pdf-original-actions',
    pdfOk ? translateBtn : null,
    pdfOk ? h('a.btn.sm', { href: api.pdfUrl(paper.id), target: '_blank', rel: 'noopener' }, '新标签页打开') : null,
    h('button.btn.sm', { type: 'button', onclick: () => uploadInput.click() }, paper.pdfFile ? '更换文件' : '上传 PDF'),
    paper.pdfFile ? h('button.btn.sm.danger', {
      type: 'button',
      onclick: async () => {
        const ok = await confirm({
          title: '取消关联这个 PDF？',
          body: '只会取消关联并删除副本，不会影响你磁盘上其他位置的原始文件。',
          okText: '取消关联',
          danger: true
        })
        if (!ok) return
        try {
          await api.updatePaper(paper.id, { pdfFile: '' })
          if (isStale()) return
          toast('已取消关联')
          render()
        } catch (err) { toast(err.message, 'error') }
      }
    }, '取消关联') : null
  )
  const originalEmpty = h('div.pdf-empty',
    h('div', { text: paper.pdfFile ? '关联的 PDF 文件不见了：' + paper.pdfFile : '这条文献还没有关联 PDF。' }),
    h('div', { style: { fontSize: '13px', color: 'var(--faint)', maxWidth: '560px' } },
      '上传后文件会复制到数据目录的 papers/ 文件夹，之后无论原文件挪到哪里都能继续打开。也可以在编辑页填写「PDF 文件名」来关联 papers/ 目录里已有的文件。'),
    h('button.btn.primary', { type: 'button', onclick: () => uploadInput.click() }, '选择 PDF 文件上传')
  )
  const pdfPanel = h('section.panel', { id: 'pdf-panel' },
    h('div.panel-head', pdfTabBar, langSwitch, originalPdfActions), uploadInput, pdfBody
  )

  /* ---------- 原文 / 译文切换（保留原版式的原位翻译） ---------- */
  function paintLangSwitch () {
    const hasTrans = !!translationInfo.exists
    langSwitch.hidden = !pdfOk || activePdfTab !== ''
    for (const btn of langSwitch.querySelectorAll('.pdf-lang-btn')) {
      const on = btn.dataset.mode === translationMode
      btn.setAttribute('aria-pressed', String(on))
      btn.disabled = btn.dataset.mode === 'trans' && !hasTrans && !translationInfo.translating
    }
    translateBtn.disabled = false
    translateBtn.textContent = translationInfo.translating
      ? '取消翻译 ' + (translationInfo.progress || 0) + (translationInfo.total ? '/' + translationInfo.total : '')
      : (hasTrans ? '重新翻译' : '翻译')
    translateBtn.title = translationInfo.translating
      ? '正在翻译：点一下取消（已经花掉的模型调用不会退回）'
      : (translationInfo.state === 'failed' && translationInfo.error
          ? '上次失败：' + translationInfo.error
          : '把原文就地翻译成中文并保留原版式，可随时切回原文')
  }

  async function refreshTranslationState (silent) {
    try {
      const res = await api.translationStatus(paper.id, signal)
      const t = (res && res.translation) || {}
      translationInfo = {
        state: t.state || 'idle', exists: !!t.exists, translating: t.state === 'running',
        progress: t.progress || 0, total: t.total || 0, stage: t.stage || '', error: t.error || ''
      }
    } catch (err) {
      if (!silent) toast(err.message, 'error')
    }
    paintLangSwitch()
  }

  function switchLang (mode) {
    if (mode === 'trans' && !translationInfo.exists) return
    translationMode = mode
    const tab = pdfTabs.get('')
    if (!tab) return
    if (activePdfViewer) tab.view = activePdfViewer.getViewState()
    tab.url = mode === 'trans' ? api.translationUrl(paper.id) : api.pdfUrl(paper.id)
    tab.title = mode === 'trans' ? 'PDF（译文）' : 'PDF'
    pdfTabs.set('', tab)
    paintPdfTabs()
    if (activePdfTab === '') {
      abortViewer()
      pdfTabController = new AbortController()
      pdfMount = openPdfDocument(pdfTabs.get(''), pdfTabController.signal)
    }
  }

  async function toggleTranslation () {
    if (translationInfo.translating) return cancelTranslation()
    return startTranslation()
  }

  async function cancelTranslation () {
    try {
      await api.cancelTranslation(paper.id)
      toast('已请求取消，正在收尾…')
    } catch (err) {
      toast(err.message, 'error')
    }
  }

  async function startTranslation () {
    if (!pdfOk) return
    translationInfo.translating = true
    paintLangSwitch()
    try {
      const res = await api.startTranslation(paper.id, translationInfo.exists)
      if (res && res.reason === 'cached') {
        await refreshTranslationState(true)
        switchLang('trans')
        return
      }
      toast('已开始翻译：完成后会自动切到译文（关掉页面任务也会在后台继续）')
      if (!translationPoll) {
        translationPoll = setInterval(async () => {
          await refreshTranslationState(true)
          if (!translationInfo.translating) {
            clearInterval(translationPoll)
            translationPoll = null
            if (translationInfo.state === 'done') {
              toast('译文已生成')
              switchLang('trans')
            } else if (translationInfo.state === 'failed') {
              toast('翻译失败：' + (translationInfo.error || '未知原因'), 'error')
            }
          }
        }, 2500)
      }
    } catch (err) {
      translationInfo.translating = false
      paintLangSwitch()
      toast(err.message, 'error')
    }
  }

  signal.addEventListener('abort', () => {
    if (translationPoll) { clearInterval(translationPoll); translationPoll = null }
  }, { once: true })

  function paintPdfTabs () {
    const files = [...pdfTabs.keys()]
    pdfTabBar.replaceChildren(...files.map((file, index) => {
      const tab = pdfTabs.get(file)
      const selected = file === activePdfTab
      const buttonId = 'pdf-document-tab-' + index
      if (selected) pdfBody.setAttribute('aria-labelledby', buttonId)
      return h('div.pdf-doc-tab', { role: 'presentation', 'data-active': String(selected) },
        h('button.pdf-doc-tab-name', {
          id: buttonId, type: 'button', role: 'tab', title: tab.title,
          'aria-controls': pdfBody.id, 'aria-selected': String(selected), tabindex: selected ? 0 : -1,
          onclick: () => selectPdfTab(file, true),
          onkeydown: ev => {
            const step = { ArrowRight: 1, ArrowLeft: -1 }[ev.key]
            if (step === undefined && ev.key !== 'Home' && ev.key !== 'End') return
            ev.preventDefault()
            const next = ev.key === 'Home' ? 0 : ev.key === 'End' ? files.length - 1 : (index + step + files.length) % files.length
            selectPdfTab(files[next], true)
          }
        }, tab.title),
        file ? h('button.pdf-doc-tab-close', {
          type: 'button', title: '关闭标签页', 'aria-label': '关闭 ' + tab.title,
          onclick: () => closePdfTab(file, true)
        }, '×') : null)
    }))
    originalPdfActions.hidden = activePdfTab !== ''
    paintLangSwitch()
  }

  function selectPdfTab (file, focus = false) {
    if (file === activePdfTab && pdfMount) {
      if (focus) pdfTabBar.querySelector('[aria-selected="true"]')?.focus()
      return pdfMount
    }
    if (isReadingOriginal()) {
      toast('正在识图识别全文，完成后可切换标签页', 'warn')
      return null
    }
    const previous = pdfTabs.get(activePdfTab)
    if (previous && activePdfViewer) previous.view = activePdfViewer.getViewState()
    activePdfViewer?.closeAnnotationEditor()
    if (pdfTabController) pdfTabController.abort()
    pdfTabController = new AbortController()
    activePdfViewer = null
    activePdfTab = file
    paintPdfTabs()
    const selected = pdfTabBar.querySelector('[aria-selected="true"]')
    if (focus || file) selected?.scrollIntoView({ block: 'nearest', inline: 'nearest' })
    if (focus) selected?.focus()
    pdfMount = openPdfDocument(pdfTabs.get(file), pdfTabController.signal)
    return pdfMount
  }

  async function openPdfDocument (tab, viewSignal) {
    if (!tab.url) {
      pdfBody.replaceChildren(originalEmpty)
      syncHeight()
      return null
    }
    const host = h('div.pdf-viewer-host', { id: 'pdf-viewer-host' }, h('div.pdf-empty', '正在打开 PDF…'))
    pdfBody.replaceChildren(host)
    syncHeight()
    const annotations = await loadPdfAnnotations(tab, viewSignal)
    if (!annotations || viewSignal.aborted) return null
    host.replaceChildren()
    const target = !tab.file && !tab.view ? figureLocation : null
    const viewer = await mountPdfViewer(host, {
      url: tab.url, title: tab.title, downloadUrl: tab.url, signal: viewSignal,
      initialView: tab.view || target,
      annotations: annotations.items,
      onAnnotationsChange: items => { if (!viewSignal.aborted) savePdfAnnotations(annotations.state, items) },
      onRegionSelected: (dataUrl, info) => {
        if (!viewSignal.aborted) onRegionSelected(dataUrl, { ...info, source: tab.file ? tab.title : '' })
      }
    })
    if (viewSignal.aborted) return null
    activePdfViewer = viewer
    syncHeight()
    if (target) {
      const topbar = document.getElementById('topbar')
      host.style.scrollMarginTop = (topbar?.getBoundingClientRect().height || 0) + 'px'
      host.scrollIntoView({ block: 'start', behavior: 'instant' })
    }
    return viewer
  }

  function openPdfAttachment (att) {
    if (!/\.pdf$/i.test(att.file)) {
      toast('此格式不支持在 PDF 阅读器中打开，可下载原文件。', 'warn')
      return
    }
    if (isReadingOriginal()) {
      toast('正在识图识别全文，完成后可切换标签页', 'warn')
      return
    }
    if (!pdfTabs.has(att.file)) {
      pdfTabs.set(att.file, { file: att.file, addedAt: att.addedAt, title: att.originalName || att.file,
        url: api.attachmentUrl(paper.id, att.file) })
    }
    selectPdfTab(att.file)
    pdfPanel.scrollIntoView({ block: 'nearest' })
  }

  function closePdfTab (file, focus = false) {
    if (!pdfTabs.has(file) || !file) return
    const files = [...pdfTabs.keys()]
    pdfTabs.delete(file)
    if (file === activePdfTab) selectPdfTab(files[files.indexOf(file) - 1], focus)
    else {
      paintPdfTabs()
      if (focus) pdfTabBar.querySelector('[aria-selected="true"]')?.focus()
    }
  }

  function abortViewer () {
    activePdfViewer?.closeAnnotationEditor()
    if (pdfTabController) pdfTabController.abort()
    activePdfViewer = null
  }

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

  const ATT_VISIBLE = 3
  let attExpanded = false

  async function loadAttachments () {
    try {
      if (isStale()) return
      const res = await api.listAttachments(paper.id, signal)
      if (isStale()) return
      const list = res.attachments || []
      paper.attachments = list
      if (!list.length) {
        attList.replaceChildren(h('div.hint.compact', '还没有补充材料（SI、附录、数据集说明等都可以放这里）'))
        return
      }
      const shown = attExpanded ? list : list.slice(0, ATT_VISIBLE)
      const restCount = list.length - shown.length
      const rows = shown.map(att => h('div.att-row',
        h('button.att-name', {
          type: 'button', onclick: () => openPdfAttachment(att),
          title: att.originalName + ' · ' + util.fileSize(att.size) +
            (att.hasText ? ' · 已抽取 ' + att.textChars + ' 字（用于总结与问答）' : ' · 尚未抽取文字')
        }, att.originalName),
        att.hasText ? h('span.att-flag', { title: '已抽取文字，会用于 AI 总结与问答' }, '已读入') : null,
        h('a.att-download', { href: api.attachmentUrl(paper.id, att.file), download: att.originalName || att.file,
          title: '下载原文件', 'aria-label': '下载 ' + att.originalName }, '↓'),
        h('button.att-del', {
          type: 'button',
          title: '删除这个补充材料',
          onclick: async () => {
            const ok = await confirm({
              title: '删除这个补充材料？',
              body: '「' + att.originalName + '」会从这篇文献上移除。',
              okText: '删除',
              danger: true
            })
            if (!ok) return
            try {
              await api.deleteAttachment(paper.id, att.file)
              closePdfTab(att.file)
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

  const moreUploadBtn = h('button.btn.sm.ghost', {
    type: 'button',
    title: '上传补充材料。PDF 可在阅读器中打开并批注。',
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

  refreshTranslationState(true)

  return {
    pdfPanel,
    morePanel,
    selectPdfTab,
    getActivePdfViewer: () => activePdfViewer,
    abortViewer
  }
}
