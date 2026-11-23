/* 批注本地草稿：服务端存不下时的兜底（数据不丢，下次打开自动补） */

export const ANNOT_DRAFT_PREFIX = 'lv:annot-draft:'
export const pendingAnnotationSaves = new Map()
export const ANNOT_DRAFT_MAX = 1024 * 1024 // 单篇草稿上限 1MB，超了就不写（localStorage 只有 5MB）

export function annotDraftKey (paperId) {
  return ANNOT_DRAFT_PREFIX + String(paperId || '')
}

export function buildAnnotKey (paperId, file, addedAt) {
  return file ? paperId + '/attachment/' + encodeURIComponent(file) + '/' + (addedAt || '') : paperId
}

function resolveStorage (storage) {
  if (storage !== undefined) return storage
  try {
    return typeof window !== 'undefined' ? window.localStorage : globalThis.localStorage
  } catch (err) {
    return null
  }
}

/**
 * 读本地草稿。
 * @returns {{items:Array, removed:Array<string>}} 没有/坏数据/隐私模式一律返回空草稿
 */
export function readAnnotDraft (paperId, storage) {
  const empty = { items: [], removed: [] }
  const store = resolveStorage(storage)
  if (!store) return empty
  let raw = null
  try {
    raw = store.getItem(annotDraftKey(paperId))
  } catch (err) {
    return empty // 隐私模式 / 存储被禁
  }
  if (!raw) return empty
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || parsed.v !== 1) return empty
    return {
      items: Array.isArray(parsed.items) ? parsed.items.filter(item => item && item.id) : [],
      removed: Array.isArray(parsed.removed) ? parsed.removed.map(String) : []
    }
  } catch (err) {
    return empty
  }
}

/** 记一份草稿（整表快照 + 这次删掉的 id）；空草稿等于删键 */
export function writeAnnotDraft (paperId, items, removed, storage) {
  const list = Array.isArray(items) ? items : []
  const gone = Array.isArray(removed) ? removed : []
  const store = resolveStorage(storage)
  if (!store) return
  try {
    if (!list.length && !gone.length) {
      store.removeItem(annotDraftKey(paperId))
      return
    }
    const payload = JSON.stringify({ v: 1, at: new Date().toISOString(), items: list, removed: gone })
    if (payload.length > ANNOT_DRAFT_MAX) return
    store.setItem(annotDraftKey(paperId), payload)
  } catch (err) {
    // 配额满 / 被禁：没有兜底也比把详情页弄崩强
  }
}

export function clearAnnotDraft (paperId, storage) {
  const store = resolveStorage(storage)
  if (!store) return
  try {
    store.removeItem(annotDraftKey(paperId))
  } catch (err) {}
}

/**
 * 合并两份批注：同一个 id 用 updatedAt 较新的那份（本地草稿通常是后改的，所以多数时候它胜出）。
 * 注意这是**并集**：服务端独有、草稿里没有的条目会保留（宁可在极少数情况下把离线删掉的
 * 一条又找回来，也不能把别人新加的批注弄丢）。真正删除靠草稿里的 removed 列表排除。
 */
export function mergeAnnotations (base, extra) {
  const out = []
  const index = new Map()
  const list = [].concat(Array.isArray(base) ? base : [], Array.isArray(extra) ? extra : [])
  for (const item of list) {
    if (!item || !item.id) continue
    const at = index.get(item.id)
    if (at === undefined) {
      index.set(item.id, out.length)
      out.push(item)
      continue
    }
    const prev = out[at]
    if (String(item.updatedAt || '') >= String(prev.updatedAt || '')) out[at] = item
  }
  return out
}
