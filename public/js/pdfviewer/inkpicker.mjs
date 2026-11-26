/* PDF 阅读器子模块：自定义取色盘（GoodNotes 风格：色相环 + 饱和度/明度面板 + 预设）
   只负责「选颜色」这一屏：拖动时实时把颜色交给上层预览，松手/确认才算一次提交。
   面板用 fixed 定位挂在 body 上，靠近取色条但不参与工具栏布局，动画只走 opacity/transform。 */
import { ANNOT_CUSTOM_MAX, clamp01, hexToHsv, hsvToHex, normalizeColor } from './annot-model.mjs'
import { el, svgNode } from './dom.mjs'

const WHEEL_SIZE = 176
/* 面板要缩到「外接圆」里，才不会顶到色相环：84×84 的外接圆半径 59.4，
   环内半径 68，留出约 9px 空隙，两个圆点也就不会叠在一起。 */
const PAD_INSET = 46
const HUE_KNOB_RADIUS = WHEEL_SIZE / 2 - 10

function chevronLeft () {
  const svg = svgNode('svg', {
    width: '14', height: '14', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    'stroke-width': '2', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false'
  })
  svg.appendChild(svgNode('path', { d: 'M14.6 5.6 8.2 12l6.4 6.4' }))
  return svg
}

function dropperIcon () {
  const svg = svgNode('svg', {
    width: '15', height: '15', viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
    'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false'
  })
  svg.appendChild(svgNode('path', { d: 'm14.6 3.9 5.5 5.5' }))
  svg.appendChild(svgNode('path', { d: 'M17.4 6.7 8.2 15.9l-1 4 4-1 9.2-9.2a2.6 2.6 0 0 0-3.6-3.6Z' }))
  svg.appendChild(svgNode('path', { d: 'm5.4 15.4-2 2a2.3 2.3 0 0 0 3.2 3.2l2-2' }))
  return svg
}

