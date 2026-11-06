/**
 * 把「从别处搬来的文本」还原成可读纯文本（浏览器与 Node 共用）。
 *
 * 为什么逻辑只写一份：标题清洗必须在**两侧**生效——
 *   服务端：抓回来的外部结果（Crossref / Europe PMC / arXiv …）在合并前后就清干净，
 *           顺带去重键与排序也用干净标题；
 *   浏览器：结果可能是**旧缓存**（存在内存里的 lastSession），显示前再兜一次。
 * 所以实现只有这一份：`shared/html-text.mjs`（ESM 命名导出），两端都从这里取——
 *   浏览器：`import * as htmlText from '/shared/html-text.mjs'`（**绝对路径**：经 /shared/ 静态路由；
 *           用相对路径会被 /__v/<ver>/ 版本化前缀多推一层，变成 /__v/shared/… 而 404/回退）
 *   Node  ：`require('../shared/html-text.mjs')`（Node 22.12+ 原生支持 require(ESM)，见 package.json engines）
 * 依赖方向：shared/ 不引用任何层（server/ 与 public/ 都可以引用它）。
 *
 * ## 关键坑（这就是 &lt;i&gt; 会漏到界面上的原因）
 * 先剥标签、后解实体的顺序是**错的**：`&lt;i&gt;` 在剥标签那一步还不是标签，
 * 等解完实体才变成 `<i>`，于是标签活着穿到了界面上（实测：
 * "Atomic-Scale Engineering of &lt;i&gt;d-&lt;/i&gt;…" 原样显示）。
 * 正确顺序是「先解实体、再剥标签」，并且要**反复几轮**：
 * 双重编码的 `&amp;lt;i&amp;gt;` 也要还原干净。
 */

export const ENTITIES = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  ndash: '–', mdash: '—', minus: '−', times: '×', middot: '·', deg: '°'
}

/** 把 HTML 实体还原成字符（`&amp;` `&#65;` `&#x42;` `&nbsp;` …）；认不出的原样保留 */
export function unescapeHtml (input) {
  return String(input == null ? '' : input)
    .replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (m, body) => {
      if (body[0] === '#') {
        const code = body[1] === 'x' || body[1] === 'X'
          ? Number.parseInt(body.slice(2), 16)
          : Number.parseInt(body.slice(1), 10)
        return Number.isFinite(code) && code > 0 ? String.fromCodePoint(code) : m
      }
      const key = body.toLowerCase()
      return key in ENTITIES ? ENTITIES[key] : m
    })
}

/**
 * 剥掉排版标签。
 * 只认**像标签的**尖括号内容（`</i>`、`<i>`、`<italic>`、`<scp class="x">`）：
 * 用宽松的 `/<[^>]*>/` 会把数学式 `a < b and c > d` 里的 `< b and c >` 也当标签吃掉；
 * 允许 `<` 后面有空白同样会中招（`<` 后的空格让 `b` 当上了标签名），
 * 所以标签名必须紧贴 `<`，属性也只认 `k="v"` 这种正经写法。
 * 上下标是化学式的关键信息（Fe<sup>II</sup> → FeII），所以**去掉标签保留内容**；
 * 其余标签也保留内容，但**只在确实分隔两个词时才补空格**：
 * 一律换成空格会把 "d-&lt;i&gt;π" 弄成 "d- π"、把 "Spin&lt;/i&gt;-Electric" 弄成 "Spin -Electric"
 * （也实测过），所以按标签两侧的字符判断——两边都是字母/数字才补一个空格。
 */
const TAG_NAME = '[A-Za-z][A-Za-z0-9:._-]*'
const TAG_ATTR = '(?:\\s+[A-Za-z_:][-A-Za-z0-9_:.]*(?:\\s*=\\s*(?:"[^"]*"|\'[^\']*\'|[^\\s<>"\']+))?)*'
// 注意 `<\/?` 后面**不能**再允许空白：允许了的话 `< b and c >` 里的 "b" 会被当成标签名
const TAG_OPEN = '<\\/?' + TAG_NAME + TAG_ATTR + '\\s*>'
export function stripTags (input) {
  return String(input == null ? '' : input)
    .replace(new RegExp('<\\s*(?:sup|sub)\\s*>', 'gi'), '')
    .replace(new RegExp('<\\s*/\\s*(?:sup|sub)\\s*>', 'gi'), '')
    // 标签夹在两个词之间时先换成一个空格，免得把两个词粘起来；否则直接删掉
    .replace(new RegExp('([A-Za-z0-9])\\s*' + TAG_OPEN + '\\s*(?=[A-Za-z0-9])', 'g'), '$1 ')
    .replace(new RegExp(TAG_OPEN, 'g'), '')
}

/**
 * 一行文本 → 纯文本：先解实体、再剥标签，反复到稳定（最多 3 轮）。
 * 收尾再清掉标签留下的空尖括号与多余空白，以及「空格 + 句读」这种残留。
 */
export function toPlainText (input, options) {
  const opts = options || {}
  let s = String(input == null ? '' : input)
  for (let i = 0; i < 3; i++) {
    const before = s
    s = stripTags(unescapeHtml(s))
    if (s === before) break
  }
  if (opts.keepAngleBrackets !== true) {
    s = s.replace(/<\s*>/g, '')                       // 标签在尖括号里被清空后剩下的 "<>"
    s = s.replace(/\s*([,.;:!?，。；：！？、）)\]])/g, '$1')
  }
  return s.replace(/\s+/g, ' ').trim()
}