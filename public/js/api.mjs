/* 与本地服务通信的接口封装（ESM：原 IIFE 脚本平移而来，保留原缩进便于与历史版本逐行对照） */

  export class ApiError extends Error {
    constructor (message, status) {
      super(message)
      this.name = 'ApiError'
      this.status = status
    }
  }

  export async function request (method, url, options) {
    const opts = options || {}
    const init = { method, headers: Object.assign({}, opts.headers) }
    if (opts.body instanceof FormData) {
      init.body = opts.body
    } else if (opts.body !== undefined) {
      init.headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(opts.body)
    }
    let res
    try {
      res = await fetch(url, init)
    } catch (err) {
      throw new ApiError('连不上本地服务，请确认文献库服务正在运行（' + err.message + '）', 0)
    }
    const text = await res.text()
    let data = null
    if (text) {
      try { data = JSON.parse(text) } catch { data = null }
    }
    if (!res.ok) {
      const msg = (data && data.error) || text || ('请求失败（HTTP ' + res.status + '）')
      throw new ApiError(msg, res.status)
    }
    return data
  }

  export function qs (params) {
    const sp = new URLSearchParams()
    for (const [key, value] of Object.entries(params || {})) {
      if (value == null || value === '' || value === false) continue
      if (Array.isArray(value)) value.forEach(v => sp.append(key, v))
      else sp.append(key, String(value))
    }
    const s = sp.toString()
    return s ? '?' + s : ''
  }

  /**
   * 读一个 SSE 流（服务端的 {type:'start'|'delta'|'done'|'error'} 协议）。
   * 对话、识图都用它，避免两份几乎一样的解析代码。
   */
  export function streamSse (path, body, hooks) {
    const handlers = hooks || {}
    return new Promise((resolve, reject) => {
      fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body || {})
      }).then(async res => {
        if (!res.ok) {
          let message = 'HTTP ' + res.status
          try {
            const j = await res.json()
            if (j && j.error) message = j.error
          } catch {}
          reject(new ApiError(message, res.status))
          return
        }
        const reader = res.body.getReader()
        const decoder = new TextDecoder()
        let buffer = ''
        let full = ''
        while (true) {
          const { value, done } = await reader.read()
          if (done) break
          buffer += decoder.decode(value, { stream: true })
          let idx
          while ((idx = buffer.indexOf('\n\n')) >= 0) {
            const chunk = buffer.slice(0, idx)
            buffer = buffer.slice(idx + 2)
            const line = chunk.split('\n').find(l => l.startsWith('data:'))
            if (!line) continue
            let payload
            try { payload = JSON.parse(line.slice(5).trim()) } catch { continue }
            if (payload.type === 'delta') {
              full += payload.text || ''
              if (handlers.onDelta) handlers.onDelta(payload.text || '')
            } else if (payload.type === 'start') {
              if (handlers.onStart) handlers.onStart(payload)
            } else if (payload.type === 'progress') {
              if (handlers.onProgress) handlers.onProgress(payload)
            } else if (payload.type === 'done') {
              if (handlers.onDone) handlers.onDone(payload)
              resolve(payload.text || full)
              return
            } else if (payload.type === 'error') {
              reject(new ApiError(payload.message || '请求失败', 500))
              return
            }
          }
        }
        resolve(full)
      }).catch(err => reject(new ApiError('连接服务失败：' + err.message, 0)))
    })
  }

  const api = {
    ApiError,    health: () => request('GET', '/api/health'),
    stats: () => request('GET', '/api/stats'),
    tags: () => request('GET', '/api/tags'),
    facets: () => request('GET', '/api/facets'),
    /** 结果集内的 facet 计数：把当前筛选参数原样带上，服务端在已生效条件之上统计 */
    queryFacets: params => {
      const qs = new URLSearchParams()
      for (const [k, v] of Object.entries(params || {})) {
        if (v === '' || v == null || (Array.isArray(v) && !v.length)) continue
        qs.set(k, Array.isArray(v) ? v.join(',') : String(v))
      }
      return request('GET', '/api/facets' + (qs.toString() ? '?' + qs.toString() : ''))
    },
    renameTag: (from, to) => request('POST', '/api/tags/rename', { body: { from, to } }),
    deleteTag: name => request('POST', '/api/tags/delete', { body: { name } }),

    // 大模型（AI 总结）
    aiSettings: () => request('GET', '/api/ai/settings'),
    aiSaveSettings: patch => request('PUT', '/api/ai/settings', { body: patch }),
    aiTest: () => request('POST', '/api/ai/test'),
    aiSummarizeSync: (paperId, options) => request('POST', '/api/ai/summarize-sync', {
      body: Object.assign({ paperId }, options || {})
    }),

    // 全库智能问答助手（Copilot）
    copilotOverview: () => request('GET', '/api/copilot/overview'),
    copilotAsk: (body, hooks) => streamSse('/api/copilot/ask', body, hooks),

    // 网络检索（Exa）
    webSearchSettings: () => request('GET', '/api/websearch/settings'),
    webSaveSettings: patch => request('PUT', '/api/websearch/settings', { body: patch }),
    webTest: () => request('POST', '/api/websearch/test', { body: {} }),
    webSearch: (q, options) => request('GET', '/api/websearch/search' + qs(Object.assign({ q: q }, options || {}))),

    /** 学术检索来源（OpenAlex）的凭据与配额状态 */
    sourceSettings: () => request('GET', '/api/sources/settings'),
    sourceSaveSettings: patch => request('PUT', '/api/sources/settings', { body: patch }),
    sourceTestOpenAlex: () => request('POST', '/api/sources/openalex/test', { body: {} }),
    /** 给「原文摘要为空」的文献补摘要（只补空的，不覆盖已有） */
    sourceBackfillAbstracts: () => request('POST', '/api/sources/abstracts/backfill', { body: {} }),

    /** 流式总结：onDelta 收到增量文本；返回最终全文 */
    aiSummarizeStream: function (paperId, handlers) {
      const hooks = handlers || {}
      return new Promise((resolve, reject) => {
        fetch('/api/ai/summarize', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ paperId, save: hooks.save !== false })
        }).then(async res => {
          if (!res.ok) {
            let message = 'HTTP ' + res.status
            try {
              const j = await res.json()
              if (j && j.error) message = j.error
            } catch {}
            reject(new ApiError(message, res.status))
            return
          }
          const reader = res.body.getReader()
          const decoder = new TextDecoder()
          let buffer = ''
          let full = ''
          while (true) {
            const { value, done } = await reader.read()
            if (done) break
            buffer += decoder.decode(value, { stream: true })
            let idx
            while ((idx = buffer.indexOf('\n\n')) >= 0) {
              const chunk = buffer.slice(0, idx)
              buffer = buffer.slice(idx + 2)
              const line = chunk.split('\n').find(l => l.startsWith('data:'))
              if (!line) continue
              let payload
              try { payload = JSON.parse(line.slice(5).trim()) } catch { continue }
              if (payload.type === 'delta') {
                full += payload.text || ''
                if (hooks.onDelta) hooks.onDelta(payload.text || '')
              } else if (payload.type === 'start' && hooks.onStart) {
                hooks.onStart(payload)
              } else if (payload.type === 'progress' && hooks.onProgress) {
                hooks.onProgress(payload)
              } else if (payload.type === 'done') {
                if (hooks.onDone) hooks.onDone(payload)
                resolve(payload.text || full)
                return
              } else if (payload.type === 'error') {
                reject(new ApiError(payload.message || '生成失败', 500))
                return
              }
            }
          }
          resolve(full)
        }).catch(err => reject(new ApiError('连接服务失败：' + err.message, 0)))
      })
    },

    // 外部文献检索（OpenAlex + Semantic Scholar）
    externalSearch: (q, options) => request('GET', '/api/external/search' + qs(Object.assign({ q }, options || {}))),

    // PDF 全文
    extractFulltext: id => request('POST', '/api/papers/' + encodeURIComponent(id) + '/fulltext'),

    // PDF 批注（荧光笔 / 便签）：整表读写。坐标是「相对该页的 0~1 归一化值」，
    // 缩放 / 适宽 / 换屏幕都不用重算，前端只管按百分比画。
    getAnnotations: id => request('GET', '/api/papers/' + encodeURIComponent(id) + '/annotations'),
    saveAnnotations: (id, items) => request('PUT', '/api/papers/' + encodeURIComponent(id) + '/annotations',
      { body: { items: Array.isArray(items) ? items : [] } }),

    // 「更多信息」补充材料
    listAttachments: id => request('GET', '/api/papers/' + encodeURIComponent(id) + '/attachments'),
    deleteAttachment: (id, file) => request('DELETE', '/api/papers/' + encodeURIComponent(id) + '/attachments/' + encodeURIComponent(file)),
    attachmentUrl: (id, file) => '/api/papers/' + encodeURIComponent(id) + '/attachments/' + encodeURIComponent(file),
    uploadAttachment: function (id, file) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('POST', '/api/papers/' + encodeURIComponent(id) + '/attachments')
        xhr.setRequestHeader('Content-Type', file.type || 'application/octet-stream')
        xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name || 'attachment.bin'))
        xhr.onload = () => {
          let data = null
          try { data = JSON.parse(xhr.responseText || 'null') } catch {}
          if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) resolve(data)
          else reject(new ApiError((data && data.error) || '上传失败（HTTP ' + xhr.status + '）', xhr.status))
        }
        xhr.onerror = () => reject(new ApiError('上传过程中连接中断', 0))
        xhr.send(file)
      })
    },

    // 摘要翻译成中文（结果会存进文献，切换中英文不会重复调用模型）
    translateSummary: (id, mode) => request('POST', '/api/papers/' + encodeURIComponent(id) + '/summary-zh',
      { body: mode ? { mode: mode } : {} }),

    // 与文献对话
    getChat: id => request('GET', '/api/papers/' + encodeURIComponent(id) + '/chat'),    clearChat: id => request('DELETE', '/api/papers/' + encodeURIComponent(id) + '/chat'),
    // 只删某一条提问（pairIndex = 这条提问在对话数组里的下标）
    deleteChatPair: (id, pairIndex) => request('DELETE',
      '/api/papers/' + encodeURIComponent(id) + '/chat?pair=' + encodeURIComponent(pairIndex)),
    // 识图提问（框选一块图/表，或在对话里贴图）：SSE 流式返回，问答会存进对话历史
    paperVisionStream: function (id, payload, handlers) {
      const hooks = handlers || {}
      const body = {
        question: payload && payload.question ? String(payload.question) : '',
        images: (payload && payload.images) || [],
        label: (payload && payload.label) || ''
      }
      return streamSse('/api/papers/' + encodeURIComponent(id) + '/vision', body, hooks)
    },
    // 把一页的渲染图交给模型抄成文字（扫描件用，逐页调用）
    transcribeImage: (id, image, page) => request('POST', '/api/papers/' + encodeURIComponent(id) + '/transcribe',
      { body: { image: image, page: page } }),
    // 把识别出来的文字存成这篇文献的正文（之后可全文检索/总结/问答）
    saveFulltextText: (id, text) => request('POST', '/api/papers/' + encodeURIComponent(id) + '/fulltext/text',
      { body: { text: text } }),
    paperChatStream: function (id, question, handlers) {
      const hooks = handlers || {}
      return streamSse('/api/papers/' + encodeURIComponent(id) + '/chat', {
        question,
        useLocal: hooks.useLocal !== false,
        useExternal: hooks.useExternal !== false,
        // 勾了「联网搜索」时用 Exa（付费，最便宜的基础档）；否则和以前一样用免费学术检索
        web: hooks.web === true
      }, hooks)
    },
    getFulltext: id => request('GET', '/api/papers/' + encodeURIComponent(id) + '/fulltext'),

    listPapers: params => request('GET', '/api/papers' + qs(params)),
    getPaper: id => request('GET', '/api/papers/' + encodeURIComponent(id)),
    createPaper: paper => request('POST', '/api/papers', { body: paper }),
    updatePaper: (id, patch) => request('PUT', '/api/papers/' + encodeURIComponent(id), { body: patch }),
    deletePaper: id => request('DELETE', '/api/papers/' + encodeURIComponent(id)),
    bulk: (ids, patch) => request('POST', '/api/papers/bulk', { body: { ids, patch } }),
    scanPdfs: () => request('GET', '/api/papers/scan/pdfs'),
    associatePdf: (paperId, file) => request('POST', '/api/papers/associate-pdf', { body: { paperId, file } }),
    cleanupDuplicates: files => request('POST', '/api/papers/cleanup-duplicates', { body: { files: files || [] } }),
    tidyPdfNames: () => request('POST', '/api/papers/tidy-pdf-names'),
    cite: (id, format) => request('GET', '/api/papers/' + encodeURIComponent(id) + '/cite' + qs({ format })),
    pdfUrl: id => '/api/papers/' + encodeURIComponent(id) + '/pdf',

    fetchDoi: doi => request('POST', '/api/fetch/doi', { body: { doi } }),
    fetchTitle: (title, limit) => request('POST', '/api/fetch/title', { body: { title, limit: limit || 6 } }),

    exportUrl: (kind, ids) => '/api/export/' + kind + qs({ ids }),

    /* ---------- 文件夹分类（库内，类似资源管理器） ---------- */
    /** 扁平列表 + 计数；前端自己拼树 */
    listFolders: () => request('GET', '/api/folders'),
    /** 新建：{path} 或 {parent,name}；已存在时幂等返回 */
    createFolder: body => request('POST', '/api/folders', { body }),
    /** 重命名：{path, name}，整棵子树一起改 */
    renameFolder: (path, name) => request('PATCH', '/api/folders', { body: { path, name } }),
    /** 删除：含子文件夹；里面的文献上移到父级 */
    deleteFolder: path => request('DELETE', '/api/folders' + qs({ path })),
    /** 把一批文献移到某个文件夹（path 为空串 = 根目录） */
    movePapers: (ids, path) => request('POST', '/api/folders/move', { body: { ids, path } }),

    /* ---------- 合并后新增的 10 个端点（契约：docs/合并新增端点契约-20261107.md） ---------- */

    /** 图表画廊（跨文献）：q 图注检索 / paperId 单篇 / type 图类型 / page / pageSize(1-100) */
    listFigures: params => request('GET', '/api/figures' + qs(params)),
    /** 图表图片流（支持 Range/ETag，直接当 <img src> 用） */
    figureImageUrl: id => '/api/figures/' + encodeURIComponent(id) + '/image',
    /** 删除画廊里的一条图表记录（不影响文献 / PDF / 其它图表） */
    deleteFigure: id => request('DELETE', '/api/figures/' + encodeURIComponent(id)),
    /** 某篇文献的图表列表 */
    paperFigures: id => request('GET', '/api/papers/' + encodeURIComponent(id) + '/figures'),
    /** 从该文献的 PDF 重新提取图表（force=true 覆盖已有记录） */
    extractFigures: (id, options) => request('POST',
      '/api/papers/' + encodeURIComponent(id) + '/figures/extract',
      { body: { force: !!(options && options.force) } }),

    /** 横向对比模板（≥4 套；无 Key 时也能列出） */
    compareTemplates: () => request('GET', '/api/compare/templates'),
    /** 横向对比：paperIds 2-8 条 + 模板 id */
    compareRun: (paperIds, template) => request('POST', '/api/compare',
      { body: { paperIds: paperIds || [], template: template || '' } }),

    /** 取已保存的深度分析（没有时为 null，不报错） */
    getDeepAnalysis: id => request('GET', '/api/papers/' + encodeURIComponent(id) + '/deep-analysis'),
    /** 运行深度分析（22 字段 + 五维评分；无 Key 时服务端本地降级，仍返回 200） */
    runDeepAnalysis: (id, options) => request('POST',
      '/api/papers/' + encodeURIComponent(id) + '/deep-analysis',
      { body: { force: !!(options && options.force) } }),

    /** 中英术语检索（PaperLibrary 术语表） */
    searchTerms: (q, options) => request('GET', '/api/terms/search' + qs(Object.assign({ q }, options || {}))),
    /** Obsidian 导出 zip 的下载地址（每篇一个 .md + media/ 图表） */
    obsidianExportUrl: () => '/api/export/obsidian',

    /** 打一个发布更新包（服务端跑 tools/make-release.js，产物落到 updates/） */
    buildRelease: () => request('POST', '/api/release/build', { body: {} }),
    importJson: payload => request('POST', '/api/import/json', { body: payload }),

    getPrefs: () => request('GET', '/api/prefs'),
    savePrefs: prefs => request('PUT', '/api/prefs', { body: prefs }),

    /** 上传 PDF：用原始字节流 + 自定义头，避免 multipart 解析 */
    uploadPdf: function (file, paperId, onProgress) {
      return new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest()
        xhr.open('POST', '/api/papers/upload')
        xhr.setRequestHeader('Content-Type', file.type || 'application/pdf')
        xhr.setRequestHeader('X-Filename', encodeURIComponent(file.name || 'document.pdf'))
        xhr.setRequestHeader('X-Paper-Id', paperId || '')
        if (onProgress && xhr.upload) {
          xhr.upload.onprogress = ev => {
            if (ev.lengthComputable) onProgress(ev.loaded / ev.total)
          }
        }
        xhr.onload = () => {
          let data = null
          try { data = JSON.parse(xhr.responseText || 'null') } catch {}
          if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) resolve(data)
          else reject(new ApiError((data && data.error) || '上传失败（HTTP ' + xhr.status + '）', xhr.status))
        }
        xhr.onerror = () => reject(new ApiError('上传过程中连接中断', 0))
        xhr.send(file)
      })
    },

    /**
     * 上传一个 PDF 做「建档预检」：服务端解析题录但不建文献记录。
     * 用查询串传批次号与原始文件名（不用 multipart，和 uploadPdf 一致）。
     */
    preflightPdf: function (file, batch, onProgress) {
      return new Promise((resolve, reject) => {
        const url = '/api/import/pdf?batch=' + encodeURIComponent(batch) +
          '&name=' + encodeURIComponent(file.name || 'document.pdf')
        const xhr = new XMLHttpRequest()
        xhr.open('POST', url)
        xhr.setRequestHeader('Content-Type', file.type || 'application/pdf')
        if (onProgress && xhr.upload) {
          xhr.upload.onprogress = ev => {
            if (ev.lengthComputable) onProgress(ev.loaded / ev.total)
          }
        }
        xhr.onload = () => {
          let data = null
          try { data = JSON.parse(xhr.responseText || 'null') } catch {}
          if (xhr.status >= 200 && xhr.status < 300 && data && data.ok) resolve(data)
          else reject(new ApiError((data && data.error) || '预检失败（HTTP ' + xhr.status + '）', xhr.status))
        }
        xhr.onerror = () => reject(new ApiError('上传过程中连接中断', 0))
        xhr.send(file)
      })
    },

    /** 确认导入：把预检过的临时文件转正并建记录 */
    confirmPdfImport: (batch, papers) => request('POST', '/api/import/pdf/confirm', { body: { batch, papers } }),

    /** 放弃这次导入，清掉服务端的临时文件 */
    cancelPdfImport: batch => request('DELETE', '/api/import/pdf' + qs({ batch }))
  }

  export default api
  export { api }
