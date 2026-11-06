#!/usr/bin/env node
/**
 * 前端 ESM 图检查（不装任何包；只用 Node 标准库 + 正则）。
 *
 * 检查四件事（对应验收：新视图可加载 = 静态 200 + import 图完整）：
 *   1. public/ 下每个 .mjs 的 import / export-from 目标都存在（相对路径、'/xxx' 两种写法）
 *   2. 依赖图无环（0 环 import 是 app.mjs / shell/app-api.mjs 的设计前提）
 *   3. 具名 import 在目标文件里真的有对应 export（拼错名字会在浏览器里静默成 undefined）
 *   4. public/index.html 里引用的本地资源存在
 *
 * 用法：node test/ui-smoke/check-imports.mjs [--root <仓库根>]
 * 退出码：0 = 全过；1 = 有 error（warn 不影响退出码）。
 */
import fs from 'node:fs'
import path from 'node:path'

const args = process.argv.slice(2)
const rootArg = args.indexOf('--root')
const ROOT = rootArg >= 0 ? path.resolve(args[rootArg + 1]) : process.cwd()
const PUBLIC = path.join(ROOT, 'public')

const errors = []
const warns = []
const err = (msg) => errors.push(msg)
const warn = (msg) => warns.push(msg)

/* ---------------- 扫描 public 下所有 .mjs ---------------- */

function walk (dir, out) {
  let entries = []
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return out
  }
  for (const entry of entries) {
    if (entry.name === 'node_modules' || entry.name.startsWith('.')) continue
    const abs = path.join(dir, entry.name)
    if (entry.isDirectory()) walk(abs, out)
    else if (/\.m?js$/.test(entry.name)) out.push(abs)
  }
  return out
}

const files = walk(PUBLIC, []).sort()
if (!files.length) {
  console.error('没有在 ' + PUBLIC + ' 下找到任何 .mjs 文件')
  process.exit(1)
}

/* ---------------- 解析 import / export-from ---------------- */

const specifiers = []
const reStatic = /(?:^|[\s;{}()])import\s+([\s\S]*?)\s+from\s*['"]([^'"]+)['"]/g
const reBare = /(?:^|[\s;{}()])import\s*['"]([^'"]+)['"]/g
// 再导出只可能是 'export * from' 或 'export { a, b as c } from'；
// 不能用「export 后面懒匹配到任意 from」——那会把整个函数体吞进「子句」里（踩过）
const reExportStar = /export\s*\*\s*from\s*['"]([^'"]+)['"]/g
const reExportNamed = /export\s*\{([^}]*)\}\s*from\s*['"]([^'"]+)['"]/g
const reDynamic = /import\s*\(\s*['"]([^'"]+)['"]\s*\)/g

function lineOf (src, index) {
  return src.slice(0, index).split('\n').length
}

/**
 * 把注释换成等量换行的空白（保留字符串字面量：
 * 代码注释里写着「import …」这类说明文字时，不能被当成真的 import 语句）。
 */
function stripComments (src) {
  let out = ''
  let i = 0
  while (i < src.length) {
    const c = src[i]
    const n = src[i + 1]
    if (c === '/' && n === '/') {
      while (i < src.length && src[i] !== '\n') i++
      continue
    }
    if (c === '/' && n === '*') {
      i += 2
      while (i < src.length && !(src[i] === '*' && src[i + 1] === '/')) {
        if (src[i] === '\n') out += '\n'
        i++
      }
      i += 2
      continue
    }
    if (c === '"' || c === "'" || c === '`') {
      const quote = c
      out += c
      i++
      while (i < src.length) {
        if (src[i] === '\\') {
          out += src[i] + (src[i + 1] || '')
          i += 2
          continue
        }
        out += src[i]
        if (src[i] === quote) { i++; break }
        i++
      }
      continue
    }
    out += c
    i++
  }
  return out
}

/** 把 import 子句拆成 { def, ns, named:[{imported, local}] } */
function parseClause (clause) {
  const out = { def: '', ns: '', named: [] }
  let rest = ' ' + clause.trim() + ' '
  const namedMatch = rest.match(/\{([^}]*)\}/)
  if (namedMatch) {
    for (const part of namedMatch[1].split(',')) {
      const t = part.trim()
      if (!t) continue
      const m = t.match(/^([\w$]+)(?:\s+as\s+([\w$]+))?$/)
      if (m) out.named.push({ imported: m[1], local: m[2] || m[1] })
      else warn('看不懂的具名导入：{' + t + '}')
    }
    rest = rest.replace(namedMatch[0], ' ')
  }
  const nsMatch = rest.match(/\*\s*as\s+([\w$]+)/)
  if (nsMatch) {
    out.ns = nsMatch[1]
    rest = rest.replace(nsMatch[0], ' ')
  }
  rest = rest.replace(/,/g, ' ').trim()
  if (rest && !out.ns) out.def = rest.split(/\s+/)[0]
  return out
}

