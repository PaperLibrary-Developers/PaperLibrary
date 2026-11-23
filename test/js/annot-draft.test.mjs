import assert from 'node:assert/strict'
import test from 'node:test'
import {
  ANNOT_DRAFT_MAX,
  annotDraftKey,
  buildAnnotKey,
  clearAnnotDraft,
  mergeAnnotations,
  readAnnotDraft,
  writeAnnotDraft
} from '../../public/js/views/detail/annot-draft.mjs'

function memoryStorage () {
  const map = new Map()
  return {
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)) },
    removeItem: k => { map.delete(k) },
    map
  }
}

test('buildAnnotKey distinguishes original PDF and attachment files', () => {
  assert.equal(buildAnnotKey('p1', '', ''), 'p1')
  assert.equal(buildAnnotKey('p1', 'si 1.pdf', '2026-01-01'), 'p1/attachment/si%201.pdf/2026-01-01')
})

test('writeAnnotDraft, readAnnotDraft and clearAnnotDraft roundtrip safely', () => {
  const storage = memoryStorage()
  writeAnnotDraft('p1', [{ id: 'a1', text: 'note' }, { text: 'no-id' }], ['a0'], storage)
  assert.deepEqual(readAnnotDraft('p1', storage), {
    items: [{ id: 'a1', text: 'note' }],
    removed: ['a0']
  })

  // Empty draft deletes key
  writeAnnotDraft('p1', [], [], storage)
  assert.equal(storage.getItem(annotDraftKey('p1')), null)

  // Oversized draft is skipped without throwing
  writeAnnotDraft('p1', [{ id: 'big', text: 'x'.repeat(ANNOT_DRAFT_MAX) }], [], storage)
  assert.equal(storage.getItem(annotDraftKey('p1')), null)

  // Corrupted or wrong-version JSON returns empty draft
  storage.setItem(annotDraftKey('p2'), '{broken')
  assert.deepEqual(readAnnotDraft('p2', storage), { items: [], removed: [] })
  storage.setItem(annotDraftKey('p2'), JSON.stringify({ v: 2, items: [{ id: 'a1' }] }))
  assert.deepEqual(readAnnotDraft('p2', storage), { items: [], removed: [] })

  clearAnnotDraft('p2', storage)
  assert.equal(storage.getItem(annotDraftKey('p2')), null)
})

test('mergeAnnotations performs union and prefers newer updatedAt on id conflicts', () => {
  const base = [
    { id: 'a1', text: 'old', updatedAt: '2026-01-01T00:00:00Z' },
    { id: 'a2', text: 'server-only', updatedAt: '2026-01-02T00:00:00Z' }
  ]
  const extra = [
    { id: 'a1', text: 'newer-draft', updatedAt: '2026-01-03T00:00:00Z' },
    { id: 'a3', text: 'draft-only', updatedAt: '2026-01-03T00:00:00Z' },
    { text: 'missing-id' }
  ]
  const merged = mergeAnnotations(base, extra)
  assert.deepEqual(merged.map(x => [x.id, x.text]), [
    ['a1', 'newer-draft'],
    ['a2', 'server-only'],
    ['a3', 'draft-only']
  ])

  // Older draft does not overwrite newer server annotation
  const keptNewer = mergeAnnotations(
    [{ id: 'a1', text: 'server-newer', updatedAt: '2026-01-05T00:00:00Z' }],
    [{ id: 'a1', text: 'draft-older', updatedAt: '2026-01-01T00:00:00Z' }]
  )
  assert.equal(keptNewer[0].text, 'server-newer')
})
