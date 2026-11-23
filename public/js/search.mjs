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

  function trimParam (v) { return String(v == null ? '' : v).trim() }

  function splitListParam (v) {
    return String(v == null ? '' : v).split(',').map(s => s.trim()).filter(Boolean)
  }

  /** 把搜索页 URLSearchParams 读进标准化的查询状态对象 */
  export function readSearchParams (params) {
    const get = params && typeof params.get === 'function'
      ? key => params.get(key)
      : key => (params && key in params ? params[key] : null)
    return {
      q: trimParam(get('q')),
      author: trimParam(get('author')),
      venue: trimParam(get('venue')),
      yearFrom: trimParam(get('yearFrom')),
      yearTo: trimParam(get('yearTo')),
      type: get('type') || 'all',
      starred: get('starred') === 'true',
      keyword: trimParam(get('keyword')),
      tags: splitListParam(get('tags')),
      hasPdf: get('hasPdf') === 'true',
      scope: get('scope') || 'metadata',
      sort: get('sort') || '',
      page: Number(get('page')) || 1,
      tab: get('tab') === 'web' ? 'web' : 'local',
      facets: {
        years: splitListParam(get('fy')),
        authors: splitListParam(get('fa')),
        venues: splitListParam(get('fv')),
        types: splitListParam(get('ftype')),
        tags: splitListParam(get('ftag'))
      },
      webFacets: {
        years: splitListParam(get('wy')),
        venues: splitListParam(get('wv')),
        authors: splitListParam(get('wa')),
        onlyOA: get('woa') === 'true'
      },
      webSort: 'relevance'
    }
  }

  /** 把查询状态对象写回 URL 参数字典（空值/默认值置为空串，便于 URL 精简） */
  export function toSearchParams (state) {
    const s = state || {}
    const f = s.facets || {}
    const w = s.webFacets || {}
    const tags = Array.isArray(s.tags) ? s.tags : []
    return {
      q: s.q || '',
      author: s.author || '',
      venue: s.venue || '',
      yearFrom: s.yearFrom || '',
      yearTo: s.yearTo || '',
      type: s.type && s.type !== 'all' ? s.type : '',
      starred: s.starred ? 'true' : '',
      keyword: s.keyword || '',
      tags: tags.length ? tags.join(',') : '',
      hasPdf: s.hasPdf ? 'true' : '',
      scope: s.scope && s.scope !== 'metadata' ? s.scope : '',
      sort: s.sort || '',
      page: s.page > 1 ? s.page : '',
      tab: s.tab === 'web' ? 'web' : '',
      fy: (f.years || []).join(','),
      fa: (f.authors || []).join(','),
      fv: (f.venues || []).join(','),
      ftype: (f.types || []).join(','),
      ftag: (f.tags || []).join(','),
      wy: (w.years || []).join(','),
      wv: (w.venues || []).join(','),
      wa: (w.authors || []).join(','),
      woa: w.onlyOA ? 'true' : ''
    }
  }

  /** 判断是否存在任意有效的本地检索条件 */
  export function hasSearchCondition (state) {
    const s = state || {}
    const f = s.facets || {}
    const facetOn = ['years', 'authors', 'venues', 'types', 'tags'].some(k => (f[k] || []).length)
    return !!(s.q || s.author || s.venue || s.keyword ||
      s.yearFrom || s.yearTo || (s.tags || []).length ||
      s.hasPdf || facetOn ||
      (s.type && s.type !== 'all'))
  }

  /** 组装 /api/papers 与 /api/facets 所需的查询参数对象 */
  export function buildSearchQuery (state, pageSize) {
    const s = state || {}
    const f = s.facets || {}
    const baseTags = Array.isArray(s.tags) ? s.tags : []
    const facetTypes = Array.isArray(f.types) ? f.types : []
    const facetTags = Array.isArray(f.tags) ? f.tags : []
    const query = {
      q: s.q,
      author: s.author,
      venue: s.venue,
      keyword: s.keyword,
      yearFrom: s.yearFrom,
      yearTo: s.yearTo,
      type: s.type,
      starred: s.starred ? 'true' : '',
      tags: baseTags,
      hasPdf: s.hasPdf,
      scope: s.scope || 'metadata',
      sort: s.sort,
      page: s.page,
      pageSize: pageSize,
      years: f.years || [],
      authors: f.authors || [],
      venues: f.venues || []
    }
    if (facetTypes.length) query.type = facetTypes.join(',')
    if (facetTags.length) query.tags = [...new Set([...baseTags, ...facetTags])]
    return query
  }

