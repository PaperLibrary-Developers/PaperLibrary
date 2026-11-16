/**
 * 自动检查更新（阶段 1 / P1-04）：只通知，不下载、不安装。
 *
 * 行为（对应方案一原文 §4.1 / §8）：
 * - 启动后 10–30 秒左右异步检查一次（随机，避免所有客户端同时打 GitHub）；
 * - 运行期间每 24 小时最多一次（用服务端 prefs 的 updateLastCheckAt 做节流，刷新/重开也遵守）；
 * - 失败静默（只记录尝试时间，不弹错误、不阻塞应用）；
 * - 发现新版本且不是“已跳过”的版本时，调用 notify（轻量提示，不打断操作）；
 * - 关闭页面/应用后不再检查：没有后台进程，定时器随页面销毁（本模块不启动任何子进程）。
 *
 * 本文件不碰 DOM，纯调度逻辑，便于用 Node 直接测（test/js/update-auto.test.mjs）。
 */

export const STARTUP_MIN_MS = 10 * 1000
export const STARTUP_MAX_MS = 30 * 1000
export const INTERVAL_MS = 24 * 60 * 60 * 1000

/**
 * 自动检查是否开启：产品已确认**默认开启**（2026-11-16）。
 * 只有用户显式关闭（prefs.updateAutoCheck === false）才停用；prefs 缺失/旧数据视为开启。
 * 默认开启只影响“检查并提示”，不会自动下载或安装。
 */
export function autoCheckEnabled (prefs) {
  return !prefs || prefs.updateAutoCheck !== false
}

/** 启动后首次检查的延迟（10–30 秒，含端点） */
export function startupDelay (random) {
  const r = typeof random === 'function' ? random() : Math.random()
  const span = STARTUP_MAX_MS - STARTUP_MIN_MS + 1
  return STARTUP_MIN_MS + Math.floor(Math.min(0.999999, Math.max(0, r)) * span)
}

/** 距上次检查是否不足 24 小时（lastCheckAt 缺失/0 → 可以检查） */
export function withinInterval (lastCheckAt, now, interval) {
  const last = Number(lastCheckAt) || 0
  const gap = typeof interval === 'number' ? interval : INTERVAL_MS
  return last > 0 && now - last < gap
}

/**
 * 创建自动检查器。依赖全部可注入，便于测试：
 *   enabled()            当前是否开启（默认关/开由调用方决定，本模块不猜）
 *   getLastCheckAt()     上次检查时间戳（毫秒）
 *   saveLastCheckAt(ts)  持久化上次检查时间（成功/失败都记，避免失败后反复重试）
 *   getSkipped()         用户“跳过此版本”的 tag
 *   check()              → /api/update/check 的结果（Promise）
 *   notify(latest)       轻量通知（只有发现新版本且未跳过时调用）
 *   onError(err)         失败回调（默认空 = 静默）
 *   now / setTimer / clearTimer / random  可注入的时钟与定时器
 */
export function createAutoChecker (options) {
  const o = options || {}
  const now = o.now || (() => Date.now())
  const setTimer = o.setTimer || ((fn, ms) => setTimeout(fn, ms))
  const clearTimer = o.clearTimer || (id => clearTimeout(id))
  const random = o.random || Math.random
  const enabled = o.enabled || (() => true)
  const getLastCheckAt = o.getLastCheckAt || (() => 0)
  const saveLastCheckAt = o.saveLastCheckAt || (() => {})
  const getSkipped = o.getSkipped || (() => '')
  const check = o.check
  const notify = o.notify || (() => {})
  const onError = o.onError || (() => {})

  if (typeof check !== 'function') throw new Error('createAutoChecker 需要 check()')

  let timer = null
  let active = false
  let lastResult = null

  function clearTimerId () {
    if (timer != null) {
      try { clearTimer(timer) } catch {}
      timer = null
    }
  }

  function schedule (delayMs) {
    clearTimerId()
    timer = setTimer(async () => {
      timer = null
      try { await runNow() } catch {}
      if (active) schedule(INTERVAL_MS)
    }, delayMs)
  }

  /** 立即走一遍“是否该检查”的判定；返回 disabled/throttled/checked/error */
  async function runNow () {
    if (!enabled()) return 'disabled'
    if (withinInterval(getLastCheckAt(), now())) return 'throttled'
    try {
      const result = await check()
      lastResult = result || null
      saveLastCheckAt(now())
      if (result && result.available && result.latest && result.latest.tag &&
          result.latest.tag !== getSkipped()) {
        notify(result.latest)
      }
      return 'checked'
    } catch (err) {
      // 失败静默：不弹窗、不阻塞；仍记时间避免网络差时反复重试
      saveLastCheckAt(now())
      try { onError(err) } catch {}
      return 'error'
    }
  }

  function start () {
    active = true
    if (!enabled()) return
    if (timer == null) schedule(startupDelay(random))
  }

  function stop () {
    active = false
    clearTimerId()
  }

  function setEnabled (value) {
    if (!value) { stop(); return }
    active = true
    if (enabled() && timer == null) schedule(startupDelay(random))
  }

  function state () {
    return { active, scheduled: timer != null, lastResult }
  }

  return { start, stop, setEnabled, runNow, state }
}
