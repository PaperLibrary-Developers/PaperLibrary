/**
 * 第三方静态资源的地址（P4.6：路径可配置，为将来换后端留口子）。
 *
 * 默认走同源 /vendor/<name>/，由服务端映射到 node_modules 里对应的可选依赖
 * （pdfjs-dist / katex）。换成别的后端（例如 Python 引擎）时，只要在启动前调用
 * setVendorBase('pdfjs', 'https://…/pdfjs') 即可，页面代码不用改。
 */

const DEFAULT_BASES = {
  pdfjs: '/vendor/pdfjs',
  katex: '/vendor/katex'
}

const bases = Object.assign({}, DEFAULT_BASES)

/** 取某个依赖的资源前缀（不带结尾斜杠） */
export function getVendorBase (name) {
  return bases[name] || ''
}

/** 覆盖某个依赖的资源前缀；传空值恢复默认 */
export function setVendorBase (name, base) {
  if (!(name in DEFAULT_BASES)) return getVendorBase(name)
  const value = String(base == null ? '' : base).replace(/\/+$/, '')
  bases[name] = value || DEFAULT_BASES[name]
  return bases[name]
}

/** 把某个依赖前缀拼成一个具体文件地址（path 以 / 开头） */
export function vendorUrl (name, path) {
  return getVendorBase(name) + (path || '')
}

/**
 * 以普通 <script> 方式加载一个外部脚本（vendor 里的 UMD 依赖：katex / mhchem）。
 * 返回 Promise<boolean>：加载成功 true，缺失/失败 false——**绝不抛异常**，
 * 调用方照常启动，只是公式降级为原文显示。
 */
export function loadScript (src, options) {
  const opts = options || {}
  if (typeof document === 'undefined') return Promise.resolve(false)
  return new Promise(resolve => {
    const el = document.createElement('script')
    el.src = src
    el.async = false
    if (opts.attributes) {
      for (const [key, value] of Object.entries(opts.attributes)) el.setAttribute(key, value)
    }
    el.onload = () => resolve(true)
    el.onerror = () => resolve(false)
    document.head.appendChild(el)
  })
}