function scanFile (abs) {
  const src = stripComments(fs.readFileSync(abs, 'utf8'))
  let m
  while ((m = reStatic.exec(src))) {
    // 子句里不可能有分号；有就说明这条匹配跨了语句（例如字符串里出现 import 字样），跳过
    if (m[1].includes(';')) continue
    specifiers.push({ from: abs, spec: m[2], kind: 'import', clause: parseClause(m[1]), line: lineOf(src, m.index) })
  }
  while ((m = reBare.exec(src))) {
    specifiers.push({ from: abs, spec: m[1], kind: 'bare', clause: parseClause(''), line: lineOf(src, m.index) })
  }
  // 「export * from」与「export { a } from」都算再导出：目标必须存在
  while ((m = reExportStar.exec(src))) {
    specifiers.push({ from: abs, spec: m[1], kind: 'export-from', clause: parseClause(''), line: lineOf(src, m.index) })
  }
  while ((m = reExportNamed.exec(src))) {
    specifiers.push({ from: abs, spec: m[2], kind: 'export-from', clause: parseClause('{' + m[1] + '}'), line: lineOf(src, m.index) })
  }
  while ((m = reDynamic.exec(src))) {
    specifiers.push({ from: abs, spec: m[1], kind: 'dynamic', clause: parseClause(''), line: lineOf(src, m.index) })
  }
}

files.forEach(scanFile)

/* ---------------- 解析目标路径 ---------------- */

function resolveSpec (fromFile, spec) {
  if (/^[a-z]+:/i.test(spec)) return null // http(s): / 协议相对 → 不归这个检查管
  if (spec.startsWith('/')) {
    // 浏览器里 '/x' 是站点根：先按 public/x 找，找不到再按仓库根 x 找（/shared/** 就是这种）
    const candidates = [path.join(PUBLIC, spec), path.join(ROOT, spec)]
    for (const c of candidates) {
      for (const cand of [c, c + '.mjs', c + '.js', path.join(c, 'index.mjs'), path.join(c, 'index.js')]) {
        try { if (fs.statSync(cand).isFile()) return cand } catch {}
      }
    }
    return { missing: spec }
  }
  if (!spec.startsWith('.')) return null // 裸模块说明引入了第三方包，下面的检查会报
  const base = path.resolve(path.dirname(fromFile), spec)
  for (const cand of [base, base + '.mjs', base + '.js', path.join(base, 'index.mjs'), path.join(base, 'index.js')]) {
    try { if (fs.statSync(cand).isFile()) return cand } catch {}
  }
  return { missing: spec }
}

/* ---------------- 导出表（用于具名 import 校验） ---------------- */

const exportCache = new Map()
function exportsOf (abs) {
  if (exportCache.has(abs)) return exportCache.get(abs)
  const info = { names: new Set(), hasDefault: false, hasStar: false }
  let src = ''
  try { src = stripComments(fs.readFileSync(abs, 'utf8')) } catch { exportCache.set(abs, info); return info }
  if (/export\s+default\b/.test(src)) info.hasDefault = true
  let m
  const reBraces = /export\s*\{([^}]*)\}/g
  while ((m = reBraces.exec(src))) {
    for (const part of m[1].split(',')) {
      const t = part.trim()
      if (!t) continue
      const as = t.match(/^([\w$]+)\s+as\s+([\w$]+)$/)
      if (as) {
        if (as[2] === 'default') info.hasDefault = true
        else info.names.add(as[2])
      } else if (t === 'default') {
        info.hasDefault = true
      } else {
        info.names.add(t)
      }
    }
  }
  const reDecl = /export\s+(?:async\s+)?(?:const|let|var|function|class)\s+([\w$]+)/g
  while ((m = reDecl.exec(src))) info.names.add(m[1])
  if (/export\s*\*\s*from/.test(src)) info.hasStar = true
  exportCache.set(abs, info)
  return info
}

/* ---------------- 1) 目标存在 + 3) 具名导出存在 ---------------- */

const edges = new Map() // abs -> [abs]

for (const item of specifiers) {
  if (!item.spec.startsWith('.') && !item.spec.startsWith('/')) {
    err(rel(item.from) + ':' + item.line + ' 引用了裸模块「' + item.spec + '」——本仓库不允许第三方包/构建链')
    continue
  }
  const resolved = resolveSpec(item.from, item.spec)
  if (resolved && resolved.missing) {
    err(rel(item.from) + ':' + item.line + ' import 目标不存在：' + item.spec)
    continue
  }
  if (!resolved) continue
  const list = edges.get(item.from) || []
  list.push(resolved)
  edges.set(item.from, list)

  if (item.kind === 'import' && item.clause) {
    const info = exportsOf(resolved)
    if (item.clause.def && !info.hasDefault && !info.hasStar) {
      err(rel(item.from) + ':' + item.line + ' 默认导入 ' + item.clause.def + '，但 ' + rel(resolved) + ' 没有 export default')
    }
    for (const { imported } of item.clause.named) {
      if (info.hasStar) continue
      if (!info.names.has(imported)) {
        err(rel(item.from) + ':' + item.line + ' 具名导入 {' + imported + '} 在 ' + rel(resolved) + ' 里没有对应 export')
      }
    }
  }
}

