/* 对应原文目录的分类树。

   三处共用同一套树：侧栏「分类文件夹」、导入时的选目录弹窗、分类管理器弹窗。
   路径就是字符串（'电催化/钙钛矿'），根目录是空串；URL 里用 '/' 代表根（空串会被 setHash 丢掉）。

   约定：任何写操作（新建/改名/删除/移动）成功后都会：
     1) 重新拉一次列表并刷新自己这棵树；
     2) 派发 document 事件 'folders:changed'，侧栏与列表各自监听，避免互相 import。 */
import * as util from './util.mjs'
import { api } from './api.mjs'
import { go } from './shell/app-api.mjs'
import { getState } from './state/store.mjs'

  const { h, toast, confirmDialog } = util

  const ROOT_LABEL = '未分类'
  const expanded = new Set()        // 记住展开状态，跨重绘仍在（按路径）
  let cache = { folders: [], rootCount: 0, total: 0 }

  /* ---------------- 数据 ---------------- */

  async function load () {
    const res = await api.listFolders()
    cache = {
      folders: res.folders || [],
      rootCount: res.rootCount || 0,
      total: res.total || 0
    }
    return cache
  }

  /** 扁平列表 → 树（每个节点带 children，按名称排序） */
  function buildTree (data) {
    const src = (data || cache).folders || []
    const byPath = new Map()
    for (const row of src) {
      byPath.set(row.path, { path: row.path, name: row.name, count: row.count || 0,
        countDeep: row.countDeep || 0, children: [] })
    }
    const roots = []
    for (const row of src) {
      const node = byPath.get(row.path)
      const parent = byPath.get(row.parent)
      if (parent) parent.children.push(node)
      else roots.push(node)
    }
    const sortRec = list => {
      list.sort((a, b) => a.name.localeCompare(b.name, 'zh-Hans-CN'))
      list.forEach(item => sortRec(item.children))
    }
    sortRec(roots)
    return roots
  }

  function labelOf (path) {
    const text = String(path || '')
    return text ? text.split('/').pop() : ROOT_LABEL
  }

  function parentOf (path) {
    const text = String(path || '')
    return text.includes('/') ? text.slice(0, text.lastIndexOf('/')) : ''
  }

  /** 跳到列表页并按该文件夹筛选（path='' 表示未分类；options.deep=true 表示含子目录） */
  function navTo (path, options) {
    const opts = options || {}
    const params = { folder: path === '' ? '/' : path }
    if (opts.deep && path !== '') params.deep = '1'
    go('', params)
  }

  function getCache () {
    return cache
  }

  /** 展开某个路径的所有祖先文件夹，方便从主区进入深层目录时侧栏树同步展开 */
  function expandAncestors (path) {
    const text = String(path || '')
    if (!text || !text.includes('/')) return
    const parts = text.split('/')
    for (let i = 1; i < parts.length; i++) {
      expanded.add(parts.slice(0, i).join('/'))
    }
  }

  function notifyChanged () {
    document.dispatchEvent(new CustomEvent('folders:changed'))
  }

  /**
   * 文件夹图标（单色线性，随 currentColor）。
   * 之前用的是 📁/📂 emoji：黄色在侧栏里比导航图标还抢眼，深浅主题下也没法跟着变色；
   * 换成 SVG 后行高、图标列宽都能和导航项对齐，激活时自动跟着文字变主色。
   *   open  展开态（多画一层「翻开的盖子」）
   *   stack 根目录「未分类」用叠放的一摞，跟文件夹区分开
   */
  function folderIco (state) {
    let body
    if (state === 'stack') {
      body = '<rect x="3.2" y="2.9" width="9.6" height="2.6" rx="1"/>' +
        '<rect x="2.3" y="6.1" width="11.4" height="2.6" rx="1"/>' +
        '<rect x="1.7" y="9.3" width="12.6" height="3.4" rx="1"/>'
    } else if (state === 'open') {
      body = '<path d="M2 12V4.4A1.4 1.4 0 0 1 3.4 3h2.8l1.4 1.7h4.4A1.4 1.4 0 0 1 13.4 6v1"/>' +
        '<path d="M2 12l1.6-4.1a1.4 1.4 0 0 1 1.3-.9h9a1 1 0 0 1 .94 1.34l-1.3 3.6a1.4 1.4 0 0 1-1.3.9H3.4A1.4 1.4 0 0 1 2 12z"/>'
    } else {
      body = '<path d="M2 4.4A1.4 1.4 0 0 1 3.4 3h2.8l1.4 1.7h4.4A1.4 1.4 0 0 1 13.4 6v5.6a1.4 1.4 0 0 1-1.4 1.4H3.4A1.4 1.4 0 0 1 2 11.6z"/>'
    }
    return h('span.fld-ico', {
      html: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" stroke="currentColor" ' +
        'stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>'
    })
  }

  /* ---------------- 树 ---------------- */

  /**
   * options:
   *   active    当前选中的路径（'' = 未分类）
   *   onPick(path)  点击一行的回调；不给则默认跳转列表
   *   mode      'side' | 'manager' | 'picker'
   *   onMutate  管理器里改名/删除/新建后触发（默认重载 + 通知）
   */
  function folderTree (options) {
    const opts = options || {}
    // 行是 treeitem，容器就得是 tree，否则读屏拿到的是「一堆孤立条目」，听不出层级
    const el = h('div.fld-tree', { role: 'tree', 'aria-label': '分类文件夹' })
    let active = opts.active == null ? null : opts.active
    let data = cache

    function row (node, depth) {
      const hasKids = node.children.length > 0
      const isOpen = expanded.has(node.path)
      const chevron = hasKids
        ? h('button.fld-caret' + (isOpen ? '.open' : ''), {
          type: 'button',
          'aria-label': isOpen ? '收起' : '展开',
          onclick: ev => {
            ev.stopPropagation()
            if (isOpen) expanded.delete(node.path)
            else expanded.add(node.path)
            paint()
          }
        }, '▸')
        : h('span.fld-caret.fld-caret--empty')

      const actions = opts.mode === 'manager'
        ? h('span.fld-actions',
          h('button.fld-act', {
            type: 'button', title: '重命名',
            onclick: ev => { ev.stopPropagation(); renameFolder(node.path) }
          }, '✎'),
          h('button.fld-act', {
            type: 'button', title: '删除（里面的文献会上移到上一级）',
            onclick: ev => { ev.stopPropagation(); removeFolder(node.path) }
          }, '🗑')
        )
        : null

      // 侧栏用 18px 起、每层 16px（左边留足折叠三角按钮的点击区）；
      // 选择器/管理器在弹窗里，紧凑一点：6px 起、每层 14px
      const side = opts.mode === 'side'
      const indent = (side ? 18 : 6) + depth * (side ? 16 : 14)
      const countText = (opts.mode === 'manager' || side) ? node.countDeep : node.count
      const rowTitle = hasKids
        ? `${node.path}（直属 ${node.count} 篇，含子文件夹共 ${node.countDeep} 篇）`
        : `${node.path}（${node.count} 篇）`
      const rowEl = h('div.fld-row' + (active === node.path ? '.active' : '') + (opts.mode === 'manager' ? '.fld-row--manager' : ''), {
        'data-path': node.path,
        role: 'treeitem',
        'aria-level': String(depth + 1),
        'aria-expanded': hasKids ? (isOpen ? 'true' : 'false') : null,
        tabindex: '0',
        title: rowTitle,
        style: { paddingLeft: indent + 'px' },
        onclick: () => pick(node.path),
        onkeydown: ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(node.path) } }
      },
        chevron,
        folderIco(isOpen && hasKids ? 'open' : 'closed'),
        h('span.fld-name', node.name),
        h('span.fld-count', String(countText)),
        actions
      )
      // 侧栏里 ▸ 绝对定位藏进缩进区：它不再占 flow 宽度，图标才能和导航图标对齐
      if (side) rowEl.style.setProperty('--fld-indent', indent + 'px')

      return h('div.fld-node',
        rowEl,
        hasKids && isOpen ? h('div.fld-kids', { role: 'group' }, node.children.map(child => row(child, depth + 1))) : null
      )
    }

    function paint () {
      const nodes = buildTree(data)
      const rootRow = h('div.fld-row.fld-row--root' + (active === '' ? '.active' : ''), {
        'data-path': '',
        role: 'treeitem',
        'aria-level': '1',
        tabindex: '0',
        title: '未分类（直接放在库里、不属于任何文件夹的文献）',
        style: opts.mode === 'side' ? { paddingLeft: '18px' } : null,
        onclick: () => pick(''),
        onkeydown: ev => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick('') } }
      },
        h('span.fld-caret.fld-caret--empty'),
        folderIco('stack'),
        h('span.fld-name', ROOT_LABEL),
        h('span.fld-count', String(data.rootCount || 0))
      )
      if (!nodes.length) {
        el.replaceChildren(
          rootRow,
          opts.mode === 'side'
            ? h('div.fld-empty', '还没有分类文件夹', h('br'), '点上面的 ＋ 新建一个')
            : h('div.fld-empty', '还没有分类文件夹')
        )
        return
      }
      el.replaceChildren(rootRow, h('div.fld-nodes', nodes.map(node => row(node, 0))))
    }

    function pick (path) {
      active = path
      paint()
      if (opts.onPick) opts.onPick(path)
      else navTo(path)
    }

    async function reload (nextActive) {
      try {
        data = await load()
      } catch (err) {
        el.replaceChildren(h('div.fld-empty', '读取分类失败：' + err.message))
        return
      }
      if (nextActive !== undefined) active = nextActive
      paint()
    }

    paint()
    return {
      el,
      reload,
      setActive (path) {
        active = path
        if (path) expandAncestors(path)
        paint()
      },
      getActive () { return active },
      treeData () { return buildTree(data) }
    }
  }

  /* ---------------- 弹窗骨架 ---------------- */

  function modal (options) {
    const opts = options || {}
    const signal = opts.signal
    const overlay = h('div.fld-modal', { role: 'dialog', 'aria-modal': 'true', 'aria-label': opts.title || '文件夹' })
    const panel = h('div.fld-panel' + (opts.wide ? '.fld-panel--wide' : ''))
    const close = () => {
      document.removeEventListener('keydown', onKey, true)
      if (signal) signal.removeEventListener('abort', close)
      overlay.remove()
    }
    const onKey = ev => {
      if (ev.key !== 'Escape') return
      ev.preventDefault()
      ev.stopImmediatePropagation()
      close()
    }
    overlay.addEventListener('mousedown', ev => { if (ev.target === overlay) close() })
    document.addEventListener('keydown', onKey, true)
    if (signal) signal.addEventListener('abort', close, { once: true })
    panel.appendChild(h('div.fld-head',
      h('div.fld-title', opts.title || '文件夹'),
      h('button.fld-x', { type: 'button', 'aria-label': '关闭', onclick: close }, '✕')))
    if (opts.body) panel.appendChild(opts.body)
    if (opts.footer) panel.appendChild(opts.footer)
    overlay.appendChild(panel)
    document.body.appendChild(overlay)
    return { overlay, panel, close }
  }

  /** 小输入弹窗：返回 Promise<string|null>（取消为 null）。比 window.prompt 在 WebView 里可靠。 */
  function askName (options) {
    const opts = options || {}
    const input = h('input.input', { type: 'text', value: opts.value || '', placeholder: opts.placeholder || '' })
    const body = h('div.fld-body.fld-body--ask', h('div.field', h('label', opts.label || '名称'), input))
    const footer = h('div.fld-foot', h('span.fld-spacer'))
    const dialog = modal({ title: opts.title || '输入名称', body, footer })
    return new Promise(resolve => {
      const done = value => { dialog.close(); resolve(value) }
      footer.appendChild(h('button.btn', { type: 'button', onclick: () => done(null) }, '取消'))
      footer.appendChild(h('button.btn.primary', { type: 'button', onclick: () => done(String(input.value || '').trim() || null) }, '确定'))
      input.addEventListener('keydown', ev => {
        if (ev.key === 'Enter') { ev.preventDefault(); done(String(input.value || '').trim() || null) }
      })
      setTimeout(() => { input.focus(); input.select() }, 30)
    })
  }

  /* ---------------- 写操作 ---------------- */

  async function createFolder (parent, name) {
    const clean = String(name || '').trim()
    if (!clean) { toast('文件夹名不能为空', 'warn'); return null }
    try {
      const res = await api.createFolder({ parent: parent || '', name: clean })
      const path = res.folder && res.folder.path
      if (parent) expanded.add(parent)
      notifyChanged()
      toast('已新建「' + clean + '」')
      return path || null
    } catch (err) {
      toast(err.message, 'error')
      return null
    }
  }

  async function renameFolder (path, name) {
    if (path == null) return false
    const target = name == null
      ? await askName({ title: '重命名文件夹', label: '新的文件夹名', value: labelOf(path) })
      : name
    if (target == null) return false
    try {
      const res = await api.renameFolder(path, target)
      notifyChanged()
      if (res.path && res.path !== path) {
        expanded.delete(path)
        expanded.add(res.path)
        // 正在浏览这个目录（或它的子孙）时，把地址一起改过去，否则列表会突然空掉
        const current = getState().query.folder
        if (current != null && (current === path || String(current).startsWith(path + '/'))) {
          navTo(res.path + String(current).slice(path.length))
        }
      }
      toast(res.renamed ? '已重命名' : '名称没有变化')
      return true
    } catch (err) {
      toast(err.message, 'error')
      return false
    }
  }

  async function removeFolder (path) {
    const ok = await confirmDialog({
      title: '删除文件夹「' + labelOf(path) + '」？',
      body: h('div',
        h('p', '子文件夹会一起删除。'),
        h('p', '里面的原文 PDF 与文献记录会移到上一级（' + (parentOf(path) ? labelOf(parentOf(path)) : '未分类') + '）。')),
      okText: '删除文件夹',
      danger: true
    })
    if (!ok) return false
    try {
      const res = await api.deleteFolder(path)
      notifyChanged()
      const current = getState().query.folder
      if (current != null && (current === path || String(current).startsWith(path + '/'))) {
        // 被删的目录（或其父链）正在浏览：退回它的上一级
        navTo(res.to || '')
      }
      toast('已删除文件夹，' + res.moved + ' 篇文献移到' + (res.to ? '「' + labelOf(res.to) + '」' : '未分类'))
      return true
    } catch (err) {
      toast(err.message, 'error')
      return false
    }
  }

  /* ---------------- 选择器（导入时用） ---------------- */

  /**
   * 打开选目录弹窗。
   *   current  当前路径（'' = 未分类）
   *   onPick(path)  确认后的回调
   */
  function openFolderPicker (options) {
    const opts = options || {}
    const body = h('div.fld-body')
    const footer = h('div.fld-foot')
    let chosen = opts.current == null ? '' : opts.current

    const tree = folderTree({
      mode: 'picker',
      active: chosen,
      onPick: path => {
        chosen = path
        paintPathLabel()
      }
    })
    const pathLabel = h('span.fld-pick-path')
    function paintPathLabel () {
      pathLabel.textContent = chosen ? chosen.split('/').map(labelOf).join(' / ') : ROOT_LABEL
    }
    paintPathLabel()

    const nameInput = h('input.input', {
      type: 'text', placeholder: '新文件夹名',
      onkeydown: ev => { if (ev.key === 'Enter') { ev.preventDefault(); doCreate() } }
    })
    async function doCreate () {
      const parent = chosen
      const created = await createFolder(parent, nameInput.value)
      if (!created) return
      nameInput.value = ''
      chosen = created
      await tree.reload(created)
      paintPathLabel()
    }

    body.appendChild(h('div.fld-left', tree.el))
    body.appendChild(h('div.fld-right',
      h('div.fld-side-title', '当前选择'),
      pathLabel,
      h('div.fld-new-row', nameInput,
        h('button.btn.sm', { type: 'button', onclick: doCreate }, '＋ 新建'))))

    footer.appendChild(h('span.fld-hint', '点左侧目录选择，「＋ 新建」会在选中的目录下建子文件夹'))
    footer.appendChild(h('span.fld-spacer'))
    const dialog = modal({ title: '选择分类文件夹', body, footer, wide: true, signal: opts.signal })
    footer.appendChild(h('button.btn', { type: 'button', onclick: dialog.close }, '取消'))
    footer.appendChild(h('button.btn.primary', {
      type: 'button',
      onclick: () => {
        dialog.close()
        if (opts.onPick) opts.onPick(chosen)
      }
    }, '确定'))
    tree.reload(chosen)
    return dialog
  }

  /* ---------------- 管理器 ---------------- */

  async function openFolderManager () {
    const body = h('div.fld-body.fld-body--manager')
    const tree = folderTree({ mode: 'manager', active: null, onPick: path => tree.setActive(path) })
    const nameInput = h('input.input', {
      type: 'text', placeholder: '新文件夹名',
      onkeydown: ev => { if (ev.key === 'Enter') { ev.preventDefault(); doCreate() } }
    })
    async function doCreate () {
      const parent = tree.getActive() || ''
      const created = await createFolder(parent, nameInput.value)
      if (!created) return
      nameInput.value = ''
      await tree.reload(created)
    }

    body.appendChild(h('div.fld-left', tree.el))
    body.appendChild(h('div.fld-right',
      h('div.fld-side-title', '新建'),
      h('div.fld-new-row', nameInput,
        h('button.btn.sm', { type: 'button', onclick: doCreate }, '＋ 新建')),
      h('div.fld-hint',
        h('p', '点一行选中，「新建」建在它下面。'),
        h('p', '✎ 重命名；🗑 删除（文献会移到上一级）。'),
        h('p', '分类只存在库里，电脑上不会建文件夹。'))))

    const footer = h('div.fld-foot',
      h('span.fld-hint', '共 ' + (cache.total || 0) + ' 篇文献'),
      h('span.fld-spacer'))
    const dialog = modal({ title: '管理分类文件夹', body, footer, wide: true })
    footer.appendChild(h('button.btn.primary', { type: 'button', onclick: dialog.close }, '完成'))

    const onChanged = () => { tree.reload() }
    document.addEventListener('folders:changed', onChanged)
    const origClose = dialog.close
    dialog.close = () => { document.removeEventListener('folders:changed', onChanged); origClose() }
    await tree.reload()
    return dialog
  }

  /* ---------------- 表单里的文件夹字段（导入/编辑用） ---------------- */

  /**
   * 返回一个 .field：显示当前目录 + 「选择」按钮。值为空串表示未分类。
   *   value     初始路径
   *   onChange(path)
   */
  function folderField (options) {
    const opts = options || {}
    let value = opts.value == null ? '' : opts.value

    const text = h('button.fld-field-value', {
      type: 'button',
      title: '点击选择分类文件夹',
      onclick: () => openPicker()
    })
    const paint = () => {
      text.textContent = value ? value.split('/').map(labelOf).join(' / ') : ROOT_LABEL + '（不分类）'
      text.classList.toggle('is-empty', !value)
    }
    paint()

    function openPicker () {
      openFolderPicker({
        current: value,
        onPick: path => {
          value = path
          paint()
          if (opts.onChange) opts.onChange(path)
        }
      })
    }

    const el = h('div.field',
      h('label', '分类文件夹'),
      h('div.fld-field', text,
        h('button.btn.sm', { type: 'button', onclick: openPicker }, '选择…')))

    // 支持外部设值（比如「应用到本批全部」）
    el.setFolder = path => { value = path == null ? '' : path; paint() }
    el.getFolder = () => value
    return el
  }

  export {
    askName,
    load, getCache, buildTree, labelOf, parentOf, navTo, notifyChanged, expandAncestors, folderIco,
    folderTree, openFolderPicker, openFolderManager, folderField,
    createFolder, renameFolder, removeFolder, ROOT_LABEL
  }
  export default {
    askName,
    load, getCache, buildTree, labelOf, parentOf, navTo, notifyChanged, expandAncestors, folderIco,
    folderTree, openFolderPicker, openFolderManager, folderField,
    createFolder, renameFolder, removeFolder, ROOT_LABEL
  }
