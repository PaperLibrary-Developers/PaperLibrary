/* 搜索栏语法：把 "#标签" 从输入里解析出来，其余部分当关键词。
   这样标签筛选就不用单独占一块界面了。（ESM：原 IIFE 脚本平移而来，保留原缩进） */

  export const TAG_TOKEN = /[#＃]([\u4e00-\u9fff\w-]{1,24})/g
  const TAG_TAIL = /[#＃]([\u4e00-\u9fff\w-]*)$/

  /** 解析搜索串 → { text, tags } */
  export function parse (input) {
    const raw = String(input || '')
    const tags = []
    const text = raw.replace(TAG_TOKEN, (all, name) => {
      if (name) tags.push(name)
      return ' '
    }).replace(/\s+/g, ' ').trim()
    return { text, tags }
  }

  /** 光标处正在输入的标签前缀（用于联想），没有则返回 null */
  export function pendingTag (input) {
    const m = TAG_TAIL.exec(String(input || ''))
    return m ? m[1] : null
  }

  /** 把标签追加到搜索串里 */
  export function appendTag (input, tag) {
    const raw = String(input || '').replace(TAG_TAIL, '').trim()
    return (raw ? raw + ' ' : '') + '#' + tag + ' '
  }

  /** 生成一段预览文本：关键词 + 已选标签 */
  export function describe (parsed) {
    const bits = []
    if (parsed.text) bits.push('关键词「' + parsed.text + '」')
    if (parsed.tags.length) bits.push('标签 ' + parsed.tags.map(t => '#' + t).join(' '))
    return bits.join(' · ')
  }

  /**
   * 归一化标题：去掉标点、多余空格和大小写差异，用来判断「是不是同一篇」。
   * 网络检索里同一个标题经常被不同来源/出版商各收录一份（预印本 + 正式版）。
   */
  export function titleKeyOf (item) {
    return String((item && item.title) || '')
      .toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fff]+/g, ' ')
      .trim()
  }

  /**
   * 按标题归组：同一个标题只把**被引数最高**的那条摆在明面上，其余挂到 duplicates。
   * 返回顺序仍按传入的相关度顺序（在每组第一次出现的位置输出），组内按被引数降序。
   */
  export function groupByTitle (list) {
    const members = new Map()
    for (const item of list || []) {
      const key = titleKeyOf(item)
      if (!key) continue                 // 没有标题的（极少）不参与折叠
      if (!members.has(key)) members.set(key, [])
      members.get(key).push(item)
    }
    for (const [key, group] of members) {
      members.set(key, group.slice().sort((a, b) =>
        (Number(b.citations) || 0) - (Number(a.citations) || 0) ||
        String(b.year || '').localeCompare(String(a.year || ''))))
    }
    const out = []
    const done = new Set()
    for (const item of list || []) {
      const key = titleKeyOf(item)
      if (!key) { out.push({ key: '', main: item, duplicates: [] }); continue }
      if (done.has(key)) continue
      done.add(key)
      const group = members.get(key) || [item]
      out.push({ key, main: group[0], duplicates: group.slice(1) })
    }
    return out
  }

