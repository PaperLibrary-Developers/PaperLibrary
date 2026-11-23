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
   - 批注（荧光笔 / 便签）只是**渲染 + 交互**：批注数据由调用方经 options.annotations 传进来，
     改完通过 options.onAnnotationsChange 交回去由调用方落库。阅读器自己不碰网络、不碰存储，
     坐标存「相对该页的 0~1 归一化值」，所以缩放 / 适宽 / 换屏幕都不用重算。
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

  // 批注颜色一律用 #rrggbb（老数据里的 yellow/green/blue/pink 会在读入时折算）。
  // 内置 4 色 + 用户自己加的颜色（调色盘，存 localStorage，跨文献/跨会话都在）。
  const ANNOT_BUILTIN_COLORS = ['#f2c433', '#3f9e77', '#4f8fd6', '#dd7a9d']
  const ANNOT_LEGACY_COLORS = { yellow: '#f2c433', green: '#3f9e77', blue: '#4f8fd6', pink: '#dd7a9d' }
  const ANNOT_DEFAULT_COLOR = ANNOT_BUILTIN_COLORS[0]
  const ANNOT_PALETTE_KEY = 'lv:annot-palette'   // 纯本地偏好（不跟库同步）
  const ANNOT_CUSTOM_MAX = 12                    // 自定义色上限：再多点起来就累了
  const ANNOT_HEX_RE = /^#([0-9a-f]{6}|[0-9a-f]{3})$/i
  const ANNOT_HL_ALPHA = 0.38                    // 高亮填充透明度（便签底色不透明）
  const ANNOT_MAX_RECTS = 120          // 一条高亮最多这么多行矩形（跨页长选区分页后各自成条）
  const ANNOT_MIN_DRAG = 4             // 便签模式：按下到松开的位移小于这个像素数算「点了一下」
  const ANNOT_QUOTE_MAX = 600
  const ANNOT_NOTE_MAX = 4000
  const ANNOT_FLASH_MS = 1400          // 定位后闪烁多久
  const ANNOT_LAYER_CLASS = 'lv-annot-layer'

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
  position: relative;          /* 调色盘按它定位 */
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

/* ============================ 批注：荧光笔 / 便签 ============================ */
/* 颜色不再写死在 CSS 里：用户可以自定义任意色值，填充 / 底色 / 描边都由脚本按
   「色值 + 透明度」内联写进去（见 hexToRgba / inkOnColor）。 */

/* 标注层：贴在每一页 .page 里（页面盒子就是坐标系），只画不挡操作 */
.lv-pdf-viewer .lv-annot-layer {
  position: absolute; inset: 0; z-index: 2;
  pointer-events: none;
}
.lv-pdf-viewer .lv-annot-mark {
  position: absolute; pointer-events: none;
  border-radius: 2px;
}

/* 定位时的闪烁：只动 box-shadow（背景色各色不同，动它会在结束时闪一下原色） */
.lv-pdf-viewer .lv-annot-flash { animation: lv-annot-flash .7s ease-out 2; }
@keyframes lv-annot-flash {
  0%   { box-shadow: 0 0 0 2px rgba(47, 111, 94, .85); }
  100% { box-shadow: 0 0 0 2px rgba(47, 111, 94, 0); }
}

/* 便签图标：小方块 + 折角，里面只画**一道横线**（单行），不用图标字体。
   底色与横线颜色由脚本按该条批注的颜色内联写入（自定义色也能用）。 */
