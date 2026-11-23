/* 设置页（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from '../util.mjs'
import { api } from '../api.mjs'
import { notifyChanged as refreshFolders, parentOf } from '../folders.mjs'
import { getState } from '../state/store.mjs'
import { refreshMeta, render, savePrefs } from '../shell/app-api.mjs'
import { autoCheckEnabled, autoDownloadEnabled } from '../update-auto.mjs'
import { isWebViewHost, requestInstall, fallbackInstallMessage } from '../update-install.mjs'
import { fetchLocalNotice, openNoticeModal } from '../notice.mjs'

  const { h, toast, rich, confirmDialog, fileSize, formatDate } = util
  const DEFAULT_AI_MAX_TOKENS = 128000
  
  function download (url) {
    const a = h('a', { href: url, download: '' })
    document.body.appendChild(a)
    a.click()
    a.remove()
  }

  function panel (title, body, right) {
    return h('section.panel',
      h('div.panel-head', title, right ? h('div.right', right) : null),
      h('div.panel-body', body)
    )
  }

  /** 无标题面板：给「栏名就是标题」的几个单面板栏用（外观与偏好），避免标题重复两次 */
  function flatPanel (body) {
    return h('section.panel.flat', h('div.panel-body', body))
  }

  /* ---------------- 折叠（栏与栏内子面板共用一套状态） ----------------
     改造前 10 个面板平铺一页：1440 宽下整页 4256px，改个主题也要先滚过「大模型」的整段提示词。
     现在按用途收成 4 栏（外观与偏好 / 资产与备份 / API服务 / 关于），栏内还会再套一层子面板，
     默认只展开第一栏；展开状态统一记在 localStorage（与侧栏收起、文件夹折叠同一套做法），
     下次进来还是原样，隐私模式下写不进去就退回默认，不报错。
     折叠本身用原生 <details>/<summary>：键盘可达、Ctrl+F 能自动展开、没有脚本状态机要维护。 */
  const SEC_KEY = 'set.sections'

  function readSectionState () {
    try { return JSON.parse(localStorage.getItem(SEC_KEY) || '{}') || {} } catch (err) { return {} }
  }

  function writeSectionState (id, open) {
    const all = readSectionState()
    if (all[id] === !!open) return
    all[id] = !!open
    try { localStorage.setItem(SEC_KEY, JSON.stringify(all)) } catch (err) { /* 隐私模式：只影响记忆，不影响折叠 */ }
  }

  /** 打开状态：没存过就用默认值（存过 'false' 也要照用户的选择来） */
  function foldState (id, defaultOpen) {
    const saved = readSectionState()[id]
    return saved == null ? !!defaultOpen : !!saved
  }

  function section (id, title, hint, panels, defaultOpen) {
    const el = h('details.set-sec', { id: 'set-sec-' + id, open: foldState(id, defaultOpen) || null },
      h('summary.set-sec-sum',
        h('span.set-sec-name', title),
        h('span.set-sec-hint', hint),
        // 箭头是纯装饰（形状由 .set-sec-mark 的 clip-path 画出），不给读屏念
        h('span.set-sec-mark', { 'aria-hidden': 'true' })
      ),
      h('div.set-sec-body', panels)
    )
    el.addEventListener('toggle', () => writeSectionState(id, el.open))
    return el
  }

  /** 栏内子面板：标题条本身是 <summary>，点一下展开/收起，状态同样记在 set.sections 里。
      标题栏右侧的东西（如「申请密钥 ↗」）要挡住冒泡，否则点它会顺带把面板收起。 */
  function foldPanel (id, title, body, right, defaultOpen) {
    const el = h('details.panel.sub', { id: 'set-sub-' + id, open: foldState(id, defaultOpen) || null },
      h('summary.panel-head',
        h('span.sub-mark', { 'aria-hidden': 'true' }),
        title,
        right ? h('div.right', { onclick: ev => ev.stopPropagation() }, right) : null
      ),
      h('div.panel-body', body)
    )
    el.addEventListener('toggle', () => writeSectionState(id, el.open))
    return el
  }

  /** 「全部展开 / 全部收起」：想用 Ctrl+F 通页找关键词时，点两下比逐个展开快。
      栏和子面板一起摊开，否则摊开的栏里还是收着的块。 */
  function expandAll (root, open) {
    root.querySelectorAll('details').forEach(item => { item.open = !!open })
  }

  async function settingsHandler (ctx) {
    const { outlet, isStale, params } = ctx
    const state = getState()
    outlet.replaceChildren(h('div.content', h('div.empty', h('div.big', '正在读取设置…'))))

    let health = {}
    let stats = {}
    try {
      const [hRes, sRes] = await Promise.all([api.health(), api.stats()])
      health = hRes
      stats = sRes.stats || {}
    } catch (err) {
      toast('读取服务信息失败：' + err.message, 'error')
    }
    if (isStale()) return
    const libraryStatsText = data => `共 ${data.total ?? 0} 条文献 · ${data.tags ?? 0} 个标签`
    const libraryStats = h('div.page-sub', libraryStatsText(stats))

    /* ---------- 偏好 ---------- */

    const viewSelect = h('select.select', {
      onchange: ev => {
        savePrefs({ view: ev.target.value })
        toast('已切换默认视图')
      }
    },
      h('option', { value: 'cards', selected: state.prefs.view === 'cards' }, '卡片视图'),
      h('option', { value: 'rows', selected: state.prefs.view === 'rows' }, '紧凑列表')
    )

    const sizeSelect = h('select.select', {
      onchange: ev => {
        savePrefs({ pageSize: Number(ev.target.value) })
        toast('每页显示 ' + ev.target.value + ' 条')
      }
    }, ...[20, 30, 50, 100].map(n =>
      h('option', { value: String(n), selected: Number(state.prefs.pageSize) === n }, n + ' 条 / 页')))

    const themeSelect = h('select.select', {
      onchange: ev => {
        savePrefs({ theme: ev.target.value })
        document.documentElement.setAttribute('data-theme', ev.target.value)
        const btn = document.getElementById('theme-toggle')
        if (btn) btn.textContent = ev.target.value === 'night' ? '☾' : '☀'
        toast('主题已切换')
      }
    },
      h('option', { value: 'paper', selected: state.prefs.theme !== 'night' }, '纸感浅色'),
      h('option', { value: 'night', selected: state.prefs.theme === 'night' }, '夜间深色')
    )

    // 「外观与偏好」栏里只有这一块，栏名即标题，所以用无标题面板（别把「外观与偏好」写两遍）
    const prefPanel = flatPanel(h('div.form-grid',
      h('div.field', h('label', '默认视图'), viewSelect),
      h('div.field', h('label', '每页条数'), sizeSelect),
      h('div.field', h('label', '主题'), themeSelect),
      h('div.field', h('label', '快捷键'),
        h('div', { style: { display: 'flex', gap: '6px', flexWrap: 'wrap', paddingTop: '5px' } },
          h('span.kbd', 'Ctrl+K'), h('span', { style: { fontSize: '12.5px', color: 'var(--muted)' } }, '搜索'),
          h('span.kbd', 'N'), h('span', { style: { fontSize: '12.5px', color: 'var(--muted)' } }, '新增'),
          h('span.kbd', 'T'), h('span', { style: { fontSize: '12.5px', color: 'var(--muted)' } }, '主题'),
          h('span.kbd', 'Ctrl+S'), h('span', { style: { fontSize: '12.5px', color: 'var(--muted)' } }, '保存表单')
        ))
    ))

    /* ---------- 导出与备份（导出 + 导入合成一块） ---------- */

    const importInput = h('input', {
      type: 'file', accept: '.json,application/json', style: { display: 'none' },
      onchange: async ev => {
        const file = ev.target.files && ev.target.files[0]
        if (!file) return
        let payload
        try {
          payload = JSON.parse(await file.text())
        } catch (err) {
          toast('这个文件不是合法 JSON：' + err.message, 'error')
          return
        }
        const count = Array.isArray(payload) ? payload.length : (payload.papers ? payload.papers.length : 0)
        const overwrite = await confirmDialog({
          title: '导入文献',
          body: h('div',
            h('p', { text: `文件里有 ${count} 条记录。` }),
            h('p', { text: '「合并」：只补充库里没有的，同 DOI 或同标题的跳过。' }),
            h('p', { text: '「覆盖」：同 DOI 或同标题的记录用文件里的内容替换。' })
          ),
          okText: '合并导入',
          cancelText: '覆盖导入'
        })
        try {
          const res = await api.importJson(Object.assign({}, payload, { overwrite: !overwrite }))
          const r = res.result
          toast(`导入完成：新增 ${r.added} 条，更新 ${r.updated} 条，跳过 ${r.skipped} 条`)
          await refreshMeta()
          render()
        } catch (err) {
          toast('导入失败：' + err.message, 'error')
        }
      }
    })

    // 导出与导入本来就是一件事（导出的 JSON 就是导入要吃的文件），合成一块，按「先出后进」排序：
    // 上面三个导出按钮产出备份，下面这一段把备份吃回来，省掉一块只有一行按钮的面板。
    const exportPanel = foldPanel('assets-export', '导出与备份',
      h('div', { style: { display: 'grid', gap: '11px' } },
        h('p', { style: { color: 'var(--muted)', margin: 0 } },
          '建议每隔一段时间导出一份 JSON 存档，它包含全部文献题录、标签、AI 总结与对话记录；换电脑或误删时用下面的「导入」原样恢复。'),
        h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
          h('button.btn.primary', { type: 'button', onclick: () => download(api.exportUrl('json')) }, '导出全部（JSON 备份）'),
          h('button.btn', { type: 'button', onclick: () => download(api.exportUrl('bibtex')) }, '导出 BibTeX（.bib）'),
          h('button.btn', { type: 'button', onclick: () => download(api.exportUrl('markdown')) }, '导出 Markdown（.md）')
        ),
        h('div.hint', 'BibTeX 可以直接导入 Zotero、EndNote、LaTeX；Markdown 适合放进笔记软件或 Git 仓库。'),
        h('div', { style: { borderTop: '1px solid var(--border)', margin: '1px 0 0' } }),
        h('div.field', h('label', '导入 JSON 备份'),
          h('div', { style: { display: 'flex', gap: '9px', flexWrap: 'wrap', alignItems: 'center' } },
            h('button.btn', { type: 'button', onclick: () => importInput.click() }, '选择 JSON 文件…'),
            h('span.hint', '支持本应用导出的 JSON（或任何含 papers 数组的同结构文件）；选完会让你挑「合并」还是「覆盖」。')
          )),
        importInput
      ))

    /* ---------- PDF 目录 ---------- */

    /** 去掉文件名开头的 id 前缀，得到给人看的名字 */
    function displayNameOf (file) {
      return String(file || '').replace(/^[a-f0-9]{8,}-/i, '')
    }

    /** 人类可读的字节数（util.fileSize 的薄封装，缺了也有兜底） */
    function fileSize (bytes) {
      const n = Number(bytes) || 0
      if (typeof util.fileSize === 'function') return util.fileSize(n)
      if (n < 1024) return n + ' B'
      if (n < 1024 * 1024) return (n / 1024).toFixed(0) + ' KB'
      return (n / 1048576).toFixed(1) + ' MB'
    }

    const tidyStatus = h('div.hint', '')
    const tidyLabel = '重整文件名（原文 + 补充材料）'
    const tidyButton = h('button.btn.sm', {
      type: 'button',
      title: '把 article/ 下的原文与补充材料统一改名：原文叫「标题.pdf」，SI 叫「标题 SI.pdf」',
      onclick: async ev => {
        const btn = ev.target
        const ok = await confirmDialog({
          title: '重整文件名？',
          body: h('div',
            h('p', '原文 PDF → 用文献标题命名；补充材料（SI）→ 用「文献标题 SI」命名（同一篇有多个时自动加序号）。'),
            h('p', '会同步更新文献记录里的引用，文件内容不动；文件名冲突会自动加序号。')
          ),
          okText: '开始重整'
        })
        if (!ok) return
        btn.setAttribute('disabled', '')
        btn.textContent = '处理中…'
        tidyStatus.textContent = '正在重命名…'
        tidyStatus.style.color = ''
        try {
          const res = await api.tidyPdfNames()
          const r = res.result
          const att = r.attachments || { changed: 0, total: 0, results: [] }
          const parts = []
          parts.push(r.total ? `原文 ${r.total} 个，改名 ${r.changed} 个` : '原文无文件')
          parts.push(att.total ? `补充材料 ${att.total} 个，改名 ${att.changed} 个` : '（补充材料无文件）')
          tidyStatus.textContent = '完成：' + parts.join('；') + '。'
          tidyStatus.style.color = 'var(--accent-text)'
          const failed = (r.results || []).concat(att.results || [])
            .filter(x => !x.changed && x.reason && x.reason !== '已经规范')
          if (failed.length) {
            tidyStatus.textContent += ` 其中 ${failed.length} 个失败（${failed[0].reason}）。`
            tidyStatus.style.color = 'var(--danger)'
          }
          toast(r.changed + att.changed ? `已重命名 ${r.changed + att.changed} 个文件` : '文件名已经规范')
          runScan()
          refreshMeta()
        } catch (err) {
          tidyStatus.textContent = '重整失败：' + err.message
          tidyStatus.style.color = 'var(--danger)'
        } finally {
          btn.removeAttribute('disabled')
          btn.textContent = tidyLabel
        }
      }
    }, tidyLabel)

    const scanBox = h('div', { style: { display: 'grid', gap: '8px' } })
    const scanButton = h('button.btn.sm', { type: 'button', onclick: () => runScan(true) }, '重新扫描')
    let scanRequest = 0

    /**
     * 打开页面只盘点。点击按钮才按磁盘分类同步并导入。
     */
    async function runScan (sync = false) {
      const current = ++scanRequest
      if (sync) scanButton.disabled = true
      scanBox.replaceChildren(h('div.hint', sync ? '正在扫描目录并导入文献…' : '正在比对文件内容…'))
      let scan = null
      let papers = []
      let result = null
      try {
        const res = sync ? await api.syncPdfDirectory() : await api.scanPdfs()
        if (sync) {
          result = res
          refreshFolders({ revealNew: true, reveal: (res.imported || []).map(item => parentOf(item.file)) })
          await refreshMeta()
          libraryStats.textContent = libraryStatsText(getState().stats || stats)
        }
        const list = await api.listPapers({ all: true, pageSize: 500 })
        scan = res.scan || {}
        papers = (list.items || []).map(p => ({ id: p.id, title: p.title, hasPdf: !!p.pdfFile }))
      } catch (err) {
        if (current === scanRequest) scanBox.replaceChildren(h('div.hint', '扫描失败：' + err.message))
        return
      } finally {
        if (sync) scanButton.disabled = false
      }
      if (current !== scanRequest || isStale()) return

      const groups = scan.duplicateGroups || []
      const orphans = scan.orphans || []
      const linked = scan.linked || []
      // 重复副本已经在「重复组」里给过清理入口了，这里只留「内容唯一、真没人认领」的文件
      const inDuplicateGroup = new Set(groups.flatMap(g => g.items.map(i => i.file)))
      const uniqueOrphans = orphans.filter(o => !inDuplicateGroup.has(o.file))
      const dupOrphans = orphans.filter(o => inDuplicateGroup.has(o.file))
      const rows = []

      if (result) {
        rows.push(h('div.hint', `扫描完成 · 新增 ${(result.imported || []).length} 篇 · 恢复关联 ${result.relinked || 0} 篇 · 失败 ${(result.failed || []).length} 个`))
        for (const item of result.failed || []) {
          rows.push(h('div.hint', item.file + '：' + item.error))
        }
      }

      rows.push(h('div.hint',
        `共 ${scan.total || 0} 个文件（${fileSize(scan.bytes || 0)}）：` +
        `${linked.length} 个已关联，${uniqueOrphans.length} 个内容唯一但没人认领，` +
        `${dupOrphans.length} 个是重复副本，${groups.length} 组内容重复` +
        (scan.duplicates ? `（可清理 ${scan.duplicates} 个，释放 ${fileSize(scan.wastedBytes || 0)}）` : '')))

      // ① 内容重复的组
      for (const g of groups) {
        const card = h('div', {
          style: {
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '9px 12px',
            display: 'grid', gap: '7px'
          }
        },
          h('div', { style: { fontWeight: '600' } },
            `内容完全相同的 ${g.items.length} 个文件（${fileSize(g.size)}，指纹 ${g.sha256}）`),
          ...g.items.map(item => h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center', flexWrap: 'wrap', fontSize: '12.5px' } },
            h('span', { style: { color: item.file === g.keep ? 'var(--accent-text)' : 'var(--muted)' } },
              (item.file === g.keep ? '★ 保留  ' : '· 副本  ') + item.file),
            item.usedBy.length
              ? h('span.hint', '被「' + String(item.usedBy[0].title).slice(0, 22) + '」使用')
              : h('span.hint', '没有记录使用')
          )),
          h('div.hint', rich('保留理由：' + g.keepReason + '。清理是**移到隔离区**，不是直接删除。')),
          g.removable.length
            ? h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
              ...g.removable.map(file => h('button.btn.sm', {
                type: 'button',
                title: '把这个副本移到 data/backups/orphans/',
                onclick: async () => {
                  const ok = await confirmDialog({
                    title: '清理这个重复副本？',
                    body: `「${file}」会被移到 data/backups/orphans/（不是直接删除），文件内容完全相同，不会影响文献。`,
                    okText: '清理',
                    danger: true
                  })
                  if (!ok) return
                  try {
                    const r = await api.cleanupDuplicates([file])
                    toast(`已移走 ${r.moved} 个副本（释放 ${fileSize(r.bytes || 0)}）`)
                    runScan()
                  } catch (err) { toast(err.message, 'error') }
                }
              }, '清理副本：' + (file.length > 40 ? file.slice(0, 38) + '…' : file))))
            : h('div.hint', '重复的那几份都有记录在用，这里不动它们。'),
          g.blocked && g.blocked.length
            ? h('div.hint', rich('另有 ' + g.blocked.length + ' 个内容相同但**已被记录引用**，已跳过（不会动）。'))
            : null
        )
        rows.push(card)
      }

      if (scan.duplicates) {
        rows.push(h('div',
          h('button.btn.sm.danger', {
            type: 'button',
            onclick: async () => {
              const ok = await confirmDialog({
                title: '清理全部无引用的重复副本？',
                body: `会把 ${scan.duplicates} 个「内容重复且没有记录引用」的文件移到 data/backups/orphans/，` +
                  '释放约 ' + fileSize(scan.wastedBytes || 0) + '。被记录引用的文件一个都不会动。',
                okText: '全部清理',
                danger: true
              })
              if (!ok) return
              try {
                const r = await api.cleanupDuplicates([])
                toast(`已移走 ${r.moved} 个副本（释放 ${fileSize(r.bytes || 0)}）`)
                runScan()
              } catch (err) { toast(err.message, 'error') }
            }
          }, `清理全部 ${scan.duplicates} 个重复副本（释放 ${fileSize(scan.wastedBytes || 0)}）`)))
      }

      // ② 内容唯一的孤儿：给「选一篇文献」的下拉，绝不猜
      for (const o of uniqueOrphans) {
        const select = h('select.select', { style: { maxWidth: '320px' } },
          h('option', { value: '' }, '选择要关联的文献…'),
          ...papers.filter(p => !p.hasPdf).map(p => h('option', { value: p.id }, String(p.title).slice(0, 60))),
          ...papers.filter(p => p.hasPdf).map(p => h('option', { value: p.id }, '（替换现有）' + String(p.title).slice(0, 52)))
        )
        rows.push(h('div', {
          style: {
            border: '1px solid var(--border)', borderRadius: 'var(--radius-sm)', padding: '9px 12px',
            display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap'
          }
        },
          h('div', { style: { flex: '1 1 240px' }, title: o.file },
            h('div', { style: { fontWeight: '600', wordBreak: 'break-all' } }, o.file),
            h('div', { style: { fontSize: '12.5px', color: 'var(--muted)' } },
              fileSize(o.size) + ' · 内容与其他文件都不同，但没有记录引用它')),
          select,
          h('button.btn.sm', {
            type: 'button',
            onclick: async () => {
              const paperId = select.value
              if (!paperId) { toast('先选一篇文献', 'warn'); return }
              const target = papers.find(p => p.id === paperId)
              const ok = await confirmDialog({
                title: '关联这个文件？',
                body: `把「${o.file}」设为「${target.title}」的 PDF 原文` + (target.hasPdf ? '（会替换它现在关联的文件，旧文件不会被删除）' : ''),
                okText: '关联'
              })
              if (!ok) return
              try {
                await api.associatePdf(paperId, o.file)
                toast('已关联')
                runScan()
              } catch (err) { toast(err.message, 'error') }
            }
          }, '关联')
        ))
      }

      // ③ 已关联的（折叠起来，平时不用看）
      if (linked.length) {
        rows.push(h('details', { style: { marginTop: '4px' } },
          h('summary', { style: { cursor: 'pointer', fontSize: '13px', color: 'var(--muted)' } },
            `已关联的 ${linked.length} 个文件（点开查看）`),
          h('div', { style: { display: 'grid', gap: '4px', marginTop: '7px' } },
            ...linked.map(f => h('div', { style: { fontSize: '12.5px', color: 'var(--muted)' }, title: f.file },
              '· ' + f.file + '　' + fileSize(f.size) +
              (f.usedBy[0] ? '　→「' + String(f.usedBy[0].title).slice(0, 30) + '」' : '')))
          )
        ))
      }

      scanBox.replaceChildren(...rows)
    }

    const pdfPanel = foldPanel('assets-pdf', '原文与补充材料',
      h('div', { style: { display: 'grid', gap: '10px' } },
        h('div', { style: { color: 'var(--muted)', fontSize: '13.5px' } },
          '把 PDF 放进 ',
          h('button.link-ish.asset-directory-link', {
            type: 'button', title: '在文件资源管理器中打开原文文件夹',
            onclick: async event => {
              const button = event.currentTarget
              button.disabled = true
              try {
                await api.openPapersDirectory()
                toast('已打开原文文件夹')
              } catch (err) {
                toast(err.message, 'error')
              } finally {
                button.disabled = false
              }
            }
          }, 'data/article/papers/'),
          '，重新扫描后按实际目录导入和分类。' +
          '根目录的 PDF 归入未分类。补充材料单独保存在 data/article/attachments/。'),
        h('div', { style: { display: 'flex', gap: '9px', alignItems: 'center', flexWrap: 'wrap' } },
          scanButton,
          tidyButton
        ),
        tidyStatus,
        scanBox
      )
    )
    runScan()

    /* ---------- 服务信息 ---------- */

    /* 停止服务按钮：panel(title, body, right) 只接受三个参数，它过去以第 4 个参数传入，
       一直没有被渲染（「服务与数据」面板里看不到它）。这里保留实现、暂不挂载，
       与改动前保持一致；要启用只需把它放进上面的 body。 */
    const stopButton = h('button.btn.danger', {
      type: 'button',
      onclick: async () => {
        const ok = await confirmDialog({
          title: '停止文献库服务？',
          body: h('div',
            h('p', '数据会先保存，然后服务进程退出。'),
            h('p', '停止后这个页面就打不开了。想再用，双击文件夹里的「PaperLibrary.exe」即可，文献数据不会丢。')
          ),
          okText: '停止服务',
          danger: true
        })
        if (!ok) return
        try {
          await fetch('/api/shutdown', { method: 'POST' })
          document.body.replaceChildren(h('div.app-loading',
            h('div.boot-mark', '文'),
            h('p', '服务已停止，数据已保存。'),
            h('p', { style: { fontSize: '13px', color: 'var(--faint)' } }, '想继续使用，双击文件夹里的「PaperLibrary.exe」。')
          ))
        } catch (err) {
          toast('停止失败：' + err.message, 'error')
        }
      }
    }, '停止服务')

    /* 服务与数据：原来 7 个字段平铺在一个 auto-fit 网格里——单条长路径被挤成一列、
       折成两行，第 7 项「最近保存」孤零零落到第二行，读起来既不对齐也没有分组。
       改成「一块路径 + 两组统计」：路径整行铺开，统计按语义分成「文献」「服务」两组，
       每组恰好 3 格等宽，任何宽度下都不会再出现落单的一格。 */
    const statCell = (label, value) => h('div.svc-cell',
      h('div.svc-v', String(value)),
      h('div.svc-k', label))

    /* 空闲自动停机的真实配置（--idle-exit）：不能写死「30 秒」，
       用户把参数改成 0 或 120 时这句话得跟着变，否则就是骗人。 */
    let presence = null
    try {
      const res = await fetch('/api/presence/status', { cache: 'no-store' })
      const payload = await res.json()
      presence = payload && payload.presence ? payload.presence : null
    } catch {
      presence = null
    }
    const idleNote = !presence
      ? null
      : presence.enabled
        ? '关掉所有网页后 ' + presence.idleLimitSeconds + ' 秒，服务会自动保存并停止、释放端口；不用再手动停。'
        : '自动停止已关闭（启动参数 --idle-exit 0）：关掉网页后服务会一直运行，需要时用命令行参数 --stop 停止。'

    const infoPanel = panel('服务与数据',
      h('div.svc',
        h('div.svc-path',
          h('div.svc-k', '数据目录'),
          // 路径单独一块：等宽字体 + 浅底，长路径优先整行排完，真放不下才在分隔符处折行
          h('div.svc-path-v', { title: health.dataDir || '' }, health.dataDir || '—')
        ),
        h('div.svc-group',
          h('div.svc-group-head', '文献'),
          h('div.svc-stats',
            statCell('文献总数', stats.total != null ? stats.total : '—'),
            statCell('已关联 PDF', stats.withPdf != null ? stats.withPdf : '—'),
            statCell('丢失的 PDF', stats.pdfMissing != null ? stats.pdfMissing : '—')
          )
        ),
        h('div.svc-group',
          h('div.svc-group-head', '服务'),
          h('div.svc-stats',
            statCell('运行时长', health.uptimeSeconds != null ? Math.max(1, Math.round(health.uptimeSeconds / 60)) + ' 分钟' : '—'),
            statCell('进程号 PID', health.pid ? String(health.pid) : '—'),
            statCell('最近保存', formatDate(health.updatedAt))
          )
        )
      ),
      // 关掉网页就会自动停，不需要手动停止（秒数取服务端 --idle-exit 的真实值）
      idleNote ? h('div.svc-note', idleNote) : null
      /* 注：原来的四行说明已删除；panel(title, body, right) 只收三个参数，
         下面这枚「停止服务」按钮过去是以第 4 个参数传入的、从未被渲染，
         这里保持「不渲染」不变，只把参数收敛成 panel 真正支持的两个。 */
    )

    /* ---------- 学术检索来源（OpenAlex Key 与配额） ---------- */

    let srcSettings = null
    try {
      srcSettings = await api.sourceSettings()
    } catch (err) {
      srcSettings = { settings: { openalex: { hasKey: false, quota: null }, note: err.message } }
    }
    if (isStale()) return
    const src = srcSettings.settings || {}
    const oa = src.openalex || {}
    const oaQuota = oa.quota || null

    /** 把配额快照说成人话：还剩多少、什么时候重置、这份数字算的是哪个「桶」 */
    function quotaText (quota) {
      if (!quota) return '还没有请求过 OpenAlex，暂时不知道剩余额度（检索一次后这里会显示）'
      const resetAt = quota.resetAt ? new Date(quota.resetAt) : null
      const left = resetAt ? Math.max(0, Math.round((resetAt.getTime() - Date.now()) / 60000)) : null
      const when = resetAt
        ? (left >= 60 ? (left / 60).toFixed(1) + ' 小时后重置' : left + ' 分钟后重置')
        : ''
      // 匿名共享 IP 和「带 key 的账号」是两个额度桶，数字不能混着看，所以标注来源
      const bucket = quota.keyed ? '按你的密钥计' : '匿名共享 IP'
      if (quota.exhausted) {
        return '额度已用尽，' + when + '（' + bucket + '；此期间的查询会自动跳过 OpenAlex，不影响其它来源' +
          (quota.keyed ? '' : '。填上自己的 key 会换成你账号的额度，不用等这个重置') + '）'
      }
      if (quota.remaining != null && quota.limit) {
        const pct = Math.round(quota.remaining / quota.limit * 100)
        return '今日剩余额度 ' + quota.remaining + ' / ' + quota.limit + '（' + bucket + '，约 ' + pct + '%）' + (when ? '，' + when : '')
      }
      return '最近一次请求成功（' + bucket + '）' + (when ? '，' + when : '')
    }

    const srcStatus = h('div', {
      style: {
        fontSize: '13px', borderRadius: 'var(--radius-sm)', padding: '9px 12px',
        background: (oaQuota && oaQuota.exhausted) ? 'var(--surface-3)' : (oa.hasKey ? 'var(--accent-soft)' : 'var(--surface-3)'),
        color: (oaQuota && oaQuota.exhausted) ? 'var(--muted)' : (oa.hasKey ? 'var(--accent-text)' : 'var(--muted)')
      }
    }, oa.hasKey
      ? ('OpenAlex 已配置（密钥 ' + (oa.keyHint || '') + (oa.fromEnv ? '，来自环境变量' : '') + '）')
      : 'OpenAlex 尚未配置密钥')
    const srcQuotaLine = h('div.hint', quotaText(oaQuota))
    const srcNote = h('div.hint', src.note || '')

    const fOaKey = h('input.input', {
      type: 'password', value: '', autocomplete: 'off',
      placeholder: oa.hasKey ? '已保存，留空表示不修改' : '粘贴 OpenAlex 的 API Key（openalex.org → Settings → API key）'
    })
    const fContact = h('input.input', {
      type: 'text', value: src.contactEmail || '', autocomplete: 'off',
      placeholder: '可选：你的邮箱（只作为礼貌标识，不再带来额外配额）'
    })
    const oaTestResult = h('div.hint', '')

    /* ---------- 给缺失的摘要做回填 ---------- */
    const oaBackfillResult = h('div.hint', '')
    const oaBackfillBtn = h('button.btn.sm', {
      type: 'button',
      onclick: async ev => {
        const btn = ev.currentTarget
        btn.setAttribute('disabled', '')
        oaBackfillResult.style.color = ''
        oaBackfillResult.replaceChildren(h('span', '正在逐篇解析 PDF 抓摘要，可能要几十秒…'))
        try {
          const res = await api.sourceBackfillAbstracts()
          const rows = (res.results || [])
          oaBackfillResult.style.color = 'var(--muted)'
          oaBackfillResult.replaceChildren(
            h('div', { style: { color: res.filled ? 'var(--accent-text)' : 'var(--muted)', marginBottom: '4px' } },
              `读了 ${res.total} 篇缺摘要的文献，补上 ${res.filled} 篇`),
            h('div', { style: { display: 'grid', gap: '3px' } },
              ...rows.map(r => h('div', null,
                (r.ok ? '✓ ' : '· ') + r.title.slice(0, 46) +
                (r.ok ? '  ← ' + r.sourceLabel + '，' + r.chars + ' 字' : '  ← ' + (r.error || '没抓到')))
              )
            )
          )
        } catch (err) {
          oaBackfillResult.style.color = 'var(--danger)'
          oaBackfillResult.replaceChildren(h('span', '抓取失败：' + err.message))
        } finally {
          btn.removeAttribute('disabled')
        }
      }
    }, '为缺失的摘要重新抓取')

    /**
     * 用服务端返回的 settings 把面板上那几行**一起**刷新。
     *
     * 以前每个按钮只顺手改自己那一两行，于是面板会自相矛盾：状态行写着「已配置（密钥 k4uj…H8Tn）」，
     * 配额行还挂着「未配置 key 时攒下的 429 / 额度已用尽」，备注行还写着「未配置 key」。
     * 现在统一走这里，保证三行说的是同一件事。
     */
    function refreshOaPanel (settings) {
      const s = settings || {}
      const o = s.openalex || {}
      const q = o.quota || null
      srcStatus.textContent = o.hasKey
        ? ('OpenAlex 已配置（密钥 ' + (o.keyHint || '') + (o.fromEnv ? '，来自环境变量' : '') + '）')
        : 'OpenAlex 尚未配置密钥'
      srcStatus.style.background = (q && q.exhausted) ? 'var(--surface-3)' : (o.hasKey ? 'var(--accent-soft)' : 'var(--surface-3)')
      srcStatus.style.color = (q && q.exhausted) ? 'var(--muted)' : (o.hasKey ? 'var(--accent-text)' : 'var(--muted)')
      srcQuotaLine.textContent = quotaText(q)
      srcNote.textContent = s.note || ''
    }

    async function saveSourceSettings (extra) {
      const patch = Object.assign({}, extra || {})
      // 密钥留空 = 「不修改」（输入框上就是这么写的）——所以空字符串不能发出去：
      // 服务端把 '' 当成有效值，会把已保存的密钥悄悄清掉。要清除请走「清除密钥」按钮。
      if (patch.openalexApiKey === undefined) {
        const typed = fOaKey.value.trim()
        if (typed) patch.openalexApiKey = typed
      }
      if (patch.contactEmail === undefined) patch.contactEmail = fContact.value.trim()
      const res = await api.sourceSaveSettings(patch)
      fOaKey.value = ''
      return res
    }

    const oaTestButton = h('button.btn.sm', {
      type: 'button',
      onclick: async ev => {
        const btn = ev.currentTarget
        btn.setAttribute('disabled', '')
        oaTestResult.style.color = ''
        oaTestResult.textContent = '正在用一个轻量请求测试（几乎不花额度）…'
        try {
          // 先把输入框里的 key 存下来，再测，免得「填了没保存就测」
          if (fOaKey.value.trim()) await saveSourceSettings({})
          const res = await api.sourceTestOpenAlex()
          if (res.ok) {
            const q = res.quota || {}
            oaTestResult.style.color = 'var(--accent-text)'
            oaTestResult.textContent = '✓ 可用' + (res.hasKey ? '（带密钥）' : '（匿名）') +
              (q.remaining != null ? '，剩余额度 ' + q.remaining + '/' + q.limit : '') +
              (res.sample ? '；示例结果：' + String(res.sample).slice(0, 40) : '')
          } else {
            oaTestResult.style.color = 'var(--danger)'
            oaTestResult.textContent = '✗ ' + res.error
          }
          if (res.settings) refreshOaPanel(res.settings)
        } catch (err) {
          oaTestResult.style.color = 'var(--danger)'
          oaTestResult.textContent = '✗ ' + err.message
        } finally {
          btn.removeAttribute('disabled')
        }
      }
    }, '保存并测试')

    const sourcesPanel = foldPanel('api-sources', '学术检索来源（OpenAlex）',
      h('div', { style: { display: 'grid', gap: '13px' } },
        h('div', { style: { color: 'var(--muted)', fontSize: '13.5px' } },
          rich('OpenAlex 现在按「每日额度」计费：不填密钥时额度按**共享 IP** 计算（别人用完你就会被限流，' +
            '会出现「429 / 额度已用尽」）；注册免费账号并填入自己的密钥后，额度按你的账号算，且**提升约 10 倍**。' +
            '密钥只存在你本机的 data/sources.json，不回显、不外传。')),
        srcStatus,
        srcQuotaLine,
        h('div.field', h('label', 'OpenAlex API Key'), fOaKey),
        h('div.field', h('label', '联系邮箱（可选）'), fContact,
          h('div.hint', 'OpenAlex 已不再用「友好池」给邮箱加配额，这里只作为请求身份标识；留空也没问题。')),
        h('div', { style: { display: 'flex', gap: '9px', alignItems: 'center', flexWrap: 'wrap' } },
          h('button.btn.sm.primary', {
            type: 'button',
            onclick: async ev => {
              const btn = ev.currentTarget
              btn.setAttribute('disabled', '')
              oaTestResult.style.color = ''
              try {
                const res = await saveSourceSettings({})
                refreshOaPanel(res.settings)
                oaTestResult.style.color = 'var(--accent-text)'
                oaTestResult.textContent = '已保存（密钥不会回显）'
              } catch (err) {
                oaTestResult.style.color = 'var(--danger)'
                oaTestResult.textContent = '保存失败：' + err.message
              } finally {
                btn.removeAttribute('disabled')
              }
            }
          }, '保存'),
          oaTestButton,
          h('a.btn.sm.ghost', {
            href: oa.signupUrl || 'https://openalex.org', target: '_blank', rel: 'noopener'
          }, '去 openalex.org 注册免费账号 ↗'),
          oa.hasKey ? h('button.btn.sm.danger', {
            type: 'button',
            onclick: async () => {
              const ok = await confirmDialog({
                title: '清除 OpenAlex 密钥？',
                body: '清除后 OpenAlex 会退回「匿名共享额度」，可能更容易被限流。其它来源不受影响。',
                okText: '清除密钥',
                danger: true
              })
              if (!ok) return
              try {
                const res = await saveSourceSettings({ openalexApiKey: '' })
                refreshOaPanel(res.settings)
                oaTestResult.style.color = 'var(--muted)'
                oaTestResult.textContent = '已清除密钥'
              } catch (err) { toast(err.message, 'error') }
            }
          }, '清除密钥') : null
        ),
        oaTestResult,
        srcNote,
        h('div', { style: { borderTop: '1px solid var(--border)', margin: '4px 0 0' } }),
        h('div', { style: { display: 'grid', gap: '9px' } },
          h('div', { style: { fontSize: '13px', fontWeight: '600' } }, '补摘要'),
          h('div', { style: { color: 'var(--muted)', fontSize: '13px' } },
            rich('有些出版社（ACS 全系、Nature、旧版 RSC/Wiley）不向 Crossref 交摘要，导入时就会是空的。' +
              '这里按「PDF 本地抽取 → OpenAlex」的顺序给你**没摘要的那些**补上；**只补空的，不会动你已经有的摘要**。')),
          h('div', { style: { display: 'flex', gap: '9px', alignItems: 'center', flexWrap: 'wrap' } },
            oaBackfillBtn),
          oaBackfillResult
        )
      )
    )

    /* ---------- 网络检索（Exa） ---------- */

    let webSettings = null
    try {
      webSettings = await api.webSearchSettings()
    } catch (err) {
      webSettings = { settings: { hasKey: false }, ready: { ready: false, reason: err.message } }
    }
    if (isStale()) return
    const web = webSettings.settings || {}
    const webReady = webSettings.ready || { ready: false, reason: '' }

    const webStatus = h('div', {
      style: {
        fontSize: '13px', borderRadius: 'var(--radius-sm)', padding: '9px 12px',
        background: webReady.ready ? 'var(--accent-soft)' : 'var(--surface-3)',
        color: webReady.ready ? 'var(--accent-text)' : 'var(--muted)'
      }
    }, webReady.ready
      ? ('Exa 已配置（密钥 ' + web.keyHint + '）：对话里的「联网检索」会用它，拿到网页正文片段后再回答')
      : ('尚未配置：' + (webReady.reason || '填上 Exa API Key 就能用；不填则继续用免费的学术检索（OpenAlex 等）')))

    const fExaKey = h('input.input', {
      type: 'password', value: '', autocomplete: 'off',
      placeholder: web.hasKey ? '已保存，留空表示不修改' : '粘贴 Exa 的 API Key（exa.ai 控制台获取）'
    })
    const fExaUseChat = h('input.check', { type: 'checkbox', checked: web.useInChat !== false })
    // 只走最便宜的一档：Search（$7/1k 次）+ 正文片段（$1/1k 页）。
    // 不请求全文、不请求 AI 摘要，也不用 Exa 的 Answer / Deep Search（那两档贵得多）。
    const fExaNum = h('input.input', { type: 'text', inputmode: 'numeric', value: String(web.numResults || 4), style: { maxWidth: '90px' } })
    const fExaCap = h('input.input', { type: 'text', inputmode: 'decimal', value: String(web.monthlyCapUsd != null ? web.monthlyCapUsd : 3), style: { maxWidth: '90px' } })
    const webTestResult = h('div.hint', '')

    const usage = (web.usage || {})
    const price = (web.price || {})
    const usageLine = h('div', {
      style: { fontSize: '13px', color: 'var(--muted)', display: 'grid', gap: '3px' }
    },
      h('div', '本月估算：已检索 ' + (usage.searches || 0) + ' 次 / 取回 ' + (usage.pages || 0) +
        ' 页正文片段，约 $' + (usage.estCostUsd != null ? usage.estCostUsd : 0) +
        '（上限 $' + (web.monthlyCapUsd != null ? web.monthlyCapUsd : 3) + '）'),
      h('div', { style: { fontSize: '12.5px', color: 'var(--faint)' } },
        '按 Exa 官网价目估算：每次检索 $' + (price.searchPerRequest != null ? price.searchPerRequest : 0.007) +
        ' + 每页正文片段 $' + (price.contentPerPage != null ? price.contentPerPage : 0.001) +
        '，当前设置下一次约 $' + (price.perSearchEstimate != null ? price.perSearchEstimate : 0.011) +
        '；$10 免费额度大约可用 ' + Math.max(1, Math.floor(10 / ((price.perSearchEstimate != null ? price.perSearchEstimate : 0.011) || 0.011))) + ' 次/月。')
    )

    async function saveWebSettings (extra) {
      const patch = Object.assign({
        exaApiKey: fExaKey.value.trim(),
        useInChat: !!fExaUseChat.checked,
        allowDeep: false,
        numResults: Number(fExaNum.value) || 4,
        monthlyCapUsd: Number(fExaCap.value) || 3
      }, extra || {})
      const res = await api.webSaveSettings(patch)
      fExaKey.value = ''
      return res
    }

    const webPanel = foldPanel('api-web', '网络检索（Exa）',
      h('div', { style: { display: 'grid', gap: '13px' } },
        webStatus,
        h('div.field', h('label', 'Exa API Key'), fExaKey,
          h('div.hint', '只保存在你本机的 data/websearch.json，不会外传，也不会回显。没填就继续用免费的学术检索。')),
        h('div.form-grid',
          h('div.field', h('label', '每次取几条'), fExaNum,
            h('div.hint', '默认 4 条。条数不影响检索费，只影响「正文片段」按页计费的那部分。')),
          h('div.field', h('label', '本月花费上限（美元）'), fExaCap,
            h('div.hint', '本地估算到顶就停止调用 Exa（不会真的扣钱，只是不再发起请求）'))
        ),
        h('label', { style: { display: 'flex', gap: '7px', alignItems: 'center', fontSize: '13.5px' } },
          fExaUseChat, '允许在对话里使用（总开关）：对话输入框旁勾选「联网搜索」时才会真正调用'),
        usageLine,
        h('div', { style: { display: 'flex', gap: '9px', flexWrap: 'wrap', alignItems: 'center' } },
          h('button.btn.primary', {
            type: 'button',
            onclick: async () => {
              try {
                await saveWebSettings()
                toast('网络检索配置已保存')
                render()
              } catch (err) { toast('保存失败：' + err.message, 'error') }
            }
          }, '保存配置'),
          h('button.btn', {
            type: 'button',
            onclick: async ev => {
              const btn = ev.currentTarget
              btn.setAttribute('disabled', '')
              webTestResult.textContent = '正在保存并测试…'
              webTestResult.style.color = ''
              try {
                await saveWebSettings()
                const res = await api.webTest()
                webTestResult.textContent = '✓ ' + (res.message || 'Exa 可用')
                webTestResult.style.color = 'var(--accent-text)'
                render()
              } catch (err) {
                webTestResult.textContent = '✗ ' + err.message
                webTestResult.style.color = 'var(--danger)'
              } finally {
                btn.removeAttribute('disabled')
              }
            }
          }, '保存并测试连接'),
          h('button.btn.ghost', {
            type: 'button',
            onclick: async () => {
              const ok = await confirmDialog({
                title: '清除 Exa 密钥？',
                body: '清除后对话里的联网检索会回到免费的学术检索（OpenAlex 等）。',
                okText: '清除', danger: true
              })
              if (!ok) return
              try {
                await api.webSaveSettings({ exaApiKey: null })
                toast('已清除 Exa 密钥')
                render()
              } catch (err) { toast('清除失败：' + err.message, 'error') }
            }
          }, '清除密钥'),
          h('button.btn.ghost', {
            type: 'button',
            title: '只重置本地估算的用量计数，不影响 Exa 后台的真实账单',
            onclick: async () => {
              try {
                await api.webSaveSettings({ resetUsage: true })
                toast('已重置本月用量估算')
                render()
              } catch (err) { toast('重置失败：' + err.message, 'error') }
            }
          }, '重置用量'),
          h('span.hint', '按 Exa 官网价目估算，仅走 Search + 正文片段这一档')
        ),
        webTestResult
      )
    )

    /* ---------- 大模型（AI 总结） ---------- */

    let aiSettings = null
    try {
      aiSettings = await api.aiSettings()
    } catch (err) {
      aiSettings = { settings: { presets: [], model: '', baseURL: '', hasKey: false }, ready: { ready: false, reason: err.message } }
    }
    if (isStale()) return
    const ai = aiSettings.settings || {}
    const aiReady = aiSettings.ready || { ready: false, reason: '' }
    // 设置没读出来时（接口报错）表单会是空的，这时**不许保存**，
    // 否则一点「保存并测试」就把 model / baseURL 整体写空（真实踩过的坑）。
    const aiLoaded = !!(ai.baseURL || ai.model)

    const aiStatus = h('div', {
      style: {
        fontSize: '13px', borderRadius: 'var(--radius-sm)', padding: '9px 12px',
        background: aiReady.ready ? 'var(--accent-soft)' : 'var(--surface-3)',
        color: aiReady.ready ? 'var(--accent-text)' : 'var(--muted)'
      }
    }, aiReady.ready
      ? `已配置：${ai.model}${ai.hasKey ? '（密钥 ' + ai.keyHint + '）' : '（本地模型，无需密钥）'}`
      : `尚未可用：${aiReady.reason || '请填写下面的配置'}`)

    const presetSelect = h('select.select', { style: { width: '100%' } },
      ...(ai.presets || []).map(p => h('option', { value: p.id, selected: ai.provider === p.id }, p.label)))
    const fBase = h('input.input', { type: 'text', value: ai.baseURL || '', placeholder: 'https://api.deepseek.com/v1' })
    const fModel = h('input.input', { type: 'text', value: ai.model || '', placeholder: 'deepseek-chat' })
    const fKey = h('input.input', { type: 'password', value: '', placeholder: ai.hasKey ? '已保存，留空表示不修改' : '粘贴你的 API 密钥', autocomplete: 'off' })
    const fTemp = h('input.input', { type: 'text', inputmode: 'decimal', value: String(ai.temperature != null ? ai.temperature : 0.3) })
    const fTokens = h('input.input', { type: 'text', id: 'ai-max-tokens', inputmode: 'numeric', value: String(ai.maxTokens ?? DEFAULT_AI_MAX_TOKENS) })
    const fPrompt = h('textarea.textarea', { rows: 7 }, ai.promptTemplate || '')
    // 新增文献后是否自动生成中文一句话总结（默认开）
    const fAutoSummary = h('input.check', { type: 'checkbox', checked: ai.autoSummaryZh !== false })
    const autoSummaryField = h('label', { style: { display: 'flex', gap: '8px', alignItems: 'center', fontSize: '13.5px' } },
      fAutoSummary, '新增文献后自动生成中文一句话总结（每次约一个很短的模型调用）')

    presetSelect.addEventListener('change', () => {
      const preset = (ai.presets || []).find(p => p.id === presetSelect.value)
      if (!preset) return
      fBase.value = preset.baseURL
      fModel.value = preset.model
      if (preset.docs) {
        fKey.placeholder = '到 ' + preset.docs + ' 申请密钥'
      }
    })

    /* 隐私提示：这段话是「数据会发给谁」，属于看一次就够、但必须能查到的东西。
       平铺在表单底部会挤掉真正要填的参数，所以收进标题栏的琥珀色三角「⚠」：点开在图标右侧展开，
       点页面别处或按 Esc 收起。监听只在打开期间挂着，页面重绘不会越积越多。 */
    const privacyNote = h('div.privacy-note', {
      hidden: '', role: 'note',
      // 气泡长在 <summary> 里：不挡冒泡的话，点文字、拖选都会把面板收起
      onclick: ev => ev.stopPropagation()
    },
      '生成总结或提问时，文献题录、正文与补充材料（SI）的可提取全文会一次发给你选择的模型服务商。' +
      '问答请求也会发送已有的 AI 总结和近期对话。')
    let privacyOutside = null
    let privacyKey = null
    function setPrivacyOpen (open) {
      privacyNote.hidden = !open
      privacyBtn.setAttribute('aria-expanded', open ? 'true' : 'false')
      privacyBtn.classList.toggle('on', !!open)
      if (open) {
        privacyOutside = ev => {
          if (privacyNote.contains(ev.target) || privacyBtn.contains(ev.target)) return
          setPrivacyOpen(false)
        }
        privacyKey = ev => { if (ev.key === 'Escape') setPrivacyOpen(false) }
        document.addEventListener('click', privacyOutside)
        document.addEventListener('keydown', privacyKey)
      } else {
        if (privacyOutside) document.removeEventListener('click', privacyOutside)
        if (privacyKey) document.removeEventListener('keydown', privacyKey)
        privacyOutside = null
        privacyKey = null
      }
    }
    const privacyBtn = h('button.privacy-btn', {
      type: 'button',
      title: '生成总结 / 提问时，哪些内容会被发出去',
      'aria-label': '生成总结 / 提问时，哪些内容会被发出去',
      'aria-expanded': 'false',
      // 标题栏在 <summary> 里：不挡住冒泡的话，点 ⚠ 会顺手把面板收起
      onclick: ev => {
        ev.preventDefault()
        ev.stopPropagation()
        setPrivacyOpen(privacyNote.hidden)
      }
    }, h('span.privacy-mark', { 'aria-hidden': 'true' }, '!'))
    const privacyBox = h('span.privacy', privacyBtn, privacyNote)

    const testResult = h('div.hint', '')
    const aiPanel = foldPanel('api-ai', '大模型',
      h('div', { style: { display: 'grid', gap: '13px' } },
        aiStatus,
        // 成对的字段用两列等宽（.form-grid.pair）：auto-fit 会在宽屏切出 5 列，
        // 服务商和模型名称就会一宽一窄、右边对不齐
        h('div.form-grid.pair',
          h('div.field', h('label', '服务商'), presetSelect),
          h('div.field', h('label', '模型名称'), fModel)
        ),
        h('div.field', h('label', '接口地址（baseURL）'), fBase,
          h('div.hint', 'OpenAI 兼容接口即可。本地 Ollama 填 http://127.0.0.1:11434/v1，无需密钥。')),
        h('div.field', h('label', 'API 密钥'), fKey,
          h('div.hint', '密钥只保存在你这台电脑的 data/settings.json 里，不会上传到任何地方。')),
        h('div.form-grid.pair',
          h('div.field', h('label', '温度 temperature'), fTemp, h('div.hint', '越低越稳定，0.2~0.4 适合总结')),
          h('div.field', h('label', { for: 'ai-max-tokens' }, '最大输出 max_tokens'), fTokens)
        ),
        h('div.field', h('label', '提示词（决定总结的结构）'), fPrompt),
        h('div.field', h('label', '自动生成'), autoSummaryField),
        h('div', { style: { display: 'flex', gap: '9px', flexWrap: 'wrap', alignItems: 'center' } },
          h('button.btn.primary', {
            type: 'button',
            onclick: async () => {
              if (!aiLoaded) { toast('配置还没读取成功，刷新页面后再保存', 'error'); return }
              if (!trimValue(fBase.value) || !trimValue(fModel.value)) {
                toast('接口地址和模型名称不能为空（空值不会覆盖已保存的配置）', 'error')
                return
              }
              try {
                const res = await api.aiSaveSettings({
                  provider: presetSelect.value,
                  baseURL: trimValue(fBase.value),
                  model: trimValue(fModel.value),
                  apiKey: fKey.value.trim(),
                  temperature: Number(fTemp.value) || 0.3,
                  maxTokens: Number(fTokens.value) || DEFAULT_AI_MAX_TOKENS,
                  promptTemplate: fPrompt.value,
                  autoSummaryZh: !!fAutoSummary.checked
                })
                fKey.value = ''
                toast('大模型配置已保存')
                render()
                void res
              } catch (err) { toast('保存失败：' + err.message, 'error') }
            }
          }, '保存配置'),
          h('button.btn', {
            type: 'button',
            onclick: async ev => {
              if (!aiLoaded) { toast('配置还没读取成功，刷新页面后再试', 'error'); return }
              if (!trimValue(fBase.value) || !trimValue(fModel.value)) {
                toast('接口地址和模型名称不能为空', 'error')
                return
              }
              const btn = ev.target
              btn.setAttribute('disabled', '')
              testResult.textContent = '正在测试…'
              try {
                await api.aiSaveSettings({
                  provider: presetSelect.value,
                  baseURL: trimValue(fBase.value),
                  model: trimValue(fModel.value),
                  apiKey: fKey.value.trim(),
                  temperature: Number(fTemp.value) || 0.3,
                  maxTokens: Number(fTokens.value) || DEFAULT_AI_MAX_TOKENS,
                  promptTemplate: fPrompt.value,
                  autoSummaryZh: !!fAutoSummary.checked
                })
                fKey.value = ''
                const res = await api.aiTest()
                testResult.textContent = (res.ok ? '✓ ' : '✗ ') + res.message
                testResult.style.color = res.ok ? 'var(--accent-text)' : 'var(--danger)'
              } catch (err) {
                testResult.textContent = '✗ ' + err.message
                testResult.style.color = 'var(--danger)'
              } finally {
                btn.removeAttribute('disabled')
              }
            }
          }, '保存并测试连接'),
          testResult
        )
      ),
      [privacyBox,
        h('a.btn.sm.ghost', { href: 'https://platform.deepseek.com/api_keys', target: '_blank', rel: 'noopener' }, '申请 DeepSeek 密钥 ↗')]
    )

    /* ---------- 关于与更新（阶段 1：只检查，不下载/不安装） ---------- */

    let currentVersion = null
    try {
      currentVersion = await api.getVersion()
    } catch (err) {
      currentVersion = null
    }
    if (isStale()) return
    const updateCurrentVersion = h('span.update-v', { id: 'update-current-version' },
      currentVersion && currentVersion.tag ? currentVersion.tag : '—')
    const updateStatus = h('div.update-status', { id: 'update-check-status', 'aria-live': 'polite' }, '尚未检查')
    const updateLatest = h('div.update-latest', { id: 'update-latest' })
    const updateProgress = h('div.update-progress', { id: 'update-progress' })
    const updateActions = h('div.update-actions', { id: 'update-actions' })
    let checkRunning = false
    let pollTimer = null
    let readyInfo = null
    let installing = false

    const updateRow = (label, value) => h('div.update-line',
      h('span.update-k', label),
      typeof value === 'string' ? h('span.update-v', value) : value)

    function setUpdateStatus (text, kind) {
      updateStatus.className = 'update-status' + (kind ? ' is-' + kind : '')
      updateStatus.textContent = text
    }

    function showUpdateActions (...nodes) {
      updateActions.replaceChildren(...nodes.filter(Boolean))
    }

    function stopStatusPoll () {
      if (pollTimer) {
        clearTimeout(pollTimer)
        pollTimer = null
      }
    }

    const updateCheckBtn = h('button.btn', {
      id: 'update-check-btn',
      type: 'button',
      onclick: () => runUpdateCheck()
    }, '检查更新')

    const updateDownloadBtn = h('button.btn.primary', {
      id: 'update-download-btn',
      type: 'button',
      onclick: () => startUpdateDownload()
    }, '下载并准备安装')

    const updateCancelBtn = h('button.btn.sm.ghost', {
      id: 'update-cancel-btn',
      type: 'button',
      onclick: () => cancelUpdateDownload()
    }, '取消下载')

    const updateLaterBtn = h('button.btn.sm.ghost', {
      id: 'update-later-btn',
      type: 'button',
      onclick: () => {
        stopStatusPoll()
        setUpdateStatus('已稍后：更新任务在后台继续，下次打开设置页可查看进度', '')
        showUpdateActions(h('button.btn.sm.ghost', {
          id: 'update-resume-btn',
          type: 'button',
          onclick: () => { startStatusPoll() }
        }, '查看进度'))
      }
    }, '稍后')

    const updateRetryBtn = h('button.btn.sm.ghost', {
      id: 'update-retry-btn',
      type: 'button',
      onclick: () => startUpdateDownload()
    }, '重试下载')

    const updateInstallBtn = h('button.btn.primary', {
      id: 'update-install-btn',
      type: 'button',
      onclick: () => prepareInstall()
    }, '立即安装')

    async function prepareInstall () {
      if (installing) return
      const version = readyInfo && readyInfo.version ? readyInfo.version : ''
      const tag = version ? 'v' + version : ''
      const accepted = await confirmDialog({
        title: '立即安装 ' + (tag || ''),
        body: h('div', { style: { display: 'grid', gap: '8px', fontSize: '13.5px' } },
          h('div', '点击「立即安装」后，应用会立即关闭并安装新版本，安装完成后自动重新打开。'),
          h('div', '文献库数据（data/）会保留，不会随更新删除。'),
          h('div', '正在进行中的 AI 请求会被中断，请等它完成或稍后再试。'),
          h('div.hint', '不会静默安装：点「稍后」可以保持当前版本，什么都不改。')
        ),
        okText: '立即安装并关闭',
        cancelText: '稍后'
      })
      if (!accepted) {
        setUpdateStatus('已取消安装，保持当前版本', '')
        return
      }
      installing = true
      updateInstallBtn.disabled = true
      setUpdateStatus('正在准备安装…', 'loading')
      showUpdateActions()
      let appliedVersion = version
      let appliedTag = tag
      try {
        const res = await api.applyUpdate()
        const applied = res && res.apply ? res.apply : {}
        if (applied.version) {
          appliedVersion = applied.version
          appliedTag = 'v' + applied.version
        }
        if (!isWebViewHost()) {
          installing = false
          updateInstallBtn.disabled = false
          setUpdateStatus('安装包已准备好' + (appliedTag ? '：' + appliedTag : '') +
            '。' + fallbackInstallMessage(), '')
          showUpdateActions(updateLaterBtn)
          return
        }
        setUpdateStatus('正在请求启动器安装' + (appliedTag ? ' ' + appliedTag : '') + '…', 'loading')
        await requestInstall({ version: appliedVersion })
        setUpdateStatus('正在关闭并安装' + (appliedTag ? ' ' + appliedTag : '') +
          '…（数据保留，完成后自动重新打开）', 'ok')
        showUpdateActions()
      } catch (err) {
        installing = false
        updateInstallBtn.disabled = false
        const code = err && err.code ? '（' + err.code + '）' : ''
        setUpdateStatus('安装未启动' + code + '：' + (err && err.message ? err.message : '未知错误'), 'err')
        showUpdateActions(updateInstallBtn, updateLaterBtn)
      }
    }

    function renderLatestRows (latest) {
      if (!latest) return []
      const rows = [
        updateRow('最新版本', latest.tag),
        updateRow('发布时间', formatDate(latest.publishedAt)),
        updateRow('大小', fileSize(latest.size))
      ]
      if (latest.notesUrl) {
        rows.push(h('div.update-line',
          h('span.update-k', '更新说明'),
          h('a.link', { href: latest.notesUrl, target: '_blank', rel: 'noopener' }, '在浏览器中查看')))
      }
      return rows
    }

    function renderCheckResult (res) {
      updateProgress.replaceChildren()
      if (res && res.available && res.latest) {
        setUpdateStatus('发现新版本 ' + res.latest.tag, 'ok')
        updateLatest.replaceChildren(...renderLatestRows(res.latest), h('button.btn.sm.ghost', {
          id: 'update-skip-btn',
          type: 'button',
          onclick: () => {
            savePrefs({ updateSkippedVersion: res.latest.tag }, { silent: true })
            toast('已跳过 ' + res.latest.tag)
            setUpdateStatus('发现新版本 ' + res.latest.tag + '（已跳过）', '')
            showUpdateActions()
          }
        }, '跳过此版本'))
        showUpdateActions(updateDownloadBtn)
        return
      }
      setUpdateStatus((res && res.message) || '已是最新版本', 'ok')
      updateLatest.replaceChildren(...renderLatestRows(res && res.latest))
      showUpdateActions()
    }

    function phaseText (snapshot) {
      const progress = snapshot.progress || {}
      const done = Number(progress.downloadedBytes) || 0
      const total = Number(progress.totalBytes) || 0
      const percent = Number(progress.percent) || 0
      switch (snapshot.phase) {
        case 'checking': return '正在检查更新…'
        case 'downloading':
          if (total > 0) {
            return '下载中 ' + percent + '%（' + (done / 1048576).toFixed(1) + ' / ' +
              (total / 1048576).toFixed(1) + ' MB）'
          }
          return '下载中…'
        case 'verifying': return '正在校验下载（大小 / SHA-256 / 签名）…'
        case 'verified': return '校验完成，准备解压…'
        case 'extracting': return '正在解压并检查更新包…'
        case 'ready': return '更新已准备好：' + (snapshot.stagingVersion || snapshot.version) +
          '（点击「立即安装」）'
        case 'failed': return '下载/校验失败：' + (snapshot.error || snapshot.code || '未知错误')
        case 'cancelled': return '已取消下载（临时文件已清理）'
        case 'idle': return '尚未检查'
        default: return String(snapshot.phase || '')
      }
    }

    function renderStatus (snapshot) {
      if (!snapshot) return
      const phase = snapshot.phase
      let kind = ''
      if (phase === 'ready') kind = 'ok'
      else if (phase === 'failed' || phase === 'cancelled') kind = 'err'
      else if (['checking', 'downloading', 'verifying', 'verified', 'extracting'].includes(phase)) kind = 'loading'
      setUpdateStatus(phaseText(snapshot), kind)
      if (phase === 'ready') {
        readyInfo = { version: snapshot.stagingVersion || snapshot.version || '' }
        updateLatest.replaceChildren(
          updateRow('待安装版本', snapshot.stagingVersion || snapshot.version),
          updateRow('当前版本', currentVersion && currentVersion.tag ? currentVersion.tag : '—')
        )
      } else if (phase === 'downloading' && snapshot.progress) {
        updateProgress.replaceChildren(h('div.update-bar', h('div.update-bar-fill', {
          style: { width: Math.max(0, Math.min(100, Number(snapshot.progress.percent) || 0)) + '%' }
        })))
      } else if (phase !== 'failed' && phase !== 'cancelled') {
        updateProgress.replaceChildren()
      }
      const running = ['checking', 'downloading', 'verifying', 'verified', 'extracting'].includes(phase)
      if (running) showUpdateActions(updateCancelBtn, updateLaterBtn)
      else if (phase === 'ready') showUpdateActions(updateInstallBtn, updateLaterBtn)
      else if (phase === 'failed') showUpdateActions(updateRetryBtn, updateLaterBtn)
      else if (phase === 'cancelled') showUpdateActions()
    }

    async function pollStatusLoop () {
      if (isStale()) { stopStatusPoll(); return }
      try {
        const snapshot = await api.updateStatus()
        renderStatus(snapshot)
        if (['checking', 'downloading', 'verifying', 'verified', 'extracting'].includes(snapshot.phase)) {
          pollTimer = setTimeout(pollStatusLoop, 800)
        } else {
          stopStatusPoll()
        }
      } catch (err) {
        stopStatusPoll()
        setUpdateStatus('读取更新状态失败：' + err.message, 'err')
      }
    }

    function startStatusPoll () {
      stopStatusPoll()
      void pollStatusLoop()
    }

    async function runUpdateCheck () {
      if (checkRunning) return
      checkRunning = true
      stopStatusPoll()
      updateCheckBtn.disabled = true
      updateCheckBtn.textContent = '检查中…'
      setUpdateStatus('正在检查更新…', 'loading')
      updateLatest.replaceChildren()
      updateProgress.replaceChildren()
      showUpdateActions()
      try {
        const res = await api.checkUpdate()
        renderCheckResult(res)
      } catch (err) {
        setUpdateStatus('检查失败：' + err.message, 'err')
        updateLatest.replaceChildren(h('div.hint',
          '无法连接更新源或更新清单无效。可以稍后重试；自动检查失败不会打扰你。'))
        showUpdateActions()
      } finally {
        checkRunning = false
        updateCheckBtn.disabled = false
        updateCheckBtn.textContent = '检查更新'
      }
    }

    async function startUpdateDownload () {
      stopStatusPoll()
      setUpdateStatus('正在启动下载…', 'loading')
      showUpdateActions(h('button.btn.sm.ghost', { type: 'button', disabled: true }, '启动中…'))
      try {
        const snapshot = await api.startUpdateDownload()
        renderStatus(snapshot)
        startStatusPoll()
      } catch (err) {
        setUpdateStatus('下载失败：' + err.message, 'err')
        showUpdateActions(updateRetryBtn)
      }
    }

    async function cancelUpdateDownload () {
      try {
        const snapshot = await api.cancelUpdateDownload()
        stopStatusPoll()
        renderStatus(snapshot)
        toast('已取消下载，临时文件已清理')
      } catch (err) {
        setUpdateStatus('取消失败：' + err.message, 'err')
      }
    }

    /* 自动检查（P1-04）：默认开（产品已确认，2026-11-16）；只通知、不下载，可显式关闭 */
    const autoEnabled = autoCheckEnabled(state.prefs)
    const autoToggle = h('input', {
      id: 'update-auto-toggle',
      type: 'checkbox',
      checked: autoEnabled,
      onchange: ev => {
        const on = !!ev.target.checked
        savePrefs({ updateAutoCheck: on }, { silent: true })
        document.dispatchEvent(new CustomEvent('update:auto-changed', { detail: { enabled: on } }))
        toast(on ? '已开启自动检查（只通知，不下载）' : '已关闭自动检查')
      }
    })
    const autoDownloadOn = autoDownloadEnabled(state.prefs)
    const autoDownloadToggle = h('input', {
      id: 'update-auto-download-toggle',
      type: 'checkbox',
      checked: autoDownloadOn,
      onchange: ev => {
        const on = !!ev.target.checked
        savePrefs({ updateAutoDownload: on }, { silent: true })
        toast(on ? '已开启自动下载：发现新版本后自动下载，仍需手动点击立即安装'
          : '已关闭自动下载')
      }
    })
    const skippedTag = state.prefs.updateSkippedVersion || ''
    const skippedBox = h('div.update-skipped', { id: 'update-skipped' },
      skippedTag
        ? h('div.update-line',
          h('span.update-k', '已跳过版本'),
          h('span.update-v', skippedTag),
          h('button.btn.sm.ghost', {
            id: 'update-unskip-btn',
            type: 'button',
            onclick: () => {
              savePrefs({ updateSkippedVersion: '' }, { silent: true })
              toast('已取消跳过 ' + skippedTag)
              skippedBox.replaceChildren()
            }
          }, '取消跳过'))
        : null)

    const noticeOpenBtn = h('button.btn.sm.ghost', {
      id: 'notice-open-btn',
      type: 'button',
      onclick: async () => {
        const notice = await fetchLocalNotice()
        if (!notice) {
          toast('当前暂无可查看的公告')
          return
        }
        const dismissedId = String(getState().prefs.noticeDismissedId || '')
        openNoticeModal(notice, {
          defaultDismissChecked: dismissedId === notice.id,
          onClose: ({ id, dismiss }) => {
            if (dismiss) savePrefs({ noticeDismissedId: id }, { silent: true })
            else if (String(getState().prefs.noticeDismissedId || '') === id) {
              savePrefs({ noticeDismissedId: '' }, { silent: true })
            }
          }
        })
      }
    }, '查看当前公告')

    const aboutPanel = panel('关于与更新',
      h('div.update-box',
        h('div.update-head',
          h('div.update-current', h('span.update-k', '当前版本'), updateCurrentVersion),
          h('div.update-head-actions', noticeOpenBtn, updateCheckBtn)
        ),
        updateStatus,
        updateProgress,
        updateLatest,
        updateActions,
        skippedBox,
        h('label.update-auto', autoToggle,
          h('span', '启动后自动检查更新（只通知，不下载）')),
        h('label.update-auto', autoDownloadToggle,
          h('span', '自动下载更新（默认关；下载后仍需点击立即安装）')),
        h('div.hint',
          '自动检查默认开启（只通知，不下载）：启动约 10–30 秒检查一次、之后每 24 小时最多一次，失败静默；可随时关闭。' +
          '自动下载默认关闭；开启后只自动下载，不会静默安装，下载完成仍需点击「立即安装」。')
      ))

    // 进入设置页时恢复正在进行的下载/已准备好的状态（只读查询，不启动任务）
    api.updateStatus().then(snapshot => {
      if (isStale()) return
      if (snapshot.phase !== 'idle') renderStatus(snapshot)
    }).catch(() => {})

    /* 四栏装配：栏名给人找东西，栏内面板保持原来的标题与结构（内容没重写，只是换了归属与折叠层级）。
       折叠状态见文件顶部的 section() / foldPanel()；栏上那行小字列出里面有什么，收起时也能定位。 */
    const prefsSec = section('prefs', '外观与偏好', '默认视图 · 每页条数 · 主题 · 快捷键',
      prefPanel, true)
    const assetsSec = section('assets', '资产与备份', '原文与补充材料 · 导出与备份',
      [pdfPanel, exportPanel])
    if (params.get('panel') === 'assets-pdf') {
      assetsSec.open = true
      pdfPanel.open = true
    }
    const apiSec = section('api', 'API服务', '大模型 · 学术检索来源（OpenAlex） · 网络检索（Exa）',
      [aiPanel, sourcesPanel, webPanel])
    const aboutSec = section('about', '关于', '关于与更新 · 服务与数据',
      [aboutPanel, infoPanel])

    const allToggle = h('button.btn.sm.ghost', {
      type: 'button',
      title: '展开/收起全部（四栏与栏内面板都算，想用 Ctrl+F 通页找词时方便）',
      onclick: ev => {
        const btn = ev.currentTarget
        const open = btn.dataset.open !== '1'
        expandAll(btn.closest('.content'), open)
        btn.dataset.open = open ? '1' : '0'
        btn.textContent = open ? '全部收起' : '全部展开'
      }
    }, '全部展开')

    outlet.replaceChildren(h('div.content.detail',
      h('div.page-head',
        h('div',
          h('div.page-title', '设置与备份'),
          libraryStats
        ),
        allToggle
      ),
      prefsSec,
      assetsSec,
      apiSec,
      aboutSec
    ))
    document.onkeydown = null
  }

  function trimValue (v) { return String(v == null ? '' : v).trim() }

  export { settingsHandler }
