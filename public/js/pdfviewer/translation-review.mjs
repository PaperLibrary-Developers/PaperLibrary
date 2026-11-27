import { h, copyText } from '../util.mjs'

function printable (text) {
  return text.replace(/<\/?(?:sup|sub|i|b|em|strong)>/gi, '').replace(/[⟦⟧]/g, '')
}

export function showRegionPreview ({ source, signal, onPreview, onApply, onClose }) {
  const previousFocus = document.activeElement
  const originals = h('section', h('h4', '所选原文'),
    ...source.map(text => h('p', { text: printable(text) })))
  const results = h('section', { hidden: '' }, h('h4', '新译文'))
  const status = h('p.lv-pdf-region-status', { role: 'status' })
  let translated = []

  const closeButton = h('button.btn.sm', { type: 'button', onclick: close }, '关闭')
  const translateButton = h('button.btn.sm.primary', { type: 'button', onclick: translate }, '重译选区')
  const copyButton = h('button.btn.sm', { type: 'button', hidden: '', onclick: async () => {
    try {
      const copied = await copyText(translated.filter(Boolean).join('\n\n'))
      status.textContent = copied ? '译文已复制' : '复制失败，可选中译文手动复制。'
    } catch (err) {
      status.textContent = err.message
    }
  } }, '复制译文')
  const applyButton = h('button.btn.sm.primary', { type: 'button', hidden: '', onclick: apply }, '应用到译文')
  const panel = h('dialog.lv-pdf-translation-preview', { 'aria-label': '框选重译预览' },
    h('header', h('h3', '框选重译'), closeButton),
    h('div.lv-pdf-region-preview-text', originals, results), status,
    h('div.lv-pdf-region-preview-actions', translateButton, copyButton, applyButton)
  )

  function close () {
    signal.removeEventListener('abort', close)
    panel.remove()
    if (!signal.aborted && previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
    onClose()
  }

  async function translate () {
    translateButton.disabled = true
    status.textContent = '正在重译选区…'
    try {
      const preview = await onPreview()
      if (!panel.isConnected) return
      translated = preview.translations.map(printable)
      results.replaceChildren(h('h4', '新译文'),
        ...translated.map(text => h('p', { text: text || '该段未取得译文' })))
      results.hidden = false
      copyButton.hidden = false
      applyButton.hidden = !preview.canApply
      translateButton.hidden = true
      status.textContent = preview.canApply ? '' : '这份预览可阅读和复制。'
      if (preview.canApply) applyButton.focus()
      else copyButton.focus()
    } catch (err) {
      status.textContent = err.message
    } finally {
      translateButton.disabled = false
    }
  }

  async function apply () {
    applyButton.disabled = true
    status.textContent = '正在检查原版式并保存…'
    try { await onApply() } catch (err) { applyFinished(err.message) }
  }

  function applyFinished (message) {
    status.textContent = message
    applyButton.disabled = false
  }

  panel.addEventListener('keydown', event => {
    if (event.key === 'Escape') {
      event.preventDefault()
      close()
    }
  })
  signal.addEventListener('abort', close, { once: true })
  document.body.appendChild(panel)
  panel.show()
  translateButton.focus()
  return { close, applyFinished }
}
