/* PDF 阅读器 DOM、SVG 图标与本地反色偏好工具 */
import { ANNOT_DEFAULT_COLOR, clamp01, normalizeColor } from './annot-model.mjs'

const INVERT_PREF_KEY = 'lv:pdf-invert'

const hasWindow = typeof window !== 'undefined'
export const requestRaf = hasWindow && window.requestAnimationFrame
  ? window.requestAnimationFrame.bind(window)
  : function (fn) { return setTimeout(fn, 16) }
export const cancelRaf = hasWindow && window.cancelAnimationFrame
  ? window.cancelAnimationFrame.bind(window)
  : function (id) { clearTimeout(id) }

export function appendChildren (parent, children) {
  if (children === null || children === undefined || children === false) return parent
  if (Array.isArray(children)) {
    for (let i = 0; i < children.length; i++) appendChildren(parent, children[i])
    return parent
  }
  if (children.nodeType === 1 || children.nodeType === 3) parent.appendChild(children)
  else parent.appendChild(document.createTextNode(String(children)))
  return parent
}

export function el (spec, attrs, children) {
  const parts = String(spec || 'div').split('.')
  const node = document.createElement(parts.shift() || 'div')
  if (parts.length) node.className = parts.join(' ')
  if (attrs) {
    const keys = Object.keys(attrs)
    for (let i = 0; i < keys.length; i++) {
      const key = keys[i]
      const value = attrs[key]
      if (value === null || value === undefined || value === false) continue
      if (key === 'text') node.textContent = String(value)
      else if (key === 'value') node.value = String(value)
      else if (key === 'class') node.className = (node.className ? node.className + ' ' : '') + String(value)
      else if (key.slice(0, 2) === 'on' && typeof value === 'function') node[key.toLowerCase()] = value
      else node.setAttribute(key, value === true ? '' : String(value))
    }
  }
  appendChildren(node, children)
  return node
}

export function svgNode (tag, attrs) {
  const node = document.createElementNS('http://www.w3.org/2000/svg', tag)
  if (attrs) {
    const keys = Object.keys(attrs)
    for (let i = 0; i < keys.length; i++) node.setAttribute(keys[i], String(attrs[keys[i]]))
  }
  return node
}

export function annotIcon (kind, color) {
  const hex = normalizeColor(color) || ANNOT_DEFAULT_COLOR
  const svg = svgNode('svg', {
    width: '14', height: '14', viewBox: '0 0 16 16',
    'aria-hidden': 'true', focusable: 'false'
  })
  if (kind === 'note') {
    svg.appendChild(svgNode('path', {
      d: 'M3 2.5h10v8.2l-3.3 3.3H3z',
      fill: hex, 'fill-opacity': '0.22', stroke: hex, 'stroke-width': '1.1', 'stroke-linejoin': 'round'
    }))
    svg.appendChild(svgNode('path', {
      d: 'M9.7 14v-3.3h3.3', fill: 'none', stroke: hex, 'stroke-width': '1.1', 'stroke-linejoin': 'round'
    }))
    svg.appendChild(svgNode('path', {
      d: 'M5.2 7.6h5.6', fill: 'none', stroke: hex, 'stroke-width': '1.4', 'stroke-linecap': 'round'
    }))
    return svg
  }
  svg.appendChild(svgNode('path', {
    d: 'M4.6 9.4 10.9 3.1a1.7 1.7 0 0 1 2.4 2.4L7 11.8z',
    fill: 'currentColor', 'fill-opacity': '0.14',
    stroke: 'currentColor', 'stroke-width': '1.1', 'stroke-linejoin': 'round'
  }))
  svg.appendChild(svgNode('path', {
    d: 'M6.9 11.7 4.7 9.5 2.3 13.7z',
    fill: hex, stroke: hex, 'stroke-width': '0.6', 'stroke-linejoin': 'round'
  }))
  return svg
}

export function isNightTheme () {
  const root = document.documentElement
  return !!root && root.getAttribute('data-theme') === 'night'
}

export function isEditableTarget (node) {
  if (!node) return false
  if (node.nodeType === 3) node = node.parentNode
  if (!node || node.nodeType !== 1) return false
  const tag = node.tagName
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
  return node.isContentEditable === true
}

export function clientRectOf (node) {
  if (!node || typeof node.getBoundingClientRect !== 'function') return null
  const rect = node.getBoundingClientRect()
  if (!rect) return null
  const out = {
    left: Number(rect.left), top: Number(rect.top),
    width: Number(rect.width), height: Number(rect.height)
  }
  if (!isFinite(out.left) || !isFinite(out.top) || !isFinite(out.width) || !isFinite(out.height)) return null
  return out
}

export function pageInfoOf (viewer, pageNumber) {
  if (!viewer || typeof viewer.getPageView !== 'function') return null
  const index = Math.round(Number(pageNumber)) - 1
  if (!(index >= 0)) return null
  let pageView = null
  try { pageView = viewer.getPageView(index) } catch (err) {}
  const div = pageView?.div
  const rect = clientRectOf(div)
  if (!rect || rect.width <= 0 || rect.height <= 0) return null
  return { pageNumber: index + 1, div, rect }
}

export function scrollPagePointIntoView (viewer, scroll, pageNumber, y, viewportFraction, behavior) {
  const page = pageInfoOf(viewer, pageNumber)
  const box = clientRectOf(scroll)
  if (!page || !box?.height) return false
  const contentTop = page.rect.top - box.top + scroll.scrollTop + clamp01(y) * page.rect.height
  const maxTop = Math.max(0, scroll.scrollHeight - scroll.clientHeight)
  const top = Math.max(0, Math.min(maxTop, contentTop - box.height * viewportFraction))
  try {
    if (typeof scroll.scrollTo === 'function') scroll.scrollTo({ top, behavior })
    else scroll.scrollTop = top
  } catch (err) {
    scroll.scrollTop = top
  }
  return true
}

export function eventPoint (ev) {
  if (!ev) return null
  const x = Number(ev.clientX)
  const y = Number(ev.clientY)
  if (!isFinite(x) || !isFinite(y)) return null
  return { x: x, y: y }
}

function getStore () {
  try {
    return typeof localStorage !== 'undefined' && localStorage ? localStorage : null
  } catch (err) {
    return null
  }
}

export function readInvertPref () {
  const store = getStore()
  if (!store) return null
  let raw = null
  try { raw = store.getItem(INVERT_PREF_KEY) } catch (err) { return null }
  if (raw === '1') return true
  if (raw === '0') return false
  return null
}

export function writeInvertPref (on) {
  const store = getStore()
  if (!store) return
  try { store.setItem(INVERT_PREF_KEY, on ? '1' : '0') } catch (err) {}
}
