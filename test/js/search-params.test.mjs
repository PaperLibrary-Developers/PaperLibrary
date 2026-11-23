import assert from 'node:assert/strict'
import test from 'node:test'
import {
  appendTag,
  buildSearchQuery,
  describe,
  groupByTitle,
  hasSearchCondition,
  parse,
  pendingTag,
  readSearchParams,
  titleKeyOf,
  toSearchParams
} from '../../public/js/search.mjs'
import { ApiError, api, qs } from '../../public/js/api.mjs'

test('search syntax parse, pendingTag, appendTag, and describe handle CJK and fullwidth hashes', () => {
  assert.deepEqual(parse('photocatalysis #催化 ＃CO2-reduction  overview'), {
    text: 'photocatalysis overview',
    tags: ['催化', 'CO2-reduction']
  })
  assert.equal(pendingTag('water splitting #催'), '催')
  assert.equal(pendingTag('water splitting #'), '')
  assert.equal(pendingTag('water splitting #tag '), null)
  assert.equal(appendTag('water #催', '催化'), 'water #催化 ')
  assert.equal(appendTag('#a', 'alpha'), '#alpha ')
  assert.equal(describe({ text: 'CO2', tags: ['催化', '综述'] }), '关键词「CO2」 · 标签 #催化 #综述')
  assert.equal(describe({ text: '', tags: [] }), '')
})

test('titleKeyOf and groupByTitle deduplicate by normalized title keeping highest citations', () => {
  assert.equal(titleKeyOf({ title: '  CO2-Reduction: A Review! ' }), 'co2 reduction a review')
  const items = [
    { id: '1', title: 'Alpha: A Study', citations: 10, year: 2023 },
    { id: '2', title: 'Beta Paper', citations: 5, year: 2024 },
    { id: '3', title: 'alpha a study!', citations: 120, year: 2022 },
    { id: '4', title: '', citations: 0 }
  ]
  const groups = groupByTitle(items)
  assert.equal(groups.length, 3)
  assert.equal(groups[0].main.id, '3')
  assert.deepEqual(groups[0].duplicates.map(d => d.id), ['1'])
  assert.equal(groups[1].main.id, '2')
  assert.equal(groups[1].duplicates.length, 0)
  assert.equal(groups[2].key, '')
  assert.equal(groups[2].main.id, '4')
})

test('readSearchParams and toSearchParams roundtrip defaults, multi-facets, and web tab state', () => {
  const empty = readSearchParams(new URLSearchParams())
  assert.equal(empty.type, 'all')
  assert.equal(empty.scope, 'metadata')
  assert.equal(empty.page, 1)
  assert.equal(empty.tab, 'local')
  assert.equal(hasSearchCondition(empty), false)

  const emptyOut = toSearchParams(empty)
  assert.equal(emptyOut.type, '')
  assert.equal(emptyOut.scope, '')
  assert.equal(emptyOut.page, '')
  assert.equal(emptyOut.tab, '')

  const raw = new URLSearchParams(
    'q=+catalysis+&author=Zhang&venue=Nature&yearFrom=2020&yearTo=2025&type=journal&starred=true' +
    '&keyword=CO2&tags=t1,%20t2&hasPdf=true&scope=fulltext&sort=year&page=3&tab=web' +
    '&fy=2023,2024&fa=Zhang,Li&fv=Nature&ftype=journal,preprint&ftag=t2,t3' +
    '&wy=2024&wv=Science&wa=Wang&woa=true'
  )
  const parsed = readSearchParams(raw)
  assert.equal(parsed.q, 'catalysis')
  assert.deepEqual(parsed.tags, ['t1', 't2'])
  assert.equal(parsed.starred, true)
  assert.equal(parsed.hasPdf, true)
  assert.equal(parsed.scope, 'fulltext')
  assert.equal(parsed.page, 3)
  assert.equal(parsed.tab, 'web')
  assert.deepEqual(parsed.facets, {
    years: ['2023', '2024'],
    authors: ['Zhang', 'Li'],
    venues: ['Nature'],
    types: ['journal', 'preprint'],
    tags: ['t2', 't3']
  })
  assert.deepEqual(parsed.webFacets, {
    years: ['2024'],
    venues: ['Science'],
    authors: ['Wang'],
    onlyOA: true
  })
  assert.equal(hasSearchCondition(parsed), true)

  const roundtrip = readSearchParams(new URLSearchParams(toSearchParams(parsed)))
  assert.deepEqual(roundtrip, parsed)
})

