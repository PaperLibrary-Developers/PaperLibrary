/* PDF.js 资源地址、动态按需加载与 PDFViewer 初始化 */
import { vendorUrl, getVendorBase, setVendorBase } from '../vendor.mjs'
import { DEP_HINT, describeError, errText } from './annot-model.mjs'

export const TEXT_LAYER_MODE_ENABLE = 1
export const MIN_CONTAINER_SIZE = 24
export const FALLBACK_FIND_STATE = { FOUND: 0, NOT_FOUND: 1, WRAPPED: 2, PENDING: 3 }

export function setPdfjsBase (base) { return setVendorBase('pdfjs', base) }
export function getPdfjsBase () { return getVendorBase('pdfjs') }

export function pdfjsUrls () {
  return {
    module: vendorUrl('pdfjs', '/build/pdf.mjs'),
    viewer: vendorUrl('pdfjs', '/web/pdf_viewer.mjs'),
    worker: vendorUrl('pdfjs', '/build/pdf.worker.min.mjs?v=spot-tint'),
    css: vendorUrl('pdfjs', '/web/pdf_viewer.css'),
    cmaps: vendorUrl('pdfjs', '/cmaps/'),
    standardFonts: vendorUrl('pdfjs', '/standard_fonts/')
  }
}

/**
 * 动态加载 pdf.js + 打开 PDF 文档 + 装配 EventBus / LinkService / FindController / PDFViewer。
 * 返回 { ok: true, viewer } 或 { ok: false, reason }（由调用方统一 fail）。
 */
export async function loadPdfDocument ({ url, state, ui, onLoadProgress, busEvents }) {
  const pdfUrls = pdfjsUrls()
  let pdfjs = null
  let viewerMod = null
  try {
    pdfjs = await import(pdfUrls.module)
    if (state.destroyed) return { ok: false, reason: '阅读器已关闭' }
    viewerMod = await import(pdfUrls.viewer)
  } catch (err) {
    return { ok: false, reason: DEP_HINT + '（' + errText(err) + '）' }
  }
  if (state.destroyed) return { ok: false, reason: '阅读器已关闭' }
  if (!pdfjs || typeof pdfjs.getDocument !== 'function' ||
    !viewerMod || typeof viewerMod.PDFViewer !== 'function') {
    return { ok: false, reason: DEP_HINT }
  }
  state.findState = viewerMod.FindState || FALLBACK_FIND_STATE
  try { pdfjs.GlobalWorkerOptions.workerSrc = pdfUrls.worker } catch (err) {}

  let pdfDocument = null
  try {
    const loadingTask = pdfjs.getDocument({
      url: url,
      cMapUrl: pdfUrls.cmaps,
      cMapPacked: true,
      standardFontDataUrl: pdfUrls.standardFonts
    })
    state.loadingTask = loadingTask
    loadingTask.onProgress = onLoadProgress
    pdfDocument = await loadingTask.promise
  } catch (err) {
    return { ok: false, reason: describeError(err) }
  }
  if (state.destroyed) return { ok: false, reason: '阅读器已关闭' }
  if (ui.total) ui.total.textContent = '/ ' + (pdfDocument.numPages || 0)
  if (ui.statusText) ui.statusText.textContent = '正在排版页面…'
  state.pdfDocument = pdfDocument

  let viewer = null
  try {
    const eventBus = new viewerMod.EventBus()
    const linkService = new viewerMod.PDFLinkService({
      eventBus: eventBus,
      externalLinkTarget: viewerMod.LinkTarget ? viewerMod.LinkTarget.BLANK : 2
    })
    const findController = new viewerMod.PDFFindController({ eventBus: eventBus, linkService: linkService })

    state.eventBus = eventBus
    state.linkService = linkService
    state.findController = findController

    Object.keys(busEvents).forEach(function (type) {
      eventBus.on(type, busEvents[type])
      state.busHandlers.push({ type: type, fn: busEvents[type] })
    })

    viewer = new viewerMod.PDFViewer({
      container: ui.scroll,
      viewer: ui.viewer,
      eventBus: eventBus,
      linkService: linkService,
      findController: findController,
      textLayerMode: TEXT_LAYER_MODE_ENABLE
    })
    linkService.setViewer(viewer)
    linkService.setDocument(pdfDocument, null)
    state.pdfViewer = viewer
  } catch (err) {
    return { ok: false, reason: '内置阅读器初始化失败：' + errText(err) }
  }

  try {
    viewer.setDocument(pdfDocument)
  } catch (err) {
    return { ok: false, reason: '内置阅读器渲染失败：' + errText(err) }
  }
  return { ok: true, viewer }
}
