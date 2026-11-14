/* 设置页（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */
import * as util from '../util.mjs'
import { api } from '../api.mjs'
import { getState } from '../state/store.mjs'
import { refreshMeta, render, savePrefs } from '../shell/app-api.mjs'

  const { h, toast, rich, confirmDialog, fileSize, formatDate } = util
  
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

  async function settingsHandler (ctx) {
    const { outlet, isStale } = ctx
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

    const prefPanel = panel('外观与偏好', h('div.form-grid',
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

    /* ---------- 导出 ---------- */

    /** 打更新包：把当前代码打成 updates/PaperLibrary-<日期>.zip（服务端跑 make-release.js） */
    const releaseStatus = h('div.hint', '')
    const releaseResult = h('div', { style: { display: 'grid', gap: '5px', marginTop: '8px' } })
    const releaseButton = h('button.btn', {
      type: 'button',
      title: '把当前代码打成发布包，覆盖 updates/ 里同一天的上一份',
      onclick: async ev => {
        const btn = ev.currentTarget
        btn.setAttribute('disabled', '')
        const label = btn.textContent
        btn.textContent = '正在打包…'
        releaseStatus.style.color = ''
        releaseStatus.textContent = '正在组装 + 隐私扫描 + 解压实测（约 10~20 秒，别关页面）…'
        releaseResult.replaceChildren()
        try {
          const res = await api.buildRelease()
          const when = new Date(res.mtime)
          const stamp = when.toLocaleString('zh-CN', { hour12: false })
          releaseStatus.style.color = 'var(--accent-text)'
          releaseStatus.textContent = `已生成 ${res.name}（${(res.size / 1048576).toFixed(1)} MB，用时 ${res.seconds} 秒）`
          releaseResult.replaceChildren(
            h('div', { style: { fontSize: '13px' } }, '→ ' + res.relPath + '　' + stamp),
            h('div.hint', res.note || ''),
            h('div.hint', rich('库里当前 ' + res.papers + ' 条文献都在 data/ 里，**不会**进这个包；发给同学的就是这个 zip。')),
            h('button.btn.sm', {
              type: 'button',
              title: '再打一次（比如刚又改了代码）',
              onclick: () => releaseButton.click()
            }, '再打一次')
          )
        } catch (err) {
          releaseStatus.style.color = 'var(--danger)'
          if (err && err.status === 409) {
            releaseStatus.textContent = err.message
          } else if (/隐私扫描/.test(err.message)) {
            releaseStatus.textContent = '已中止：包里发现了不该出现的内容，没有生成新包（上一份还在）。'
            releaseResult.replaceChildren(
              h('pre.import-error', { style: { whiteSpace: 'pre-wrap', margin: 0, fontSize: '12.5px' } },
                String(err.message).split('\n').slice(1).join('\n').trim() || '（明细见服务端日志）'),
              h('div.hint', '照上面指出的文件与类型改掉那一处，再点一次即可——每次都是重新扫描当前代码。'),
              h('button.btn.sm', { type: 'button', onclick: () => releaseButton.click() }, '改好了，重试')
            )
          } else {
            releaseStatus.textContent = '打包失败：' + err.message
            releaseResult.replaceChildren(h('button.btn.sm', { type: 'button', onclick: () => releaseButton.click() }, '重试'))
          }
        } finally {
          btn.removeAttribute('disabled')
          btn.textContent = label
        }
      }
    }, '导出更新包')

    const exportPanel = panel('导出与备份',
      h('div', { style: { display: 'grid', gap: '10px' } },
        h('p', { style: { color: 'var(--muted)', margin: 0 } },
          '建议每隔一段时间导出一份 JSON 存档，它包含全部文献题录、标签、AI 总结与对话记录，可以用下面的「导入」原样恢复。'),
        h('div', { style: { display: 'flex', gap: '8px', flexWrap: 'wrap' } },
          h('button.btn.primary', { type: 'button', onclick: () => download(api.exportUrl('json')) }, '导出全部（JSON 备份）'),
          h('button.btn', { type: 'button', onclick: () => download(api.exportUrl('bibtex')) }, '导出 BibTeX（.bib）'),
          h('button.btn', { type: 'button', onclick: () => download(api.exportUrl('markdown')) }, '导出 Markdown（.md）')
        ),
        h('div.hint', 'BibTeX 可以直接导入 Zotero、EndNote、LaTeX；Markdown 适合放进笔记软件或 Git 仓库。'),
        h('hr', { style: { border: 0, borderTop: '1px solid var(--border)', margin: '2px 0' } }),
        h('div', { style: { color: 'var(--muted)', fontSize: '13.5px' } },
          '改了代码想更新发布包时用下面这个：把当前代码打成离线完整版（含便携 Node），' +
          '产物落在 updates/ 目录，同一天重复打包会覆盖上一份。'),
        h('div', { style: { display: 'flex', gap: '9px', alignItems: 'center', flexWrap: 'wrap' } },
          releaseButton,
          h('span.hint', '打包前会扫描整包，确认没有你的 Key、文献标题与用户名')
        ),
        releaseStatus,
        releaseResult
      )
    )

    /* ---------- 导入 ---------- */

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

    const importPanel = panel('导入',
      h('div', { style: { display: 'grid', gap: '10px' } },
        h('p', { style: { color: 'var(--muted)', margin: 0 } }, '支持导入本应用导出的 JSON 备份（或任何包含 papers 数组的同结构文件）。'),
        h('div', { style: { display: 'flex', gap: '8px' } },
          h('button.btn', { type: 'button', onclick: () => importInput.click() }, '选择 JSON 文件…')
        ),
        importInput
      )
    )

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

    /**
     * 扫描原文目录：**按内容**比对，找出重复文件与孤儿文件。
     * 关联动作由用户从下拉里挑文献（以前是拿文件名去猜，会把别人的文件认错）。
     */
    async function runScan () {
      scanBox.replaceChildren(h('div.hint', '正在比对文件内容…'))
      let scan = null
      let papers = []
      try {
        const [res, list] = await Promise.all([
          api.scanPdfs(),
          api.listPapers({ all: true, pageSize: 500 })
        ])
        scan = res.scan || {}
        papers = (list.items || []).map(p => ({ id: p.id, title: p.title, hasPdf: !!p.pdfFile }))
      } catch (err) {
        scanBox.replaceChildren(h('div.hint', '扫描失败：' + err.message))
        return
      }

      const groups = scan.duplicateGroups || []
      const orphans = scan.orphans || []
      const linked = scan.linked || []
      // 重复副本已经在「重复组」里给过清理入口了，这里只留「内容唯一、真没人认领」的文件
      const inDuplicateGroup = new Set(groups.flatMap(g => g.items.map(i => i.file)))
      const uniqueOrphans = orphans.filter(o => !inDuplicateGroup.has(o.file))
      const dupOrphans = orphans.filter(o => inDuplicateGroup.has(o.file))
      const rows = []

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

    const pdfPanel = panel('原文与补充材料',
      h('div', { style: { display: 'grid', gap: '10px' } },
        h('div', { style: { color: 'var(--muted)', fontSize: '13.5px' } },
          '原文 PDF 与补充材料（SI）都放在 data/article/ 下面（原文在 papers/，补充材料在 attachments/），' +
          '整个 article/ 目录可以直接拷走备份。'),
        h('div', { style: { display: 'flex', gap: '9px', alignItems: 'center', flexWrap: 'wrap' } },
          h('button.btn.sm', { type: 'button', onclick: runScan }, '重新扫描'),
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

    const sourcesPanel = panel('学术检索来源（OpenAlex）',
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

    const webPanel = panel('网络检索（Exa，只走最便宜的基础检索）',
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
    const fTokens = h('input.input', { type: 'text', inputmode: 'numeric', value: String(ai.maxTokens != null ? ai.maxTokens : 1200) })
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

    const testResult = h('div.hint', '')
    const aiPanel = panel('大模型（用于自动总结文献）',
      h('div', { style: { display: 'grid', gap: '13px' } },
        aiStatus,
        h('div.form-grid',
          h('div.field', h('label', '服务商'), presetSelect),
          h('div.field', h('label', '模型名称'), fModel,
            h('div.hint', rich('要填接口的**模型 id**（DeepSeek 就是 deepseek-chat），不是网页版上显示的名字')))
        ),
        h('div.field', h('label', '接口地址（baseURL）'), fBase,
          h('div.hint', 'OpenAI 兼容接口即可。本地 Ollama 填 http://127.0.0.1:11434/v1，无需密钥。')),
        h('div.field', h('label', 'API 密钥'), fKey,
          h('div.hint', '密钥只保存在你这台电脑的 data/settings.json 里，不会上传到任何地方。')),
        h('div.form-grid',
          h('div.field', h('label', '温度 temperature'), fTemp, h('div.hint', '越低越稳定，0.2~0.4 适合总结')),
          h('div.field', h('label', '最大输出 max_tokens'), fTokens, h('div.hint', '总结一般 1000~1500 够用'))
        ),
        h('div.field', h('label', '提示词（决定总结的结构）'), fPrompt),
        h('div.field', h('label', '自动生成'), autoSummaryField,
          h('div.hint', '关掉之后，中文一句话总结只在你手动点「译成中文 / 生成中文总结」时才生成。')),
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
                  maxTokens: Number(fTokens.value) || 1200,
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
                  maxTokens: Number(fTokens.value) || 1200,
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
        ),
        h('div.hint', '隐私提示：点「生成总结」时，这篇文献的题录与正文节选会发送给你选择的模型服务商；密钥与总结结果都只存在本机。')
      ),
      h('a.btn.sm.ghost', { href: 'https://platform.deepseek.com/api_keys', target: '_blank', rel: 'noopener' }, '申请 DeepSeek 密钥 ↗')
    )

    /* ---------- 导入与资料上限（可调，防止电脑太卡） ---------- */

    let limits = { importMaxFiles: 20, chatBodyChars: 14000, chatAttachChars: 12000, chatAttachTotalChars: 24000 }
    const limitRanges = {
      importMaxFiles: [1, 200, 10],
      chatBodyChars: [2000, 60000, 2000],
      chatAttachChars: [2000, 80000, 2000],
      chatAttachTotalChars: [2000, 200000, 2000]
    }
    try {
      const res = await api.getPrefs()
      const got = (res && res.prefs) || {}
      for (const k of Object.keys(limits)) {
        const v = Number(got[k])
        if (Number.isFinite(v)) limits[k] = v
      }
    } catch (err) { /* 读不到就用默认值 */ }
    if (isStale()) return
    const limitStatus = h('div.hint', '')

    function limitInput (key, label, hint) {
      const [min, max, step] = limitRanges[key]
      return h('div.field',
        h('label', label),
        h('input.input', {
          type: 'number', min: String(min), max: String(max), step: String(step), value: String(limits[key]),
          onchange: async ev => {
            const raw = Number(ev.target.value)
            const v = Number.isFinite(raw) ? Math.min(max, Math.max(min, Math.round(raw))) : limits[key]
            ev.target.value = String(v)
            if (v === limits[key]) return
            limits[key] = v
            try {
              await api.savePrefs({ [key]: v })
              limitStatus.style.color = 'var(--accent-text)'
              limitStatus.textContent = '已保存：' + label + ' = ' + v
            } catch (err) {
              limitStatus.style.color = 'var(--danger)'
              limitStatus.textContent = '保存失败：' + err.message
            }
          }
        }),
        h('div.hint', hint)
      )
    }

    const limitsPanel = panel('导入与资料上限', h('div', { style: { display: 'grid', gap: '13px' } },
      h('div', { style: { color: 'var(--muted)', fontSize: '13.5px' } },
        '一次导入太多 PDF 会占内存、让电脑发卡；每次对话送给模型的资料越长，模型越慢也越贵。' +
        '这几个上限都可以按你的机器和网络调——改完立刻生效，不用重启。'),
      h('div.form-grid',
        limitInput('importMaxFiles', '一次最多导入几个 PDF', '范围 1–200，默认 20。超过会提示分批导入。'),
        limitInput('chatAttachChars', '每个补充材料（SI）的字符数', '范围 2000–80000，默认 12000。SI 会按你的问题检索相关片段，不是只取开头。')
      ),
      h('div.form-grid',
        limitInput('chatBodyChars', '正文送给模型的字符数', '范围 2000–60000，默认 14000。'),
        limitInput('chatAttachTotalChars', '所有补充材料合计上限', '范围 2000–200000，默认 24000。')
      ),
      limitStatus
    ))

    outlet.replaceChildren(h('div.content.detail',
      h('div.page-head', h('div',
        h('div.page-title', '设置与备份'),
        h('div.page-sub', `共 ${stats.total != null ? stats.total : 0} 条文献 · ${stats.tags != null ? stats.tags : 0} 个标签`)
      )),
      prefPanel,
      aiPanel,
      sourcesPanel,
      limitsPanel,
      webPanel,
      exportPanel,
      importPanel,
      pdfPanel,
      infoPanel
    ))
    document.onkeydown = null
  }

  function trimValue (v) { return String(v == null ? '' : v).trim() }

  export { settingsHandler }