test('hasSearchCondition detects facet-only and single-filter queries', () => {
  const base = readSearchParams(new URLSearchParams())
  assert.equal(hasSearchCondition(base), false)

  const facetOnly = readSearchParams(new URLSearchParams('fy=2025'))
  assert.equal(hasSearchCondition(facetOnly), true)

  const typeOnly = readSearchParams(new URLSearchParams('type=preprint'))
  assert.equal(hasSearchCondition(typeOnly), true)

  const pdfOnly = readSearchParams(new URLSearchParams('hasPdf=true'))
  assert.equal(hasSearchCondition(pdfOnly), true)
})

test('buildSearchQuery merges facet tags uniquely and lets facet types override single type', () => {
  const state = readSearchParams(new URLSearchParams(
    'q=electrocatalysis&type=journal&tags=t1,t2&starred=true&scope=all&page=2' +
    '&fy=2023,2024&fa=Alice&fv=JACS&ftype=journal,preprint&ftag=t2,t3'
  ))
  const query = buildSearchQuery(state, 30)
  assert.equal(query.q, 'electrocatalysis')
  assert.equal(query.type, 'journal,preprint')
  assert.deepEqual(query.tags, ['t1', 't2', 't3'])
  assert.deepEqual(query.years, ['2023', '2024'])
  assert.deepEqual(query.authors, ['Alice'])
  assert.deepEqual(query.venues, ['JACS'])
  assert.equal(query.starred, 'true')
  assert.equal(query.scope, 'all')
  assert.equal(query.page, 2)
  assert.equal(query.pageSize, 30)
})

test('api.mjs qs() skips empty/false values and preserves arrays and unicode encoding', () => {
  assert.equal(qs({}), '')
  assert.equal(qs({ a: '', b: null, c: undefined, d: false }), '')
  assert.equal(
    qs({ q: '催化 CO2', tags: ['a', 'b'], hasPdf: true, page: 2, empty: '' }),
    '?q=%E5%82%AC%E5%8C%96+CO2&tags=a&tags=b&hasPdf=true&page=2'
  )
})

test('api listPapers, queryFacets, annotations, and error handling build exact requests', async () => {
  const calls = []
  const origFetch = globalThis.fetch
  globalThis.fetch = async (url, init) => {
    calls.push({ url, init })
    if (url.startsWith('/api/error-case')) {
      return {
        ok: false,
        status: 400,
        text: async () => JSON.stringify({ error: '参数错误' })
      }
    }
    return {
      ok: true,
      status: 200,
      text: async () => JSON.stringify({ ok: true, items: [], total: 0 })
    }
  }

  try {
    const state = readSearchParams(new URLSearchParams('q=catalyst&tags=t1&ftag=t2&fy=2024,2025&scope=fulltext'))
    const query = buildSearchQuery(state, 20)
    await api.listPapers(query)
    await api.queryFacets(query)
    await api.getAnnotations('p/1', undefined, 'si 1.pdf')
    await api.saveAnnotations('p/1', [{ id: 'a1' }])
    await api.externalSearch('MOF', { limit: 10, oa: false })

    assert.equal(
      calls[0].url,
      '/api/papers?q=catalyst&type=all&tags=t1&tags=t2&scope=fulltext&page=1&pageSize=20&years=2024&years=2025'
    )
    assert.equal(calls[0].init.method, 'GET')

    assert.equal(
      calls[1].url,
      '/api/facets?q=catalyst&type=all&tags=t1%2Ct2&hasPdf=false&scope=fulltext&page=1&pageSize=20&years=2024%2C2025'
    )

    assert.equal(calls[2].url, '/api/papers/p%2F1/attachments/si%201.pdf/annotations')
    assert.equal(calls[3].url, '/api/papers/p%2F1/annotations')
    assert.equal(calls[3].init.method, 'PUT')
    assert.equal(calls[3].init.headers['Content-Type'], 'application/json')
    assert.deepEqual(JSON.parse(calls[3].init.body), { items: [{ id: 'a1' }] })

    assert.equal(calls[4].url, '/api/external/search?q=MOF&limit=10')

    await assert.rejects(
      async () => {
        globalThis.fetch = async () => ({
          ok: false,
          status: 422,
          text: async () => JSON.stringify({ error: '无效查询' })
        })
        await api.listPapers({ q: 'bad' })
      },
      err => err instanceof ApiError && err.status === 422 && err.message === '无效查询'
    )
  } finally {
    globalThis.fetch = origFetch
  }
})
