/* PDF 阅读器内置样式表注入（按 id 去重，只插一次） */

export const STYLE_ID = 'lv-pdfviewer-style'
export const CSS_LINK_ID = 'lv-pdfviewer-css'

export const STYLE_TEXT = `
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
.lv-pdf-viewer .lv-pdf-search { flex: 1 1 110px; width: auto; min-width: 78px; max-width: 260px; }

.lv-pdf-viewer .lv-pdf-num,
.lv-pdf-viewer .lv-pdf-count,
.lv-pdf-viewer .lv-pdf-zoom {
  flex: 0 0 auto;
  font-size: 12px; color: var(--muted, #75726a);
  white-space: nowrap; font-variant-numeric: tabular-nums;
}
.lv-pdf-viewer .lv-pdf-count { min-width: 92px; }
.lv-pdf-viewer .lv-pdf-zoom { min-width: 42px; text-align: center; }

/* ---------- 阅读区 ---------- */
.lv-pdf-viewer .lv-pdf-body { position: relative; flex: 1 1 auto; min-height: 0; }
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

/* ---------- 反色 ---------- */
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
  position: absolute; left: 50%; top: 8px; transform: translateX(-50%);
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

.lv-pdf-viewer .lv-pdf-icon,
.lv-pdf-viewer .lv-annot-kind {
  display: inline-flex; align-items: center; justify-content: center;
  flex: 0 0 auto; line-height: 0;
}
.lv-pdf-viewer .lv-pdf-icon { width: 14px; height: 14px; }
.lv-pdf-viewer .lv-annot-kind { width: 14px; height: 14px; }
.lv-pdf-viewer .lv-pdf-icon svg,
.lv-pdf-viewer .lv-annot-kind svg { display: block; }
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
