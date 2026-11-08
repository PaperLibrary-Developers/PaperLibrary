/**
 * 全局状态（P4.4）：state 只住在这里。
 *
 * 视图不再各自持有一份可变的全局 state：
 *   - 读：`getState()`
 *   - 写：`patch({ ... })`，写完自动通知订阅者
 *   - 订阅：`subscribe(fn)`，返回取消订阅函数
 *
 * 为什么 getState() 返回的是同一个 state 对象而不是深拷贝：
 * 视图里有大量「异步拿到数据后再读一次当前筛选条件」的写法（`const s = getState()`
 * 之后 await），返回深拷贝会让它们在 await 之后读到**过期**的快照，等于改语义。
 * 收口的目标是「写入只有一个入口」，所以写动作全部走 patch()，读共享同一份对象。
 * 将来要真正冻结成只读快照时，把 getState() 换成浅拷贝并逐个修掉「读后写」即可。
 */

const STORE_KEY = 'lv:prefs'

/** 本地存储里的偏好（隐私模式/无 localStorage 时退化为空对象） */
function readStoredPrefs () {
  try {
    if (typeof localStorage === 'undefined') return {}
    return JSON.parse(localStorage.getItem(STORE_KEY) || '{}') || {}
  } catch {
    return {}
  }
}

const state = {
  prefs: Object.assign({ theme: 'paper', view: 'cards', sort: 'updatedAt', pageSize: 30 }, readStoredPrefs()),
  // folder: null = 不按文件夹过滤；'' = 只看未分类（根）；'A/B' = 某个文件夹
  query: { q: '', qRaw: '', tags: [], extraTags: [], type: 'all', starred: false, sort: '', page: 1, folder: null },
  tags: [],
  stats: null,
  titleMap: new Map(),
  booted: false
}

/** 订阅者集合：state 发生变化时同步调用（回调里不要再 patch，否则会自己叫自己） */
const listeners = new Set()

/** 只读入口：拿到的是当前状态对象（见文件头说明，不深拷贝） */
export function getState () {
  return state
}

/**
 * 写入口：把补丁合并进 state。
 * `patch({ tags: [...] })` 合并顶层键；`patch({ query: { q: 'x' } })` 只覆盖 query 里的 q。
 * 返回被改到的键名数组，方便调用方判断要不要重绘。
 */
export function patch (changes) {
  if (!changes) return []
  const changed = []
  for (const [key, value] of Object.entries(changes)) {
    if (key === 'query') {
      Object.assign(state.query, value || {})
      changed.push('query')
      continue
    }
    if (key === 'prefs') {
      Object.assign(state.prefs, value || {})
      changed.push('prefs')
      continue
    }
    state[key] = value
    changed.push(key)
  }
  if (changed.length) notify(changed)
  return changed
}

/** 订阅 state 变化；返回取消订阅函数 */
export function subscribe (fn) {
  if (typeof fn !== 'function') return () => {}
  listeners.add(fn)
  return () => listeners.delete(fn)
}

function notify (changed) {
  for (const fn of listeners) {
    try {
      fn(state, changed)
    } catch (err) {
      // 订阅者自己的异常不能打断写入流程（也不能让后续订阅者收不到通知）
      console.error('[state] 订阅者报错：', err)
    }
  }
}

/** 把当前偏好写进 localStorage（纯本地的那份；服务端同步由 app.savePrefs 负责） */
export function persistPrefs () {
  try {
    if (typeof localStorage === 'undefined') return false
    localStorage.setItem(STORE_KEY, JSON.stringify(state.prefs))
    return true
  } catch {
    return false
  }
}

export { STORE_KEY }
