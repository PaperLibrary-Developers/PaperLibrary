/* 内置 PDF 阅读器（pdf.js 封装）
   ---------------------------------------------------------------
   - 以 ES module 加载：pdf.js 本体在运行时用动态 import() 拉取
     （顶层不写 import，只有 mount() 里按需拉；可选依赖缺失时如实降级）
   - 依赖是可选的 pdfjs-dist（服务端把 /vendor/pdfjs/* 指到 node_modules/pdfjs-dist/*）。
     没装、PDF 打不开、worker 起不来……一律「清掉已插入的 DOM + 返回 { ok:false, reason }」，
     绝不抛异常，调用方可以回退到浏览器自带的 PDF 查看器
   - 对外导出 mount(container, { url, title, downloadUrl }) + 反色偏好读写 + setPdfjsBase
   - 反色是**独立开关**：只由工具栏「反色」按钮改，选择记在 localStorage（lv:pdf-invert），
     下次打开、换一篇文献都沿用上次的选择；不跟系统/夜间主题联动
   - 所有样式自带（注入 <head>，只插一次），不修改 public/styles.css
   --------------------------------------------------------------- */
import { vendorUrl, getVendorBase, setVendorBase } from './vendor.mjs'

/** 覆盖 pdf.js 的资源前缀（P4.6：换后端/换 CDN 时用，必须在 mount 之前调用） */
export function setPdfjsBase (base) { return setVendorBase('pdfjs', base) }
/** 当前 pdf.js 的资源前缀 */
export function getPdfjsBase () { return getVendorBase('pdfjs') }

  /* ------------------------------- 常量 ------------------------------- */

  /**
   * pdf.js 的资源地址（P4.6：路径可配置，为将来换后端留口子）。
   * 前缀来自 vendor.mjs，默认同源 /vendor/pdfjs（服务端映射到 node_modules/pdfjs-dist）；
   * 换后端时在启动前调用 setPdfjsBase('https://…/pdfjs') 即可，本文件不用改。
   */
  function pdfjsUrls () {
    return {
      module: vendorUrl('pdfjs', '/build/pdf.mjs'),
      viewer: vendorUrl('pdfjs', '/web/pdf_viewer.mjs'),
      worker: vendorUrl('pdfjs', '/build/pdf.worker.min.mjs'),
      css: vendorUrl('pdfjs', '/web/pdf_viewer.css'),
      cmaps: vendorUrl('pdfjs', '/cmaps/'),                       // 中日韩编码表
      standardFonts: vendorUrl('pdfjs', '/standard_fonts/')
    }
  }

  const STYLE_ID = 'lv-pdfviewer-style'                // 我们自己的样式表（只插一次）
  const CSS_LINK_ID = 'lv-pdfviewer-css'               // pdf.js 的 pdf_viewer.css（只插一次）

  // 反色偏好：跨文献、跨会话都只认「用户最后一次点的是什么」。
  // 不放 lv:prefs：那份偏好会同步到服务端，而这是个纯本地的显示开关。
  const INVERT_PREF_KEY = 'lv:pdf-invert'              // '1' 反色 / '0' 正常；没有这个键 = 从没点过

  const MIN_SCALE = 0.5                                // 缩放范围 0.5 ~ 3 倍
  const MAX_SCALE = 3
  const SCALE_STEP = 0.15
  const TEXT_LAYER_MODE_ENABLE = 1                     // pdf.js TextLayerMode.ENABLE：文本层开，可选中/复制，搜索高亮才有意义
  const MIN_CONTAINER_SIZE = 24                        // 容器小于这个尺寸（还没布局好）时先不套用「适宽/适页」

  const DEP_HINT = '内置阅读器需要可选依赖 pdfjs-dist：请在项目目录执行 npm install'
  const GENERIC_FAIL = '内置阅读器打不开这个 PDF'

  // 截图（整页导出 / 框选提问）相关
  const CAPTURE_MAX_SCALE = 4                          // 重渲染缩放的硬上限：再大只是白烧内存
  const CAPTURE_MAX_PIXELS = 16 * 1024 * 1024          // 单张离屏画布的面积上限（约 1600 万像素）
  const CAPTURE_MAX_SIDE = 8192                        // 单边上限：部分浏览器（移动端）超了直接画不出东西
  const DEFAULT_CAPTURE_FORMAT = 'image/jpeg'          // 整页导出默认 JPEG：体积小，够 OCR 用
  const DEFAULT_CAPTURE_QUALITY = 0.85
  const DEFAULT_REGION_FORMAT = 'image/png'            // 框选出来的是「一小块」，用无损的 PNG
  const REGION_MIN_SIDE = 8                            // 框宽或高小于这个像素数：当成用户点空了，不回调

  // pdf.js 的 FindState（viewerMod.FindState 拿不到时的兜底值）
  const FALLBACK_FIND_STATE = { FOUND: 0, NOT_FOUND: 1, WRAPPED: 2, PENDING: 3 }

  /* ------------------------------ 样式表 ------------------------------ */
  /* 全部用项目已有的 CSS 变量 + 兜底值；夜间由 [data-theme="night"] 覆盖变量，这里不用重定义。
     额外接管 pdf_viewer.css 的 --page-margin / --page-border：默认值会让页面之间出现
     "-8px 负边距 + 9px 透明边框" 的怪空隙。 */

  const STYLE_TEXT = `
.lv-pdf-viewer {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 100%;
  min-height: 240px;
  overflow: hidden;
  font-family: var(--font-sans, sans-serif);
  font-size: 13px;
  color: var(--text, #26251f);
  background: var(--bg, #f3f1ea);
  border: 1px solid var(--border, #e2dfd5);
  border-radius: var(--radius-sm, 8px);
  --page-margin: 0 auto 10px;
  --page-border: 0;
  --pdfViewer-padding-bottom: 0;
}

/* ---------- 工具栏 ---------- */
.lv-pdf-viewer .lv-pdf-toolbar {
  flex: 0 0 auto;
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 8px;
  padding: 6px 8px;
  background: var(--surface-2, #f8f7f3);
  border-bottom: 1px solid var(--border, #e2dfd5);
}
.lv-pdf-viewer .lv-pdf-group { display: flex; align-items: center; gap: 4px; min-width: 0; flex-wrap: nowrap; }
.lv-pdf-viewer .lv-pdf-group.grow { flex: 1 1 220px; }
.lv-pdf-viewer .lv-pdf-group.right { margin-left: auto; }
.lv-pdf-viewer .lv-pdf-sep { flex: 0 0 1px; width: 1px; height: 18px; background: var(--border, #e2dfd5); }

.lv-pdf-viewer .lv-pdf-btn {
  display: inline-flex; align-items: center; justify-content: center; gap: 4px;
  padding: 4px 9px; font-size: 12.5px; line-height: 1.6;
  background: var(--surface, #fffefb); color: var(--text, #26251f);
  border: 1px solid var(--border, #e2dfd5); border-radius: var(--radius-sm, 8px);
  cursor: pointer; white-space: nowrap; text-decoration: none;
  transition: background .15s, border-color .15s, color .15s;
}
.lv-pdf-viewer .lv-pdf-btn:hover { background: var(--surface-3, #efeee8); border-color: var(--border-strong, #cfcabb); }
.lv-pdf-viewer .lv-pdf-btn:active { transform: translateY(1px); }
.lv-pdf-viewer .lv-pdf-btn.active {
  background: var(--accent-soft, #e4efea);
  border-color: var(--accent, #2f6f5e);
  color: var(--accent-text, #235648);
  font-weight: 600;
}
.lv-pdf-viewer .lv-pdf-btn:disabled { opacity: .45; cursor: default; transform: none; }

.lv-pdf-viewer .lv-pdf-input {
  min-width: 0;
  padding: 4px 6px; font-size: 12.5px; line-height: 1.6;
  background: var(--surface, #fffefb); color: var(--text, #26251f);
  border: 1px solid var(--border, #e2dfd5); border-radius: var(--radius-sm, 8px);
  outline: none;
}
.lv-pdf-viewer .lv-pdf-input:focus { border-color: var(--accent, #2f6f5e); }
.lv-pdf-viewer .lv-pdf-input:focus-visible,
.lv-pdf-viewer .lv-pdf-btn:focus-visible { outline: 2px solid var(--accent, #2f6f5e); outline-offset: 1px; }
.lv-pdf-viewer .lv-pdf-page { flex: 0 0 auto; width: 46px; text-align: center; font-variant-numeric: tabular-nums; }
/* 搜索框必须能「缩」：写死 150px 时，面板一窄这一组就溢出，
   压到右边那组按钮上（看着就是「按钮错位/叠在一起」）。
   现在它按剩余空间伸缩，实在放不下时整组换行，不会互相重叠。 */
.lv-pdf-viewer .lv-pdf-search { flex: 1 1 110px; width: auto; min-width: 78px; max-width: 260px; }

.lv-pdf-viewer .lv-pdf-num,
.lv-pdf-viewer .lv-pdf-count,
.lv-pdf-viewer .lv-pdf-zoom {
  flex: 0 0 auto;
  font-size: 12px; color: var(--muted, #75726a);
  white-space: nowrap; font-variant-numeric: tabular-nums;
}
/* 命中数从「」到「第 3 / 共 12 处」长度会变，固定一点宽度免得整排按钮左右抖 */
.lv-pdf-viewer .lv-pdf-count { min-width: 92px; }
.lv-pdf-viewer .lv-pdf-zoom { min-width: 42px; text-align: center; }

/* ---------- 阅读区 ---------- */
.lv-pdf-viewer .lv-pdf-body { position: relative; flex: 1 1 auto; min-height: 0; }
/* 这个就是 PDFViewer 的 container：pdf.js 要求它是绝对定位 */
.lv-pdf-viewer .lv-pdf-scroll {
  position: absolute; inset: 0;
  overflow: auto;
  overscroll-behavior: contain;
  background: var(--surface-2, #f8f7f3);
  scrollbar-width: thin;
  scrollbar-color: var(--border-strong, #cfcabb) transparent;
}
.lv-pdf-viewer .lv-pdf-scroll:focus { outline: none; }
.lv-pdf-viewer.lv-night .lv-pdf-scroll { background: var(--bg, #14161a); }
.lv-pdf-viewer .lv-pdf-scroll::-webkit-scrollbar { width: 10px; height: 10px; }
.lv-pdf-viewer .lv-pdf-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong, #cfcabb); border-radius: 99px;
  border: 3px solid transparent; background-clip: content-box;
}

/* 覆盖 pdf.js 默认外观：页面之间 10px、淡阴影 + 圆角，跟网站其它卡片一致 */
.lv-pdf-viewer .pdfViewer {
  padding: 14px 10px 18px;
  background: transparent;
}
.lv-pdf-viewer .pdfViewer .page {
  margin: 0 auto 10px;
  border: 0;
  border-radius: var(--radius-sm, 8px);
  box-shadow: var(--shadow-1, 0 1px 2px rgba(35, 33, 25, .05), 0 1px 3px rgba(35, 33, 25, .04));
  background-color: var(--page-bg-color, #fff);
}
.lv-pdf-viewer .pdfViewer .page:last-child { margin-bottom: 0; }
.lv-pdf-viewer .pdfViewer .page .canvasWrapper { border-radius: var(--radius-sm, 8px); }

/* 夜间：搜索高亮的颜色比 pdf.js 默认的洋红舒服一些 */
.lv-pdf-viewer.lv-night .textLayer .highlight {
  --highlight-bg-color: rgb(224 172 75 / .35);
  --highlight-selected-bg-color: rgb(124 192 168 / .5);
}

/* ---------- 反色（只由工具栏按钮控制，不跟系统/夜间主题联动）---------- */
.lv-pdf-viewer.lv-invert .pdfViewer .page canvas { filter: invert(1) hue-rotate(180deg); }
.lv-pdf-viewer.lv-invert .pdfViewer { --page-bg-color: #17191d; }

/* ---------- 加载提示 ---------- */
.lv-pdf-viewer .lv-pdf-status {
  position: absolute; inset: 0; z-index: 3;
  display: flex; align-items: center; justify-content: center; gap: 9px;
  padding: 16px; text-align: center;
  font-size: 13px; color: var(--muted, #75726a);
  background: var(--surface-2, #f8f7f3);
  pointer-events: none;
}
.lv-pdf-viewer.lv-night .lv-pdf-status { background: var(--bg, #14161a); }
.lv-pdf-viewer .lv-pdf-spinner {
  width: 14px; height: 14px; border-radius: 50%;
  border: 2px solid var(--border-strong, #cfcabb);
  border-top-color: var(--accent, #2f6f5e);
  animation: lv-pdf-spin .8s linear infinite;
}
@keyframes lv-pdf-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .lv-pdf-viewer .lv-pdf-spinner { animation: none; }
}

/* ---------- 框选提问 ---------- */
/* 「框选」按钮上的小图标：纯 CSS 画的虚线取景框，不依赖字体/图标库 */
.lv-pdf-viewer .lv-pdf-icon-frame {
  display: inline-block; width: 12px; height: 10px;
  border: 1.5px dashed currentColor; border-radius: 2px;
}
/* 取景层：盖在阅读区上，平时 display:none（由脚本切成 block） */
.lv-pdf-viewer .lv-pdf-region-layer {
  position: absolute; inset: 0; z-index: 4;
  cursor: crosshair;
  overflow: hidden;
  background: rgba(64, 60, 48, .10);
  touch-action: none;
  -webkit-user-select: none; user-select: none;
}
.lv-pdf-viewer.lv-night .lv-pdf-region-layer { background: rgba(0, 0, 0, .26); }
/* 拖出来的矩形：虚线边框高亮；框外用一个超大 spread 的 box-shadow 再压暗一层，
   这样不用额外塞 4 块遮罩 div，框内也始终是透亮的 */
.lv-pdf-viewer .lv-pdf-region-box {
  position: absolute; display: none;
  border: 1px dashed var(--accent, #2f6f5e);
  background: rgba(47, 111, 94, .10);
  box-shadow: 0 0 0 9999px rgba(20, 18, 12, .22);
  pointer-events: none;
}
.lv-pdf-viewer .lv-pdf-region-hint {
  position: absolute; left: 50%; top: 10px; transform: translateX(-50%);
  max-width: calc(100% - 20px);
  padding: 4px 10px; font-size: 12px; line-height: 1.5;
  color: var(--text, #26251f); background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5); border-radius: 99px;
  box-shadow: var(--shadow-1, 0 1px 2px rgba(35, 33, 25, .06));
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  pointer-events: none;
}
`

  /* ------------------------------ 小工具 ------------------------------ */

  const hasWindow = typeof window !== 'undefined'
  const requestRaf = hasWindow && window.requestAnimationFrame
    ? window.requestAnimationFrame.bind(window)
    : function (fn) { return setTimeout(fn, 16) }
  const cancelRaf = hasWindow && window.cancelAnimationFrame
    ? window.cancelAnimationFrame.bind(window)
    : function (id) { clearTimeout(id) }

  /** 创建元素：el('div.a.b', { title: 'x', text: '内容', onclick: fn }, [子节点]) */
  function el (spec, attrs, children) {
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

  /** 往父节点塞子节点：自动展开数组、跳过空值、字符串变文本节点 */
  function appendChildren (parent, children) {
    if (children === null || children === undefined || children === false) return parent
    if (Array.isArray(children)) {
      for (let i = 0; i < children.length; i++) appendChildren(parent, children[i])
      return parent
    }
    if (children.nodeType === 1 || children.nodeType === 3) parent.appendChild(children)
    else parent.appendChild(document.createTextNode(String(children)))
    return parent
  }

  /** 只插一次总样式表；失败也不影响功能 */
  function injectStyle () {
    if (document.getElementById(STYLE_ID)) return
    const head = document.head || document.documentElement
    if (!head) return
    const style = document.createElement('style')
    style.id = STYLE_ID
    style.textContent = STYLE_TEXT
    head.appendChild(style)
  }

  /** 引入 pdf.js 的 pdf_viewer.css（按 id 去重，404 也不抛，最多是文本层没样式） */
  function injectViewerCss () {
    if (document.getElementById(CSS_LINK_ID)) return
    const head = document.head || document.documentElement
    if (!head) return
    const link = document.createElement('link')
    link.id = CSS_LINK_ID
    link.rel = 'stylesheet'
    link.href = pdfjsUrls().css
    head.appendChild(link)
  }

  function isNightTheme () {
    const root = document.documentElement
    return !!root && root.getAttribute('data-theme') === 'night'
  }

  /** 拿本地存储；被禁用（隐私模式、file://）就返回 null，绝不让阅读器因此起不来 */
  function getStore () {
    try {
      return typeof localStorage !== 'undefined' && localStorage ? localStorage : null
    } catch (err) {
      return null
    }
  }

  /**
   * 读反色偏好。
   * @returns {boolean|null} true/false = 用户点过；null = 从没点过（本次会话默认正常显示）
   */
  function readInvertPref () {
    const store = getStore()
    if (!store) return null
    let raw = null
    try { raw = store.getItem(INVERT_PREF_KEY) } catch (err) { return null }
    if (raw === '1') return true
    if (raw === '0') return false
    return null
  }

  /** 记反色偏好；写不进去也不影响本次显示 */
  function writeInvertPref (on) {
    const store = getStore()
    if (!store) return
    try { store.setItem(INVERT_PREF_KEY, on ? '1' : '0') } catch (err) {}
  }

  function errText (err) {
    if (!err) return '未知错误'
    if (typeof err === 'string') return err
    return String(err.message || err.name || err)
  }

  /** 把 pdf.js 的英文报错翻译成能看懂的中文 */
  function describeError (err) {
    const name = (err && err.name) || ''
    const msg = errText(err)
    if (name === 'MissingPDFException') return '找不到这个 PDF 文件（可能已被移动或删除）'
    if (name === 'InvalidPDFException') return '这个文件不是有效的 PDF，或者已经损坏'
    if (name === 'PasswordException') return '这个 PDF 有密码保护，内置阅读器暂时打不开'
    if (name === 'UnexpectedResponseException') return '取 PDF 时服务端返回了异常（' + msg + '）'
    if (/fake worker|worker|dynamically imported|Failed to fetch|NetworkError|404/i.test(msg)) {
      return DEP_HINT + '（' + msg + '）'
    }
    return '打不开这个 PDF：' + msg
  }

  function clampScale (value) {
    if (!isFinite(value) || value <= 0) return 1
    return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value))
  }

  /** 输入框 / 可编辑区域里的按键不拦（否则没法在页码、搜索框里打字） */
  function isEditableTarget (node) {
    if (!node) return false
    if (node.nodeType === 3) node = node.parentNode
    if (!node || node.nodeType !== 1) return false
    const tag = node.tagName
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true
    return node.isContentEditable === true
  }

  /** 给下载链接起个像样的文件名（去掉不能用在文件名里的字符） */
  function downloadName (title) {
    const base = String(title || 'document').replace(/\s+/g, ' ').trim().replace(/[\\/:*?"<>|\u0000-\u001f]+/g, '_')
    const safe = base.slice(0, 80).replace(/[. ]+$/, '') || 'document'
    return safe + (/\.[Pp][Dd][Ff]$/.test(safe) ? '' : '.pdf')
  }

  /* --------------------------- 截图用的小工具 --------------------------- */

  /** 数值夹取：非法值（undefined / NaN / 字符串垃圾）返回 fallback */
  function clampNumber (value, min, max, fallback) {
    const n = Number(value)
    if (!isFinite(n)) return fallback
    return Math.min(max, Math.max(min, n))
  }

  /** 把「左上角 + 宽高」整理成规范矩形；任何一项不是数字就返回 null */
  function normalizeRect (raw) {
    if (!raw) return null
    const x1 = Number(raw.left)
    const y1 = Number(raw.top)
    const x2 = x1 + Number(raw.width)
    const y2 = y1 + Number(raw.height)
    if (!isFinite(x1) || !isFinite(y1) || !isFinite(x2) || !isFinite(y2)) return null
    return {
      left: Math.min(x1, x2),
      top: Math.min(y1, y2),
      width: Math.abs(x2 - x1),
      height: Math.abs(y2 - y1)
    }
  }

  /** 两个矩形（视口坐标）的重叠面积；不相交返回 0 */
  function overlapArea (a, b) {
    const w = Math.min(a.left + a.width, b.left + b.width) - Math.max(a.left, b.left)
    const h = Math.min(a.top + a.height, b.top + b.height) - Math.max(a.top, b.top)
    return w > 0 && h > 0 ? w * h : 0
  }

  /** 两个矩形之间的距离（相交为 0）：给「框落在页面之间的空隙上」兜底用 */
  function rectDistance (a, b) {
    const dx = Math.max(0, Math.max(a.left - (b.left + b.width), b.left - (a.left + a.width)))
    const dy = Math.max(0, Math.max(a.top - (b.top + b.height), b.top - (a.top + a.height)))
    return Math.sqrt(dx * dx + dy * dy)
  }

  /** 取元素的视口矩形；拿不到（或数值不合法）返回 null */
  function clientRectOf (node) {
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

  /* ------------------------------- 主流程 ------------------------------- */

  /**
   * 挂载一个内置 PDF 阅读器。
   * @param {HTMLElement} container 挂载点（调用方保证它有确定的宽高）
   * @param {{ url:string, title?:string, downloadUrl?:string }} options
   * @returns {Promise<{ok:true, destroy:() => void} | {ok:false, reason:string}>}
   */
  async function mount (container, options) {
    const opts = options || {}
    const url = opts.url === null || opts.url === undefined ? '' : String(opts.url).trim()

    // 参数问题直接返回，连 DOM 都不碰
    if (!container || container.nodeType !== 1) {
      return { ok: false, reason: '内置阅读器没拿到有效的挂载点' }
    }
    if (!url) return { ok: false, reason: '内置阅读器没拿到 PDF 地址' }

    /* 每个实例一份状态：mount 被调用两次互不影响（没有全局单例） */
    const state = {
      destroyed: false,      // 逻辑上已销毁（destroy() 被调过，或初始化失败）
      tornDown: false,       // 资源已经真正清理过
      loadingTask: null,
      pdfViewer: null,
      findController: null,
      linkService: null,
      eventBus: null,
      resizeObserver: null,
      themeObserver: null,
      busHandlers: [],       // 挂在 pdf.js EventBus 上的监听
      domListeners: [],      // 挂在 window / document 上的监听
      rafId: 0,
      presetScale: 'page-width',  // 默认「适应宽度」；null 表示用户手动缩放过
      invert: false,              // 当前是否反色；初值在 buildUi 之后由本地偏好决定
      matchQuery: '',
      findState: FALLBACK_FIND_STATE,
      pdfDocument: null,      // PDFDocumentProxy：整页导出时按页码取页用（teardown 里置空）
      regionSelect: false,    // 是否处于「框选提问」模式
      regionDrag: null,       // 框选拖拽中的起点（视口坐标 + 取景层当时的矩形）
      regionListeners: []     // 框选期间临时挂上的监听（退出框选立刻摘掉；domListeners 里也各有一份）
    }

    // 界面节点（teardown 时统一置空）
    const ui = {
      root: null, toolbar: null, body: null, scroll: null, viewer: null, status: null, statusText: null,
      pageInput: null, total: null, btnPrevPage: null, btnNextPage: null,
      zoom: null, btnZoomOut: null, btnZoomIn: null, btnFitWidth: null, btnFitPage: null,
      searchInput: null, count: null, btnPrevMatch: null, btnNextMatch: null,
      btnInvert: null, linkDownload: null, linkOpen: null,
      btnRegion: null, regionLayer: null, regionBox: null, regionHint: null
    }

    /* ------------------------- 清理（destroy / 失败共用） ------------------------- */

    function teardown () {
      if (state.tornDown) return
      state.tornDown = true

      // 1) 取消排队中的「重新适宽」
      if (state.rafId) {
        try { cancelRaf(state.rafId) } catch (err) {}
        state.rafId = 0
      }

      // 2) pdf.js 事件总线上的监听
      const bus = state.eventBus
      if (bus) {
        for (let i = 0; i < state.busHandlers.length; i++) {
          try { bus.off(state.busHandlers[i].type, state.busHandlers[i].fn) } catch (err) {}
        }
      }
      state.busHandlers.length = 0

      // 3) 观察器
      if (state.resizeObserver) {
        try { state.resizeObserver.disconnect() } catch (err) {}
        state.resizeObserver = null
      }
      if (state.themeObserver) {
        try { state.themeObserver.disconnect() } catch (err) {}
        state.themeObserver = null
      }

      // 4) window / document 上的监听（第三参数要和注册时一致，捕获阶段挂的必须按捕获阶段摘）
      for (let i = 0; i < state.domListeners.length; i++) {
        const item = state.domListeners[i]
        try { item.target.removeEventListener(item.type, item.fn, item.capture === true) } catch (err) {}
      }
      state.domListeners.length = 0
      // 框选模式的临时监听也在 domListeners 里（上面已摘），这里只需清干净拖拽状态
      state.regionListeners.length = 0
      state.regionSelect = false
      state.regionDrag = null

      // 5) pdf.js：先摘掉文档（会顺带停渲染、abort 内部监听），再销毁 loadingTask
      const viewer = state.pdfViewer
      state.pdfViewer = null
      state.pdfDocument = null
      if (viewer) {
        try { viewer.setDocument(null) } catch (err) {}
        try { if (typeof viewer.cleanup === 'function') viewer.cleanup() } catch (err) {}
      }
      if (state.findController) {
        try { state.findController.setDocument(null) } catch (err) {}
        state.findController = null
      }
      if (state.linkService) {
        try { state.linkService.setDocument(null) } catch (err) {}
        state.linkService = null
      }
      const task = state.loadingTask
      state.loadingTask = null
      if (task) {
        try {
          const pending = typeof task.destroy === 'function' ? task.destroy() : null
          // 销毁过程中的拒绝不再往外冒
          if (pending && typeof pending.catch === 'function') pending.catch(function () {})
        } catch (err) {}
      }
      state.eventBus = null

      // 6) 清掉自己插进 container 的节点：mount 只往里放一个根节点，移除它就等于清空容器
      try {
        const root = ui.root
        if (root && root.parentNode) {
          if (typeof root.remove === 'function') root.remove()
          else if (typeof root.parentNode.removeChild === 'function') root.parentNode.removeChild(root)
          else root.parentNode.childNodes = (root.parentNode.childNodes || []).filter(n => n !== root)
        }
      } catch (err) {}
      ui.root = null; ui.toolbar = null; ui.body = null
      ui.scroll = null; ui.viewer = null; ui.status = null; ui.statusText = null
      ui.btnRegion = null; ui.regionLayer = null; ui.regionBox = null; ui.regionHint = null
    }

    /** 失败回退：清掉所有已插入的 DOM，交给调用方换浏览器自带查看器 */
    function fail (reason) {
      state.destroyed = true
      teardown()
      return { ok: false, reason: reason || GENERIC_FAIL }
    }

    function destroy () {
      if (state.destroyed) return       // 反复调用安全
      state.destroyed = true
      teardown()
    }

    /* ------------------------------ 界面行为 ------------------------------ */

    function setCount (text) {
      if (ui.count) ui.count.textContent = text || ''
    }

    function hideStatus () {
      if (ui.status && ui.status.parentNode) ui.status.parentNode.removeChild(ui.status)
      ui.status = null
      ui.statusText = null
    }

    function onLoadProgress (data) {
      if (state.destroyed || !ui.statusText) return
      const loaded = Number(data && data.loaded) || 0
      const total = Number(data && data.total) || 0
      if (total > 0 && loaded > 0) {
        ui.statusText.textContent = '正在加载 PDF… ' + Math.min(100, Math.round(loaded / total * 100)) + '%'
      }
    }

    /**
     * 反色状态：应用 + 记到本地偏好。
     * @param {boolean} on
     * @param {boolean} [remember] 是否写进偏好（用户点按钮时写；挂载时的初始化不写）
     */
    function setInvert (on, remember) {
      state.invert = !!on
      if (remember) writeInvertPref(state.invert)
      if (ui.root) ui.root.classList.toggle('lv-invert', state.invert)
      if (ui.btnInvert) {
        ui.btnInvert.classList.toggle('active', state.invert)
        ui.btnInvert.setAttribute('aria-pressed', state.invert ? 'true' : 'false')
        ui.btnInvert.title = state.invert ? '当前：反色显示（点击恢复正常）' : '反色显示（夜间看 PDF 不刺眼）'
      }
    }

    /**
     * 挂载时恢复反色：只认「用户最后一次的选择」。
     * 从没点过 → 正常显示（不跟随系统/夜间主题——白天黑夜来回切时自动反色，
     * 会让人分不清「这次有没有反色」，反色本身也可能触发渲染问题）。
     */
    function restoreInvertPref () {
      setInvert(readInvertPref() === true)
    }

    function isInverted () {
      return state.invert
    }

    /** 主题变化：只换阅读区底色。反色是独立开关，不跟着变 */
    function applyTheme () {
      if (state.tornDown) return
      if (ui.root) ui.root.classList.toggle('lv-night', isNightTheme())
    }

    function syncPageButtons () {
      const viewer = state.pdfViewer
      const total = viewer && viewer.pagesCount ? viewer.pagesCount : 0
      const current = viewer ? (viewer.currentPageNumber || 1) : 1
      if (ui.btnPrevPage) ui.btnPrevPage.disabled = !total || current <= 1
      if (ui.btnNextPage) ui.btnNextPage.disabled = !total || current >= total
    }

    function syncZoom () {
      const viewer = state.pdfViewer
      const scale = viewer && viewer.currentScale ? viewer.currentScale : 1
      if (ui.zoom) ui.zoom.textContent = Math.round(scale * 100) + '%'
      if (ui.btnZoomOut) ui.btnZoomOut.disabled = scale <= MIN_SCALE + 0.001
      if (ui.btnZoomIn) ui.btnZoomIn.disabled = scale >= MAX_SCALE - 0.001
      if (ui.btnFitWidth) ui.btnFitWidth.classList.toggle('active', state.presetScale === 'page-width')
      if (ui.btnFitPage) ui.btnFitPage.classList.toggle('active', state.presetScale === 'page-fit')
    }

    function syncPageInput () {
      const viewer = state.pdfViewer
      if (!ui.pageInput || !viewer) return
      // 正在输入时不要抢用户的光标
      if (document.activeElement !== ui.pageInput) ui.pageInput.value = String(viewer.currentPageNumber || 1)
      syncPageButtons()
    }

    function jumpToPage (value) {
      const viewer = state.pdfViewer
      if (!viewer || !viewer.pagesCount) return
      const raw = Number(value)
      if (!isFinite(raw)) { syncPageInput(); return }
      const target = Math.min(Math.max(Math.round(raw), 1), viewer.pagesCount)
      // currentPageNumber 的 setter 会校验并滚动到该页
      try { viewer.currentPageNumber = target } catch (err) {}
      syncPageInput()
    }

    function stepPage (delta) {
      const viewer = state.pdfViewer
      if (!viewer || !viewer.pagesCount) return
      jumpToPage((viewer.currentPageNumber || 1) + delta)
    }

    function applyZoom (scale) {
      const viewer = state.pdfViewer
      if (!viewer) return
      state.presetScale = null          // 手动缩放 → 退出「适宽 / 适页」
      const next = clampScale(Math.round(scale * 1000) / 1000)
      try { viewer.currentScale = next } catch (err) {}
      syncZoom()
    }

    function zoomBy (delta) {
      const viewer = state.pdfViewer
      if (!viewer) return
      applyZoom((viewer.currentScale || 1) + delta)
    }

    function applyFit (value, force) {
      state.presetScale = value
      const viewer = state.pdfViewer
      if (!viewer) { syncZoom(); return }
      // 容器还没量到尺寸（例如挂载时被隐藏）时先记住，等尺寸出来再应用
      if (!force && ui.scroll &&
        (ui.scroll.clientWidth < MIN_CONTAINER_SIZE || ui.scroll.clientHeight < MIN_CONTAINER_SIZE)) {
        syncZoom()
        return
      }
      try { viewer.currentScaleValue = value } catch (err) {}
      syncZoom()
    }

    /** 容器尺寸变化（例如拖动详情页的分隔条）→ 重新套用「适宽 / 适页」 */
    function scheduleScaleRefresh () {
      if (state.destroyed || state.tornDown || state.rafId) return
      state.rafId = requestRaf(function () {
        state.rafId = 0
        if (state.destroyed || state.tornDown || !state.pdfViewer) return
        if (state.presetScale) applyFit(state.presetScale, true)
        else syncZoom()
      })
    }

    function onGlobal (target, type, fn) {
      if (!target || typeof target.addEventListener !== 'function') return
      target.addEventListener(type, fn, { passive: true })
      state.domListeners.push({ target: target, type: type, fn: fn })
    }

    /* -------------------------------- 搜索 -------------------------------- */

    function dispatchFind (type, findPrevious) {
      const bus = state.eventBus
      if (!bus) return
      try {
        bus.dispatch('find', {
          source: state,
          type: type || '',                 // '' = 新搜索；'again' = 上一条/下一条
          query: state.matchQuery || '',
          caseSensitive: false,
          entireWord: false,
          highlightAll: true,
          findPrevious: !!findPrevious,
          matchDiacritics: false
        })
      } catch (err) {}
    }

    function clearSearch () {
      const had = !!state.matchQuery
      state.matchQuery = ''
      if (ui.searchInput) ui.searchInput.value = ''
      setCount('')
      syncMatchButtons()
      if (had && state.eventBus) {
        // 让 PDFFindController 关掉高亮、忘掉当前匹配
        try { state.eventBus.dispatch('findbarclose', { source: state }) } catch (err) {}
      }
    }

    function runSearch (findPrevious) {
      const raw = ui.searchInput ? String(ui.searchInput.value) : ''
      if (!raw.trim()) { clearSearch(); return }
      state.matchQuery = raw
      setCount('搜索中…')
      syncMatchButtons()
      dispatchFind('', !!findPrevious)
    }

    function findAgain (findPrevious) {
      if (!state.matchQuery) return
      dispatchFind('again', !!findPrevious)
    }

    function syncMatchButtons () {
      const ready = !!state.matchQuery && !!state.pdfViewer
      if (ui.btnPrevMatch) ui.btnPrevMatch.disabled = !ready
      if (ui.btnNextMatch) ui.btnNextMatch.disabled = !ready
    }

    function onMatchesCount (evt) {
      if (state.destroyed || !state.matchQuery) return
      const counts = (evt && evt.matchesCount) || {}
      const total = Number(counts.total) || 0
      const current = Number(counts.current) || 0
      // 统计还没算完时 pdf.js 会先报 0/0，这时候先别急着说「没有找到」，
      // 真的没有匹配会由 updatefindcontrolstate 的 NOT_FOUND 下结论
      if (!total) { setCount('搜索中…'); return }
      setCount(current > 0 ? '第 ' + current + ' / 共 ' + total + ' 处' : '共 ' + total + ' 处')
    }

    function onFindControlState (evt) {
      if (state.destroyed || !state.matchQuery) return
      const findState = state.findState || FALLBACK_FIND_STATE
      const value = evt && evt.state
      if (value === findState.NOT_FOUND) setCount('没有找到匹配')
      else if (value === findState.PENDING && ui.count && !ui.count.textContent) setCount('搜索中…')
    }

    /* ------------------------------ pdf.js 事件 ------------------------------ */

    function onPagesInit () {
      if (state.destroyed) return
      const viewer = state.pdfViewer
      const total = viewer && viewer.pagesCount ? viewer.pagesCount : 0
      if (ui.total) ui.total.textContent = '/ ' + total
      if (ui.pageInput && viewer) ui.pageInput.value = String(viewer.currentPageNumber || 1)
      syncPageButtons()
      applyFit(state.presetScale || 'page-width')   // 默认「适应宽度」
      syncZoom()
      hideStatus()
      // 焦点没在别处时，把焦点交给阅读区，方向键/PageUp 才好用
      try {
        const active = document.activeElement
        if (ui.scroll && (!active || active === document.body)) ui.scroll.focus({ preventScroll: true })
      } catch (err) {}
    }

    function onPageChanging (evt) {
      if (state.destroyed || !evt) return
      if (ui.pageInput && document.activeElement !== ui.pageInput) {
        ui.pageInput.value = String(evt.pageNumber || 1)
      }
      syncPageButtons()
    }

    function onScaleChanging (evt) {
      if (state.destroyed) return
      const scale = (evt && evt.scale) || (state.pdfViewer && state.pdfViewer.currentScale) || 1
      if (ui.zoom) ui.zoom.textContent = Math.round(scale * 100) + '%'
      syncZoom()
    }

    /* -------------------------------- 键盘 -------------------------------- */

    function onViewerKeydown (ev) {
      if (state.destroyed || ev.defaultPrevented) return
      // 框选模式优先：Esc 退出框选；其余按键一律不再触发翻页等阅读器快捷键
      // （输入框照常打字：这里没有 preventDefault，target 上的处理器也已经先跑过了）
      if (state.regionSelect) {
        if (ev.key === 'Escape' || ev.key === 'Esc') {
          ev.preventDefault()
          ev.stopPropagation()
          cancelRegionSelect()
        }
        return
      }
      if (ev.ctrlKey || ev.metaKey || ev.altKey) return
      if (isEditableTarget(ev.target)) return     // 输入框里的按键不拦
      switch (ev.key) {
        case 'PageUp': ev.preventDefault(); stepPage(-1); break
        case 'PageDown': ev.preventDefault(); stepPage(1); break
        case 'ArrowUp': ev.preventDefault(); stepPage(-1); break
        case 'ArrowDown': ev.preventDefault(); stepPage(1); break
        case 'Home':
          ev.preventDefault()
          jumpToPage(1)
          break
        case 'End':
          ev.preventDefault()
          jumpToPage(state.pdfViewer ? state.pdfViewer.pagesCount : 1)
          break
        default:
          break
      }
    }

    function onPageInputKeydown (ev) {
      if (ev.key !== 'Enter') return
      ev.preventDefault()
      ev.stopPropagation()
      jumpToPage(ui.pageInput ? ui.pageInput.value : 1)
    }

    function onSearchKeydown (ev) {
      if (ev.key === 'Enter') {
        ev.preventDefault()
        ev.stopPropagation()
        runSearch(ev.shiftKey)          // Shift + 回车 = 上一个匹配
      } else if (ev.key === 'Escape') {
        ev.preventDefault()
        ev.stopPropagation()
        clearSearch()
      }
    }

    /* ------------------- 截图导出 / 框选提问（新增的对外能力） ------------------- */
    /* 这两条能力都走「离屏 canvas 重新渲染」这条路，原因有两个：
       1) 屏幕上那张 canvas 是按「当前缩放 × devicePixelRatio」画的，直接裁下来分辨率不够，
          扫描件再拿去 OCR 就糊了；重新渲染时想用多高的 scale 就用多高；
       2) 离屏渲染完全不碰屏幕上已有的页面，也不改滚动位置 / 缩放，用户看不到闪烁和位移。
       代价是每次截图要重渲染一页（几百毫秒量级），所以只在用户主动截图时才做。 */

    // 有 Pointer Events 就用它（触屏 / 手写笔也能框选），没有就退回 mouse
    const REGION_HAS_POINTER = typeof window.PointerEvent === 'function'
    const REGION_DOWN = REGION_HAS_POINTER ? 'pointerdown' : 'mousedown'
    const REGION_MOVE = REGION_HAS_POINTER ? 'pointermove' : 'mousemove'
    const REGION_UP = REGION_HAS_POINTER ? 'pointerup' : 'mouseup'

    /** 框选期间临时挂的监听：既进 state.domListeners（destroy 兜底），也单独记一份（退出框选时立刻摘掉） */
    function bindTemp (target, type, fn, options) {
      if (!target || typeof target.addEventListener !== 'function') return
      target.addEventListener(type, fn, options || false)
      const item = { target: target, type: type, fn: fn, capture: !!(options && options.capture === true) }
      state.domListeners.push(item)
      state.regionListeners.push(item)
    }

    /** 摘掉框选期间挂的监听，并从 state.domListeners 里同步删掉（反复进出不会越堆越多） */
    function unbindTemp () {
      for (let i = 0; i < state.regionListeners.length; i++) {
        const item = state.regionListeners[i]
        try { item.target.removeEventListener(item.type, item.fn, item.capture === true) } catch (err) {}
        const at = state.domListeners.indexOf(item)
        if (at >= 0) state.domListeners.splice(at, 1)
      }
      state.regionListeners.length = 0
    }

    /** 取鼠标 / 指针事件的视口坐标；拿不到返回 null */
    function eventPoint (ev) {
      if (!ev) return null
      const x = Number(ev.clientX)
      const y = Number(ev.clientY)
      if (!isFinite(x) || !isFinite(y)) return null
      return { x: x, y: y }
    }

    /**
     * 把一页渲染到「独立的离屏 canvas」：不碰屏幕上的页面，也不改滚动位置 / 缩放。
     * @param {object} pdfPage PDFPageProxy
     * @param {number} scale 纯 PDF 缩放（不含 pdf.js 给 viewport 乘的 CSS 单位换算系数）
     * @param {number=} rotation 页面旋转角，跟屏幕上那页保持一致，否则裁切坐标会歪
     * @returns {Promise<{canvas:HTMLCanvasElement, viewport:object}|null>}
     */
    async function renderPageToCanvas (pdfPage, scale, rotation) {
      if (!pdfPage || typeof pdfPage.getViewport !== 'function' || typeof pdfPage.render !== 'function') return null
      const params = { scale: scale }
      if (isFinite(rotation)) params.rotation = rotation    // 不传时 pdf.js 用页面自身的 rotate
      const viewport = pdfPage.getViewport(params)
      const vw = Math.max(1, viewport.width)
      const vh = Math.max(1, viewport.height)
      // 画布保护：面积 / 单边超限就等比缩小（浏览器对单张 canvas 有硬上限，超了只会得到空白图）。
      // 注意缩的是「画布尺寸」而不是 scale —— viewport 是裁切坐标的基准，缩画布之后用 render 的
      // transform 把整页按同比例画进去，裁切换算公式就不用跟着改。
      const shrink = Math.min(1,
        Math.sqrt(CAPTURE_MAX_PIXELS / Math.max(1, vw * vh)),
        CAPTURE_MAX_SIDE / Math.max(vw, vh))
      const width = Math.max(1, Math.floor(vw * shrink))
      const height = Math.max(1, Math.floor(vh * shrink))
      const canvas = document.createElement('canvas')
      canvas.width = width
      canvas.height = height
      const ctx = typeof canvas.getContext === 'function' ? canvas.getContext('2d') : null
      if (!ctx) return null
      // JPEG 没有透明通道：先铺白底，否则 PDF 的空白处会是黑的
      ctx.fillStyle = '#ffffff'
      ctx.fillRect(0, 0, width, height)
      const k = width / vw
      const task = pdfPage.render({
        canvas: canvas,                       // pdf.js 4.x 忽略这个字段，5.x 起要求传，带上更保险
        canvasContext: ctx,
        viewport: viewport,
        transform: [k, 0, 0, k, 0, 0]         // 画布被缩过时让内容跟着缩（k = 1 就是恒等变换）
      })
      await task.promise
      return { canvas: canvas, viewport: viewport }
    }

    /** toDataURL 的包装：格式兜底；画布过大 / 被判定为异常时浏览器会返回 "data:,"，一律当失败 */
    function canvasDataUrl (canvas, format, quality) {
      try {
        const type = format === 'image/png' || format === 'image/webp' ? format : DEFAULT_CAPTURE_FORMAT
        const url = type === 'image/png' ? canvas.toDataURL(type) : canvas.toDataURL(type, quality)
        if (typeof url !== 'string' || url.slice(0, 10) !== 'data:image') return null
        return url
      } catch (err) {
        return null
      }
    }

    /** 截图参数兜底：scale / format / quality 都夹到安全范围 */
    function captureOptions (options, defaultFormat, defaultScale) {
      const o = options || {}
      return {
        scale: clampNumber(o.scale, 0.1, CAPTURE_MAX_SCALE, defaultScale),
        format: o.format === 'image/png' || o.format === 'image/webp'
          ? o.format
          : (defaultFormat || DEFAULT_CAPTURE_FORMAT),
        quality: clampNumber(o.quality, 0.1, 1, DEFAULT_CAPTURE_QUALITY)
      }
    }

    /** 按页码拿 PDFPageProxy：优先复用屏幕上那一页已经取到的，省一次 worker 往返 */
    async function getPdfPage (pageNumber) {
      const viewer = state.pdfViewer
      if (viewer && typeof viewer.getPageView === 'function') {
        const pageView = viewer.getPageView(pageNumber - 1)
        if (pageView && pageView.pdfPage) return pageView.pdfPage
      }
      const doc = state.pdfDocument
      if (doc && typeof doc.getPage === 'function') return await doc.getPage(pageNumber)
      return null
    }

    /**
     * 找出被矩形（视口坐标）盖住的那一页。
     * 优先用 PDFViewer.getPageView(i)（能同时拿到 div / viewport / pdfPage），
     * 拿不到就退回扫 .page[data-page-number]（只认页码，渲染时再按页码取 pdfPage）。
     * 一页都没压到（比如框在页与页之间的空隙上）就退回离框最近的一页。
     * @returns {{pageNumber:number, pageView:object|null, rect:object}|null}
     */
    function findPageTarget (sel) {
      const candidates = []
      const viewer = state.pdfViewer
      if (viewer && typeof viewer.getPageView === 'function') {
        const total = viewer.pagesCount || 0
        for (let i = 0; i < total; i++) {
          const pageView = viewer.getPageView(i)
          const div = pageView && pageView.div
          if (!div || typeof div.getBoundingClientRect !== 'function') continue
          candidates.push({ pageNumber: i + 1, pageView: pageView, div: div })
        }
      }
      if (!candidates.length && ui.viewer && typeof ui.viewer.querySelectorAll === 'function') {
        const nodes = ui.viewer.querySelectorAll('.page[data-page-number]') || []
        for (let i = 0; i < nodes.length; i++) {
          const node = nodes[i]
          if (!node || typeof node.getAttribute !== 'function') continue
          const num = Math.round(Number(node.getAttribute('data-page-number')))
          if (!isFinite(num) || num < 1) continue
          candidates.push({ pageNumber: num, pageView: null, div: node })
        }
      }
      let best = null
      let bestArea = 0
      let nearest = null
      let nearestDist = Infinity
      for (let i = 0; i < candidates.length; i++) {
        const rect = clientRectOf(candidates[i].div)
        if (!rect || rect.width <= 0 || rect.height <= 0) continue
        const area = overlapArea(sel, rect)
        if (area > bestArea) {
          bestArea = area
          best = { pageNumber: candidates[i].pageNumber, pageView: candidates[i].pageView, rect: rect }
        }
        const dist = rectDistance(sel, rect)
        if (dist < nearestDist) {
          nearestDist = dist
          nearest = { pageNumber: candidates[i].pageNumber, pageView: candidates[i].pageView, rect: rect }
        }
      }
      return best || nearest
    }

    /**
     * 从高清画布上裁一块，画到第二张小 canvas 上（只把这一块编码成 dataURL，不把整页带出去）。
     * 入参是「高清画布像素」坐标，内部再夹一次边界；裁不出有效区域返回 null。
     */
    function cropCanvasRegion (canvas, sx, sy, sw, sh, format, quality) {
      const cw = canvas.width
      const ch = canvas.height
      if (!(cw > 0) || !(ch > 0)) return null
      const x = Math.min(Math.max(0, Math.floor(sx)), cw - 1)
      const y = Math.min(Math.max(0, Math.floor(sy)), ch - 1)
      const w = Math.max(1, Math.min(cw - x, Math.round(sw)))
      const h = Math.max(1, Math.min(ch - y, Math.round(sh)))
      const out = document.createElement('canvas')
      out.width = w
      out.height = h
      const ctx = typeof out.getContext === 'function' ? out.getContext('2d') : null
      if (!ctx) return null
      ctx.fillStyle = '#ffffff'                 // 裁切结果同样铺白底（JPEG 没有透明通道）
      ctx.fillRect(0, 0, w, h)
      ctx.drawImage(canvas, x, y, w, h, 0, 0, w, h)
      const dataUrl = canvasDataUrl(out, format, quality)
      if (!dataUrl) return null
      return { dataUrl: dataUrl, width: w, height: h }
    }

    /**
     * 「视口坐标矩形」→ 截图。框选和 captureRegion 共用这一条路径。
     * 坐标换算分三步写出来，方便对照：
     *   ① 框相对该页 div 的偏移（CSS 像素）；
     *   ② 页面 div 的显示宽度 → 该页 viewport 宽度：正常情况下 pdf.js 把 viewport 直接当 CSS 像素用，
     *      两者相等；但外层若套了 CSS transform / 浏览器缩放，显示宽度就和 viewport 不一致了。
     *      按这个比例归一之后，「用户看到的框」和「裁出来的像素」永远对得上（旋转页也一样，
     *      因为 viewport 本身已经含了 rotation）；
     *   ③ viewport → 高清离屏画布：高清画布是按 scale = hiScale 重新渲染的
     *      （pdfPage.getViewport 的 scale 是纯 PDF 缩放，不含 pdf.js 给 viewport 乘的 CSS 换算系数），
     *      所以还要再乘一个「画布宽 / viewport 宽」。
     *   ② ③ 连乘后其实就等于「画布宽 / 页面显示宽」，分步写只是为了说清每一步在干什么。
     * @returns {Promise<{dataUrl:string, info:{page:number,width:number,height:number}}|null>}
     */
    async function captureClientRect (clientRect, options) {
      try {
        if (state.destroyed || state.tornDown) return null
        const sel = normalizeRect(clientRect)
        if (!sel || sel.width < 1 || sel.height < 1) return null
        const target = findPageTarget(sel)
        if (!target) return null
        const pageRect = target.rect
        const view = target.pageView && target.pageView.viewport ? target.pageView.viewport : null
        // 框选默认按「当前缩放的两倍」重渲染，且不低于 2.5：太小 OCR 认不出来，太大只是浪费内存
        const currentScale = clampNumber(state.pdfViewer && state.pdfViewer.currentScale, 0.1, 10, 1)
        const autoScale = Math.min(CAPTURE_MAX_SCALE, Math.max(2.5, currentScale * 2))
        const o = captureOptions(options, DEFAULT_REGION_FORMAT, autoScale)
        const pdfPage = (target.pageView && target.pageView.pdfPage) || await getPdfPage(target.pageNumber)
        if (!pdfPage || state.destroyed) return null
        const rendered = await renderPageToCanvas(pdfPage, o.scale, view ? view.rotation : undefined)
        if (!rendered || state.destroyed) return null
        const canvas = rendered.canvas
        const kDivToViewport = view && view.width > 0 && pageRect.width > 0 ? view.width / pageRect.width : 1
        const kViewportToCanvas = view && view.width > 0
          ? canvas.width / view.width
          : canvas.width / Math.max(1, pageRect.width)
        const k = kDivToViewport * kViewportToCanvas
        const cropped = cropCanvasRegion(
          canvas,
          (sel.left - pageRect.left) * k,
          (sel.top - pageRect.top) * k,
          sel.width * k,
          sel.height * k,
          o.format, o.quality
        )
        if (!cropped) return null
        return {
          dataUrl: cropped.dataUrl,
          info: { page: target.pageNumber, width: cropped.width, height: cropped.height }
        }
      } catch (err) {
        return null
      }
    }

    /* ---- 框选模式的取景层与交互 ---- */

    function syncRegionButton () {
      if (!ui.btnRegion) return
      const on = !!state.regionSelect
      ui.btnRegion.classList.toggle('active', on)
      ui.btnRegion.setAttribute('aria-pressed', on ? 'true' : 'false')
      ui.btnRegion.title = on
        ? '正在框选：在 PDF 上按住鼠标拖一个框（Esc 取消）'
        : '在 PDF 上拖一个框，把这一块发给模型识别'
    }

    /** 画取景框：参数是「取景层内」的坐标（= 视口坐标 − 取景层左上角） */
    function drawRegionBox (x1, y1, x2, y2) {
      if (!ui.regionBox || !state.regionDrag) return
      const base = state.regionDrag.base
      ui.regionBox.style.display = 'block'
      ui.regionBox.style.left = (Math.min(x1, x2) - base.left) + 'px'
      ui.regionBox.style.top = (Math.min(y1, y2) - base.top) + 'px'
      ui.regionBox.style.width = Math.abs(x2 - x1) + 'px'
      ui.regionBox.style.height = Math.abs(y2 - y1) + 'px'
    }

    function onRegionDown (ev) {
      if (state.destroyed || !state.regionSelect) return
      if (ev.button !== undefined && ev.button !== 0) return      // 只认左键
      const pt = eventPoint(ev)
      if (!pt) return
      const base = clientRectOf(ui.regionLayer)
      if (!base) return
      if (typeof ev.preventDefault === 'function') ev.preventDefault()   // 别把拖拽当成选中文字
      state.regionDrag = { x0: pt.x, y0: pt.y, base: base }
      drawRegionBox(pt.x, pt.y, pt.x, pt.y)
    }

    function onRegionMove (ev) {
      const drag = state.regionDrag
      if (!drag || state.destroyed || !state.regionSelect) return
      const pt = eventPoint(ev)
      if (!pt) return
      if (typeof ev.preventDefault === 'function') ev.preventDefault()
      drawRegionBox(drag.x0, drag.y0, pt.x, pt.y)
    }

    function onRegionUp (ev) {
      const drag = state.regionDrag
      if (!drag || state.destroyed || !state.regionSelect) return
      const pt = eventPoint(ev) || { x: drag.x0, y: drag.y0 }
      const sel = normalizeRect({
        left: Math.min(drag.x0, pt.x),
        top: Math.min(drag.y0, pt.y),
        width: Math.abs(pt.x - drag.x0),
        height: Math.abs(pt.y - drag.y0)
      })
      // 松开就完成：先退出框选模式（按钮复位、监听摘掉），再去异步渲染 ——
      // 渲染要几百毫秒，不能让界面一直卡在框选态
      cancelRegionSelect()
      if (!sel || sel.width < REGION_MIN_SIDE || sel.height < REGION_MIN_SIDE) return   // 框太小 = 用户点空了
      handleRegionSelection(sel)
    }

    /** 框选完成 → 截图 → 回调调用方（回调抛异常不能影响阅读器） */
    async function handleRegionSelection (sel) {
      try {
        const cb = opts.onRegionSelected
        if (typeof cb !== 'function') return
        const shot = await captureClientRect(sel, null)     // null：走默认（PNG + 自动高分辨率）
        if (!shot || state.destroyed) return
        try {
          cb(shot.dataUrl, shot.info)
        } catch (err) {
          // 调用方自己的回调炸了，跟我们无关
        }
      } catch (err) {}
    }

    /** Esc 最高优先级：挂在 document 捕获阶段，焦点在哪都能退出，也压过阅读器的翻页快捷键 */
    function onRegionKeydown (ev) {
      if (state.destroyed || !state.regionSelect || !ev) return
      if (ev.key !== 'Escape' && ev.key !== 'Esc') return
      if (typeof ev.preventDefault === 'function') ev.preventDefault()
      if (typeof ev.stopPropagation === 'function') ev.stopPropagation()
      cancelRegionSelect()
    }

    /** 取景层盖住的是兄弟节点 ui.scroll（不是它的祖先），滚轮事件不会自动传给阅读区，手动转交 */
    function onRegionWheel (ev) {
      if (state.destroyed || !state.regionSelect || !ui.scroll || !ev) return
      if (typeof ev.preventDefault === 'function') ev.preventDefault()
      const unit = ev.deltaMode === 1 ? 16 : (ev.deltaMode === 2 ? (ui.scroll.clientHeight || 1) : 1)
      ui.scroll.scrollTop += (Number(ev.deltaY) || 0) * unit
      ui.scroll.scrollLeft += (Number(ev.deltaX) || 0) * unit
    }

    /** 进入框选模式（幂等）。工具栏按钮和 res.startRegionSelect() 都走这里 */
    function startRegionSelect () {
      try {
        if (state.destroyed || state.tornDown) return
        if (state.regionSelect) return
        if (!ui.regionLayer || !ui.body) return
        const viewer = state.pdfViewer
        if (!viewer || !viewer.pagesCount) return          // 文档还没排好版，先不让框
        state.regionSelect = true
        state.regionDrag = null
        if (ui.regionBox) ui.regionBox.style.display = 'none'
        ui.regionLayer.style.display = 'block'
        syncRegionButton()
        bindTemp(ui.regionLayer, REGION_DOWN, onRegionDown, { passive: false })
        // 拖拽时指针会跑出取景层，move / up 必须挂 window 才收得到
        bindTemp(window, REGION_MOVE, onRegionMove, { passive: false })
        bindTemp(window, REGION_UP, onRegionUp, { passive: false })
        bindTemp(ui.regionLayer, 'wheel', onRegionWheel, { passive: false })
        bindTemp(document, 'keydown', onRegionKeydown, { capture: true })
      } catch (err) {
        cancelRegionSelect()      // 任何一步出问题都别把界面留在半开的框选态
      }
    }

    /** 退出框选模式（幂等）：按钮复位、取景层收起、临时监听全部摘掉 */
    function cancelRegionSelect () {
      try {
        state.regionSelect = false
        state.regionDrag = null
        unbindTemp()
        if (ui.regionLayer) ui.regionLayer.style.display = 'none'
        if (ui.regionBox) ui.regionBox.style.display = 'none'
        syncRegionButton()
      } catch (err) {}
    }

    /** 工具栏「框选」按钮：点一下进入，再点一下退出 */
    function toggleRegionSelect () {
      try {
        if (state.destroyed) return
        if (state.regionSelect) cancelRegionSelect()
        else startRegionSelect()
      } catch (err) {}
    }

    /* ---- 对外 API（全部保证不抛异常） ---- */

    /** 总页数；读不到返回 0 */
    function getPageCount () {
      try {
        if (state.destroyed) return 0
        const viewer = state.pdfViewer
        const total = (viewer && viewer.pagesCount) || (state.pdfDocument && state.pdfDocument.numPages) || 0
        const n = Math.round(Number(total))
        return isFinite(n) && n > 0 ? n : 0
      } catch (err) {
        return 0
      }
    }

    /** 跳到第 n 页（1 起）；越界自动夹到有效范围，失败静默 */
    function goToPage (pageNumber) {
      try {
        if (state.destroyed || state.tornDown) return
        jumpToPage(pageNumber)
      } catch (err) {}
    }

    /**
     * 整页导出：用独立的离屏 canvas 按 scale 重渲染第 n 页，输出 dataURL。
     * 默认 2 倍 JPEG（体积小、够 OCR 用）；越界 / 出错 / 已销毁一律返回 null。
     * @returns {Promise<string|null>}
     */
    async function capturePage (pageNumber, options) {
      try {
        if (state.destroyed || state.tornDown) return null
        const total = getPageCount()
        const page = Math.round(Number(pageNumber))
        if (!isFinite(page) || page < 1 || page > total) return null        // 越界返回 null
        const o = captureOptions(options, DEFAULT_CAPTURE_FORMAT, 2)
        const pdfPage = await getPdfPage(page)
        if (!pdfPage || state.destroyed) return null
        const rendered = await renderPageToCanvas(pdfPage, o.scale, undefined)
        if (!rendered || state.destroyed) return null
        return canvasDataUrl(rendered.canvas, o.format, o.quality)
      } catch (err) {
        return null
      }
    }

    /**
     * 截取阅读区里的一块。rect 是「相对 container 的 CSS 像素」。
     * 默认 PNG（框选出来的是一小块，无损更合适），出错返回 null。
     * @returns {Promise<{dataUrl:string, info:{page:number,width:number,height:number}}|null>}
     */
    async function captureRegion (rect, options) {
      try {
        if (state.destroyed || state.tornDown) return null
        const sel = normalizeRect(rect)
        if (!sel || sel.width < 1 || sel.height < 1) return null
        // 入参相对 container：container 自己可能已经滚动过、或者跟着页面滚走了，
        // 先换成视口坐标，才能和页面 div 的 getBoundingClientRect() 对齐
        const base = clientRectOf(ui.scroll) || { left: 0, top: 0 }
        return await captureClientRect({
          left: sel.left + base.left,
          top: sel.top + base.top,
          width: sel.width,
          height: sel.height
        }, options)
      } catch (err) {
        return null
      }
    }

    /* ------------------------------ 搭工具栏骨架 ------------------------------ */

    function buildUi () {
      const downloadHref = opts.downloadUrl ? String(opts.downloadUrl) : url

      ui.btnPrevPage = el('button.lv-pdf-btn', { type: 'button', title: '上一页（PageUp / ↑）', text: '‹ 上一页' })
      ui.pageInput = el('input.lv-pdf-input.lv-pdf-page', {
        type: 'text', value: '1', inputmode: 'numeric', autocomplete: 'off', spellcheck: 'false',
        'aria-label': '当前页码', title: '输入页码后按回车跳转'
      })
      ui.total = el('span.lv-pdf-num', { text: '/ —' })
      ui.btnNextPage = el('button.lv-pdf-btn', { type: 'button', title: '下一页（PageDown / ↓）', text: '下一页 ›' })

      ui.btnZoomOut = el('button.lv-pdf-btn', { type: 'button', title: '缩小（最小 50%）', text: '−' })
      ui.zoom = el('span.lv-pdf-zoom', { text: '100%' })
      ui.btnZoomIn = el('button.lv-pdf-btn', { type: 'button', title: '放大（最大 300%）', text: '＋' })
      ui.btnFitWidth = el('button.lv-pdf-btn', { type: 'button', title: '页面宽度铺满阅读区', text: '适宽' })
      ui.btnFitPage = el('button.lv-pdf-btn', { type: 'button', title: '整页完整显示在阅读区', text: '适页' })

      ui.searchInput = el('input.lv-pdf-input.lv-pdf-search', {
        type: 'text', placeholder: '搜索全文，回车查找', autocomplete: 'off', spellcheck: 'false',
        'aria-label': '在 PDF 里搜索'
      })
      ui.count = el('span.lv-pdf-count', { text: '' })
      ui.btnPrevMatch = el('button.lv-pdf-btn', { type: 'button', title: '上一个匹配（Shift + 回车）', text: '上一条' })
      ui.btnNextMatch = el('button.lv-pdf-btn', { type: 'button', title: '下一个匹配（回车）', text: '下一条' })

      ui.btnInvert = el('button.lv-pdf-btn', {
        type: 'button', text: '反色', 'aria-pressed': 'false', title: '反色显示（点一次就记住，下次打开还是这个状态）'
      })
      // 「框选提问」：图标（纯 CSS 画的虚线框）+ 文字，激活时加 .active
      ui.btnRegion = el('button.lv-pdf-btn', {
        type: 'button', 'aria-pressed': 'false',
        title: '在 PDF 上拖一个框，把这一块发给模型识别'
      }, [el('span.lv-pdf-icon-frame', { 'aria-hidden': 'true' }), '框选'])
      ui.linkDownload = el('a.lv-pdf-btn', {
        href: downloadHref, title: '下载这个 PDF 文件', text: '下载',
        download: downloadName(opts.title)
      })
      ui.linkOpen = el('a.lv-pdf-btn', {
        href: url, target: '_blank', rel: 'noopener noreferrer',
        title: '在新标签页里用浏览器自带查看器打开', text: '新标签页打开'
      })

      ui.toolbar = el('div.lv-pdf-toolbar', {
        role: 'toolbar', 'aria-label': 'PDF 工具栏'
      }, [
        el('div.lv-pdf-group', null, [ui.btnPrevPage, ui.pageInput, ui.total, ui.btnNextPage]),
        el('span.lv-pdf-sep', { 'aria-hidden': 'true' }),
        el('div.lv-pdf-group', null, [ui.btnZoomOut, ui.zoom, ui.btnZoomIn, ui.btnFitWidth, ui.btnFitPage]),
        el('span.lv-pdf-sep', { 'aria-hidden': 'true' }),
        el('div.lv-pdf-group.grow', null, [ui.searchInput, ui.count, ui.btnPrevMatch, ui.btnNextMatch]),
        el('div.lv-pdf-group.right', null, [ui.btnRegion, ui.btnInvert, ui.linkDownload, ui.linkOpen])
      ])

      ui.viewer = el('div.pdfViewer')     // 这个 div 就是 PDFViewer 的 viewer 参数
      ui.scroll = el('div.lv-pdf-scroll', {
        tabindex: '0',
        style: 'position:absolute;top:0;right:0;bottom:0;left:0;overflow:auto'
      }, ui.viewer)
      // 框选取景层：平时 display:none，进入框选模式才显示（由脚本切）。
      // 放在 ui.body 里当 ui.scroll 的兄弟节点、inset:0 —— 和阅读区完全同一个盒子，
      // 但不能放进 ui.scroll 内部：那是滚动容器，覆盖层会跟着内容滚、还会被 overflow 裁掉。
      ui.regionBox = el('div.lv-pdf-region-box', { 'aria-hidden': 'true' })
      ui.regionHint = el('div.lv-pdf-region-hint', {
        text: '按住鼠标拖一个框，把这一块发给模型识别（Esc 取消）'
      })
      ui.regionLayer = el('div.lv-pdf-region-layer', {
        style: 'display:none', 'aria-hidden': 'true'
      }, [ui.regionHint, ui.regionBox])
      ui.statusText = el('span', { text: '正在加载 PDF…' })
      ui.status = el('div.lv-pdf-status', { role: 'status' }, [el('span.lv-pdf-spinner'), ui.statusText])
      ui.body = el('div.lv-pdf-body', { style: 'position:relative;flex:1 1 auto;min-height:0' },
        [ui.scroll, ui.regionLayer, ui.status])
      ui.root = el('div.lv-pdf-viewer', {
        style: 'position:relative;display:flex;flex-direction:column;width:100%;height:100%;overflow:hidden'
      }, [ui.toolbar, ui.body])

      // 交互
      ui.btnPrevPage.onclick = function () { stepPage(-1) }
      ui.btnNextPage.onclick = function () { stepPage(1) }
      ui.pageInput.onkeydown = onPageInputKeydown
      ui.pageInput.onfocus = function () { try { ui.pageInput.select() } catch (err) {} }
      ui.pageInput.onblur = function () { syncPageInput() }
      ui.btnZoomOut.onclick = function () { zoomBy(-SCALE_STEP) }
      ui.btnZoomIn.onclick = function () { zoomBy(SCALE_STEP) }
      ui.btnFitWidth.onclick = function () { applyFit('page-width') }
      ui.btnFitPage.onclick = function () { applyFit('page-fit') }
      ui.searchInput.onkeydown = onSearchKeydown
      ui.searchInput.oninput = function () { if (!ui.searchInput.value) clearSearch() }
      ui.btnPrevMatch.onclick = function () { findAgain(true) }
      ui.btnNextMatch.onclick = function () { findAgain(false) }
      ui.btnInvert.onclick = function () {
        // 只有这里能改反色：点一次算一次，记进偏好，下次打开还是这个状态
        setInvert(!state.invert, true)
      }
      ui.root.addEventListener('keydown', onViewerKeydown)
      state.domListeners.push({ target: ui.root, type: 'keydown', fn: onViewerKeydown })

      ui.btnRegion.onclick = function () { toggleRegionSelect() }
      syncRegionButton()

      syncPageButtons()
      syncMatchButtons()
      syncZoom()
    }

    /* -------------------------------- 正式流程 -------------------------------- */

    try {
      // 1) 先插样式和工具栏骨架（任何一步失败都能整块清掉）
      try {
        injectStyle()
        injectViewerCss()
      } catch (err) {
        // 样式插不进去不阻断功能，继续
      }
      buildUi()
      container.appendChild(ui.root)
      applyTheme()
      restoreInvertPref()          // 反色 = 用户上一次的选择，和系统主题无关

      // 主题变化实时响应
      if (typeof window.MutationObserver === 'function' && document.documentElement) {
        state.themeObserver = new window.MutationObserver(function () { applyTheme() })
        state.themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
      }

      // 2) 动态 import pdf.js（这是**可选的第三方依赖**，必须运行时按需拉；URL 可配置）
      const pdfUrls = pdfjsUrls()
      let pdfjs = null
      let viewerMod = null
      try {
        pdfjs = await import(pdfUrls.module)
        viewerMod = await import(pdfUrls.viewer)
      } catch (err) {
        return fail(DEP_HINT + '（' + errText(err) + '）')
      }
      if (!pdfjs || typeof pdfjs.getDocument !== 'function' ||
        !viewerMod || typeof viewerMod.PDFViewer !== 'function') {
        return fail(DEP_HINT)
      }
      state.findState = viewerMod.FindState || FALLBACK_FIND_STATE
      try { pdfjs.GlobalWorkerOptions.workerSrc = pdfUrls.worker } catch (err) {}

      // 3) 打开文档
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
        return fail(describeError(err))
      }
      if (ui.total) ui.total.textContent = '/ ' + (pdfDocument.numPages || 0)
      if (ui.statusText) ui.statusText.textContent = '正在排版页面…'
      state.pdfDocument = pdfDocument      // 截图（整页导出）时按页码取页用

      // 4) 事件总线 / 链接服务 / 搜索控制器 / 阅读器本体
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

        // 先挂监听，再绑定文档，免得错过 pagesinit
        const busEvents = {
          pagesinit: onPagesInit,
          pagechanging: onPageChanging,
          scalechanging: onScaleChanging,
          updatefindmatchescount: onMatchesCount,
          updatefindcontrolstate: onFindControlState
        }
        Object.keys(busEvents).forEach(function (type) {
          eventBus.on(type, busEvents[type])
          state.busHandlers.push({ type: type, fn: busEvents[type] })
        })

        viewer = new viewerMod.PDFViewer({
          container: ui.scroll,          // 外层滚动容器（CSS 里是绝对定位，pdf.js 要求如此）
          viewer: ui.viewer,             // 里面那个 .pdfViewer
          eventBus: eventBus,
          linkService: linkService,
          findController: findController,
          textLayerMode: TEXT_LAYER_MODE_ENABLE   // 文本层开着：能选中、能复制、搜索高亮才有意义
        })
        linkService.setViewer(viewer)
        linkService.setDocument(pdfDocument, null)
        state.pdfViewer = viewer
      } catch (err) {
        return fail('内置阅读器初始化失败：' + errText(err))
      }

      // 5) 渲染（容器尺寸变化时靠 ResizeObserver 重新套用适宽/适页）
      try {
        viewer.setDocument(pdfDocument)
      } catch (err) {
        return fail('内置阅读器渲染失败：' + errText(err))
      }
      if (typeof window.ResizeObserver === 'function') {
        state.resizeObserver = new window.ResizeObserver(function () { scheduleScaleRefresh() })
        state.resizeObserver.observe(ui.body)
      }
      onGlobal(window, 'resize', scheduleScaleRefresh)   // 双保险（老浏览器 / 视口缩放）

      // 对外 API：除 destroy 之外，还提供「整页导出 + 框选提问」这组方法。
      // 全部保证不抛异常：capturePage / captureRegion 出错返回 null，goToPage 静默失败，
      // getPageCount 读不到返回 0。
      return {
        ok: true,
        destroy: destroy,
        getPageCount: getPageCount,
        capturePage: capturePage,
        captureRegion: captureRegion,
        startRegionSelect: startRegionSelect,
        cancelRegionSelect: cancelRegionSelect,
        goToPage: goToPage,
        isInverted: isInverted
      }
    } catch (err) {
      return fail('内置阅读器初始化失败：' + errText(err))
    }
  }

  // 反色偏好的读写也放出来：阅读器在桩环境里挂不起来时，
  // 测试仍能直接验证「点过什么、下次读回什么」这段逻辑。
  export { mount, readInvertPref, writeInvertPref }
  export default { mount, readInvertPref, writeInvertPref }