.lv-pdf-viewer .lv-annot-note {
  position: absolute; width: 20px; height: 20px; margin: 0; padding: 0;
  display: flex; align-items: center; justify-content: center;
  transform: translate(-50%, -50%);
  color: #3a2d06; background: #f2c433;
  border: 1px solid rgba(60, 45, 5, .30); border-radius: 5px 5px 5px 1px;
  box-shadow: 0 1px 3px rgba(30, 26, 12, .22);
  cursor: pointer; pointer-events: auto;
  transition: transform .12s ease, box-shadow .12s ease;
}
.lv-pdf-viewer .lv-annot-note::before {
  content: ""; width: 10px; height: 1.5px; border-radius: 1px;
  background: currentColor; opacity: .85;
}
.lv-pdf-viewer .lv-annot-note:hover {
  transform: translate(-50%, -50%) scale(1.15);
  box-shadow: 0 2px 7px rgba(30, 26, 12, .30);
  z-index: 1;
}
.lv-pdf-viewer .lv-annot-note:focus-visible { outline: 2px solid var(--accent, #2f6f5e); outline-offset: 1px; }

/* 进入标注模式时的说明条（常驻，不用定时器；退出模式立刻消失） */
.lv-pdf-viewer .lv-annot-hint {
  position: absolute; left: 50%; top: 8px; transform: translateX(-50%);
  z-index: 3; max-width: calc(100% - 24px);
  padding: 4px 10px; font-size: 12px; line-height: 1.5;
  color: var(--accent-text, #235648); background: var(--accent-soft, #e4efea);
  border: 1px solid rgba(47, 111, 94, .28); border-radius: 99px;
  box-shadow: var(--shadow-1, 0 1px 2px rgba(35, 33, 25, .06));
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  pointer-events: none;
}

/* 标注模式下的光标提示：荧光笔 = 选文字，便签 = 点位置 */
.lv-pdf-viewer.lv-annot-picking .lv-pdf-scroll { cursor: text; }
.lv-pdf-viewer.lv-annot-picking .lv-pdf-scroll .page { cursor: text; }
.lv-pdf-viewer.lv-annot-pinning .lv-pdf-scroll,
.lv-pdf-viewer.lv-annot-pinning .lv-pdf-scroll .page { cursor: copy; }

/* 工具栏 / 列表共用的批注图标（内联 SVG）：
   荧光笔 = 一支斜放的笔（笔头用所选颜色），便签 = 便签纸 + 一道横线（单行）。 */
.lv-pdf-viewer .lv-pdf-icon,
.lv-pdf-viewer .lv-annot-kind {
  display: inline-flex; align-items: center; justify-content: center;
  flex: 0 0 auto; line-height: 0;
}
.lv-pdf-viewer .lv-pdf-icon { width: 14px; height: 14px; }
.lv-pdf-viewer .lv-annot-kind { width: 14px; height: 14px; }
.lv-pdf-viewer .lv-pdf-icon svg,
.lv-pdf-viewer .lv-annot-kind svg { display: block; }
/* 颜色选择：一颗圆形色点按钮 + 弹出调色盘（内置 4 色 + 用户自定义 + 「＋」取色）
   调色盘挂在工具栏里（position:absolute），所以工具栏本身要 relative。 */
.lv-pdf-viewer .lv-pdf-color {
  width: 26px; height: 26px; padding: 0; flex: 0 0 auto;
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--surface, #fffefb); border: 1px solid var(--border, #e2dfd5);
  border-radius: 50%; cursor: pointer;
  transition: border-color .15s, box-shadow .15s;
}
.lv-pdf-viewer .lv-pdf-color:hover { border-color: var(--border-strong, #cfcabb); }
.lv-pdf-viewer .lv-pdf-color[aria-expanded="true"] {
  border-color: var(--accent, #2f6f5e);
  box-shadow: 0 0 0 2px var(--accent-soft, #e4efea);
}
.lv-pdf-viewer .lv-pdf-color-dot {
  width: 14px; height: 14px; border-radius: 50%;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .16);
}

.lv-pdf-viewer .lv-pdf-palette {
  position: absolute; z-index: 6;
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  width: 158px; padding: 9px;
  background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5);
  border-radius: 10px;
  box-shadow: var(--shadow-2, 0 6px 22px rgba(35, 33, 25, .10), 0 2px 6px rgba(35, 33, 25, .06));
}
.lv-pdf-viewer .lv-pdf-palette[hidden] { display: none; }
.lv-pdf-viewer .lv-palette-dot {
  position: relative; width: 22px; height: 22px; padding: 0;
  border: 2px solid transparent; border-radius: 50%; cursor: pointer;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .14);
  transition: transform .12s ease, border-color .12s ease;
}
.lv-pdf-viewer .lv-palette-dot:hover { transform: scale(1.12); }
.lv-pdf-viewer .lv-palette-dot:focus-visible { outline: 2px solid var(--accent, #2f6f5e); outline-offset: 1px; }
.lv-pdf-viewer .lv-palette-dot.active {
  border-color: var(--accent, #2f6f5e);
  box-shadow: inset 0 0 0 2px var(--surface, #fffefb);
}
/* 自定义色右上角的「×」：只在 hover 时露出来，避免误删 */
.lv-pdf-viewer .lv-palette-del {
  position: absolute; top: -5px; right: -5px;
  width: 13px; height: 13px; border-radius: 50%;
  display: none; align-items: center; justify-content: center;
  font-size: 9px; line-height: 1; color: #fff;
  background: var(--danger, #b4453a); border: 1px solid var(--surface, #fffefb);
}
.lv-pdf-viewer .lv-palette-dot.custom:hover .lv-palette-del { display: flex; }
.lv-pdf-viewer .lv-palette-add {
  width: 22px; height: 22px; padding: 0; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 14px; line-height: 1;
  color: var(--muted, #75726a); background: transparent;
  border: 1px dashed var(--border-strong, #cfcabb); cursor: pointer;
}
.lv-pdf-viewer .lv-palette-add:hover { color: var(--accent-text, #235648); border-color: var(--accent, #2f6f5e); }
.lv-pdf-viewer .lv-palette-add[disabled] { opacity: .4; cursor: default; }
.lv-pdf-viewer .lv-palette-hint {
  flex: 0 0 100%; font-size: 11px; line-height: 1.4; color: var(--muted, #75726a);
}

/* 批注列表：贴在阅读区右侧的抽屉（z-index 3，低于框选取景层 4：
   进框选时它被盖住是符合预期的，框选是模态操作） */
.lv-pdf-viewer .lv-annot-panel {
  position: absolute; top: 0; right: 0; bottom: 0; width: 258px; z-index: 3;
  display: flex; flex-direction: column; min-height: 0;
  background: var(--surface, #fffefb);
  border-left: 1px solid var(--border, #e2dfd5);
  box-shadow: -8px 0 20px rgba(28, 25, 15, .10);
}
.lv-pdf-viewer .lv-annot-panel[hidden] { display: none; }
.lv-pdf-viewer .lv-annot-panel-head {
  flex: 0 0 auto; display: flex; align-items: center; gap: 6px;
  padding: 7px 8px 7px 10px;
  font-size: 12.5px; font-weight: 600; color: var(--text, #26251f);
  border-bottom: 1px solid var(--border, #e2dfd5);
}
.lv-pdf-viewer .lv-annot-panel-head .right { margin-left: auto; display: flex; gap: 4px; }
.lv-pdf-viewer .lv-annot-list {
  flex: 1 1 auto; min-height: 0; overflow: auto; padding: 6px;
  display: flex; flex-direction: column; gap: 4px;
  scrollbar-width: thin;
}
.lv-pdf-viewer .lv-annot-item {
  display: flex; gap: 6px; padding: 6px 6px 6px 8px;
  border: 1px solid transparent; border-radius: 6px; cursor: pointer;
}
.lv-pdf-viewer .lv-annot-item:hover { background: var(--surface-2, #f8f7f3); border-color: var(--border, #e2dfd5); }
.lv-pdf-viewer .lv-annot-item.active { background: var(--accent-soft, #e4efea); border-color: var(--accent, #2f6f5e); }
.lv-pdf-viewer .lv-annot-item .meta {
  flex: 0 0 auto; width: 30px; display: flex; flex-direction: column; align-items: center; gap: 3px;
}
.lv-pdf-viewer .lv-annot-page {
  font-size: 11px; color: var(--muted, #75726a); font-variant-numeric: tabular-nums; white-space: nowrap;
}
.lv-pdf-viewer .lv-annot-item .body { flex: 1 1 auto; min-width: 0; }
.lv-pdf-viewer .lv-annot-quote-text,
.lv-pdf-viewer .lv-annot-note-text {
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2;
  overflow: hidden; word-break: break-word;
}
.lv-pdf-viewer .lv-annot-quote-text { font-size: 12px; line-height: 1.5; color: var(--text, #26251f); }
.lv-pdf-viewer .lv-annot-note-text {
  margin-top: 2px; font-size: 11.5px; line-height: 1.5; color: var(--muted, #75726a);
  white-space: pre-wrap;
}
.lv-pdf-viewer .lv-annot-item .acts { flex: 0 0 auto; display: flex; flex-direction: column; gap: 2px; }
.lv-pdf-viewer .lv-annot-item .del {
  flex: 0 0 auto; width: 20px; height: 20px; padding: 0;
  font-size: 13px; line-height: 1; color: var(--faint, #9c988e);
  background: transparent; border: 0; border-radius: 4px; cursor: pointer;
}
.lv-pdf-viewer .lv-annot-item .del:hover { color: var(--danger, #b4453a); background: var(--danger-soft, #fbeeec); }
.lv-pdf-viewer .lv-annot-empty {
  padding: 18px 12px; font-size: 12.5px; line-height: 1.75;
  color: var(--muted, #75726a); text-align: center; white-space: pre-line;
}

/* ---------- 批注编辑气泡（挂在 <body> 下，position:fixed，不受阅读区 overflow 裁剪） ---------- */
.lv-annot-popwrap { position: fixed; inset: 0; z-index: 9999; }
.lv-annot-pop {
  position: fixed;
  display: flex; flex-direction: column; gap: 8px;
  width: 306px; max-width: calc(100vw - 16px); box-sizing: border-box;
  padding: 10px;
  font-family: var(--font-sans, sans-serif); font-size: 13px; line-height: 1.6;
  color: var(--text, #26251f); background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5); border-radius: 10px;
  box-shadow: 0 12px 32px rgba(28, 25, 15, .20), 0 2px 6px rgba(28, 25, 15, .08);
}
.lv-annot-pop-head { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted, #75726a); }
.lv-annot-pop-head b { color: var(--text, #26251f); }
.lv-annot-quote {
  max-height: 76px; overflow: auto;
  padding: 5px 8px; font-size: 12px; line-height: 1.55;
  color: var(--muted, #75726a); background: var(--surface-2, #f8f7f3);
  border-left: 2px solid var(--accent, #2f6f5e); border-radius: 0 5px 5px 0;
  white-space: pre-wrap; word-break: break-word;
}
.lv-annot-pop textarea {
  width: 100%; box-sizing: border-box; min-height: 78px; max-height: 40vh; resize: vertical;
  padding: 6px 8px; font: inherit; font-size: 12.5px; line-height: 1.6;
  color: inherit; background: var(--surface-2, #f8f7f3);
  border: 1px solid var(--border, #e2dfd5); border-radius: 6px; outline: none;
}
.lv-annot-pop textarea:focus { border-color: var(--accent, #2f6f5e); }
.lv-annot-pop-foot { display: flex; align-items: center; gap: 6px; }
.lv-annot-pop-foot .right { margin-left: auto; display: flex; align-items: center; gap: 6px; }
/* 自动保存状态：平时灰字，存完变绿（accent），不需要用户再点按钮 */
.lv-annot-status { font-size: 11.5px; line-height: 1.4; color: var(--muted, #75726a); white-space: nowrap; }
.lv-annot-status.saved { color: var(--accent-text, #235648); }
.lv-annot-btn {
  padding: 3px 9px; font: inherit; font-size: 12.5px; line-height: 1.7;
  color: var(--text, #26251f); background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5); border-radius: 6px; cursor: pointer;
}
.lv-annot-btn:hover { background: var(--surface-3, #efeee8); border-color: var(--border-strong, #cfcabb); }
.lv-annot-btn:focus-visible { outline: 2px solid var(--accent, #2f6f5e); outline-offset: 1px; }
.lv-annot-btn.primary { color: #fff; background: var(--accent, #2f6f5e); border-color: var(--accent, #2f6f5e); }
.lv-annot-btn.primary:hover { filter: brightness(1.07); background: var(--accent, #2f6f5e); }
.lv-annot-btn.danger { color: var(--danger, #b4453a); border-color: rgba(180, 69, 58, .45); }
.lv-annot-btn.danger.armed { color: #fff; background: var(--danger, #b4453a); border-color: var(--danger, #b4453a); }
/* 气泡里的换色点：和调色盘同一份颜色列表，颜色由脚本内联写进 background */
.lv-annot-swatches { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; max-width: 148px; }
.lv-annot-swatch {
  width: 20px; height: 20px; padding: 0; border-radius: 50%;
  border: 2px solid transparent; box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .12);
  cursor: pointer;
}
.lv-annot-swatch.active { border-color: var(--accent, #2f6f5e); }
.lv-annot-swatch:hover { transform: scale(1.08); }
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

  /** 创建 SVG 子节点（普通 el() 只能建 HTML 元素） */
  function svgNode (tag, attrs) {
    const node = document.createElementNS('http://www.w3.org/2000/svg', tag)
    if (attrs) {
      const keys = Object.keys(attrs)
      for (let i = 0; i < keys.length; i++) node.setAttribute(keys[i], String(attrs[keys[i]]))
    }
    return node
  }

  /**
   * 颜色规整：'yellow' / '#ABC' / '#aabbcc' → '#aabbcc'；认不出来返回空串。
   * 老数据里存的是键名，读入时统一折算，之后全链路只用色值。
   */
  function normalizeColor (value) {
    const raw = String(value || '').trim().toLowerCase()
    if (raw && ANNOT_LEGACY_COLORS[raw]) return ANNOT_LEGACY_COLORS[raw]
    if (ANNOT_HEX_RE.test(raw)) {
      return raw.length === 4
        ? '#' + raw[1] + raw[1] + raw[2] + raw[2] + raw[3] + raw[3]
        : raw
    }
    return ''
  }

  /** #rrggbb → rgba(...)：荧光笔的填充是半透明的 */
  function hexToRgba (hex, alpha) {
    const value = normalizeColor(hex) || ANNOT_DEFAULT_COLOR
    return 'rgba(' + parseInt(value.slice(1, 3), 16) + ', ' +
      parseInt(value.slice(3, 5), 16) + ', ' +
      parseInt(value.slice(5, 7), 16) + ', ' + alpha + ')'
  }

  /** 这个底色上该用深字还是白字（便签图标里那道横线 / 笔身对比） */
  function inkOnColor (hex) {
    const value = normalizeColor(hex) || ANNOT_DEFAULT_COLOR
    const luma = (0.299 * parseInt(value.slice(1, 3), 16) +
      0.587 * parseInt(value.slice(3, 5), 16) +
      0.114 * parseInt(value.slice(5, 7), 16)) / 255
    return luma > 0.62 ? '#3a2d06' : '#ffffff'
  }

  /** 读用户自定义色；坏数据 / 隐私模式一律当空，绝不让阅读器因此起不来 */
  function readCustomColors () {
    let raw = null
    try { raw = window.localStorage.getItem(ANNOT_PALETTE_KEY) } catch (err) { return [] }
    if (!raw) return []
    try {
      const parsed = JSON.parse(raw)
      if (!Array.isArray(parsed)) return []
      const out = []
      for (let i = 0; i < parsed.length && out.length < ANNOT_CUSTOM_MAX; i++) {
        const hex = normalizeColor(parsed[i])
        if (hex && out.indexOf(hex) < 0) out.push(hex)
      }
      return out
    } catch (err) {
      return []
    }
  }

  function writeCustomColors (list) {
    try { window.localStorage.setItem(ANNOT_PALETTE_KEY, JSON.stringify(list || [])) } catch (err) {}
  }

  /** 调色盘 = 内置 4 色 + 自定义色（去重，内置永远在前） */
  function annotPalette () {
    const out = ANNOT_BUILTIN_COLORS.slice()
    const custom = readCustomColors()
    for (let i = 0; i < custom.length; i++) {
      if (out.indexOf(custom[i]) < 0) out.push(custom[i])
    }
    return out
  }

  function isBuiltinColor (hex) {
    return ANNOT_BUILTIN_COLORS.indexOf(normalizeColor(hex)) >= 0
  }

  /**
   * 批注图标（14×14 内联 SVG，不依赖图标库）：
   *   highlight → 一支斜放的笔，**笔头是传入的颜色**
   *   note      → 便签纸（带折角）+ 一道横线（单行）
   * @param {'highlight'|'note'} kind
   * @param {string} color #rrggbb（也接受老的键名，内部会折算）
   */
  function annotIcon (kind, color) {
    const hex = normalizeColor(color) || ANNOT_DEFAULT_COLOR
    const svg = svgNode('svg', {
      width: '14', height: '14', viewBox: '0 0 16 16',
      'aria-hidden': 'true', focusable: 'false'
    })
    if (kind === 'note') {
      // 纸面：淡色填充 + 同色描边；右下角折一下；中间一道横线
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
    // 笔身用 currentColor（跟着按钮文字在明暗主题下都清楚），只有笔头取色
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
   * @param {{
   *   url:string, title?:string, downloadUrl?:string,
   *   initialView?:{page:number, scale:number, preset:string|null},
   *   onRegionSelected?:(dataUrl:string, info:object)=>void,
   *   annotations?:Array<object>, onAnnotationsChange?:(items:Array<object>)=>void
   * }} options 批注（荧光笔 / 便签）由调用方传入并自行落库，阅读器不碰网络
   * @returns {Promise<{ok:true, destroy:() => void} | {ok:false, reason:string}>}
   */
  async function mount (container, options) {
    const opts = options || {}
    const signal = opts.signal
    const url = opts.url === null || opts.url === undefined ? '' : String(opts.url).trim()

    // 参数问题直接返回，连 DOM 都不碰
    if (!container || container.nodeType !== 1) {
      return { ok: false, reason: '内置阅读器没拿到有效的挂载点' }
    }
    if (!url) return { ok: false, reason: '内置阅读器没拿到 PDF 地址' }
    if (signal && signal.aborted) return { ok: false, reason: '阅读器已关闭' }

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
      regionListeners: [],    // 框选期间临时挂上的监听（退出框选立刻摘掉；domListeners 里也各有一份）
      /* ---- 批注（荧光笔 / 便签）---- */
      annotations: [],        // 当前文献的批注（外部传入，内部只做读写 + 回调）
      annotMode: null,        // null | 'highlight' | 'note'
      annotColor: ANNOT_DEFAULT_COLOR,  // 当前颜色（#rrggbb）
      annotPress: null,       // 便签模式按下时的记点（起点 + 按下前的选区）
      paletteOpen: false,     // 调色盘是否展开
      paletteListeners: [],   // 调色盘展开期间的「点外面就收起」监听
      annotPop: null,         // 打开着的编辑气泡（挂在 body 下，销毁时必须手动摘掉）
      annotPopId: '',         // 气泡正在编辑哪一条（删除别条时用来判断要不要保留未存的改动）
      annotPopFlush: null,    // 气泡的「立刻存」回调：关闭前统一调一次，避免丢最后几个字
      annotPopDocListeners: [], // 气泡打开期间挂在 document 上的监听
      annotPanelOpen: false,
      annotFlashId: '',       // 正在闪烁的批注 id（列表里点「定位」时设）
      annotFlashTimer: 0
    }

    // 界面节点（teardown 时统一置空）
    const ui = {
      root: null, toolbar: null, body: null, scroll: null, viewer: null, status: null, statusText: null,
      pageInput: null, total: null, btnPrevPage: null, btnNextPage: null,
      zoom: null, btnZoomOut: null, btnZoomIn: null, btnFitWidth: null, btnFitPage: null,
      searchInput: null, count: null, btnPrevMatch: null, btnNextMatch: null,
      btnInvert: null, linkDownload: null, linkOpen: null,
      btnRegion: null, regionLayer: null, regionBox: null, regionHint: null,
      btnHighlight: null, btnNote: null, btnColor: null, btnAnnotList: null,
      highlightIcon: null, noteIcon: null, colorDot: null,
      palette: null, colorInput: null,
      annotPanel: null, annotList: null, annotHint: null
    }

    /* ------------------------- 清理（destroy / 失败共用） ------------------------- */

    function teardown () {
      if (state.tornDown) return
      state.tornDown = true
      if (signal) signal.removeEventListener('abort', destroy)

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

      // 4.5) 批注：编辑气泡挂在 <body> 下（不在 root 里），必须手动摘；闪烁定时器也清掉
      if (state.annotFlashTimer) {
        try { clearTimeout(state.annotFlashTimer) } catch (err) {}
        state.annotFlashTimer = 0
      }
      try { closeAnnotPop({ keepChanges: false }) } catch (err) {}
      try { closePalette() } catch (err) {}
      state.paletteListeners.length = 0
      state.paletteOpen = false
      state.annotMode = null
      state.annotPress = null
      state.annotPanelOpen = false
      state.annotations = []

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

    if (signal) signal.addEventListener('abort', destroy, { once: true })

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
      const prior = opts.initialView
      if (prior && prior.preset === null && Number.isFinite(prior.scale)) applyZoom(prior.scale)
      else applyFit(prior?.preset === 'page-fit' ? 'page-fit' : 'page-width')
      if (prior && prior.page > 1) jumpToPage(prior.page)
      if (prior?.page <= total && Number.isFinite(prior.y)) {
        scrollPagePointIntoView(prior.page, prior.y, 0.6, 'instant')
      }
      hideStatus()
      // 批注：页已经排好版，把传进来的批注画上去（之后每页渲染完 pagerendered 会再补一次）
      paintAllAnnotations()
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
      // 标注模式：Esc 先退出标注（比翻页优先，但只吃掉这一个键）
      if (state.annotMode && (ev.key === 'Escape' || ev.key === 'Esc')) {
        ev.preventDefault()
        ev.stopPropagation()
        setAnnotMode(null)
        return
      }
      if (ev.key === 'h' || ev.key === 'H') { ev.preventDefault(); setAnnotMode('highlight'); return }
      if (ev.key === 'n' || ev.key === 'N') { ev.preventDefault(); setAnnotMode('note'); return }
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
        // 框选是模态操作：先把标注模式和编辑气泡收起来，免得两个模式叠在一起
        closeAnnotPop()
        setAnnotMode(null)
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

    /* ------------------- 批注：荧光笔 / 便签（渲染 + 交互） ------------------- */
    /* 数据流：调用方把批注数组传进来（options.annotations）→ 这里只负责画和改；
       改完通过 options.onAnnotationsChange 交回去，由调用方决定怎么存。
       本文件不写网络、不写 localStorage，测试/桩环境里也照常能挂起来。

       坐标系：所有矩形与点位都存「相对该页的 0~1」，画的时候用百分比。
       页面 div 是滚动容器里唯一知道「这一页多大」的东西，缩放/适宽/适页都由它自己变，
       所以归一化之后我们什么都不用重算，也不会出现「放大后高亮跑偏」。

       交互：
         · 荧光笔：选中文字 → mouseup 时把选区的每一行矩形按页分组，一条批注一行一个色块；
           跨页选择就自动拆成「每页一条」，引文各存一份（比一条记录挂多页简单也更好定位）。
         · 便签：点一下页面 → 在落点放一个 20px 的小图标，气泡里写内容；按下时的选区会
           被记下来当引文（浏览器紧接着会清掉选区，所以必须在 mousedown 里抢）。 */

    /** 生成一个稳定的批注 id（只含字母数字，方便做属性选择器 / 调试） */
    function annotId () {
      return 'n' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7)
    }

    function nowStamp () {
      return new Date().toISOString()
    }

    function clamp01 (value) {
      const n = Number(value)
      if (!isFinite(n)) return 0
      return Math.min(1, Math.max(0, n))
    }

    /** 0~1 → 百分比字符串（保留两位小数就够了，再多只是字符串更长） */
    function pct (value) {
      return (Math.round(clamp01(value) * 10000) / 100) + '%'
    }

    /** 当前所有「已排版」页的位置信息（页码 + 页面 div + 视口矩形），按页码升序 */
    function pageInfos () {
      const out = []
      const viewer = state.pdfViewer
      if (!viewer || typeof viewer.getPageView !== 'function') return out
      const total = Math.min(viewer.pagesCount || 0, 4000)
      for (let i = 0; i < total; i++) {
        let pageView = null
        try { pageView = viewer.getPageView(i) } catch (err) { pageView = null }
        const div = pageView && pageView.div
        if (!div) continue
        const rect = clientRectOf(div)
        if (!rect || rect.width <= 0 || rect.height <= 0) continue
        out.push({ pageNumber: i + 1, div: div, rect: rect })
      }
      return out
    }

    function pageInfoOf (pageNumber) {
      const viewer = state.pdfViewer
      if (!viewer || typeof viewer.getPageView !== 'function') return null
      const index = Math.round(Number(pageNumber)) - 1
      if (!(index >= 0)) return null
      let pageView = null
      try { pageView = viewer.getPageView(index) } catch (err) { pageView = null }
      const div = pageView && pageView.div
      if (!div) return null
      const rect = clientRectOf(div)
      if (!rect || rect.width <= 0 || rect.height <= 0) return null
      return { pageNumber: index + 1, div: div, rect: rect }
    }

    /** 点（视口坐标）落在哪一页里 */
    function pageAtPoint (infos, x, y) {
      for (let i = 0; i < infos.length; i++) {
        const r = infos[i].rect
        if (x >= r.left && x <= r.left + r.width && y >= r.top && y <= r.top + r.height) return infos[i]
      }
      return null
    }

    /** 矩形（视口坐标）中心点落在哪一页里 —— 跨页选区就是靠这个分页的 */
    function pageAtRectCenter (infos, rect) {
      const x = rect.left + rect.width / 2
      const y = rect.top + rect.height / 2
      return pageAtPoint(infos, x, y)
    }

    /** 视口矩形 → 相对该页的 0~1 盒子；整块都在页外就返回 null */
    function normBox (rect, pageRect) {
      if (!rect || !pageRect || !(pageRect.width > 0) || !(pageRect.height > 0)) return null
      const left = Math.max(rect.left, pageRect.left)
      const top = Math.max(rect.top, pageRect.top)
      const right = Math.min(rect.left + rect.width, pageRect.left + pageRect.width)
      const bottom = Math.min(rect.top + rect.height, pageRect.top + pageRect.height)
      if (right - left < 1 || bottom - top < 1) return null
      return {
        x: (left - pageRect.left) / pageRect.width,
        y: (top - pageRect.top) / pageRect.height,
        w: (right - left) / pageRect.width,
        h: (bottom - top) / pageRect.height
      }
    }

    /** 视口点 → 相对该页的 0~1 点 */
    function normPoint (pt, pageRect) {
      if (!pt || !pageRect || !(pageRect.width > 0) || !(pageRect.height > 0)) return null
      return {
        x: clamp01((pt.x - pageRect.left) / pageRect.width),
        y: clamp01((pt.y - pageRect.top) / pageRect.height)
      }
    }

    /** 找到某个已有的标注层（没有就返回 null，不创建） */
    function findAnnotLayer (pageDiv) {
      if (!pageDiv) return null
      const children = pageDiv.children || []
      for (let i = children.length - 1; i >= 0; i--) {
        const node = children[i]
        if (node && node.classList && node.classList.contains(ANNOT_LAYER_CLASS)) return node
      }
      return null
    }

    /** 找到（必要时创建）某一页的标注层。层永远是 .page 的最后一个子节点 + z-index:2 */
    function ensureAnnotLayer (pageDiv) {
      if (!pageDiv) return null
      const exist = findAnnotLayer(pageDiv)
      if (exist) return exist
      const layer = el('div.' + ANNOT_LAYER_CLASS, { 'aria-hidden': 'true' })
      try { pageDiv.appendChild(layer) } catch (err) { return null }
      return layer
    }

    /** 一条批注 → 若干 DOM 节点（荧光笔一条会出多个矩形，便签只有一个图标） */
    function annotNodes (item) {
      const nodes = []
      const flash = !!state.annotFlashId && state.annotFlashId === item.id
      const color = normalizeColor(item.color) || ANNOT_DEFAULT_COLOR
      if (item.type === 'note') {
        const pt = item.point || { x: 0, y: 0 }
        const btn = el('button.lv-annot-note', {
          type: 'button',
          'data-annot-id': item.id,
          title: (item.note ? String(item.note).slice(0, 60) : '（空便签）') + ' · 点击查看 / 编辑',
          'aria-label': '便签：' + (item.note || '（空）')
        })
        btn.style.left = pct(pt.x)
        btn.style.top = pct(pt.y)
        // 底色 / 横线颜色按该条批注自己的颜色来（自定义色也支持）
        btn.style.background = color
        btn.style.color = inkOnColor(color)
        btn.style.borderColor = hexToRgba(color, 0.45)
        if (flash) btn.classList.add('lv-annot-flash')
        btn.onclick = ev => {
          if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
          // 键盘回车 / 脚本触发时 clientX/Y 是 0，退回到图标自己的位置当锚点
          let anchor = null
          if (ev && (ev.clientX || ev.clientY)) anchor = { x: ev.clientX, y: ev.clientY }
          else {
            const rect = clientRectOf(btn)
            if (rect) anchor = { x: rect.left + rect.width / 2, y: rect.top + rect.height }
          }
          openAnnotPop(item, { anchor: anchor })
        }
        nodes.push(btn)
        return nodes
      }
      const rects = Array.isArray(item.rects) ? item.rects : []
      for (let i = 0; i < rects.length; i++) {
        const r = rects[i]
        if (!r) continue
        const mark = el('div.lv-annot-mark.lv-annot-hl', { 'data-annot-id': item.id })
        mark.style.left = pct(r.x)
        mark.style.top = pct(r.y)
        mark.style.width = pct(r.w)
        mark.style.height = pct(r.h)
        mark.style.background = hexToRgba(color, ANNOT_HL_ALPHA)   // 半透明色块，压字不糊
        if (flash) mark.classList.add('lv-annot-flash')
        nodes.push(mark)
      }
      return nodes
    }

    /** 重画某一页的批注（幂等：每次都是整层替换，重复调用不会叠加节点） */
    function paintPageAnnotations (pageNumber) {
      if (state.destroyed || state.tornDown) return
      const info = pageInfoOf(pageNumber)
      if (!info) return
      const nodes = []
      for (let i = 0; i < state.annotations.length; i++) {
        const item = state.annotations[i]
        if (Number(item.page) !== Number(pageNumber)) continue
        const group = annotNodes(item)
        for (let k = 0; k < group.length; k++) nodes.push(group[k])
      }
      // 这页没有批注、也没画过：连层都不用建（省一个 div / 页）
      let layer = findAnnotLayer(info.div)
      if (!nodes.length && !layer) return
      if (!layer) layer = ensureAnnotLayer(info.div)
      if (!layer) return
      try { layer.replaceChildren.apply(layer, nodes) } catch (err) { layer.textContent = '' }
    }

    /** 重画所有已排版的页 */
    function paintAllAnnotations () {
      const infos = pageInfos()
      for (let i = 0; i < infos.length; i++) paintPageAnnotations(infos[i].pageNumber)
    }

    /** 在屏幕上找某条批注的节点（列表里点「编辑」时用它当锚点） */
    function findAnnotNode (id) {
      if (!id || !ui.viewer || typeof ui.viewer.querySelectorAll !== 'function') return null
      const nodes = ui.viewer.querySelectorAll('[data-annot-id]')
      for (let i = 0; i < nodes.length; i++) {
        if (nodes[i].getAttribute('data-annot-id') === id) return nodes[i]
      }
      return null
    }

    /** 把批注变化交回调用方（回调炸了也不能影响阅读器） */
    function emitAnnotations () {
      if (state.destroyed || state.tornDown) return
      const cb = opts.onAnnotationsChange
      if (typeof cb !== 'function') return
      try { cb(state.annotations.slice()) } catch (err) {}
    }

    /** 外部数据 → 内部可信形状；认不出来的丢弃（老数据 / 手改过的 JSON 都走这里） */
    function sanitizeAnnot (raw) {
      if (!raw || typeof raw !== 'object') return null
      const type = raw.type === 'note' ? 'note' : (raw.type === 'highlight' ? 'highlight' : '')
      if (!type) return null
      const id = String(raw.id || '').slice(0, 64)
      if (!id) return null
      const rects = []
      const rawRects = Array.isArray(raw.rects) ? raw.rects : []
      for (let i = 0; i < rawRects.length; i++) {
        const r = rawRects[i]
        if (!r) continue
        const w = Number(r.w)
        const h = Number(r.h)
        if (!isFinite(w) || !isFinite(h) || w <= 0 || h <= 0) continue
        rects.push({ x: clamp01(r.x), y: clamp01(r.y), w: clamp01(w), h: clamp01(h) })
        if (rects.length >= ANNOT_MAX_RECTS) break
      }
      const pointRaw = raw.point && typeof raw.point === 'object' ? raw.point : null
      const point = pointRaw
        ? { x: clamp01(pointRaw.x), y: clamp01(pointRaw.y) }
        : (rects[0] ? { x: rects[0].x, y: rects[0].y } : null)
      if (type === 'highlight' && !rects.length) return null
      if (type === 'note' && !point) return null
      return {
        id: id,
        type: type,
        page: Math.max(1, Math.round(Number(raw.page) || 1)),
        // 老数据是 yellow/green/blue/pink，新数据是 #rrggbb，这里统一成色值
        color: normalizeColor(raw.color) || ANNOT_DEFAULT_COLOR,
        rects: rects,
        point: type === 'note' ? point : null,
        quote: String(raw.quote || '').slice(0, ANNOT_QUOTE_MAX),
        note: String(raw.note || '').slice(0, ANNOT_NOTE_MAX),
        createdAt: String(raw.createdAt || ''),
        updatedAt: String(raw.updatedAt || '')
      }
    }

    /** 整组替换批注（详情页读到服务端数据后调用；也可用于外部刷新） */
    function setAnnotations (items) {
      if (state.destroyed || state.tornDown) return
      const next = []
      const list = Array.isArray(items) ? items : []
      for (let i = 0; i < list.length; i++) {
        const item = sanitizeAnnot(list[i])
        if (item) next.push(item)
      }
      state.annotations = next
      paintAllAnnotations()
      renderAnnotPanel()
      syncAnnotCount()
    }

    function getAnnotations () {
      return state.annotations.slice()
    }

    /** 当前选区 → { text, rects }；没有可见选区返回 null */
    function selectionSnapshot () {
      const sel = typeof window.getSelection === 'function' ? window.getSelection() : null
      if (!sel || !sel.rangeCount || sel.isCollapsed) return null
      const range = sel.getRangeAt(0)
      if (!range || typeof range.getClientRects !== 'function') return null
      const text = String(sel.toString() || '').replace(/\s+/g, ' ').trim()
      const list = range.getClientRects()
      const rects = []
      for (let i = 0; i < list.length && rects.length < ANNOT_MAX_RECTS; i++) {
        const r = list[i]
        if (!r || !(r.width > 1) || !(r.height > 1)) continue
        rects.push({ left: r.left, top: r.top, width: r.width, height: r.height })
      }
      if (!rects.length) return null
      return { text: text.slice(0, ANNOT_QUOTE_MAX), rects: rects }
    }

    /** 两条高亮是不是「同一处」（同页 + 每行矩形几乎重合）；用来挡住重复标记 */
    function isSameHighlight (a, b) {
      if (!a || !b || Number(a.page) !== Number(b.page)) return false
      const ra = Array.isArray(a.rects) ? a.rects : []
      const rb = Array.isArray(b.rects) ? b.rects : []
      if (ra.length !== rb.length) return false
      for (let i = 0; i < ra.length; i++) {
        if (Math.abs(ra[i].x - rb[i].x) > 0.004 || Math.abs(ra[i].y - rb[i].y) > 0.004 ||
          Math.abs(ra[i].w - rb[i].w) > 0.004 || Math.abs(ra[i].h - rb[i].h) > 0.004) return false
      }
      return true
    }

    /** 把当前选区做成高亮（跨页自动拆成每页一条）；成功返回 true */
    function createHighlightFromSelection () {
      const snap = selectionSnapshot()
      if (!snap) return false
      const infos = pageInfos()
      if (!infos.length) return false
      const groups = new Map()      // pageNumber -> rects[]
      for (let i = 0; i < snap.rects.length; i++) {
        const target = pageAtRectCenter(infos, snap.rects[i])
        if (!target) continue
        const box = normBox(snap.rects[i], target.rect)
        if (!box) continue
        if (!groups.has(target.pageNumber)) groups.set(target.pageNumber, [])
        const bucket = groups.get(target.pageNumber)
        if (bucket.length < ANNOT_MAX_RECTS) bucket.push(box)
      }
      if (!groups.size) return false
      const stamp = nowStamp()
      const created = []
      let added = 0
      groups.forEach((rects, page) => {
        const item = {
          id: annotId(),
          type: 'highlight',
          page: page,
          color: state.annotColor,
          rects: rects,
          point: null,
          quote: snap.text,
          note: '',
          createdAt: stamp,
          updatedAt: stamp
        }
        // 已经标过同一处就不要再叠一条：叠起来颜色发闷，列表里也会出好几行一样的。
        // （点空白处时浏览器会保留上一次的选区，所以「同一处反复触发」是真会发生的）
        const dup = state.annotations.find(other => other.type === 'highlight' && isSameHighlight(other, item))
        if (dup) {
          created.push(dup)
          return
        }
        state.annotations.push(item)
        added++
        created.push(item)
      })
      // 选完就把浏览器那层临时选中清掉，不然会和刚画的高亮叠在一起
      try {
        const sel = window.getSelection()
        if (sel && typeof sel.removeAllRanges === 'function') sel.removeAllRanges()
      } catch (err) {}
      for (let i = 0; i < created.length; i++) paintPageAnnotations(created[i].page)
      renderAnnotPanel()
      syncAnnotCount()
      if (added) emitAnnotations()                 // 全是重复的就不必再写一遍库
      if (created.length) flashAnnotation(created[0].id, created[0].page)
      return added > 0
    }

    /** 让某条批注闪一下（定位的视觉反馈）。只设标记，重画时自然带上动画类 */
    function flashAnnotation (id, pageNumber) {
      state.annotFlashId = id
      if (state.annotFlashTimer) {
        try { clearTimeout(state.annotFlashTimer) } catch (err) {}
      }
      // 立刻重画一次：闪烁类必须和节点一起出现，等下一次重画就已经过点了
      try { paintPageAnnotations(pageNumber) } catch (err) {}
      state.annotFlashTimer = setTimeout(() => {
        state.annotFlashTimer = 0
        if (state.destroyed || state.tornDown) return
        state.annotFlashId = ''
        try { paintPageAnnotations(pageNumber) } catch (err) {}
      }, ANNOT_FLASH_MS)
    }

    /** 批注在页面里的纵向锚点（0~1）：便签取落点，荧光笔取最上面那一行 */
    function annotationAnchorY (item) {
      if (!item) return 0
      if (item.type === 'note') return clamp01(item.point ? item.point.y : 0)
      const rects = Array.isArray(item.rects) ? item.rects : []
      let y = 1
      for (let i = 0; i < rects.length; i++) {
        const value = Number(rects[i] && rects[i].y)
        if (isFinite(value) && value < y) y = value
      }
      return y === 1 ? 0 : clamp01(y)
    }

    /**
     * 把某条批注滚到视口里：不是「跳到某一页」，而是按它在页面内的相对位置**精确对位**
     * （落在视口从上往下 30% 处，上面留一点上下文）。
     * 页面 div 在 pagesinit 之后就有确定尺寸，所以绝大多数情况不用等这一页渲染完。
     * @returns {boolean} 对位成功 true；页还没排出来 false（调用方退回按页跳）
     */
    function scrollPagePointIntoView (page, y, viewportFraction, behavior) {
      if (!ui.scroll) return false
      const info = pageInfoOf(page)
      if (!info) return false
      const box = clientRectOf(ui.scroll)
      if (!box || !box.height) return false
      const offsetInPage = clamp01(y) * info.rect.height
      // 视口坐标 → 滚动内容坐标：这一页在视口里的位置 + 已滚过的距离 + 页内偏移
      const contentTop = (info.rect.top - box.top) + (ui.scroll.scrollTop || 0) + offsetInPage
      const target = contentTop - box.height * viewportFraction
      const maxTop = Math.max(0, (ui.scroll.scrollHeight || 0) - (ui.scroll.clientHeight || 0))
      const top = Math.max(0, maxTop > 0 ? Math.min(maxTop, target) : target)
      try {
        if (typeof ui.scroll.scrollTo === 'function') ui.scroll.scrollTo({ top, behavior })
        else ui.scroll.scrollTop = top
      } catch (err) {
        try { ui.scroll.scrollTop = top } catch (err2) {}
      }
      return true
    }

    /** 列表里点一条 → 滚到它在页面里的具体位置并闪一下 */
    function locateAnnotation (id) {
      const item = state.annotations.find(a => a.id === id)
      if (!item) return
      // 精确对位优先；页 div 还没建出来（极早期 / 文档还没排版）才退回「按页跳」
      if (!scrollPagePointIntoView(item.page, annotationAnchorY(item), 0.3, 'smooth')) goToPage(item.page)
      paintPageAnnotations(item.page)
      flashAnnotation(item.id, item.page)      // flash 内部会再按当前滚动位置重画一次
      if (state.annotPanelOpen) renderAnnotPanel()
    }

    function deleteAnnotation (id) {
      const index = state.annotations.findIndex(a => a.id === id)
      if (index < 0) return
      const removed = state.annotations.splice(index, 1)[0]
      // 删的正是气泡里那条 → 丢掉未存的改动；删的是别的条目 → 先把它正在编辑的内容存掉
      closeAnnotPop({ keepChanges: state.annotPopId !== id })
      if (removed) paintPageAnnotations(removed.page)
      renderAnnotPanel()
      syncAnnotCount()
      emitAnnotations()
    }

    /* ---- 编辑气泡（挂在 <body> 下：不受阅读区 overflow 裁剪，也不会被缩放影响） ---- */

    function bindAnnotDoc (type, fn) {
      if (typeof document.addEventListener !== 'function') return
      document.addEventListener(type, fn, true)
      const item = { target: document, type: type, fn: fn, capture: true }
      state.annotPopDocListeners.push(item)
      state.domListeners.push(item)
    }

    function unbindAnnotDoc () {
      for (let i = 0; i < state.annotPopDocListeners.length; i++) {
        const item = state.annotPopDocListeners[i]
        try { item.target.removeEventListener(item.type, item.fn, true) } catch (err) {}
        const at = state.domListeners.indexOf(item)
        if (at >= 0) state.domListeners.splice(at, 1)
      }
      state.annotPopDocListeners.length = 0
    }

    /**
     * 关掉编辑气泡。
     * 默认会先 flush：用户在气泡里改了字、还没到防抖时间就点外面 / 按 Esc，也不能丢。
     * @param {{keepChanges?:boolean}} [options] keepChanges === false 时丢弃未保存的改动（删除时用）
     */
    function closeAnnotPop (options) {
      const opts = options || {}
      const flush = state.annotPopFlush
      state.annotPopFlush = null          // 先摘掉，避免 flush 过程中又触发关闭
      if (flush && opts.keepChanges !== false) {
        try { flush() } catch (err) {}
      }
      unbindAnnotDoc()
      const wrap = state.annotPop
      state.annotPop = null
      state.annotPopId = ''
      try {
        if (wrap && wrap.parentNode) wrap.parentNode.removeChild(wrap)
      } catch (err) {}
    }

    /** 气泡摆位：优先贴在锚点下方居中，贴边就翻到上方 / 夹进视口 */
    function placeAnnotPop (pop, anchor) {
      if (!pop) return
      const margin = 8
      const vw = (typeof window !== 'undefined' && window.innerWidth) || 1024
      const vh = (typeof window !== 'undefined' && window.innerHeight) || 768
      const width = pop.offsetWidth || 306
      const height = pop.offsetHeight || 200
      const baseX = anchor ? anchor.x : vw - width - 28
      const baseY = anchor ? anchor.y : Math.round(vh / 3)
      let left = baseX - width / 2
      let top = baseY + 14
      left = Math.min(Math.max(margin, left), Math.max(margin, vw - width - margin))
      if (top + height > vh - margin) top = Math.max(margin, baseY - height - 14)
      pop.style.left = Math.round(left) + 'px'
      pop.style.top = Math.round(top) + 'px'
    }

    /**
     * 打开批注编辑气泡。
     * @param {object} item 批注（draft = true 时是还没入库的新批注）
     * @param {{draft?:boolean, anchor?:{x:number,y:number}}} options
     */
    function openAnnotPop (item, options) {
      if (state.destroyed || state.tornDown || !item) return
      closeAnnotPop()
      const o = options || {}
      const isDraft = !!o.draft
      const draft = {
        id: item.id,
        type: item.type === 'note' ? 'note' : 'highlight',
        page: Math.max(1, Math.round(Number(item.page) || 1)),
        color: normalizeColor(item.color) || ANNOT_DEFAULT_COLOR,
        rects: Array.isArray(item.rects) ? item.rects.slice(0, ANNOT_MAX_RECTS) : [],
        point: item.point || null,
        quote: String(item.quote || '').slice(0, ANNOT_QUOTE_MAX),
        note: String(item.note || '').slice(0, ANNOT_NOTE_MAX),
        createdAt: item.createdAt || nowStamp(),
        updatedAt: item.updatedAt || ''
      }
      const isNote = draft.type === 'note'

      const wrap = el('div.lv-annot-popwrap')
      const pop = el('div.lv-annot-pop', {
        role: 'dialog',
        'aria-modal': 'false',
        'aria-label': isDraft ? (isNote ? '新建便签' : '编辑荧光笔') : '编辑批注'
      })

      const head = el('div.lv-annot-pop-head', null, [
        el('b', { text: isNote ? '便签' : '荧光笔' }),
        el('span', { text: '· 第 ' + draft.page + ' 页' }),
        el('span', { style: 'margin-left:auto', text: isDraft ? '新批注' : '自动保存' })
      ])
      pop.appendChild(head)

      if (draft.quote) {
        pop.appendChild(el('div.lv-annot-quote', { text: '“' + draft.quote + '”' }))
      }

      const ta = el('textarea.lv-annot-input', {
        rows: '3',
        placeholder: isNote ? '写点什么…（边写边自动保存）' : '备注（可留空，边写边自动保存）',
        'aria-label': isNote ? '便签内容' : '荧光笔备注'
      })
      ta.value = draft.note
      pop.appendChild(ta)

      // 换色：内置 4 色 + 自定义（和工具栏的调色盘是同一份列表），选完立刻落库
      const swatches = el('div.lv-annot-swatches')
      annotPalette().forEach(hex => {
        const dot = el('button.lv-annot-swatch', {
          type: 'button',
          'data-color': hex,
          title: hex,
          'aria-label': '用颜色 ' + hex,
          'aria-pressed': hex === draft.color ? 'true' : 'false'
        })
        dot.style.background = hex
        if (hex === draft.color) dot.classList.add('active')
        dot.onclick = () => {
          draft.color = hex
          const all = swatches.children || []
          for (let i = 0; i < all.length; i++) {
            const on = all[i].getAttribute('data-color') === hex
            all[i].classList.toggle('active', on)
            all[i].setAttribute('aria-pressed', on ? 'true' : 'false')
          }
          applyChanges()          // 换色也立刻落库，不用等关闭
        }
        swatches.appendChild(dot)
      })

      const foot = el('div.lv-annot-pop-foot')
      let armed = false
      let armTimer = 0
      // 删除按钮一直有：新便签如果还没落库（没写字），两步确认后就是「关掉这张空便签」
      const del = el('button.lv-annot-btn.danger', { type: 'button', text: '删除' })
      del.onclick = () => {
        if (!armed) {
          armed = true
          del.classList.add('armed')
          del.textContent = '确认删除'
          if (armTimer) clearTimeout(armTimer)
          armTimer = setTimeout(() => {
            armed = false
            del.classList.remove('armed')
            del.textContent = '删除'
          }, 2600)
          return
        }
        if (!persisted) { closeAnnotPop({ keepChanges: false }); return }
        deleteAnnotation(draft.id)
      }
      foot.appendChild(del)
      foot.appendChild(swatches)

      /* ---- 自动保存 ---- */
      /* 规则：
           · 输入停下 500ms 就存一次（不用点按钮）；
           · 关掉气泡（点外面 / Esc / 「完成」/ 切到别的批注）时立刻补存，绝不丢最后几个字；
           · 新便签还没写内容就不建记录，免得留下一堆空便签；
           · 换颜色也立刻存。 */
      const status = el('span.lv-annot-status', { text: isDraft ? '输入即自动保存' : '已自动保存' })
      if (!isDraft) status.classList.add('saved')
      const actions = el('div.right')
      actions.appendChild(status)
      const done = el('button.lv-annot-btn', { type: 'button', text: '完成', title: '关闭（内容已自动保存）' })
      done.onclick = () => closeAnnotPop()
      actions.appendChild(done)
      foot.appendChild(actions)

      let savedText = draft.note        // 上一次真正落库的正文
      let savedColor = draft.color
      let persisted = !isDraft          // 是否已在内存数组里（新便签第一次落库后变 true）
      let saveTimer = 0

      /** 把当前编辑结果写进内存 + 通知调用方落库；返回是否真的改了东西 */
      function applyChanges () {
        if (state.destroyed || state.tornDown) return false
        const raw = String(ta.value || '').replace(/\r\n/g, '\n').slice(0, ANNOT_NOTE_MAX)
        const text = raw.trim()
        if (!persisted && !text) {                 // 空便签：等真的写了再建记录
          status.textContent = '输入即自动保存'
          status.classList.remove('saved')
          return false
        }
        if (persisted && text === savedText && draft.color === savedColor) {
          status.textContent = '已自动保存'
          status.classList.add('saved')
          return false
        }
        const next = {
          id: draft.id,
          type: draft.type,
          page: draft.page,
          color: draft.color,
          rects: draft.rects,
          point: draft.type === 'note' ? draft.point : null,
          quote: draft.quote,
          note: text,
          createdAt: draft.createdAt,
          updatedAt: nowStamp()
        }
        const index = state.annotations.findIndex(a => a.id === next.id)
        if (index >= 0) state.annotations[index] = next
        else state.annotations.push(next)
        const firstSave = !persisted
        persisted = true
        savedText = text
        savedColor = next.color
        paintPageAnnotations(next.page)
        renderAnnotPanel()
        syncAnnotCount()
        emitAnnotations()                          // detail 那边负责真正的 PUT
        status.textContent = '已自动保存'
        status.classList.add('saved')
        if (firstSave) flashAnnotation(next.id, next.page)
        return true
      }

      /** 立刻把待存的改动写掉（防抖计时器还在排队时用） */
      function flushNow () {
        if (saveTimer) {
          try { clearTimeout(saveTimer) } catch (err) {}
          saveTimer = 0
        }
        try { applyChanges() } catch (err) {}
      }
      state.annotPopFlush = flushNow               // closeAnnotPop 会先调它

      ta.oninput = () => {
        status.textContent = '保存中…'
        status.classList.remove('saved')
        if (saveTimer) {
          try { clearTimeout(saveTimer) } catch (err) {}
        }
        saveTimer = setTimeout(() => {
          saveTimer = 0
          try { applyChanges() } catch (err) {}
        }, 500)
      }

      pop.appendChild(foot)
      wrap.appendChild(pop)
      wrap.onclick = ev => { if (ev.target === wrap) closeAnnotPop() }

      // 先隐藏再插入，量完尺寸再摆位，避免出现在左上角闪一下
      pop.style.visibility = 'hidden'
      document.body.appendChild(wrap)
      state.annotPop = wrap
      state.annotPopId = draft.id
      placeAnnotPop(pop, o.anchor || null)
      pop.style.visibility = ''

      ta.onkeydown = ev => {
        if (ev.key === 'Escape' || ev.key === 'Esc') {
          ev.preventDefault()
          ev.stopPropagation()
          closeAnnotPop()          // 关闭前会 flush，不用另存
          return
        }
        if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
          ev.preventDefault()
          closeAnnotPop()
        }
      }
      bindAnnotDoc('keydown', ev => {
        if (ev.key !== 'Escape' && ev.key !== 'Esc') return
        if (state.annotPop !== wrap) return
        if (typeof ev.preventDefault === 'function') ev.preventDefault()
        closeAnnotPop()
      })
      try {
        ta.focus()
        const end = ta.value.length
        if (typeof ta.setSelectionRange === 'function') ta.setSelectionRange(end, end)
      } catch (err) {}
    }

    /* ---- 批注列表（右侧抽屉：展示 / 定位 / 编辑 / 删除） ---- */

    function syncAnnotCount () {
      if (!ui.btnAnnotList) return
      const total = state.annotations.length
      ui.btnAnnotList.textContent = total ? '批注 ' + total : '批注'
      ui.btnAnnotList.title = total
        ? '共 ' + total + ' 条批注（点击展开列表，再点可定位）'
        : '还没有批注：用「荧光笔」选文字，或用「便签」点页面'
    }

    function renderAnnotPanel () {
      if (!ui.annotList) return
      const list = state.annotations.slice().sort((a, b) => {
        const byPage = Number(a.page) - Number(b.page)
        if (byPage) return byPage
        return String(a.createdAt || '').localeCompare(String(b.createdAt || ''))
      })
      if (!list.length) {
        ui.annotList.replaceChildren(el('div.lv-annot-empty', {
          text: '还没有批注。\n用工具栏的「荧光笔」选中文字，\n或用「便签」在页面上点一下。'
        }))
        return
      }
      const rows = []
      list.forEach(item => {
        const quote = String(item.quote || '')
        const note = String(item.note || '')
        const kindLabel = item.type === 'note' ? '便签' : '荧光笔'
        const row = el('div.lv-annot-item', {
          'data-annot-id': item.id,
          'data-kind': item.type,
          title: '点击定位到第 ' + item.page + ' 页的这一处（' + kindLabel + '）'
        }, [
          el('div.meta', null, [
            el('div.lv-annot-page', { text: 'P' + item.page }),
            // 图标按类型区分：荧光笔 = 一支笔（笔头取该条的颜色），便签 = 便签纸 + 单行
            el('div.lv-annot-kind', { 'aria-hidden': 'true' }, [annotIcon(item.type, item.color)])
          ]),
          el('div.body', null, [
            quote ? el('div.lv-annot-quote-text', { text: quote }) : null,
            note ? el('div.lv-annot-note-text', { text: note }) : (quote ? null : el('div.lv-annot-note-text', { text: '（空）' }))
          ]),
          el('div.acts', null, [
            el('button.del', { type: 'button', text: '✎', title: '打开这条批注（可改备注 / 换颜色）', 'aria-label': '打开这条批注' }),
            el('button.del', { type: 'button', text: '✕', title: '删除这条批注', 'aria-label': '删除这条批注' })
          ])
        ])
        if (state.annotFlashId && state.annotFlashId === item.id) row.classList.add('active')
        row.onclick = () => locateAnnotation(item.id)

        const acts = row.lastChild
        const editBtn = acts.children[0]
        const del = acts.children[1]
        editBtn.onclick = ev => {
          if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
          const node = findAnnotNode(item.id)
          const rect = node ? clientRectOf(node) : null
          openAnnotPop(item, {
            anchor: rect ? { x: rect.left + rect.width / 2, y: rect.top + rect.height } : null
          })
        }
        // ✕ 删除：跟气泡里一样的两步确认（列表里误点的代价太高）
        let armed = false
        del.onclick = ev => {
          if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
          if (armed) { deleteAnnotation(item.id); return }
          armed = true
          del.textContent = '✕?'
          del.style.color = 'var(--danger, #b4453a)'
          setTimeout(() => {
            if (!del.parentNode) return
            armed = false
            del.textContent = '✕'
            del.style.color = ''
          }, 2600)
        }
        rows.push(row)
      })
      ui.annotList.replaceChildren.apply(ui.annotList, rows)
    }

    function showAnnotPanel (on) {
      state.annotPanelOpen = !!on
      if (ui.annotPanel) ui.annotPanel.hidden = !state.annotPanelOpen
      if (ui.btnAnnotList) {
        ui.btnAnnotList.classList.toggle('active', state.annotPanelOpen)
        ui.btnAnnotList.setAttribute('aria-pressed', state.annotPanelOpen ? 'true' : 'false')
      }
      if (state.annotPanelOpen) renderAnnotPanel()
    }

    /* ---- 标注模式（荧光笔 / 便签） ---- */

    function setAnnotMode (mode) {
      const want = mode === 'highlight' || mode === 'note' ? mode : null
      state.annotMode = want && want === state.annotMode ? null : want    // 再点一次同一个按钮 = 退出
      state.annotPress = null
      if (ui.root) {
        ui.root.classList.toggle('lv-annot-picking', state.annotMode === 'highlight')
        ui.root.classList.toggle('lv-annot-pinning', state.annotMode === 'note')
      }
      if (ui.annotHint) {
        ui.annotHint.hidden = !state.annotMode
        if (state.annotMode === 'highlight') ui.annotHint.textContent = '荧光笔：在 PDF 上选中文字即可高亮（Esc 退出）'
        else if (state.annotMode === 'note') ui.annotHint.textContent = '便签：在 PDF 上点一下放便签（Esc 退出）'
      }
      syncAnnotButtons()
      if (!state.annotMode) closeAnnotPop()
    }

    function syncAnnotButtons () {
      if (ui.btnHighlight) {
        const on = state.annotMode === 'highlight'
        ui.btnHighlight.classList.toggle('active', on)
        ui.btnHighlight.setAttribute('aria-pressed', on ? 'true' : 'false')
      }
      if (ui.btnNote) {
        const on = state.annotMode === 'note'
        ui.btnNote.classList.toggle('active', on)
        ui.btnNote.setAttribute('aria-pressed', on ? 'true' : 'false')
      }
      // 选了别的颜色就把两个图标重画一遍（笔头 / 便签线条跟着变），只在真的变了时动 DOM
      syncAnnotIcon(ui.highlightIcon, 'highlight')
      syncAnnotIcon(ui.noteIcon, 'note')
      syncAnnotCount()
    }

    /* ---- 颜色 / 调色盘 ---- */

    /** 选一个颜色（幂等）：圆点、两个图标、之后的批注都用它 */
    function setAnnotColor (hex) {
      const next = normalizeColor(hex) || ANNOT_DEFAULT_COLOR
      state.annotColor = next
      if (ui.colorDot) ui.colorDot.style.background = next
      if (ui.btnColor) ui.btnColor.title = '批注颜色 ' + next + '（点开可以选色 / 加自己的颜色）'
      syncAnnotButtons()
    }

    /** 把一个色值加进自定义调色盘（重复的不加；满了就顶掉最早的那个） */
    function addCustomColor (hex) {
      const value = normalizeColor(hex)
      if (!value) return false
      const list = readCustomColors()
      if (list.indexOf(value) >= 0) return true
      if (list.length >= ANNOT_CUSTOM_MAX) list.shift()
      list.push(value)
      writeCustomColors(list)
      return true
    }

    function removeCustomColor (hex) {
      const value = normalizeColor(hex)
      writeCustomColors(readCustomColors().filter(item => item !== value))
      if (state.annotColor === value) setAnnotColor(ANNOT_DEFAULT_COLOR)
      renderPalette()          // 已经用过这个颜色的批注保持原样，不动历史数据
    }

    /** 重画调色盘：内置 4 色 + 自定义（hover 出 ×）+ 「＋」取色 */
    function renderPalette () {
      if (!ui.palette) return
      const colors = annotPalette()
      const customCount = readCustomColors().length
      const nodes = []
      colors.forEach(hex => {
        const custom = !isBuiltinColor(hex)
        const dot = el('button.lv-palette-dot', {
          type: 'button',
          title: custom ? hex + '（自定义，点右上角 × 可删除）' : hex,
          'aria-label': '颜色 ' + hex,
          'aria-pressed': hex === state.annotColor ? 'true' : 'false'
        })
        dot.style.background = hex
        if (hex === state.annotColor) dot.classList.add('active')
        if (custom) {
          dot.classList.add('custom')
          const remove = el('span.lv-palette-del', { text: '×', 'aria-hidden': 'true' })
          remove.onclick = ev => {
            if (ev && typeof ev.stopPropagation === 'function') ev.stopPropagation()
            removeCustomColor(hex)
          }
          dot.appendChild(remove)
        }
        dot.onclick = () => {
          setAnnotColor(hex)
          closePalette()
        }
        nodes.push(dot)
      })
      const full = customCount >= ANNOT_CUSTOM_MAX
      const add = el('button.lv-palette-add', {
        type: 'button', text: '＋',
        title: full ? ('自定义颜色最多 ' + ANNOT_CUSTOM_MAX + ' 个，先删一个再加') : '用取色器加一个自己的颜色',
        disabled: full ? true : null,
        'aria-label': '添加自定义颜色'
      })
      add.onclick = () => {
        if (full) return
        try { ui.colorInput.value = state.annotColor } catch (err) {}
        try { ui.colorInput.click() } catch (err) {}
      }
      nodes.push(add)
      nodes.push(el('div.lv-palette-hint', { text: full ? '已达自定义上限' : '点 ＋ 取色器加颜色' }))
      ui.palette.replaceChildren.apply(ui.palette, nodes)
    }

    /** 调色盘贴到颜色按钮下方；右边放不下就往左收，别顶出工具栏 */
    function placePalette () {
      if (!ui.palette || !ui.btnColor) return
      const rect = clientRectOf(ui.btnColor)
      const base = clientRectOf(ui.toolbar)
      if (!rect || !base) return
      const width = ui.palette.offsetWidth || 158
      const maxLeft = Math.max(4, base.width - width - 4)
      const left = Math.min(Math.max(4, rect.left - base.left), maxLeft)
      ui.palette.style.left = Math.round(left) + 'px'
      ui.palette.style.top = Math.round(rect.top - base.top + rect.height + 6) + 'px'
    }

    function bindPaletteDoc () {
      if (typeof document.addEventListener !== 'function') return
      const onDown = ev => {
        const target = ev && ev.target
        if (!target) return
        if (ui.palette && ui.palette.contains(target)) return
        if (ui.btnColor && ui.btnColor.contains(target)) return
        closePalette()
      }
      const onKey = ev => {
        if (ev && (ev.key === 'Escape' || ev.key === 'Esc')) closePalette()
      }
      document.addEventListener('mousedown', onDown, true)
      document.addEventListener('keydown', onKey, true)
      const items = [
        { target: document, type: 'mousedown', fn: onDown, capture: true },
        { target: document, type: 'keydown', fn: onKey, capture: true }
      ]
      for (let i = 0; i < items.length; i++) {
        state.paletteListeners.push(items[i])
        state.domListeners.push(items[i])
      }
    }

    function unbindPaletteDoc () {
      for (let i = 0; i < state.paletteListeners.length; i++) {
        const item = state.paletteListeners[i]
        try { item.target.removeEventListener(item.type, item.fn, true) } catch (err) {}
        const at = state.domListeners.indexOf(item)
        if (at >= 0) state.domListeners.splice(at, 1)
      }
      state.paletteListeners.length = 0
    }

    function togglePalette (force) {
      if (state.destroyed || state.tornDown) return
      const open = force === undefined ? !state.paletteOpen : !!force
      if (!open) { closePalette(); return }
      if (state.paletteOpen) return
      state.paletteOpen = true
      renderPalette()
      if (ui.palette) ui.palette.hidden = false
      placePalette()
      if (ui.btnColor) ui.btnColor.setAttribute('aria-expanded', 'true')
      bindPaletteDoc()
    }

    function closePalette () {
      if (!state.paletteOpen) return
      state.paletteOpen = false
      if (ui.palette) ui.palette.hidden = true
      if (ui.btnColor) ui.btnColor.setAttribute('aria-expanded', 'false')
      unbindPaletteDoc()
    }

    /** 图标颜色跟随当前选择；用 data-color 记住上次画的是哪一色，避免每次 sync 都重建节点 */
    function syncAnnotIcon (host, kind) {
      if (!host) return
      if (host.getAttribute('data-color') === state.annotColor && host.firstChild) return
      host.setAttribute('data-color', state.annotColor)
      try { host.replaceChildren(annotIcon(kind, state.annotColor)) } catch (err) {}
    }

    /** 按下：便签模式要把「按下之前的选区」抢下来（浏览器随后会清掉它） */
    function onAnnotDown (ev) {
      if (state.destroyed || !state.annotMode) return
      if (ev.button !== undefined && ev.button !== 0) return
      if (state.annotMode !== 'note') { state.annotPress = null; return }
      const pt = eventPoint(ev)
      state.annotPress = pt ? { x: pt.x, y: pt.y, snap: selectionSnapshot() } : null
    }

    /** 松开：荧光笔 → 把选区做成高亮；便签 → 在落点开一个新便签 */
    function onAnnotUp (ev) {
      if (state.destroyed || !state.annotMode) return
      const target = ev && ev.target
      // 点在已有便签图标上时交给图标自己的 click，不再叠一个新的
      if (target && typeof target.closest === 'function' && target.closest('.lv-annot-note')) {
        state.annotPress = null
        return
      }
      if (state.annotMode === 'highlight') {
        createHighlightFromSelection()
        return
      }
      const press = state.annotPress
      state.annotPress = null
      const pt = eventPoint(ev)
      if (!pt) return
      // 明显拖动 = 用户在选文字（想给便签配引文），不要弹出气泡
      if (press && (Math.abs(pt.x - press.x) > ANNOT_MIN_DRAG || Math.abs(pt.y - press.y) > ANNOT_MIN_DRAG)) return
      const infos = pageInfos()
      const page = pageAtPoint(infos, pt.x, pt.y)
      if (!page) return
      const point = normPoint(pt, page.rect)
      if (!point) return
      openAnnotPop({
        id: annotId(),
        type: 'note',
        page: page.pageNumber,
        color: state.annotColor,
        rects: [],
        point: point,
        quote: press && press.snap ? press.snap.text : '',
        note: '',
        createdAt: nowStamp(),
        updatedAt: ''
      }, { draft: true, anchor: pt })
    }

    /** 某一页渲染完成 → 把那页的批注补画上去（缩放/重排后 pdf.js 会重发这个事件） */
    function onPageRendered (evt) {
      if (state.destroyed || state.tornDown || !evt) return
      const pageNumber = Math.round(Number(evt.pageNumber))
      if (!(pageNumber > 0)) return
      paintPageAnnotations(pageNumber)
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

      // 批注：荧光笔（选文字）/ 便签（点位置）/ 颜色 / 批注列表
      // 图标：荧光笔 = 一支笔（笔头随所选颜色），便签 = 便签纸 + 一道横线（单行）
      ui.highlightIcon = el('span.lv-pdf-icon', { 'aria-hidden': 'true' }, [annotIcon('highlight', state.annotColor)])
      ui.noteIcon = el('span.lv-pdf-icon', { 'aria-hidden': 'true' }, [annotIcon('note', state.annotColor)])
      ui.btnHighlight = el('button.lv-pdf-btn', {
        type: 'button', 'aria-pressed': 'false',
        title: '荧光笔：开着的时候在 PDF 上选中文字，就会画上高亮'
      }, [ui.highlightIcon, '荧光笔'])
      ui.btnNote = el('button.lv-pdf-btn', {
        type: 'button', 'aria-pressed': 'false',
        title: '便签：开着的时候在 PDF 上点一下，就贴一张便签'
      }, [ui.noteIcon, '便签'])
      // 颜色：一颗圆点按钮，点开是调色盘（内置 4 色 + 自定义 + 「＋」自己取色）
      ui.colorDot = el('span.lv-pdf-color-dot')
      ui.btnColor = el('button.lv-pdf-color', {
        type: 'button', 'aria-expanded': 'false', 'aria-haspopup': 'true',
        title: '批注颜色（点开可以选色 / 加自己的颜色）', 'aria-label': '批注颜色'
      }, [ui.colorDot])
      ui.palette = el('div.lv-pdf-palette', { hidden: true, role: 'group', 'aria-label': '批注调色盘' })
      // 系统取色器：调色盘里的「＋」借它取任意颜色（原生控件省事且一定有）
      ui.colorInput = el('input', {
        type: 'color', value: ANNOT_DEFAULT_COLOR,
        style: 'position:absolute;width:1px;height:1px;opacity:0;pointer-events:none;left:-10px;top:-10px',
        'aria-hidden': 'true', tabindex: '-1'
      })
      ui.btnAnnotList = el('button.lv-pdf-btn', {
        type: 'button', 'aria-pressed': 'false', text: '批注',
        title: '展开批注列表：点一条就跳到它在哪一页'
      })

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
        el('div.lv-pdf-group', null, [ui.btnHighlight, ui.btnNote, ui.btnColor]),
        el('span.lv-pdf-sep', { 'aria-hidden': 'true' }),
        el('div.lv-pdf-group.grow', null, [ui.searchInput, ui.count, ui.btnPrevMatch, ui.btnNextMatch]),
        el('div.lv-pdf-group.right', null, [ui.btnAnnotList, ui.btnRegion, ui.btnInvert, ui.linkDownload, ui.linkOpen]),
        // 调色盘 + 取色器：挂在工具栏里（工具栏 position:relative），弹出时贴着颜色按钮
        ui.palette,
        ui.colorInput
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

      // 批注列表抽屉 + 模式提示条（都在 body 里，不参与滚动）
      ui.annotList = el('div.lv-annot-list')
      ui.annotPanel = el('div.lv-annot-panel', { hidden: true }, [
        el('div.lv-annot-panel-head', null, [
          el('span', { text: '批注' }),
          el('div.right', null, [el('button.lv-pdf-btn', {
            type: 'button', text: '关闭',
            title: '收起批注列表',
            onclick: () => showAnnotPanel(false)
          })])
        ]),
        ui.annotList
      ])
      ui.annotHint = el('div.lv-annot-hint', { hidden: true })

      ui.body = el('div.lv-pdf-body', { style: 'position:relative;flex:1 1 auto;min-height:0' },
        [ui.scroll, ui.annotPanel, ui.annotHint, ui.regionLayer, ui.status])
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

      // 批注：两个模式按钮 + 颜色圆点（点开调色盘）；mousedown/mouseup 挂在滚动容器上（事件从文本层冒上来）
      ui.btnHighlight.onclick = function () { setAnnotMode('highlight') }
      ui.btnNote.onclick = function () { setAnnotMode('note') }
      ui.btnColor.onclick = function () { togglePalette() }
      ui.colorInput.onchange = function () {
        const hex = normalizeColor(ui.colorInput.value)
        if (!hex) return
        addCustomColor(hex)
        setAnnotColor(hex)
        renderPalette()             // 让新颜色立刻出现在调色盘里
      }
      ui.btnAnnotList.onclick = function () { showAnnotPanel(!state.annotPanelOpen) }
      onGlobal(ui.scroll, 'mousedown', onAnnotDown)
      onGlobal(ui.scroll, 'mouseup', onAnnotUp)
      setAnnotColor(state.annotColor)   // 初始化圆点 / 两个图标的颜色
      renderAnnotPanel()
      syncAnnotButtons()

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
      setAnnotations(opts.annotations || [])   // 批注数据由调用方给（可能还没加载完，为空也照常）

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
        if (state.destroyed) return fail('阅读器已关闭')
        viewerMod = await import(pdfUrls.viewer)
      } catch (err) {
        return fail(DEP_HINT + '（' + errText(err) + '）')
      }
      if (state.destroyed) return fail('阅读器已关闭')
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
      if (state.destroyed) return fail('阅读器已关闭')
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
          pagerendered: onPageRendered,
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
        getViewState: () => ({ page: state.pdfViewer?.currentPageNumber || 1,
          scale: state.pdfViewer?.currentScale, preset: state.presetScale }),
        isInverted: isInverted,
        // 批注：读写整组 / 跳到某一条（数据本身仍由调用方落库）
        closeAnnotationEditor: () => closeAnnotPop(),
        getAnnotations: getAnnotations,
        setAnnotations: setAnnotations,
        goToAnnotation: locateAnnotation
      }
    } catch (err) {
      return fail('内置阅读器初始化失败：' + errText(err))
    }
  }

  // 反色偏好的读写也放出来：阅读器在桩环境里挂不起来时，
  // 测试仍能直接验证「点过什么、下次读回什么」这段逻辑。
  export { mount, readInvertPref, writeInvertPref }
  export default { mount, readInvertPref, writeInvertPref }
