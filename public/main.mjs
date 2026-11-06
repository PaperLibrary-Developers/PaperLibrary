/* 前端唯一入口（P4.1/P4.5）：index.html 里只有这一条 <script type="module">。
   - 依赖图由 import 静态表达，漏写一个模块会立刻报错，而不是某个视图运行到一半静默失败
   - KaTeX 仍是普通脚本（vendor 里的 UMD）：可选依赖，缺失/404 时静默降级为原文显示
   - vendor 路径从 vendor.mjs 取（P4.6 可配置） */
import { boot } from './js/app.mjs'
import { vendorUrl, loadScript, setVendorBase } from './js/vendor.mjs'

// 将来换后端/换 CDN 时：setVendorBase('pdfjs', 'https://…/pdfjs')；这里把入口暴露到全局便于调试
globalThis.SciLibVendor = { setVendorBase, vendorUrl }

const katexReady = await loadScript(vendorUrl('katex', '/katex.min.js'))
if (katexReady) await loadScript(vendorUrl('katex', '/contrib/mhchem.min.js'))

boot()
