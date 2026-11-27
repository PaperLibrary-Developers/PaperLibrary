/* PDF 阅读器内置样式表注入（按 id 去重，只插一次） */

export const STYLE_ID = 'lv-pdfviewer-style'
export const CSS_LINK_ID = 'lv-pdfviewer-css'

export const STYLE_TEXT = `
.lv-pdf-translation-preview {
  position: fixed; inset: auto 16px 16px auto; margin: 0; z-index: 120;
  width: min(410px, calc(100vw - 32px)); max-height: min(62vh, 640px);
  box-sizing: border-box; padding: 15px; overflow: hidden;
  border: 1px solid var(--border); border-radius: var(--radius-sm, 8px);
  background: var(--surface, #fffefb); color: var(--text, #26251f);
  box-shadow: 0 12px 44px #0003;
}
.lv-pdf-translation-preview[open] { display: flex; flex-direction: column; }
.lv-pdf-translation-preview header, .lv-pdf-region-preview-actions {
  display: flex; align-items: center; justify-content: space-between; gap: 8px;
}
.lv-pdf-translation-preview header, .lv-pdf-translation-preview footer { flex: none; }
.lv-pdf-translation-preview .lv-pdf-region-close {
  color: var(--surface); background: var(--danger); border-color: var(--danger);
}
.lv-pdf-translation-preview .lv-pdf-region-close:hover { filter: brightness(.9); }
.lv-pdf-translation-preview h3, .lv-pdf-translation-preview h4 { margin: 0; }
.lv-pdf-translation-preview [hidden] { display: none; }
.lv-pdf-translation-preview h4 { font-size: 13px; }
.lv-pdf-region-preview-text { min-height: 0; overflow: auto; margin-block: 12px; }
.lv-pdf-region-preview-text section + section {
  margin-top: 10px; border-top: 1px solid var(--border); padding-top: 10px;
}
.lv-pdf-region-preview-text p, .lv-pdf-region-status {
  font-size: 13px; margin: 6px 0; overflow-wrap: anywhere;
}
.lv-pdf-region-status { color: var(--muted); }
.lv-pdf-region-preview-actions { justify-content: flex-end; flex-wrap: wrap; }

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
  position: relative;
  display: flex;
  /* 一行排完为默认目标：整体居中，放不下才折行（不再固定两排） */
  flex-wrap: wrap;
  align-items: center;
  justify-content: center;
  gap: 5px;
  padding: 5px 5px;
  background: var(--surface-2, #f8f7f3);
  border-bottom: 1px solid var(--border, #e2dfd5);
}
.lv-pdf-viewer .lv-pdf-group { display: flex; align-items: center; gap: 2px; min-width: 0; flex-wrap: nowrap; }

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
/* 适宽 / 适页：共用一层描边和圆角，中间一道分隔线，点哪半边就是哪种 */
.lv-pdf-viewer .lv-pdf-seg {
  display: inline-flex; align-items: stretch; overflow: hidden;
  background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5);
  border-radius: var(--radius-sm, 8px);
}
.lv-pdf-viewer .lv-pdf-seg-btn {
  border: 0; background: transparent; color: var(--text, #26251f);
  padding: 4px 6px; font-size: 12.5px; line-height: 1.6;
  cursor: pointer; white-space: nowrap;
  transition: background .15s, color .15s;
}
.lv-pdf-viewer .lv-pdf-seg-btn + .lv-pdf-seg-btn { border-left: 1px solid var(--border, #e2dfd5); }
.lv-pdf-viewer .lv-pdf-seg-btn:hover { background: var(--surface-3, #efeee8); }
.lv-pdf-viewer .lv-pdf-seg-btn.active {
  background: var(--accent-soft, #e4efea); color: var(--accent-text, #235648); font-weight: 600;
}
.lv-pdf-viewer .lv-pdf-seg-btn:focus-visible { outline: 2px solid var(--accent, #2f6f5e); outline-offset: -2px; }

/* 撤销 / 重做：一个连体框，两个图标按钮共用一层描边；不可用时整格变淡但不塌陷 */
.lv-pdf-viewer .lv-pdf-history {
  display: inline-flex; align-items: stretch;
  background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5);
  border-radius: var(--radius-sm, 8px);
}
.lv-pdf-viewer .lv-pdf-history > .lv-pdf-btn {
  border: 0; border-radius: 0; background: transparent;
  height: 30px; padding: 6px 3px; gap: 0;
}
.lv-pdf-viewer .lv-pdf-history > .lv-pdf-btn + .lv-pdf-btn { border-left: 1px solid var(--border, #e2dfd5); }
.lv-pdf-viewer .lv-pdf-history > .lv-pdf-btn:first-child { border-radius: calc(var(--radius-sm, 8px) - 1px) 0 0 calc(var(--radius-sm, 8px) - 1px); }
.lv-pdf-viewer .lv-pdf-history > .lv-pdf-btn:last-child { border-radius: 0 calc(var(--radius-sm, 8px) - 1px) calc(var(--radius-sm, 8px) - 1px) 0; }
.lv-pdf-viewer .lv-pdf-history > .lv-pdf-btn:hover:not([disabled]) { background: var(--surface-3, #efeee8); }
.lv-pdf-viewer .lv-pdf-history > .lv-pdf-btn[disabled] { opacity: .38; }

/* 荧光笔 · 橡皮擦 · 便签 · 批注：一个连体框，中间用分隔线断开（和「适宽/适页」同一套观感） */
.lv-pdf-viewer .lv-pdf-toolseg {
  display: inline-flex; align-items: stretch;
  background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5);
  border-radius: var(--radius-sm, 8px);
}
.lv-pdf-viewer .lv-pdf-toolseg > .lv-pdf-btn {
  border: 0; border-radius: 0; background: transparent;
  height: 30px; padding: 6px 6px; gap: 0;
}
.lv-pdf-viewer .lv-pdf-toolseg > .lv-pdf-btn + .lv-pdf-btn { border-left: 1px solid var(--border, #e2dfd5); }
/* 不做 overflow:hidden：首尾按钮自己带圆角，hover 的底色也不会溢出到框外 */
.lv-pdf-viewer .lv-pdf-toolseg > .lv-pdf-btn:first-child { border-radius: calc(var(--radius-sm, 8px) - 1px) 0 0 calc(var(--radius-sm, 8px) - 1px); }
.lv-pdf-viewer .lv-pdf-toolseg > .lv-pdf-btn:last-child { border-radius: 0 calc(var(--radius-sm, 8px) - 1px) calc(var(--radius-sm, 8px) - 1px) 0; }
.lv-pdf-viewer .lv-pdf-toolseg > .lv-pdf-btn:hover { background: var(--surface-3, #efeee8); }
.lv-pdf-viewer .lv-pdf-toolseg > .lv-pdf-btn.active {
  background: var(--accent-soft, #e4efea); color: var(--accent-text, #235648);
}
/* 只有一个图标的按钮：左右内边距收窄、上下补足，高度与文字按钮一致 */
.lv-pdf-viewer .lv-pdf-iconbtn { padding: 6px 7px; gap: 0; }

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
.lv-pdf-viewer .lv-pdf-page { flex: 0 0 auto; width: 28px; padding: 4px 1px; text-align: center; font-variant-numeric: tabular-nums; }
/* width: 0 让输入框的固有宽度不参与最小宽度计算：搜索组因此能一路压到按钮的宽度，
   而不会被输入框默认的 20 字符宽度顶住（否则窄屏下按钮会溢出工具栏）。 */
.lv-pdf-viewer .lv-pdf-search { flex: 1 1 110px; width: 0; min-width: 70px; max-width: 260px; }

.lv-pdf-viewer .lv-pdf-num,
.lv-pdf-viewer .lv-pdf-count,
.lv-pdf-viewer .lv-pdf-zoom {
  flex: 0 0 auto;
  font-size: 12px; color: var(--muted, #75726a);
  white-space: nowrap; font-variant-numeric: tabular-nums;
}
.lv-pdf-viewer .lv-pdf-count { min-width: 80px; }
/* 没有搜索结果时不留 80px 空位：窄屏里那段空白会把「上一条 / 下一条」推到行尾 */
.lv-pdf-viewer .lv-pdf-count:empty { min-width: 0; }
.lv-pdf-viewer .lv-pdf-zoom { min-width: 34px; text-align: center; }

/* ---------- 阅读区 ---------- */
.lv-pdf-viewer .lv-pdf-body { position: relative; display: flex; flex: 1 1 auto; min-height: 0; overflow: hidden; }
.lv-pdf-viewer .lv-pdf-main { position: relative; flex: 1 1 auto; min-width: 0; overflow: hidden; }
.lv-pdf-viewer .lv-pdf-scroll {
  position: absolute; inset: 0;
  overflow: auto;
  overscroll-behavior: contain;
  background: var(--surface-2, #f8f7f3);
  scrollbar-width: thin;
  scrollbar-color: var(--border-strong, #cfcabb) transparent;
  /* 把页面内容装进自己的层叠上下文：pdf.js 的 .linkAnnotation 带 z-index:12，
     不隔离时会盖过侧边的批注面板（z-index:3），点批注项会被文档超链接吃掉。
     隔离后页面里再高的 z-index 也只在滚动区内比较，面板/框选层/状态层正常压在页面之上。 */
  isolation: isolate;
}
.lv-pdf-viewer .lv-pdf-scroll:focus { outline: none; }
.lv-pdf-viewer.lv-night .lv-pdf-scroll { background: var(--bg, #14161a); }
.lv-pdf-viewer .lv-pdf-scroll::-webkit-scrollbar { width: 10px; height: 10px; }
.lv-pdf-viewer .lv-pdf-scroll::-webkit-scrollbar-thumb {
  background: var(--border-strong, #cfcabb); border-radius: 99px;
  border: 3px solid transparent; background-clip: content-box;
}

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

.lv-pdf-viewer.lv-night .textLayer .highlight {
  --highlight-bg-color: rgb(224 172 75 / .35);
  --highlight-selected-bg-color: rgb(124 192 168 / .5);
}

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
.lv-pdf-viewer .lv-pdf-icon-frame {
  display: inline-block; width: 12px; height: 10px;
  border: 1.5px dashed currentColor; border-radius: 2px;
}
.lv-pdf-viewer .lv-pdf-region-layer {
  position: absolute; inset: 0; z-index: 4;
  cursor: crosshair;
  overflow: hidden;
  background: rgba(64, 60, 48, .10);
  touch-action: none;
  -webkit-user-select: none; user-select: none;
}
.lv-pdf-viewer.lv-night .lv-pdf-region-layer { background: rgba(0, 0, 0, .26); }
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

/* ---------- 左侧导航抽屉（批注 / 搜索共用） ----------
   宽度从 0 过渡到 320px：阅读区被同步挤窄，文档不会被侧栏盖住；
   只在 width 上做过渡，内容宽度固定，所以是「拉开」而不是「压扁」。 */
.lv-pdf-viewer .lv-pdf-drawer {
  flex: 0 0 auto; width: 0; min-width: 0;
  overflow: hidden;
  background: var(--surface, #fffefb);
  transition: width .26s cubic-bezier(.22, .61, .36, 1);
}
.lv-pdf-viewer .lv-pdf-drawer.is-open { width: 320px; }
/* 手机宽度下 PDF 栏本身只有 300 多像素：侧栏改为整屏，避免只剩一条缝 */
@media (max-width: 520px) {
  .lv-pdf-viewer .lv-pdf-drawer.is-open { width: 100%; }
  .lv-pdf-viewer .lv-pdf-drawer-inner { width: 100%; }
}
.lv-pdf-viewer .lv-pdf-drawer-inner {
  width: 320px; height: 100%; min-height: 0;
  display: flex; flex-direction: column;
  /* 描边画在内层：外层收起时宽度正好是 0，不会留 1px */
  border-right: 1px solid var(--border, #e2dfd5);
}
.lv-pdf-viewer .lv-pdf-drawer-head {
  flex: 0 0 auto; display: flex; align-items: center; gap: 6px;
  padding: 7px 8px 7px 8px;
  border-bottom: 1px solid var(--border, #e2dfd5);
}
.lv-pdf-viewer .lv-pdf-drawer-close {
  /* 标题在左、× 靠右 */
  flex: 0 0 auto; margin-left: auto; width: 24px; height: 24px; padding: 0;
  display: grid; place-items: center;
  font-size: 16px; line-height: 1; color: var(--muted, #75726a);
  background: transparent; border: 0; border-radius: 6px; cursor: pointer;
}
.lv-pdf-viewer .lv-pdf-drawer-close:hover { background: var(--surface-3, #efeee8); color: var(--text, #26251f); }
.lv-pdf-viewer .lv-pdf-drawer-title {
  font-size: 12.5px; font-weight: 600; color: var(--text, #26251f);
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.lv-pdf-viewer .lv-pdf-drawer-pane {
  flex: 1 1 auto; min-height: 0;
  display: flex; flex-direction: column;
}
.lv-pdf-viewer .lv-pdf-drawer-pane[hidden] { display: none; }
.lv-pdf-viewer .lv-pdf-drawer-searchrow {
  flex: 0 0 auto; display: flex; align-items: center; gap: 6px;
  padding: 8px; border-bottom: 1px solid var(--border, #e2dfd5);
}
.lv-pdf-viewer .lv-pdf-search-input {
  flex: 1 1 auto; min-width: 0;
  padding: 5px 8px; font-size: 12.5px; line-height: 1.6;
  color: var(--text, #26251f); background: var(--surface-2, #f8f7f3);
  border: 1px solid var(--border, #e2dfd5); border-radius: 6px; outline: none;
}
.lv-pdf-viewer .lv-pdf-search-input:focus { border-color: var(--accent, #2f6f5e); background: var(--surface, #fffefb); }
.lv-pdf-viewer .lv-pdf-search-go {
  flex: 0 0 auto; width: 28px; height: 28px; padding: 0;
  display: grid; place-items: center;
  color: var(--accent-text, #235648); background: var(--accent-soft, #e4efea);
  border: 1px solid rgba(47, 111, 94, .28); border-radius: 6px; cursor: pointer;
}
.lv-pdf-viewer .lv-pdf-search-go:hover { filter: brightness(.96); }
.lv-pdf-viewer .lv-pdf-search-meta {
  flex: 0 0 auto; display: flex; align-items: center; gap: 8px;
  padding: 7px 10px; font-size: 12px; color: var(--muted, #75726a);
  border-bottom: 1px solid var(--border, #e2dfd5);
}
.lv-pdf-viewer .lv-pdf-search-count {
  flex: 1 1 auto; min-width: 0;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
}
.lv-pdf-viewer .lv-pdf-search-steps { display: flex; gap: 4px; }
.lv-pdf-viewer .lv-pdf-search-step {
  width: 24px; height: 22px; padding: 0; display: grid; place-items: center;
  font-size: 12px; color: var(--text, #26251f); background: var(--surface, #fffefb);
  border: 1px solid var(--border, #e2dfd5); border-radius: 5px; cursor: pointer;
}
.lv-pdf-viewer .lv-pdf-search-step:hover:not([disabled]) { background: var(--surface-3, #efeee8); }
.lv-pdf-viewer .lv-pdf-search-step[disabled] { opacity: .4; cursor: default; }
.lv-pdf-viewer .lv-pdf-search-list {
  flex: 1 1 auto; min-height: 0; overflow: auto; padding: 6px;
  display: flex; flex-direction: column; gap: 4px;
  scrollbar-width: thin;
}
.lv-pdf-viewer .lv-pdf-hit {
  display: flex; gap: 8px; align-items: flex-start; width: 100%; text-align: left;
  padding: 7px 8px; font: inherit; font-size: 12px; line-height: 1.55;
  color: var(--text, #26251f); background: transparent;
  border: 1px solid transparent; border-radius: 6px; cursor: pointer;
}
.lv-pdf-viewer .lv-pdf-hit:hover { background: var(--surface-2, #f8f7f3); border-color: var(--border, #e2dfd5); }
.lv-pdf-viewer .lv-pdf-hit.is-active {
  background: var(--accent-soft, #e4efea); border-color: var(--accent, #2f6f5e);
  color: var(--accent-text, #235648);
}
.lv-pdf-viewer .lv-pdf-hit-page {
  flex: 0 0 auto; padding-top: 1px;
  font-size: 11px; color: var(--muted, #75726a); font-variant-numeric: tabular-nums;
}
.lv-pdf-viewer .lv-pdf-hit-text { flex: 1 1 auto; min-width: 0; word-break: break-word; }
.lv-pdf-viewer .lv-pdf-hit mark { padding: 0 1px; border-radius: 2px; background: #f7dd7d; color: #2a2410; }
.lv-pdf-viewer .lv-pdf-search-empty {
  padding: 20px 12px; text-align: center;
  font-size: 12.5px; line-height: 1.7; color: var(--muted, #75726a);
}

/* ============================ 批注：荧光笔 / 便签 ============================ */
.lv-pdf-viewer .lv-annot-layer {
  position: absolute; inset: 0; z-index: 2;
  pointer-events: none;
}
.lv-pdf-viewer .lv-annot-mark {
  position: absolute; pointer-events: none;
  border-radius: 2px;
}

.lv-pdf-viewer .lv-annot-flash { animation: lv-annot-flash .7s ease-out 2; }
@keyframes lv-annot-flash {
  0%   { box-shadow: 0 0 0 2px rgba(47, 111, 94, .85); }
  100% { box-shadow: 0 0 0 2px rgba(47, 111, 94, 0); }
}

.lv-pdf-viewer .lv-annot-note svg { width: 13px; height: 13px; display: block; }
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

.lv-pdf-viewer .lv-annot-hint {
  /* 放在底部：顶部区域要和取色条、工具栏错开 */
  position: absolute; left: 50%; bottom: 14px; transform: translateX(-50%);
  z-index: 3; max-width: calc(100% - 24px);
  padding: 4px 10px; font-size: 12px; line-height: 1.5;
  color: var(--accent-text, #235648); background: var(--accent-soft, #e4efea);
  border: 1px solid rgba(47, 111, 94, .28); border-radius: 99px;
  box-shadow: var(--shadow-1, 0 1px 2px rgba(35, 33, 25, .06));
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  pointer-events: none;
}

.lv-pdf-viewer.lv-annot-picking .lv-pdf-scroll { cursor: text; }
.lv-pdf-viewer.lv-annot-picking .lv-pdf-scroll .page { cursor: text; }
.lv-pdf-viewer.lv-annot-pinning .lv-pdf-scroll,
.lv-pdf-viewer.lv-annot-pinning .lv-pdf-scroll .page { cursor: copy; }
/* 橡皮擦：自定义光标（小橡皮 + 十字定位），光标热点在橡皮尖上 */
.lv-pdf-viewer.lv-annot-erasing .lv-pdf-scroll,
.lv-pdf-viewer.lv-annot-erasing .lv-pdf-scroll .page {
  cursor: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='26' height='26' viewBox='0 0 26 26'><g fill='none' stroke='%2326251f' stroke-width='1.6' stroke-linejoin='round'><path d='M4.4 16.2 12.8 7.8a2.4 2.4 0 0 1 3.4 0l3.6 3.6a2.4 2.4 0 0 1 0 3.4l-5 5H8.6z' fill='%23fffefb'/><path d='M9.6 11 15.4 16.8'/></g><path d='M3 21.6h18' stroke='%23f2c433' stroke-width='2.4' stroke-linecap='round'/></svg>") 4 21, cell;
}
/* 擦除时不让文字被选中，否则拖一路会拖出一个蓝色选区 */
.lv-pdf-viewer.lv-annot-erasing .lv-pdf-scroll { user-select: none; }

.lv-pdf-viewer .lv-pdf-icon,
.lv-pdf-viewer .lv-annot-kind {
  display: inline-flex; align-items: center; justify-content: center;
  flex: 0 0 auto; line-height: 0;
}
.lv-pdf-viewer .lv-pdf-icon { width: 18px; height: 18px; }
.lv-pdf-viewer .lv-annot-kind { width: 14px; height: 14px; }
.lv-pdf-viewer .lv-pdf-icon svg { display: block; width: 18px; height: 18px; }
.lv-pdf-viewer .lv-annot-kind svg { display: block; width: 14px; height: 14px; }
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

/* ---------- 荧光笔取色条（GoodNotes 风格：一排圆点，选中的加一圈） ----------
   挂在工具栏下方、跟着「荧光笔」按钮走；工具开着就一直显示，换色不用反复开关。 */
.lv-pdf-viewer .lv-pdf-inkbar {
  position: absolute; z-index: 6;
  display: flex; align-items: center; gap: 7px;
  padding: 7px 9px;
  background: var(--surface, #fffefb);
  border: 1px solid var(--border-strong, #cfcabb);
  border-radius: 999px;
  box-shadow: var(--shadow-2, 0 6px 22px rgba(35, 33, 25, .10), 0 2px 6px rgba(35, 33, 25, .06));
  transform-origin: 20% 0;
  animation: lv-inkbar-in .16s cubic-bezier(.22, .61, .36, 1);
}
.lv-pdf-viewer .lv-pdf-inkbar[hidden] { display: none; }
@keyframes lv-inkbar-in {
  from { opacity: 0; transform: translateY(-4px) scale(.96); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}
.lv-pdf-viewer .lv-ink-dot {
  position: relative; width: 22px; height: 22px; padding: 0;
  border: 0; border-radius: 50%; cursor: pointer;
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .12);
  transition: transform .12s ease, box-shadow .12s ease;
}
.lv-pdf-viewer .lv-ink-dot:hover { transform: scale(1.1); }
.lv-pdf-viewer .lv-ink-dot:focus-visible { outline: 2px solid var(--accent, #2f6f5e); outline-offset: 2px; }
.lv-pdf-viewer .lv-ink-dot.active {
  transform: scale(1.06);
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .12), 0 0 0 2px var(--surface, #fffefb), 0 0 0 4px var(--accent, #2f6f5e);
}
.lv-pdf-viewer .lv-ink-del {
  position: absolute; top: -5px; right: -5px;
  width: 13px; height: 13px; border-radius: 50%;
  display: none; align-items: center; justify-content: center;
  font-size: 9px; line-height: 1; color: #fff;
  background: var(--danger, #b4453a); border: 1px solid var(--surface, #fffefb);
}
.lv-pdf-viewer .lv-ink-dot.custom:hover .lv-ink-del { display: flex; }
/* 临时色点（当前色还没存进预设）：多一圈虚线，提示「还没保存」 */
.lv-pdf-viewer .lv-ink-dot.lv-ink-temp { box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .12), 0 0 0 2px transparent; }
.lv-pdf-viewer .lv-ink-dot.lv-ink-temp::after {
  content: ''; position: absolute; inset: -3px; border-radius: 50%;
  border: 1px dashed rgba(0, 0, 0, .30); pointer-events: none;
}
.lv-pdf-viewer .lv-ink-add {
  width: 22px; height: 22px; padding: 0; border-radius: 50%;
  display: inline-flex; align-items: center; justify-content: center;
  font-size: 13px; line-height: 1;
  color: var(--muted, #75726a); background: transparent;
  border: 1px dashed var(--border-strong, #cfcabb); cursor: pointer;
}
.lv-pdf-viewer .lv-ink-add:hover { color: var(--accent-text, #235648); border-color: var(--accent, #2f6f5e); }
/* 自己调的颜色不在预设里：＋ 的虚线换成当前色，表示「你现在用的是这个」 */
.lv-pdf-viewer .lv-pdf-inkbar.is-custom .lv-ink-add {
  border-style: solid; border-color: var(--inkbar-current, var(--accent, #2f6f5e));
}
.lv-pdf-viewer .lv-ink-add[disabled] { opacity: .4; cursor: default; }

/* ---------- 自定义取色盘（GoodNotes 风格：色相环 + 饱和度/明度面板） ----------
   挂在 body 上做 fixed 浮层，不参与工具栏布局；开关只过渡 opacity / transform，
   拖动时用 .is-dragging 关掉 knob 的过渡，跟手不延迟。 */
.lv-inkp {
  position: fixed; z-index: 9998;
  width: 236px; box-sizing: border-box;
  padding: 10px 14px 12px;
  display: flex; flex-direction: column; gap: 8px;
  font-family: var(--font-sans, sans-serif); font-size: 12.5px; line-height: 1.6;
  color: var(--text, #26251f); background: var(--surface, #fffefb);
  border: 1px solid var(--border-strong, #cfcabb); border-radius: 18px;
  box-shadow: 0 18px 42px rgba(28, 25, 15, .18), 0 3px 10px rgba(28, 25, 15, .07);
  opacity: 0; visibility: hidden; pointer-events: none;
  transform: translateY(-6px) scale(.96); transform-origin: 50% 0;
  transition: opacity .16s ease, transform .22s cubic-bezier(.22, .61, .36, 1), visibility .22s;
}
.lv-inkp.is-open { opacity: 1; visibility: visible; pointer-events: auto; transform: none; }
.lv-inkp-head { display: flex; align-items: center; gap: 6px; }
.lv-inkp-title { flex: 1 1 auto; font-size: 12px; font-weight: 600; color: var(--text, #26251f); }
.lv-inkp-back,
.lv-inkp-drop {
  flex: 0 0 auto; width: 24px; height: 24px; padding: 0;
  display: grid; place-items: center;
  color: var(--muted, #75726a); background: transparent;
  border: 0; border-radius: 7px; cursor: pointer;
}
.lv-inkp-back:hover,
.lv-inkp-drop:hover { background: var(--surface-3, #efeee8); color: var(--text, #26251f); }
.lv-inkp-back:focus-visible,
.lv-inkp-drop:focus-visible,
.lv-inkp-add:focus-visible { outline: 2px solid var(--accent, #2f6f5e); outline-offset: 1px; }
.lv-inkp-wheel {
  position: relative; width: 176px; height: 176px; margin: 2px auto 0;
  border-radius: 50%; cursor: crosshair; touch-action: none;
  -webkit-user-select: none; user-select: none;
  background: conic-gradient(from 0deg,
    #ff0000, #ff8000, #ffff00, #80ff00, #00ff00, #00ff80,
    #00ffff, #0080ff, #0000ff, #8000ff, #ff00ff, #ff0080, #ff0000);
  box-shadow: 0 1px 4px rgba(28, 25, 15, .16);
}
.lv-inkp-sv {
  position: absolute; inset: 46px; border-radius: 12px; overflow: hidden;
  background-image:
    linear-gradient(to top, #000, rgba(0, 0, 0, 0)),
    linear-gradient(to right, #fff, var(--inkp-hue, #f00));
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .14), 0 0 0 4px var(--surface, #fffefb);
}
.lv-inkp-knob {
  position: absolute; width: 15px; height: 15px; margin: -7.5px 0 0 -7.5px;
  border-radius: 50%; background: transparent;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(0, 0, 0, .28), 0 2px 6px rgba(0, 0, 0, .28);
  pointer-events: none; z-index: 2;
  transition: left .13s cubic-bezier(.22, .61, .36, 1), top .13s cubic-bezier(.22, .61, .36, 1);
}
.lv-inkp.is-dragging .lv-inkp-knob { transition: none; }
.lv-inkp-knob-sv { z-index: 3; }
.lv-inkp-foot { display: flex; align-items: center; gap: 8px; }
.lv-inkp-add {
  display: inline-flex; align-items: center; gap: 7px; white-space: nowrap;
  padding: 3px 7px 3px 3px; margin-left: -3px;
  font: inherit; font-size: 11.5px; color: var(--muted, #75726a);
  background: transparent; border: 0; border-radius: 999px; cursor: pointer;
  transition: background .15s, color .15s;
}
.lv-inkp-add:hover { background: var(--surface-3, #efeee8); color: var(--text, #26251f); }
.lv-inkp-blob {
  position: relative; flex: 0 0 auto; width: 18px; height: 18px; border-radius: 50%;
  background: var(--inkp-current, #f2c433);
  box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .16);
  transition: transform .16s cubic-bezier(.22, .61, .36, 1);
}
.lv-inkp-blob::after {
  content: '+'; position: absolute; right: -5px; top: -5px;
  width: 12px; height: 12px; border-radius: 50%;
  font-size: 9px; line-height: 12px; text-align: center; font-weight: 700;
  color: #fff; background: var(--accent, #2f6f5e);
  box-shadow: 0 0 0 1.5px var(--surface, #fffefb);
}
.lv-inkp-add.is-pulse .lv-inkp-blob { transform: scale(1.18); }
.lv-inkp-add.is-done { color: var(--accent-text, #235648); }
.lv-inkp-add.is-done .lv-inkp-blob { transform: scale(1.06); }
.lv-inkp-add.is-done .lv-inkp-blob::after { content: '✓'; }
.lv-inkp-hex {
  flex: 0 0 auto; margin-left: auto; display: flex; align-items: center; gap: 3px;
  padding: 2px 8px 2px 6px;
  background: var(--surface-2, #f8f7f3);
  border: 1px solid var(--border, #e2dfd5); border-radius: 999px;
  transition: border-color .15s, background .15s;
}
.lv-inkp-hex:focus-within { border-color: var(--accent, #2f6f5e); background: var(--surface, #fffefb); }
.lv-inkp-hex-tag {
  font-size: 9.5px; letter-spacing: .06em; color: var(--faint, #9c988e); font-weight: 600;
}
.lv-inkp-hex-input {
  width: 54px; padding: 0; border: 0; outline: none;
  font: inherit; font-size: 11.5px; letter-spacing: .05em; text-transform: uppercase;
  color: var(--text, #26251f); background: transparent;
}
@media (prefers-reduced-motion: reduce) {
  .lv-inkp, .lv-inkp-knob, .lv-inkp-blob { transition-duration: .01ms; }
}

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

/* ---------- 批注编辑气泡 ---------- */
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
.lv-annot-swatches { display: flex; align-items: center; flex-wrap: wrap; gap: 6px; max-width: 148px; }
.lv-annot-swatch {
  width: 20px; height: 20px; padding: 0; border-radius: 50%;
  border: 2px solid transparent; box-shadow: inset 0 0 0 1px rgba(0, 0, 0, .12);
  cursor: pointer;
}
.lv-annot-swatch.active { border-color: var(--accent, #2f6f5e); }
.lv-annot-swatch:hover { transform: scale(1.08); }
`

export function injectStyle () {
  if (document.getElementById(STYLE_ID)) return
  const head = document.head || document.documentElement
  if (!head) return
  const style = document.createElement('style')
  style.id = STYLE_ID
  style.textContent = STYLE_TEXT
  head.appendChild(style)
}

export function injectViewerCss (cssUrl) {
  if (document.getElementById(CSS_LINK_ID)) return
  const head = document.head || document.documentElement
  if (!head) return
  const link = document.createElement('link')
  link.id = CSS_LINK_ID
  link.rel = 'stylesheet'
  link.href = cssUrl
  head.appendChild(link)
}
