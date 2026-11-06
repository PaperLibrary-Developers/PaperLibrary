/* 年份时间线（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from '../util.mjs'
import { api } from '../api.mjs'
import { filterByTag, go, render } from '../shell/app-api.mjs'

  const { h, authorsText, typeLabel, starsHtml } = util
  
  async function timeline (ctx) {
    const { outlet, isStale } = ctx
    outlet.replaceChildren(h('div.content', h('div.empty', h('div.big', '正在整理时间线…'))))

    let items = []
    let stats = {}
    try {
      const [list, s] = await Promise.all([
        api.listPapers({ all: true, pageSize: 500, sort: 'yearDesc' }),
        api.stats()
      ])
      items = list.items || []
      stats = s.stats || {}
    } catch (err) {
      outlet.replaceChildren(h('div.content', h('div.empty',
        h('div.big', '读取失败'),
        h('p', { text: err.message }),
        h('button.btn', { type: 'button', onclick: () => render() }, '重试')
      )))
      return
    }
    if (isStale()) return

    /* ---------- 按年份分组 ---------- */
    const groups = new Map()
    for (const item of items) {
      const year = item.year || '年份未知'
      if (!groups.has(year)) groups.set(year, [])
      groups.get(year).push(item)
    }
    const years = [...groups.keys()].sort((a, b) => {
      if (a === '年份未知') return 1
      if (b === '年份未知') return -1
      return Number(b) - Number(a)
    })

    /* ---------- 统计概览 ---------- */
    const statGrid = h('div.stat-grid',
      h('div.stat', h('div.n', String(stats.total != null ? stats.total : items.length)), h('div.k', '文献总数')),
      h('div.stat', h('div.n', String(stats.withPdf || 0)), h('div.k', '已关联 PDF')),
      h('div.stat', h('div.n', String(stats.starred || 0)), h('div.k', '我的收藏')),
      h('div.stat', h('div.n', String(stats.tags || 0)), h('div.k', '标签数量')),
      h('div.stat', h('div.n', stats.avgRating ? String(stats.avgRating) : '—'), h('div.k', '平均评分'))
    )

    /* ---------- 年份分布 ---------- */
    const byYear = (stats.byYear || []).slice(0, 14)
    const maxYear = Math.max(1, ...byYear.map(y => y.count))
    const distribution = h('section.panel',
      h('div.panel-head', '按年份分布', h('div.right', h('span.hint', '当年收录数量'))),
      h('div.panel-body', byYear.length
        ? h('div.bars', byYear.map(y => h('div.bar-row',
          h('span', { text: String(y.year) }),
          h('div.bar-track', h('i', { style: { width: Math.round(y.count / maxYear * 100) + '%' } })),
          h('span.bar-num', String(y.count))
        )))
        : h('div.hint', '还没有带年份的文献'))
    )

    /* ---------- 时间线 ---------- */
    const timelineEl = h('div.timeline', years.map(year => h('div.tl-year',
      h('div.y', String(year)),
      h('div.tl-items', groups.get(year).map(item => h('div.tl-item', {
        style: { cursor: 'pointer' },
        onclick: () => { go('#/paper/' + encodeURIComponent(item.id) ); }
      },
        h('div', { style: { fontFamily: 'var(--font-serif)', fontSize: '16px', fontWeight: '600' } }, item.title),
        h('div', { style: { color: 'var(--muted)', fontSize: '12.8px', marginTop: '3px' } },
          [authorsText(item.authors, 3), item.venue, typeLabel(item.type)].filter(Boolean).join(' · ')),
        h('div', { style: { display: 'flex', gap: '5px', marginTop: '6px', flexWrap: 'wrap', alignItems: 'center' } },
          (item.tags || []).slice(0, 6).map(tag => h('button.tag.clickable', {
            type: 'button',
            title: '按这个标签筛选',
            onclick: ev => {
              ev.stopPropagation()
              if (filterByTag) filterByTag(tag, { resetType: true, resetStarred: true })
            }
          }, tag)),
          h('span', { style: { marginLeft: 'auto', color: 'var(--star)', fontSize: '12px' }, html: starsHtml(item.rating) })
        )
      )))
    )))

    outlet.replaceChildren(h('div.content',
      h('div.page-head',
        h('div',
          h('div.page-title', '年份时间线'),
          h('div.page-sub', `${items.length} 条文献，跨 ${(stats.byYear || []).length} 个年份`)
        ),
        h('button.btn', { type: 'button', onclick: () => window.print() }, '打印 / 存 PDF')
      ),
      statGrid,
      h('div', { style: { height: '16px' } }),
      distribution,
      h('div', { style: { height: '20px' } }),
      years.length ? timelineEl : h('div.empty', h('div.big', '还没有文献'))
    ))
    if (document.onkeydown) document.onkeydown = null
  }

  export { timeline }
