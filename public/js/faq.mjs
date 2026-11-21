/* Copilot 的离线知识：未配置大模型时，这个页面完全靠这些固定内容工作。

   每条 FAQ 都是「一句问题 + 一段能照着做的回答 + 直达按钮」，
   不做语义理解，只按关键词做包含匹配——目标是新手输入「导入」「备份」也能立刻找到那一条。
   links 里的 hash 都是应用内路由，点了直接过去。 */

export const QUICK_START = [
  {
    title: '导入文献',
    desc: '把 PDF 拖进虚线框，或粘 DOI 抓题录。',
    cta: '去导入',
    hash: '#/new'
  },
  {
    title: '整理分类',
    desc: '左侧栏建分类文件夹，单篇或批量移动。',
    cta: '回文献库',
    hash: '#/'
  },
  {
    title: '查找文献',
    desc: '关键词、#标签、作者/年份字段限定。',
    cta: '去搜索',
    hash: '#/search'
  },
  {
    title: '导出引用',
    desc: '详情页引用面板：GB/T 7714、BibTeX。',
    cta: '看手册',
    hash: '#/guide'
  }
]

export const FAQ_GROUPS = [
  {
    name: '导入与入库',
    items: [
      {
        q: '怎么把下载好的 PDF 导进来？',
        a: '点左侧「＋ 新增文献」（或地址栏 #/new），把 PDF 拖进虚线框，可一次拖多篇。每篇会生成一张表单，核对标题、作者、年份后点「导入」即可；题录识别不全也可以手动改。',
        keywords: ['导入', 'pdf', '拖', '新建', '添加', '入库'],
        links: [{ label: '去导入', hash: '#/new' }]
      },
      {
        q: '粘贴 DOI 没反应、抓不到题录？',
        a: '先确认 DOI 完整（形如 10.1021/jacs.0c01234）。出版社接口偶尔限流，可以过一会儿再试；也可以先手动填写题录，或把 PDF 拖进来让系统解析。',
        keywords: ['doi', '抓取', '题录', '失败', '没反应', '元数据'],
        links: [{ label: '去新建', hash: '#/new' }]
      },
      {
        q: '已经有一堆 PDF 放在文件夹里，能批量识别吗？',
        a: '把 PDF 放进 data/article/papers/ 的分类子目录，再到「设置 → 资产与备份 → 原文与补充材料」点「重新扫描」。文件会按目录就地入库，根目录的 PDF 归入未分类；也可以到 #/new 一次多选拖入。',
        keywords: ['批量', '扫描', '目录', '文件夹', '已有', '导入'],
        links: [{ label: '去设置', hash: '#/settings' }]
      },
      {
        q: '导入后摘要是空的，怎么办？',
        a: '有些出版社（ACS 全系、Nature、部分 Wiley/RSC）不向 Crossref 交摘要。到「设置 → API服务 → 学术检索来源（OpenAlex）」点「补摘要」，它会按「PDF 本地抽取 → OpenAlex」的顺序补，只补空的，不动你已经有的内容。',
        keywords: ['摘要', '空', '补摘要', 'openalex', '没有摘要'],
        links: [{ label: '去设置', hash: '#/settings' }]
      }
    ]
  },
  {
    name: '分类与整理',
    items: [
      {
        q: '怎么新建、重命名、删除分类文件夹？',
        a: '分类对应 data/article/papers/ 下的实际目录。左侧栏「＋」新建子目录，「⚙」打开管理器。重命名会同步修改磁盘目录；删除分类会将其中的原文 PDF 和文献移到上一级。',
        keywords: ['分类', '文件夹', '新建', '重命名', '改名', '删除', '目录'],
        links: [{ label: '回文献库', hash: '#/' }, { label: '看手册 3.2', hash: '#/guide' }]
      },
      {
        q: '怎么改某一篇文献的分类？',
        a: '三个入口任选：① 列表每行最左边（星标右边）的文件夹按钮；② 卡片底部那个文件夹胶囊；③ 打开文献详情页，「文献信息」里的「分类」。点开后在目录树里选一个即可，弹窗里也能直接新建。',
        keywords: ['改', '分类', '移动', '单篇', '归属', '文件夹'],
        links: [{ label: '回文献库', hash: '#/' }]
      },
      {
        q: '怎么一次移动很多篇文献？',
        a: '列表顶部点「批量」→ 勾选文献（或「全选」）→ 底部浮动条点「移动到目录」→ 选目录确定。完成后会提示移动了几篇，侧栏计数和列表自动刷新。',
        keywords: ['批量', '移动', '多篇', '一起', '分类'],
        links: [{ label: '回文献库', hash: '#/' }]
      }
    ]
  },
  {
    name: '检索',
    items: [
      {
        q: '怎么按标签找文献？',
        a: '顶部搜索框输入 # 会出现标签联想，选一个即可；也可以直接写 #钙钛矿。列表页的筛选条上也有标签 chips，点一下就是同一个筛选。',
        keywords: ['标签', 'tag', '#', '筛选', '检索'],
        links: [{ label: '去搜索', hash: '#/search' }]
      },
      {
        q: '怎么按作者、年份、期刊限定？',
        a: '用字段前缀：author:、year:、venue:，可以组合，例如 author:李雷 AND year:2024。支持 AND / OR / NOT（中文可写 与 / 或 / 非），引号里的短语会整体匹配。',
        keywords: ['作者', '年份', '期刊', '字段', 'author', 'year', 'venue', '布尔'],
        links: [{ label: '去搜索', hash: '#/search' }]
      },
      {
        q: '搜不到正文里的词，怎么办？',
        a: '在搜索页把检索范围从「题录」切到「PDF 全文正文」，同时这篇文献要先有可读正文：详情页点过「提取全文」，或导入 PDF 时已自动解析。扫描版 PDF 需要先做文字识别。',
        keywords: ['全文', '正文', '搜不到', '提取', 'pdf', '扫描件'],
        links: [{ label: '去搜索', hash: '#/search' }]
      }
    ]
  },
  {
    name: '阅读与加工',
    items: [
      {
        q: 'AI 总结 / 对话为什么是灰的？',
        a: '这两项需要大模型：到「设置 → API服务 → 大模型」填服务商、模型 id（例如 deepseek-chat）、接口地址和 API Key，保存并测试。没有配置时，检索、分类、横向对比、图表、导出都不受影响。',
        keywords: ['ai', '总结', '对话', '灰', '大模型', 'key', '配置'],
        links: [{ label: '去设置', hash: '#/settings' }]
      },
      {
        q: '图表画廊、横向对比、深度分析在哪？',
        a: '都在左侧导航：图表画廊按图注跨文献找图（需要 PDF 已提取图片）；横向对比选 2–8 篇同主题文献，按模板把同样字段排成表；深度分析给单篇 22 个字段和五维评分，结果缓存在本地。',
        keywords: ['图表', '图', '对比', '横向', '深度', '分析', '画廊'],
        links: [{ label: '图表画廊', hash: '#/figures' }, { label: '横向对比', hash: '#/compare' }, { label: '深度分析', hash: '#/deep' }]
      },
      {
        q: '怎么导出 BibTeX 或 GB/T 7714 引用？',
        a: '打开文献详情页，引用面板里可以复制或导出 GB/T 7714、BibTeX 等格式；整库导出在「设置 → 资产与备份 → 导出与备份」，可导 JSON / BibTeX / Markdown。',
        keywords: ['导出', 'bibtex', '引用', 'gbt', '文献格式', 'citation'],
        links: [{ label: '回文献库', hash: '#/' }, { label: '去设置', hash: '#/settings' }]
      }
    ]
  },
  {
    name: '数据与备份',
    items: [
      {
        q: '数据存在哪？怎么备份、换电脑怎么迁？',
        a: '所有数据都在 data/ 目录（唯一的真源，不上云）。到「设置 → 资产与备份 → 导出与备份」点「导出全部（JSON 备份）」；换电脑后在同一个面板的「导入 JSON 备份」里选那个文件即可恢复。建议定期导一份。',
        keywords: ['数据', '备份', '导出', '导入', '迁移', '换电脑', 'json'],
        links: [{ label: '去设置', hash: '#/settings' }]
      },
      {
        q: '关掉网页后，服务会一直占着端口吗？',
        a: '不会。所有页面关闭 30 秒后，服务会自动保存并退出、释放端口。只有三种情况需要手动停：不想等 30 秒、服务卡住没反应、或用 --idle-exit 0 启动过。',
        keywords: ['端口', '关闭', '停止', '服务', '自动', '退出'],
        links: [{ label: '看手册 3.7', hash: '#/guide' }]
      }
    ]
  },
  {
    name: '打不开 / 出问题',
    items: [
      {
        q: '页面打不开、一直转圈怎么办？',
        a: '先确认服务在跑：双击 PaperLibrary.exe。地址应是 http://127.0.0.1:4321/ 。能打开但界面是旧的，按 Ctrl+Shift+R 强刷。仍不行就看 data/logs/launcher.log 里的报错。',
        keywords: ['打不开', '转圈', '白屏', '启动', '报错', '旧', '缓存'],
        links: [{ label: '看手册第 4 章', hash: '#/guide' }]
      },
      {
        q: '端口被占用 / 想换一个端口？',
        a: '换端口启动：python -m paperlib --port 4322（然后访问 127.0.0.1:4322）；如果是旧实例没退干净，先关掉所有页面等 30 秒让它自动退出，再重新启动。',
        keywords: ['端口', '占用', '4321', '换端口', 'stop', '冲突'],
        links: [{ label: '看手册 2.3', hash: '#/guide' }]
      }
    ]
  }
]

/** 关键词包含匹配：每个词都要命中（问题 / 回答 / keywords 任一栏）。空查询返回空数组。 */
export function matchFaq (text) {
  const query = String(text || '').trim().toLowerCase()
  if (!query) return []
  const tokens = query.split(/\s+/).filter(Boolean)
  const out = []
  for (const group of FAQ_GROUPS) {
    for (const item of group.items) {
      const hay = (item.q + ' ' + item.a + ' ' + (item.keywords || []).join(' ')).toLowerCase()
      if (tokens.every(token => hay.includes(token))) out.push({ group: group.name, item })
    }
  }
  return out
}

export default { QUICK_START, FAQ_GROUPS, matchFaq }
