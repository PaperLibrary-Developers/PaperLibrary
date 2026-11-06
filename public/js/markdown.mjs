/* 轻量 Markdown 渲染器（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照）
   —— KaTeX 公式走 globalThis.katex（vendor/katex 以普通脚本加载，缺失时降级为原文） */

  export const escapeHtml = s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')

  const REF_RE = /\[\s*(\d{1,3}(?:\s*[-,，]\s*\d{1,3})*)\s*\]/g
  const MD_LINK_RE = /\[([^\]\n]*)\]\((https?:\/\/[^)\s]+)\)/g
  const BARE_URL_RE = /(^|[\s(（])(https?:\/\/[^\s<>()（）]+|www\.[^\s<>()（）]+|10\.\d{4,9}\/[^\s<>()（）]+)/g
  const WIKI_RE = /\[\[([^\]\n]{1,120})\]\]/g

  /* ------------------------------ 公式（KaTeX） ------------------------------ */

  /** KaTeX 是否就绪（未装可选依赖 katex 时为 false，此时原样显示） */
  function hasKatex () {
    return typeof window !== 'undefined' && globalThis.katex && typeof globalThis.katex.renderToString === 'function'
  }

  /**
   * 渲染一段 LaTeX。
   * 出错时**原样返回 LaTeX**（不是丢掉、不是抛异常）：宁可难看，不能丢内容。
   */
  function renderMath (tex, displayMode) {
    const source = String(tex == null ? '' : tex).trim()
    if (!source) return ''
    const shown = displayMode ? '$$' + source + '$$' : '$' + source + '$'
    if (!hasKatex()) return '<span class="math-raw" title="未安装公式排版依赖 katex，暂时按原样显示">' + escapeHtml(shown) + '</span>'
    try {
      const html = globalThis.katex.renderToString(source, {
        displayMode: !!displayMode,
        throwOnError: false,        // 单条公式写错时只把那一处标红，不炸整页
        strict: false,              // 容忍 Unicode、非标准命令
        trust: false,
        output: 'htmlAndMathml'     // 带上 MathML：选中/朗读/复制都还能拿到内容
      })
      return '<span class="math' + (displayMode ? ' math-display' : '') + '" data-tex="' +
        escapeHtml(source) + '">' + html + '</span>'
    } catch (err) {
      return '<span class="math-raw" title="' + escapeHtml(String(err && err.message || '公式渲染失败')) + '">' +
        escapeHtml(shown) + '</span>'
    }
  }

  /**
   * 把文本里的公式抠出来换成占位符（必须先于转义与行内规则执行）。
   * 识别：$$…$$（可跨行已在外部处理）、$…$、\(…\)、\[…\]。
   * 守卫（避免把「价格 $100」这类误当公式）：
   *   - 开定界符后面不能是空白，闭定界符前面不能是空白；
   *   - 闭定界符后面不能紧跟数字；
   *   - \$ 是转义美元，不参与匹配。
   * @returns {{ text:string, store:string[] }}
   */
  function extractMath (raw, store) {
    let text = String(raw == null ? '' : raw)
    const push = html => {
      store.push(html)
      return '\u0000M' + (store.length - 1) + '\u0000'
    }
    // \[ … \] / \( … \) ：反斜杠形式没有歧义，先处理
    text = text.replace(/\\\[([\s\S]+?)\\\]/g, (m, tex) => push(renderMath(tex, true)))
    text = text.replace(/\\\(([\s\S]+?)\\\)/g, (m, tex) => push(renderMath(tex, false)))
    // $$ … $$（同一行内的写法）
    text = text.replace(/\$\$([^$]+?)\$\$/g, (m, tex) => push(renderMath(tex, true)))
    // $ … $（行内；不跨行）
    text = text.replace(/(^|[^\\$])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\d)/g,
      (m, pre, tex) => pre + push(renderMath(tex, false)))
    // 还原 \$ 转义
    text = text.replace(/\\\$/g, '$')
    return { text, store }
  }

  function safeUrl (url) {
    const u = String(url || '').trim()
    if (/^(https?:|mailto:|\/|#)/i.test(u)) return u
    return '#'
  }

  function link (url, text, cls) {
    return `<a href="${escapeHtml(safeUrl(url))}"${cls ? ` class="${cls}"` : ''} target="_blank" rel="noopener noreferrer">${text}</a>`
  }

  function splitTableRow (line) {
    let s = String(line).trim()
    if (s.startsWith('|')) s = s.slice(1)
    if (s.endsWith('|')) s = s.slice(0, -1)
    return s.split('|').map(c => c.trim())
  }

  /**
   * 把「数学模式之外」的 LaTeX 文本命令转成 HTML。
   *
   * 为什么需要：我们的提示词让模型用 LaTeX 写公式，它偶尔会**顺手把斜体也写成 LaTeX**：
   *   `利用含环烯醚萜植物（\textit{Cornus officinalis} 和 \textit{Gardenia jasminoides}）`
   * 这些命令**不在 `$…$` 里**，KaTeX 不管它们，于是页面上原样显示出 `\textit{…}`。
   * 这里在抠掉公式与行内代码之后把常见的文本命令转成对应标签。
   *
   * 只在数学模式之外生效——`$\mathrm{kJ\ mol^{-1}}$`、`$\ce{C-C}$` 这类早被抠成占位符了，动不到。
   */
  const LATEX_TEXT_RULES = [
    [/\\emph\s*\{([^{}]*)\}/g, '<em>$1</em>'],
    [/\\text(?:it|sl)\s*\{([^{}]*)\}/g, '<em>$1</em>'],
    [/\\textbf\s*\{([^{}]*)\}/g, '<strong>$1</strong>'],
    [/\\texttt\s*\{([^{}]*)\}/g, '<code>$1</code>'],
    [/\\underline\s*\{([^{}]*)\}/g, '<u>$1</u>'],
    [/\\textsuperscript\s*\{([^{}]*)\}/g, '<sup>$1</sup>'],
    [/\\textsubscript\s*\{([^{}]*)\}/g, '<sub>$1</sub>'],
    // 纯粹是字体切换、没有语义的：去掉命令，保留内容
    [/\\(?:textrm|textsf|textsc|textnormal|mbox|hbox|mathrm|mathbf|mathit)\s*\{([^{}]*)\}/g, '$1']
  ]

  function latexTextToHtml (text) {
    let s = String(text == null ? '' : text)
    if (s.indexOf('\\') < 0) return s
    // 嵌套写法（\textbf{\textit{X}}）需要多跑几轮从内到外消化
    for (let round = 0; round < 4; round++) {
      const before = s
      for (const [re, rep] of LATEX_TEXT_RULES) s = s.replace(re, rep)
      if (s === before) break
    }
    return s
  }

  /**
   * 行内公式 → 占位符；不做 HTML 转义（由调用方决定）。
   * **顺序很重要**：先把反引号包裹的行内代码抠出来，再抠公式——
   * 否则 `` `$x$` `` 这种「把公式当代码示例」的写法会被渲染成公式（踩过一次）。
   * 最后才处理裸写的 LaTeX 文本命令（此时公式与代码都已经不在文本里了）。
   */
  function escapeAndExtract (line, tmpl) {
    if (!tmpl) return escapeHtml(line)
    let text = String(line == null ? '' : line)
    text = text.replace(/`([^`\n]+)`/g, (m, code) => {
      tmpl.push('<code>' + escapeHtml(code) + '</code>')
      return '\u0000M' + (tmpl.length - 1) + '\u0000'
    })
    return latexTextToHtml(extractMath(text, tmpl).text)
  }

  /** 行内语法（入参必须是已转义/已抠出公式的文本） */
  function inline (escaped, ctx) {
    const store = []
    const stash = html => {
      store.push(html)
      return '\u0000X' + (store.length - 1) + '\u0000'
    }
    let text = escaped

    // 行内代码
    text = text.replace(/`([^`\n]+)`/g, (m, code) => stash('<code>' + code + '</code>'))
    // Markdown 链接
    text = text.replace(MD_LINK_RE, (m, label, url) => stash(link(url, label)))
    // [[文献名]]
    text = text.replace(WIKI_RE, (m, name) => {
      const target = ctx.byTitle ? ctx.byTitle(name) : null
      if (target) return stash(`<a href="#/paper/${encodeURIComponent(target.id)}" class="ref" title="库内文献">${name}</a>`)
      return stash(`<span class="ref" title="库内暂时没有这篇文献">${name}</span>`)
    })
    // [1] / [1,2] 引用角标
    text = text.replace(REF_RE, (m, nums) => {
      const list = String(nums).split(/[-,，]/).map(n => n.trim()).filter(Boolean)
      const chips = list.map(n => `<span class="ref">[${n}]</span>`).join('<span class="ref-sep"> </span>')
      return stash(chips)
    })
    // 裸链接与 DOI
    text = text.replace(BARE_URL_RE, (m, prefix, url) => {
      const href = /^10\./.test(url) ? 'https://doi.org/' + url : (/^www\./.test(url) ? 'https://' + url : url)
      return prefix + stash(link(href, escapeHtml(url)))
    })
    // 粗体 / 斜体 / 删除线 / 高亮
    text = text.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    text = text.replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    text = text.replace(/~~([^~\n]+)~~/g, '<del>$1</del>')
    text = text.replace(/==([^=\n]+)==/g, '<mark>$1</mark>')
    // #标签
    text = text.replace(/(^|[\s(（])([#＃])([\u4e00-\u9fff\w-]{1,24})/g,
      (m, pre, hash, name) => pre + stash(`<span class="ref" title="标签：${name}">#${name}</span>`))

    text = text.replace(/\u0000X(\d+)\u0000/g, (m, i) => store[Number(i)])
    return text
  }

  export function render (source, options) {
    const opts = options || {}
    const ctx = { byTitle: opts.byTitle || null }
    if (!source || !String(source).trim()) return ''
    const lines = String(source).replace(/\r\n?/g, '\n').replace(/\t/g, '  ').split('\n')
    const out = []
    /** 公式占位符池：extractMath 把渲染好的公式塞进来，最后统一还原（先于行内池） */
    const tmpl = []

    let i = 0
    let para = []
    const flushPara = () => {
      if (!para.length) return
      const raw = para.join('\n').trim()
      para = []
      if (!raw) return
      // 每一行先抠公式（未转义状态），再走行内规则
      const withBreaks = raw.split('\n').map(l => inline(escapeAndExtract(l, tmpl), ctx)).join('<br>')
      out.push('<p>' + withBreaks + '</p>')
    }

    while (i < lines.length) {
      const line = lines[i]

      // 独立成块的公式：$$…$$ 或 \[…\]（可以跨行）
      const blockOpen = /^\s*(\$\$|\\\[)\s*$/.exec(line)
      const blockInline = /^\s*(\$\$|\\\[)\s*([\s\S]+?)\s*(\$\$|\\\])\s*$/.exec(line)
      if (blockOpen) {
        flushPara()
        const open = blockOpen[1]
        const close = open === '$$' ? '$$' : '\\]'
        const body = []
        i++
        while (i < lines.length && lines[i].trim() !== close) {
          body.push(lines[i])
          i++
        }
        i++ // 跳过结束行
        out.push(renderMath(body.join('\n'), true))
        continue
      }
      if (blockInline) {
        flushPara()
        out.push(renderMath(blockInline[2], true))
        i++
        continue
      }

      // 围栏代码
      const fence = /^\s*(```|~~~)\s*([\w+-]*)\s*$/.exec(line)
      if (fence) {
        flushPara()
        const marker = fence[1]
        const body = []
        i++
        while (i < lines.length && !new RegExp('^\\s*' + marker).test(lines[i])) {
          body.push(lines[i])
          i++
        }
        i++ // 跳过结束围栏
        const lang = fence[2] ? ` data-lang="${escapeHtml(fence[2])}"` : ''
        out.push(`<pre${lang}><code>${escapeHtml(body.join('\n'))}</code></pre>`)
        continue
      }

      // 空行
      if (!line.trim()) {
        flushPara()
        i++
        continue
      }

      // 分隔线
      if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) {
        flushPara()
        out.push('<hr>')
        i++
        continue
      }

      // 标题
      const heading = /^\s{0,3}(#{1,6})\s+(.*)$/.exec(line)
      if (heading) {
        flushPara()
        const level = Math.min(heading[1].length, 4)
        out.push(`<h${level}>${inline(escapeAndExtract(heading[2].trim(), tmpl), ctx)}</h${level}>`)
        i++
        continue
      }

      // 表格
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
        flushPara()
        const head = splitTableRow(line)
        i += 2
        const rows = []
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) {
          rows.push(splitTableRow(lines[i]))
          i++
        }
        let html = '<table><thead><tr>' + head.map(c => `<th>${inline(escapeAndExtract(c, tmpl), ctx)}</th>`).join('') + '</tr></thead><tbody>'
        for (const row of rows) {
          html += '<tr>' + head.map((_, idx) => `<td>${inline(escapeAndExtract(row[idx] || '', tmpl), ctx)}</td>`).join('') + '</tr>'
        }
        html += '</tbody></table>'
        out.push(html)
        continue
      }

      // 引用
      if (/^\s*>\s?/.test(line)) {
        flushPara()
        const body = []
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          body.push(lines[i].replace(/^\s*>\s?/, ''))
          i++
        }
        out.push('<blockquote>' + inline(escapeAndExtract(body.join('\n'), tmpl).replace(/\n/g, '<br>'), ctx) + '</blockquote>')
        continue
      }

      // 列表（含待办）
      const isUl = /^\s*([-*+]|\d+[.)])\s+/.test(line)
      if (isUl) {
        flushPara()
        const ordered = /^\s*\d+[.)]\s+/.test(line)
        const items = []
        while (i < lines.length && /^\s*([-*+]|\d+[.)])\s+/.test(lines[i])) {
          let content = lines[i].replace(/^\s*([-*+]|\d+[.)])\s+/, '')
          let box = ''
          const todo = /^\[([ xX])\]\s*(.*)$/.exec(content)
          if (todo) {
            box = `<input type="checkbox" disabled${todo[1].toLowerCase() === 'x' ? ' checked' : ''}> `
            content = todo[2]
          }
          items.push('<li>' + box + inline(escapeAndExtract(content, tmpl), ctx) + '</li>')
          i++
        }
        out.push(`<${ordered ? 'ol' : 'ul'}>` + items.join('') + `</${ordered ? 'ol' : 'ul'}>`)
        continue
      }

      para.push(line)
      i++
    }
    flushPara()
    // 还原公式占位符（必须**在最后**：行内池已经还原过了，剩下的大写字母 M 占位符就是公式）
    let html = out.join('\n')
    if (tmpl.length) html = html.replace(/\u0000M(\d+)\u0000/g, (m, idx) => tmpl[Number(idx)] || '')
    return html
  }

  /** 从文本里收集 [n] 引用编号，用于生成参考文献列表 */
  export function collectRefs (text) {
    const found = new Map()
    let m
    const re = new RegExp(REF_RE.source, 'g')
    while ((m = re.exec(String(text || '')))) {
      for (const n of String(m[1]).split(/[-,，]/).map(s => s.trim()).filter(Boolean)) {
        const num = Number(n)
        if (!Number.isFinite(num)) continue
        found.set(num, (found.get(num) || 0) + 1)
      }
    }
    return [...found.entries()].map(([num, count]) => ({ num, count })).sort((a, b) => a.num - b.num)
  }

  export const refChip = (num, title) =>
    `<span class="ref" title="${escapeHtml(title || '')}">[${num}]</span>`

  /** 判断文本里是否有需要提醒作者补全的引用编号（正文里有、参考文献里没有） */
  export function missingRefs (text, refs) {
    const cited = collectRefs(text).map(r => r.num)
    const defined = new Set((refs || []).map(r => Number(r.num)))
    return cited.filter(n => !defined.has(n))
  }