/* api: { ui, onPreview(hex), onCommit(hex), onAddPreset(hex) -> bool, presetCount() -> number } */
export function createInkPicker (api) {
  const { ui } = api
  let root = null
  let wheel = null
  let svBox = null
  let hueKnob = null
  let svKnob = null
  let addBtn = null
  let addLabel = null
  let hexInput = null
  let dropBtn = null
  let isOpen = false
  let hue = 46
  let sat = 0.8
  let val = 0.95
  let dragging = null
  let rafId = 0
  let addTimer = 0
  let outsideHandler = null

  function currentHex () {
    return hsvToHex(hue, sat, val)
  }

  function paint () {
    const hex = currentHex()
    if (svBox) svBox.style.setProperty('--inkp-hue', 'hsl(' + Math.round(hue) + ', 100%, 50%)')
    if (hueKnob) {
      const rad = hue * Math.PI / 180
      hueKnob.style.left = Math.round(WHEEL_SIZE / 2 + HUE_KNOB_RADIUS * Math.sin(rad)) + 'px'
      hueKnob.style.top = Math.round(WHEEL_SIZE / 2 - HUE_KNOB_RADIUS * Math.cos(rad)) + 'px'
    }
    if (svKnob) {
      const size = WHEEL_SIZE - PAD_INSET * 2
      svKnob.style.left = Math.round(PAD_INSET + sat * size) + 'px'
      svKnob.style.top = Math.round(PAD_INSET + (1 - val) * size) + 'px'
    }
    if (addBtn) addBtn.style.setProperty('--inkp-current', hex)
    if (hexInput && document.activeElement !== hexInput) hexInput.value = hex.slice(1).toUpperCase()
    return hex
  }

  /* 拖动时用 rAF 合并：一帧最多刷新一次颜色，避免每个 pointermove 都重排 */
  function emitPreview () {
    if (rafId) return
    rafId = requestAnimationFrame(function () {
      rafId = 0
      if (!isOpen) return
      api.onPreview(paint())
    })
  }

  function applyPoint (x, y) {
    const cx = WHEEL_SIZE / 2
    const size = WHEEL_SIZE - PAD_INSET * 2
    if (dragging === 'sv') {
      sat = clamp01((x - PAD_INSET) / size)
      val = 1 - clamp01((y - PAD_INSET) / size)
    } else {
      hue = (Math.atan2(x - cx, -(y - cx)) * 180 / Math.PI + 360) % 360
    }
    emitPreview()
  }

  function onPointerDown (ev) {
    if (!isOpen || dragging) return
    if (ev.button !== undefined && ev.button !== 0) return
    const box = wheel.getBoundingClientRect()
    if (!box.width) return
    const x = ev.clientX - box.left
    const y = ev.clientY - box.top
    const inner = PAD_INSET - 3
    const inside = x >= inner && x <= box.width - inner && y >= inner && y <= box.height - inner
    dragging = inside ? 'sv' : 'hue'
    wheel.classList.add('is-dragging')
    try { wheel.setPointerCapture(ev.pointerId) } catch (err) {}
    if (ev.cancelable) ev.preventDefault()
    applyPoint(x, y)
  }

  function onPointerMove (ev) {
    if (!dragging) return
    const box = wheel.getBoundingClientRect()
    if (!box.width) return
    applyPoint(ev.clientX - box.left, ev.clientY - box.top)
  }

  function onPointerUp () {
    if (!dragging) return
    dragging = null
    wheel.classList.remove('is-dragging')
    if (rafId) { try { cancelAnimationFrame(rafId) } catch (err) {} rafId = 0 }
    const hex = paint()
    try { api.onCommit(hex) } catch (err) {}
  }

  function flashAdd (ok, full) {
    if (!addBtn) return
    addBtn.classList.remove('is-done')
    addBtn.classList.add('is-pulse')
    setTimeout(function () { if (addBtn) addBtn.classList.remove('is-pulse') }, 320)
    if (!ok) {
      addBtn.title = full
        ? '自定义颜色最多 ' + ANNOT_CUSTOM_MAX + ' 个，先删一个再加'
        : '这个颜色已经在预设里了'
      return
    }
    addBtn.classList.add('is-done')
    if (addLabel) addLabel.textContent = '已加入预设'
    if (addTimer) clearTimeout(addTimer)
    addTimer = setTimeout(function () {
      addTimer = 0
      if (!addBtn) return
      addBtn.classList.remove('is-done')
      if (addLabel) addLabel.textContent = '添加到预设'
    }, 1400)
  }

  function addPreset () {
    const hex = currentHex()
    const before = api.presetCount()
    const full = before >= ANNOT_CUSTOM_MAX
    let ok = false
    try { ok = api.onAddPreset(hex, full) } catch (err) { ok = false }
    flashAdd(ok, full)
  }

  function commitHex () {
    const hex = normalizeColor('#' + String(hexInput.value || '').replace(/^#+/, '').trim())
    if (!hex) { paint(); return }
    setFromColor(hex)
    api.onPreview(hex)
    api.onCommit(hex)
  }

  function ensureDom () {
    if (root || !ui.toolbar) return
    wheel = el('div.lv-inkp-wheel', { role: 'presentation' })
    svBox = el('div.lv-inkp-sv', { 'aria-hidden': 'true' })
    hueKnob = el('span.lv-inkp-knob.lv-inkp-knob-hue', { 'aria-hidden': 'true' })
    svKnob = el('span.lv-inkp-knob.lv-inkp-knob-sv', { 'aria-hidden': 'true' })
    wheel.appendChild(svBox)
    wheel.appendChild(hueKnob)
    wheel.appendChild(svKnob)

    addLabel = el('span.lv-inkp-add-label', { text: '添加到预设' })
    addBtn = el('button.lv-inkp-add', {
      type: 'button', title: '把当前颜色存进预设，之后可以一键取用'
    }, [el('span.lv-inkp-blob', { 'aria-hidden': 'true' }), addLabel])

    hexInput = el('input.lv-inkp-hex-input', {
      type: 'text', maxlength: '7', spellcheck: 'false', autocomplete: 'off',
      'aria-label': '十六进制颜色值', placeholder: 'F2C433'
    })
    dropBtn = el('button.lv-inkp-drop', {
      type: 'button', title: '从屏幕上取一个颜色', 'aria-label': '从屏幕上取色'
    }, dropperIcon())

    const head = el('div.lv-inkp-head', null, [
      el('button.lv-inkp-back', {
        type: 'button', title: '返回预设颜色', 'aria-label': '返回预设颜色'
      }, chevronLeft()),
      el('span.lv-inkp-title', { text: '自定义颜色' }),
      dropBtn
    ])

    root = el('div.lv-inkp', {
      role: 'dialog', 'aria-label': '自定义颜色', 'aria-hidden': 'true'
    }, [
      head,
      wheel,
      el('div.lv-inkp-foot', null, [
        addBtn,
        el('label.lv-inkp-hex', null, [
          el('span.lv-inkp-hex-tag', { text: 'HEX' }),
          hexInput
        ])
      ])
    ])
    document.body.appendChild(root)

    root.querySelector('.lv-inkp-back').onclick = function () { close() }
    wheel.addEventListener('pointerdown', onPointerDown)
    wheel.addEventListener('pointermove', onPointerMove)
    wheel.addEventListener('pointerup', onPointerUp)
    wheel.addEventListener('pointercancel', onPointerUp)
    addBtn.onclick = addPreset
    hexInput.onkeydown = function (ev) {
      if (ev.key === 'Enter') { ev.preventDefault(); commitHex(); hexInput.blur() }
    }
    hexInput.onblur = commitHex
    if (typeof window.EyeDropper !== 'function') dropBtn.hidden = true
    dropBtn.onclick = function () {
      if (typeof window.EyeDropper !== 'function') return
      try {
        new window.EyeDropper().open().then(function (res) {
          const hex = normalizeColor(res && res.sRGBHex)
          if (!hex || !isOpen) return
          setFromColor(hex)
          api.onPreview(hex)
          api.onCommit(hex)
        }).catch(function () {})
      } catch (err) {}
    }
  }

  function place () {
    if (!root || !ui.palette) return
    const bar = ui.palette.getBoundingClientRect()
    const width = root.offsetWidth || 236
    const height = root.offsetHeight || 280
    const vw = window.innerWidth || 1024
    const vh = window.innerHeight || 768
    const margin = 8
    const barVisible = bar.bottom > 0 && bar.top < vh
    let left = bar.left + (bar.width - width) / 2
    let top = bar.bottom + 8
    if (barVisible) {
      // 下面放不下就翻到取色条上方，保证整块都在视口里
      if (top + height > vh - margin) top = bar.top - height - 8
    } else {
      // 取色条本身已经滚出视口（页面滚下去了）：贴住视口底部，视线不用乱找
      top = vh - height - 16
    }
    left = Math.min(Math.max(margin, left), Math.max(margin, vw - width - margin))
    top = Math.min(Math.max(margin, top), Math.max(margin, vh - height - margin))
    root.style.left = Math.round(left) + 'px'
    root.style.top = Math.round(top) + 'px'
  }

  function bindOutside () {
    if (outsideHandler || typeof document.addEventListener !== 'function') return
    outsideHandler = function (ev) {
      const target = ev && ev.target
      if (!target || !root) return
      if (root.contains(target)) return
      if (ui.palette && ui.palette.contains(target)) return

      if (typeof target.closest === 'function' && target.closest('.lv-inkp')) return
      close()
    }
    document.addEventListener('mousedown', outsideHandler, true)
  }

  function unbindOutside () {
    if (!outsideHandler) return
    try { document.removeEventListener('mousedown', outsideHandler, true) } catch (err) {}
    outsideHandler = null
  }

  function setFromColor (hex) {
    const hsv = hexToHsv(hex)
    if (hsv) {
      hue = hsv.h
      sat = hsv.s
      val = hsv.v
    }
    paint()
  }

  function open (hex) {
    ensureDom()
    if (!root) return
    setFromColor(hex)
    if (!hexInput.hidden && document.activeElement !== hexInput) hexInput.value = currentHex().slice(1).toUpperCase()
    isOpen = true
    root.classList.add('is-open')
    root.setAttribute('aria-hidden', 'false')
    place()
    bindOutside()
  }

  function close () {
    if (!isOpen || !root) return
    isOpen = false
    root.classList.remove('is-open', 'is-dragging')
    root.setAttribute('aria-hidden', 'true')
    if (rafId) { try { cancelAnimationFrame(rafId) } catch (err) {} rafId = 0 }
    if (addTimer) { clearTimeout(addTimer); addTimer = 0 }
    if (addBtn) addBtn.classList.remove('is-done')
    if (addLabel) addLabel.textContent = '添加到预设'
    unbindOutside()
  }

  function destroy () {
    close()
    if (root && root.parentNode) {
      try { root.parentNode.removeChild(root) } catch (err) {}
    }
    root = null
    wheel = svBox = hueKnob = svKnob = addBtn = addLabel = hexInput = dropBtn = null
  }

  return {
    open: open,
    destroy: destroy,
    close: close,
    place: place,
    isOpen: function () { return isOpen },
    refresh: paint
  }
}