/* ---------------- 1b) import * as ns 之后解构出来的名字，必须在目标模块的导出里 ---------------- */
// 这一类错误最容易漏：'import * as util' 拿到的是命名空间对象，
// 'const { truncate } = util' 在 util 没导出 truncate 时不会报 import 错，只在运行时炸（踩过）。
const nsImports = []
for (const item of specifiers) {
  if (item.kind !== 'import' || !item.clause || !item.clause.ns) continue
  const resolved = resolveSpec(item.from, item.spec)
  if (!resolved || resolved.missing) continue
  nsImports.push({ from: item.from, alias: item.clause.ns, resolved, line: item.line })
}

for (const ns of nsImports) {
  let src = ''
  try { src = stripComments(fs.readFileSync(ns.from, 'utf8')) } catch { continue }
  const re = new RegExp('(?:const|let|var)\\s*\\{([^}]*)\\}\\s*=\\s*' + ns.alias + '\\b', 'g')
  let m
  while ((m = re.exec(src))) {
    const info = exportsOf(ns.resolved)
    if (info.hasStar) continue
    for (const part of m[1].split(',')) {
      const t = part.trim()
      if (!t) continue
      const name = t.split(/[:=]/)[0].trim()
      if (!name || !/^[\w$]+$/.test(name)) continue
      if (!info.names.has(name)) {
        err(rel(ns.from) + ':' + lineOf(src, m.index) + ' 从 ' + rel(ns.resolved) +
          ' 的解构里取出 {' + name + '}，但那个模块没有导出它')
      }
    }
  }
}

/* ---------------- 2) 无环 ---------------- */

const WHITE = 0
const GREY = 1
const BLACK = 2
const marks = new Map()
const cycles = []

function dfs (abs, stack) {
  marks.set(abs, GREY)
  stack.push(abs)
  for (const next of edges.get(abs) || []) {
    if (marks.get(next) === GREY) {
      const from = stack.indexOf(next)
      cycles.push(stack.slice(from).concat(next).map(rel).join(' → '))
      continue
    }
    if ((marks.get(next) || WHITE) === WHITE) dfs(next, stack)
  }
  stack.pop()
  marks.set(abs, BLACK)
}

const starts = files.slice()
for (const file of starts) {
  if ((marks.get(file) || WHITE) === WHITE) dfs(file, [])
}
for (const cycle of cycles) err('import 成环：' + cycle)

/* ---------------- 4) index.html 引用的本地资源 ---------------- */

const indexHtml = path.join(PUBLIC, 'index.html')
if (fs.existsSync(indexHtml)) {
  const html = fs.readFileSync(indexHtml, 'utf8')
  const reRef = /(?:src|href)\s*=\s*"(\/[^"]+)"/g
  let m
  while ((m = reRef.exec(html))) {
    const spec = m[1].split('?')[0]
    if (spec.startsWith('/api/')) continue
    if (spec.startsWith('/vendor/')) { warns.push('注意：' + spec + ' 属于可选 vendor（缺失时前端降级）'); continue }
    if (resolveSpec(indexHtml, spec)) continue
    err('index.html 引用的资源不存在：' + spec)
  }
} else {
  warn('没有找到 public/index.html')
}

/* ---------------- 三个新视图必须在 ---------------- */

for (const view of ['figures', 'compare', 'deep']) {
  const p = path.join(PUBLIC, 'js', 'views', view + '.mjs')
  if (!fs.existsSync(p)) err('缺少新视图文件：public/js/views/' + view + '.mjs')
}

/* ---------------- 输出 ---------------- */

function rel (abs) {
  return path.relative(ROOT, abs).split(path.sep).join('/')
}

const cycleCount = cycles.length
console.log('检查根目录：' + ROOT)
console.log('扫描文件：' + files.length + ' 个 .mjs，解析 import/export 语句 ' + specifiers.length + ' 条')
console.log('依赖边：' + [...edges.values()].reduce((n, l) => n + l.length, 0) + ' 条，环：' + cycleCount + ' 个')
if (warns.length) {
  console.log('\n提示（不影响退出码）：')
  for (const w of warns) console.log('  · ' + w)
}
if (errors.length) {
  console.log('\n错误（' + errors.length + ' 条）：')
  for (const e of errors) console.log('  ✗ ' + e)
  process.exit(1)
}
console.log('\n✓ import 图完整、无环；具名导入与 index.html 引用都核对通过。')
