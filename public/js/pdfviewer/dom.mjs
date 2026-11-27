/* PDF 阅读器 DOM 与 SVG 图标工具（批注图标含荧光笔 / 便签 / 橡皮擦） */
import { ANNOT_DEFAULT_COLOR, clamp01, normalizeColor } from './annot-model.mjs'

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

/* 同一页里可能同时有好几个橡皮擦图标（工具栏 + 批注列表），
   clipPath 的 id 必须各不相同，否则后来的会覆盖前面的。 */
let eraserSeq = 0

export function annotIcon (kind, color) {
  const hex = normalizeColor(color) || ANNOT_DEFAULT_COLOR
  /* 24 视框、1.8 描边、全圆角端点：和 macOS / iOS 上那套线性图标同一路数。
     颜色只用在「正在用的那支笔」上（笔头划过的一道 + 便签的折角），
     其余线条跟随按钮文字色，深浅主题都不用改。 */
  const svg = svgNode('svg', {
    width: '18', height: '18', viewBox: '0 0 24 24', fill: 'none',
    stroke: 'currentColor', 'stroke-width': '1.8', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
    'aria-hidden': 'true', focusable: 'false'
  })
  if (kind === 'eraser') {
    /* 橡皮擦：斜放的圆角橡皮，被一道分隔线切成两段——
       右上是擦头（浅蓝），左下是橡皮主体（极淡的 currentColor 填充），
       底部再加一道粗横线当纸面。两段都有填充，18px 下也能看出「块面」。
       尺寸对齐左右两格：荧光笔墨迹 18.1×17.8、便签 18×17.2，
       这里按 18×17.6 反推——橡皮本体 15.6×9.8、纸面横线跨 17.4。 */
    const id = 'lv-eraser-clip-' + (++eraserSeq)
    const defs = svgNode('defs')
    const clip = svgNode('clipPath', { id: id })
    clip.appendChild(svgNode('rect', { x: '13.6', y: '3', width: '9', height: '18' }))
    defs.appendChild(clip)
    svg.appendChild(defs)

    // 以 (12,11.6) 为中心斜放；本体 14.8×9.2，旋转后包围盒约 18.5×18
    const body = svgNode('g', { transform: 'rotate(-40 12 11.6)' })
    const rectAttrs = { x: '4.6', y: '6.6', width: '14.8', height: '9.2', rx: '2.6' }
    // 主体底：极淡的 currentColor，深浅主题都成立
    body.appendChild(svgNode('rect', Object.assign({}, rectAttrs, {
      fill: 'currentColor', 'fill-opacity': '0.10', stroke: 'none'
    })))
    // 擦头：同一矩形只留分隔线右边，填浅蓝
    const cap = svgNode('g', { 'clip-path': 'url(#' + id + ')' })
    cap.appendChild(svgNode('rect', Object.assign({}, rectAttrs, {
      fill: '#7cc7e0', 'fill-opacity': '0.95', stroke: 'none'
    })))
    body.appendChild(cap)
    body.appendChild(svgNode('rect', Object.assign({}, rectAttrs, {
      fill: 'none', stroke: 'currentColor', 'stroke-width': '1.9'
    })))
    body.appendChild(svgNode('path', { d: 'M13.6 6.6v9.2', 'stroke-width': '1.9' }))
    svg.appendChild(body)
    // 纸面：比橡皮略窄的一道粗横线，正好把整体高度顶到 17.6
    svg.appendChild(svgNode('path', { d: 'M4.1 20.3h15.8', 'stroke-width': '2.6' }))
    return svg
  }
  if (kind === 'note') {
    // 便签：带折角的便条 + 两行字
    svg.appendChild(svgNode('path', {
      d: 'M15.4 3.4H6.2A2.8 2.8 0 0 0 3.4 6.2v11.6a2.8 2.8 0 0 0 2.8 2.8h11.6a2.8 2.8 0 0 0 2.8-2.8V9.4Z'
    }))
    svg.appendChild(svgNode('path', { d: 'M7.8 12.4h8.4M7.8 16h5.2' }))
    svg.appendChild(svgNode('path', {
      d: 'M15.4 3.4v6h6', fill: hex, 'fill-opacity': '0.22', stroke: hex
    }))
    return svg
  }
  // 荧光笔：斜握的笔身 + 笔头，底部一道当前颜色的划痕
  svg.appendChild(svgNode('path', { d: 'm9.4 11.2-5.6 5.6v3.3h8.8l3-3' }))
  svg.appendChild(svgNode('path', { d: 'M21.9 11.8 17.6 16.1a2.2 2.2 0 0 1-3.1 0l-5.2-5.2a2.2 2.2 0 0 1 0-3.1L13.6 3.5Z' }))
  svg.appendChild(svgNode('path', { d: 'M3.8 21.3h16.4', stroke: hex, 'stroke-width': '2.6' }))
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

/**
 * scrollPagePointIntoView 的反向换算：把「视口里的锚点」还原成 { page, y }。
 *
 * 换源（原文 ↔ 译文）或换标签页时阅读器是重新挂载的，只记住页码会让人回到「这一页的顶部」，
 * 所以要把页内位置一起记下来。锚点取视口高度的 viewportFraction 处——和恢复时用的是
 * 同一个参考点，来回切换才不会一页一页地往下漂。
 * 锚点可能落不在当前页里（页很短、缩放很小），向前/向后找到真正盖住它的那一页再算 y。
 */
export function viewportAnchor (viewer, scroll, viewportFraction) {
  const box = clientRectOf(scroll)
  if (!box || !box.height) return null
  const total = Number(viewer && viewer.pagesCount) || 0
  if (!total) return null
  const anchor = box.top + box.height * (Number.isFinite(viewportFraction) ? viewportFraction : 0.6)
  let pageNumber = Math.min(Math.max(Math.round(Number(viewer.currentPageNumber) || 1), 1), total)
  for (let guard = 0; guard <= total; guard++) {
    const page = pageInfoOf(viewer, pageNumber)
    if (!page) return null
    if (anchor < page.rect.top && pageNumber > 1) { pageNumber--; continue }
    if (anchor > page.rect.top + page.rect.height && pageNumber < total) { pageNumber++; continue }
    return { page: pageNumber, y: clamp01((anchor - page.rect.top) / page.rect.height) }
  }
  return null
}

export function eventPoint (ev) {
  if (!ev) return null
  const x = Number(ev.clientX)
  const y = Number(ev.clientY)
  if (!isFinite(x) || !isFinite(y)) return null
  return { x: x, y: y }
}

