/* 详情页子模块：PDF 阅读器多标签页（原文 + PDF 补充材料）、逐份批注读写与补充材料列表 */
import * as util from '../../util.mjs'
import { api } from '../../api.mjs'
import * as pdfviewer from '../../pdfviewer.mjs'
import { showRegionPreview } from '../../pdfviewer/translation-review.mjs'
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
    /* tab.lang 只在原文标签页上有意义（'' 标签既能显示原文也能显示译文）；
       补充材料永远按原文存，所以一律传 'orig'。 */
    const file = tab.file
    const lang = tab.file ? 'orig' : (tab.lang || 'orig')
    const key = buildAnnotKey(paper.id, file, tab.addedAt, lang)
    await pendingAnnotationSaves.get(key)
    if (viewSignal.aborted) return null
    let items = []
    let loaded = false
    try {
      const res = await api.getAnnotations(paper.id, viewSignal, file, lang)
      items = Array.isArray(res && res.items) ? res.items : []
      loaded = true
    } catch (err) {
      // 草稿仍可读取。只有成功读到服务端列表后，才会静默补存草稿。
    }
    if (viewSignal.aborted) return null
    const draft = readAnnotDraft(key)
    const state = { file, lang, key, knownIds: items.map(item => item.id), removed: new Set(draft.removed), warned: false }
    if (draft.items.length || draft.removed.length) {
      items = mergeAnnotations(items, draft.items).filter(item => !state.removed.has(item.id))
      if (loaded) savePdfAnnotations(state, items, true)
    }
    return { state, items }
  }

  function savePdfAnnotations (state, items, silent = false) {
    const { key, file, lang } = state
    const list = Array.isArray(items) ? items : []
    const ids = list.map(item => item && item.id).filter(Boolean)
    const saving = (pendingAnnotationSaves.get(key) || Promise.resolve())
      .then(() => api.saveAnnotations(paper.id, list, file, lang))
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
  let translationInfo = { state: 'idle', exists: false }
  let translationPoll = null
  let translationAction = false
  let translationRevision = 0
  let regionSelection = 0
  let regionPanel = null
  let activePdfTab = ''
  let pdfMount = null
  const pdfTabBar = h('div.pdf-doc-tabs', { role: 'tablist', 'aria-label': 'PDF 文档' })
  const pdfBody = h('div', { id: 'pdf-document-panel', role: 'tabpanel' })
  const translateBtn = h('button.btn.sm', {
    type: 'button',
    title: '把原文就地翻译成中文并保留原版式，可随时切回原文',
    onclick: () => toggleTranslation()
  }, '翻译')
  /* 原文 / 翻译开关不再单独占一个位置：它被渲染进「PDF」标签内部（见 paintPdfTabs），
     这样面板头只剩「标签条 + 动作组」两组，宽度变化时不会再出现两组各自 margin-left:auto
     把开关挤到中间的错位。 */
  const langSwitch = h('div.pdf-lang-switch', { role: 'group', 'aria-label': '原文 / 翻译', hidden: '' },
    h('button.pdf-lang-btn', { type: 'button', 'data-mode': 'orig', 'aria-pressed': 'true', onclick: () => switchLang('orig') }, '原文'),
    h('button.pdf-lang-btn', { type: 'button', 'data-mode': 'trans', 'aria-pressed': 'false', onclick: () => switchLang('trans') }, '翻译')
  )
  const originalPdfActions = h('div.pdf-head-actions',
    pdfOk ? translateBtn : null,
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
    h('div.panel-head.pdf-head', pdfTabBar, originalPdfActions), uploadInput, pdfBody
  )

  /* ---------- 原文 / 译文切换（保留原版式的原位翻译） ---------- */
  function paintLangSwitch () {
    const hasTrans = !!translationInfo.exists
    const translating = translationInfo.state === 'running'
    // 开关已经是「PDF」标签的一部分，标签在不在当前页都保留位置，只靠样式表达非活动态，
    // 避免切到 SI 标签页时标签条宽度跳变。翻译能力本身仍只在有 PDF 时可用。
    langSwitch.hidden = !pdfOk
    for (const btn of langSwitch.querySelectorAll('.pdf-lang-btn')) {
      const on = btn.dataset.mode === translationMode
      btn.setAttribute('aria-pressed', String(on))
      btn.disabled = btn.dataset.mode === 'trans' && !hasTrans
    }
    translateBtn.disabled = translationAction
    translateBtn.textContent = translating
      ? (translationInfo.retryBlock ? '取消重译写回'
          : '取消翻译 ' + (translationInfo.progress || 0) + (translationInfo.total ? '/' + translationInfo.total : ''))
      : (hasTrans ? '重新翻译' : '翻译')
    translateBtn.title = translating
      ? '正在翻译：点一下取消（已经花掉的模型调用不会退回）'
      : (translationInfo.state === 'failed' && translationInfo.error
          ? '上次失败：' + translationInfo.error
          : '把原文就地翻译成中文并保留原版式，可随时切回原文')
  }

  function updateTranslationState (state) {
    const wasRunning = translationInfo.state === 'running'
    translationInfo = state
    paintLangSwitch()
    if (!wasRunning) return
    if (state.retryBlock) {
      if (state.state === 'done') {
        regionPanel?.close()
        toast('已更新所选区域的译文')
        const tab = pdfTabs.get('')
        if (tab.lang === 'trans') {
          tab.url = translatedPdfUrl()
          if (activePdfTab === '') switchLang('trans')
        }
      } else if (state.state === 'failed') {
        const message = state.error || '已保留当前译文'
        if (regionPanel) regionPanel.applyFinished(message)
        else toast(message, 'error')
      } else if (state.state === 'cancelled') {
        regionPanel?.applyFinished('已取消写回')
      }
      return
    }
    if (state.state === 'done') {
      const captions = state.renderedCaptions ? `，已翻译 ${state.renderedCaptions} 条图表题注` : ''
      const retained = state.preservedOriginal ? `，${state.preservedOriginal} 个文本块仍显示原文` : ''
      toast('译文已生成' + captions + retained, retained ? 'warn' : undefined)
      switchLang('trans')
    } else if (state.state === 'failed') {
      toast('翻译失败：' + (state.error || '未知原因'), 'error')
    }
  }

  async function refreshTranslationState () {
    const revision = translationRevision
    try {
      const { translation } = await api.translationStatus(paper.id, signal)
      if (!signal.aborted && !translationAction && revision === translationRevision) {
        updateTranslationState(translation)
      }
    } catch {
      // 保留上一次状态，后台任务仍在运行时会继续轮询。
    }
  }

  function scheduleTranslationPoll () {
    if (signal.aborted || translationPoll !== null || translationInfo.state !== 'running') return
    translationPoll = setTimeout(async () => {
      await refreshTranslationState()
      translationPoll = null
      scheduleTranslationPoll()
    }, 2500)
  }

  /* 标签按钮上的说明文字（悬停提示）随原文/译文变化，但标签正文固定为「PDF」——
     当前是哪一版由标签内部的开关显示，不需要再在标签名后面追一个「（译文）」。 */
  function paintOriginalTabTitle () {
    const btn = pdfTabBar.querySelector('.pdf-doc-tab[data-original="true"] .pdf-doc-tab-name')
    if (btn) btn.title = (pdfTabs.get('') || {}).title || 'PDF'
  }

  function translatedPdfUrl () {
    const url = api.translationUrl(paper.id)
    return translationInfo.outputRevision ? url + '?v=' + encodeURIComponent(translationInfo.outputRevision) : url
  }

  function switchLang (mode) {
    if (mode === 'trans' && !translationInfo.exists) return
    translationMode = mode
    const tab = pdfTabs.get('')
    if (!tab) return
    if (activePdfTab === '' && activePdfViewer) tab.view = activePdfViewer.getViewState()
    tab.url = mode === 'trans' ? translatedPdfUrl() : api.pdfUrl(paper.id)
    tab.title = mode === 'trans' ? 'PDF（译文）' : 'PDF'
    // 原文 / 译文各存一份批注：换版本要连批注一起换，不能把英文的坐标叠到中文上
    tab.lang = mode
    paintLangSwitch()
    paintOriginalTabTitle()
    // 正在看某份补充材料时点开关：先切回原文标签页，再按新版本重新挂载。
    if (activePdfTab !== '') {
      selectPdfTab('')
      return
    }
    abortViewer()
    pdfTabController = new AbortController()
    pdfMount = openPdfDocument(tab, pdfTabController.signal)
  }

  async function toggleTranslation () {
    if (!pdfOk || translationAction) return
    const cancelling = translationInfo.state === 'running'
    translationAction = true
    translationRevision++
    paintLangSwitch()
    try {
      const res = cancelling
        ? await api.cancelTranslation(paper.id)
        : await api.startTranslation(paper.id, translationInfo.exists)
      if (signal.aborted) return
      updateTranslationState(res.state)
      if (cancelling) {
        if (res.state.state === 'running') toast('已请求取消，正在收尾…')
      } else if (res.reason === 'cached') {
        switchLang('trans')
      } else if (res.started) {
        toast('已开始翻译：完成后会自动切到译文（关掉页面任务也会在后台继续）')
      }
    } catch (err) {
      if (!signal.aborted) toast(err.message, 'error')
    } finally {
      translationAction = false
      if (!signal.aborted) {
        paintLangSwitch()
        scheduleTranslationPoll()
      }
    }
  }

  async function applyRegion (token, viewSignal) {
    if (translationAction || translationInfo.state === 'running') throw new Error('这篇文献正在翻译，请稍后再试')
    translationAction = true
    translationRevision++
    paintLangSwitch()
    try {
      const res = await api.translationRegion(paper.id, { action: 'apply', token }, viewSignal)
      if (!signal.aborted) updateTranslationState(res.state)
    } finally {
      translationAction = false
      if (!signal.aborted) {
        paintLangSwitch()
        scheduleTranslationPoll()
      }
    }
  }

  async function openRegionPreview (area, revision, viewSignal) {
    if (translationAction || translationInfo.state === 'running') {
      toast('这篇文献正在翻译，请稍后再试', 'warn')
      return
    }
    const sequence = ++regionSelection
    regionPanel?.close()
    try {
      const draft = await api.translationRegion(paper.id, { action: 'inspect', revision, ...area }, viewSignal)
      if (viewSignal.aborted || sequence !== regionSelection) return
      regionPanel = showRegionPreview({
        source: draft.source, signal: viewSignal,
        onPreview: () => api.translationRegion(paper.id, { action: 'preview', token: draft.token }, viewSignal),
        onApply: () => applyRegion(draft.token, viewSignal),
        onClose: () => { regionPanel = null }
      })
    } catch (err) {
      if (!viewSignal.aborted && sequence === regionSelection) toast(err.message, 'warn')
    }
  }

  signal.addEventListener('abort', () => {
    clearTimeout(translationPoll)
  }, { once: true })

  function paintPdfTabs () {
    const files = [...pdfTabs.keys()]
    pdfTabBar.replaceChildren(...files.map((file, index) => {
      const tab = pdfTabs.get(file)
      const selected = file === activePdfTab
      const buttonId = 'pdf-document-tab-' + index
      if (selected) pdfBody.setAttribute('aria-labelledby', buttonId)
      return h('div.pdf-doc-tab', {
        role: 'presentation', 'data-active': String(selected), 'data-original': file ? null : 'true'
      },
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
      }, file ? tab.title : 'PDF'),
      // 原文标签页把「原文 / 翻译」开关收进标签内部，面板头因此只剩「标签条 + 动作组」两段
      file ? null : langSwitch,
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
    const isTranslation = tab.lang === 'trans'
    const [annotations, review] = await Promise.all([
      loadPdfAnnotations(tab, viewSignal),
      isTranslation ? api.translationReview(paper.id, viewSignal).catch(() => null) : null
    ])
    if (!annotations || viewSignal.aborted) return null
    host.replaceChildren()
    const target = !tab.file && !tab.view ? figureLocation : null
    const viewer = await mountPdfViewer(host, {
      url: tab.url, title: tab.title, downloadUrl: tab.url, signal: viewSignal,
      initialView: tab.view || target,
      annotations: annotations.items,
      onTranslationRegion: review?.revision
        ? area => openRegionPreview(area, review.revision, viewSignal)
        : undefined,
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

  if (pdfOk) refreshTranslationState().then(scheduleTranslationPoll)

  return {
    pdfPanel,
    morePanel,
    selectPdfTab,
    getActivePdfViewer: () => activePdfViewer,
    abortViewer
  }
}
