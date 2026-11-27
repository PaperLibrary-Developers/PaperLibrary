import { h, copyText } from '../util.mjs'
import { injectStyle } from './styles.mjs'

function printable (text) {
  return text.replace(/<\/?(?:sup|sub|i|b|em|strong)>/gi, '').replace(/[⟦⟧]/g, '')
}

export function showRegionPreview ({ draft, onAction, focus }) {
  injectStyle()
  const previousFocus = document.activeElement
  const originals = h('section', h('h4', '所选原文'),
    ...draft.source.map(text => h('p', { text: printable(text) })))
  const results = h('section', { hidden: '' }, h('h4', '新译文'))
  const status = h('p.lv-pdf-region-status', { role: 'status' })
  let translated = []

  const closeButton = h('button.btn.sm.danger.lv-pdf-region-close', {
    type: 'button', onclick: () => onAction('close'), title: '关闭并丢弃此草稿'
  }, '关闭')
  const translateButton = h('button.btn.sm.primary', {
    type: 'button', onclick: () => onAction('preview')
  }, '重译选区')
  const copyButton = h('button.btn.sm', { type: 'button', hidden: '', onclick: async () => {
    try {
      const copied = await copyText(translated.filter(Boolean).join('\n\n'))
      showError(copied ? '译文已复制' : '复制失败，可选中译文手动复制。')
    } catch (err) {
      showError(err.message)
    }
  } }, '复制译文')
  const applyButton = h('button.btn.sm.primary', {
    type: 'button', hidden: '', onclick: () => onAction('apply')
  }, '应用到译文')
  const panel = h('dialog.lv-pdf-translation-preview', { 'aria-label': '框选重译预览' },
    h('header', h('h3', '框选重译'), closeButton),
    h('div.lv-pdf-region-preview-text', originals, results),
    h('footer', status, h('div.lv-pdf-region-preview-actions', translateButton, copyButton, applyButton))
  )

  function update (current) {
    const incoming = current.translations.map(printable)
    if (incoming.length !== translated.length || incoming.some((text, i) => text !== translated[i])) {
      translated = incoming
      results.replaceChildren(h('h4', '新译文'),
        ...translated.map(text => h('p', { text: text || '该段未取得译文' })))
    }
    const hasTranslation = translated.some(Boolean)
    results.hidden = !hasTranslation
    copyButton.hidden = !hasTranslation
    translateButton.hidden = !['selected', 'previewing'].includes(current.phase)
    translateButton.disabled = current.phase === 'previewing'
    applyButton.hidden = !current.canApply && current.phase !== 'applying'
    applyButton.disabled = current.phase === 'applying'
    status.textContent = current.message || ({
      previewing: '正在重译选区…',
      applying: '正在检查原版式并保存…',
      ready: current.canApply ? '' : '这份预览可阅读和复制。'
    }[current.phase] || '')
    status.hidden = !status.textContent
  }

  function showError (message) {
    status.textContent = message
    status.hidden = false
  }

  function dispose (restoreFocus = false) {
    const hadFocus = panel.contains(document.activeElement)
    panel.remove()
    if (restoreFocus && hadFocus && previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
  }

  // Escape can still cancel the PDF reader's active tool. Discarding this draft is explicit.
  panel.addEventListener('cancel', event => event.preventDefault())
  update(draft)
  document.body.appendChild(panel)
  panel.show()
  if (focus) translateButton.focus({ preventScroll: true })
  else if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true })
  return { update, showError, dispose }
}
