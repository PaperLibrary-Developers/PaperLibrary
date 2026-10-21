/**
 * server.js — 文献管理网站后端服务器
 *
 * 启动: node server.js
 * 然后访问: http://localhost:3000
 */
const express = require('express');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 3000;
const PAPER_ROOT = __dirname;
const DATA_DIR = path.join(PAPER_ROOT, 'data');
const PAPERS_JSON = path.join(DATA_DIR, 'papers.json');

const app = express();

// ====== 静态文件 ======
app.use(express.static(path.join(PAPER_ROOT, 'public')));
app.use('/data', express.static(DATA_DIR));
// PDF 文件服务（允许前端通过 /pdf/ 访问原始 PDF）
app.use('/pdf', express.static(PAPER_ROOT, {
  setHeaders(res, filePath) {
    if (filePath.endsWith('.pdf')) {
      res.setHeader('Content-Type', 'application/pdf');
      res.setHeader('Accept-Ranges', 'bytes');
    }
  },
}));

// ====== 加载数据 ======
let papers = [];
let lastLoadTime = 0;

function loadPapers() {
  try {
    const stat = fs.statSync(PAPERS_JSON);
    if (stat.mtimeMs > lastLoadTime) {
      papers = JSON.parse(fs.readFileSync(PAPERS_JSON, 'utf-8'));
      lastLoadTime = stat.mtimeMs;
      console.log(`📄 加载了 ${papers.length} 篇论文 (${new Date().toLocaleTimeString()})`);
    }
  } catch (err) {
    console.error('加载论文数据失败:', err.message);
  }
}

// 初始加载
loadPapers();
// 每 30 秒检查文件更新
setInterval(loadPapers, 30000);

// ====== API ======

// 获取文件夹列表和统计
app.get('/api/folders', (req, res) => {
  loadPapers();
  const folders = {};
  for (const p of papers) {
    const folder = p.folder || '根目录';
    if (!folders[folder]) {
      folders[folder] = { name: folder, count: 0, hasSummary: 0 };
    }
    folders[folder].count++;
    if (p.summary) folders[folder].hasSummary++;
  }
  res.json(Object.values(folders));
});

// 获取论文列表（支持筛选和分页）
app.get('/api/papers', (req, res) => {
  loadPapers();
  const { folder, page = 1, limit = 50 } = req.query;
  const pageNum = parseInt(page);
  const limitNum = Math.min(parseInt(limit), 100);

  let filtered = papers;
  if (folder && folder !== '全部') {
    filtered = papers.filter(p => p.folder === folder);
  }

  // 按年份倒序，无年份的排在最后
  filtered.sort((a, b) => {
    if (a.year && b.year) return b.year - a.year;
    if (a.year) return -1;
    if (b.year) return 1;
    return a.title.localeCompare(b.title);
  });

  const total = filtered.length;
  const start = (pageNum - 1) * limitNum;
  const pagePapers = filtered.slice(start, start + limitNum);

  // 返回精简版（不含全文）
  res.json({
    total,
    page: pageNum,
    limit: limitNum,
    papers: pagePapers.map(p => ({
      id: p.id,
      title: p.title,
      titleCN: p.titleCN || '',
      authors: p.authors || [],
      year: p.year,
      journal: p.journal || '',
      folder: p.folder,
      filename: p.filename,
      keywords: p.keywords || [],
      reactionTypes: p.reactionTypes || [],
      summary: p.summary || '',
      abstract: p.abstract || '',
      figureCount: (p.figures || []).length,
      pageCount: p.pageCount,
      hasSummary: !!p.summary,
    })),
  });
});

// 获取单篇论文详情
app.get('/api/papers/:id', (req, res) => {
  loadPapers();
  const paper = papers.find(p => p.id === req.params.id);
  if (!paper) return res.status(404).json({ error: '论文未找到' });

  res.json({
    ...paper,
    // 全文只返回前 2000 字符的预览，完整文本通过单独请求获取
    fullTextPreview: (paper.fullText || '').slice(0, 2000),
  });
});

// 获取论文完整文本
app.get('/api/papers/:id/fulltext', (req, res) => {
  loadPapers();
  const paper = papers.find(p => p.id === req.params.id);
  if (!paper) return res.status(404).json({ error: '论文未找到' });
  res.json({ fullText: paper.fullText || '' });
});

// 搜索
app.get('/api/search', (req, res) => {
  loadPapers();
  const { q, folder } = req.query;
  if (!q || !q.trim()) {
    return res.json({ query: '', total: 0, results: [] });
  }

  const query = q.trim().toLowerCase();
  const searchFolder = folder && folder !== '全部' ? folder : null;

  const results = [];
  for (const paper of papers) {
    if (searchFolder && paper.folder !== searchFolder) continue;

    // 构建搜索文本
    const searchText = [
      paper.title,
      paper.titleCN || '',
      (paper.authors || []).join(' '),
      paper.journal || '',
      paper.filename,
      paper.abstract || '',
      paper.summary || '',
      (paper.keywords || []).join(' '),
      (paper.reactionTypes || []).join(' '),
      (paper.figures || []).map(f => (f.searchableText || '') + ' ' + (f.caption || '')).join(' '),
    ].join(' ').toLowerCase();

    if (searchText.includes(query)) {
      // 计算相关性分数
      let score = 0;
      if (paper.title.toLowerCase().includes(query)) score += 10;
      if ((paper.titleCN || '').includes(query)) score += 10;
      if ((paper.keywords || []).some(k => k.toLowerCase().includes(query))) score += 8;
      if ((paper.reactionTypes || []).some(r => r.toLowerCase().includes(query))) score += 7;
      if ((paper.summary || '').toLowerCase().includes(query)) score += 5;
      if ((paper.abstract || '').toLowerCase().includes(query)) score += 3;
      if (paper.filename.toLowerCase().includes(query)) score += 2;

      // 检查全文中的匹配
      const fullLower = (paper.fullText || '').toLowerCase();
      const ftCount = (fullLower.match(new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g')) || []).length;
      score += Math.min(ftCount, 20) * 0.5;

      // 检查图表描述
      const figMatches = (paper.figures || []).filter(f =>
        (f.searchableText || '').toLowerCase().includes(query) ||
        (f.caption || '').toLowerCase().includes(query)
      );
      if (figMatches.length > 0) {
        score += figMatches.length * 6;
      }

      results.push({
        id: paper.id,
        title: paper.title,
        titleCN: paper.titleCN || '',
        authors: paper.authors || [],
        year: paper.year,
        journal: paper.journal || '',
        folder: paper.folder,
        filename: paper.filename,
        keywords: paper.keywords || [],
        summary: paper.summary || '',
        figureCount: (paper.figures || []).length,
        matchedFigures: figMatches ? figMatches.length : 0,
        hasSummary: !!paper.summary,
        score,
        // 生成匹配片段
        snippet: generateSnippet(searchText, query),
      });
    }
  }

  // 按分数排序
  results.sort((a, b) => b.score - a.score);

  res.json({
    query: q,
    total: results.length,
    folder: searchFolder,
    results,
  });
});

function generateSnippet(text, query) {
  const idx = text.indexOf(query);
  if (idx < 0) return text.slice(0, 200) + '...';
  const start = Math.max(0, idx - 80);
  const end = Math.min(text.length, idx + query.length + 120);
  let snippet = (start > 0 ? '...' : '') + text.slice(start, end) + (end < text.length ? '...' : '');
  return snippet;
}

// ====== 启动服务器 ======
app.listen(PORT, () => {
  console.log('');
  console.log('╔══════════════════════════════════════════════╗');
  console.log('║   📚 文献管理检索系统 v1.0                    ║');
  console.log(`║   http://localhost:${PORT}                      ║`);
  console.log('║                                              ║');
  console.log('║   搜索 API: /api/search?q=关键词              ║');
  console.log('║   论文 API: /api/papers                      ║');
  console.log('║   统计 API: /api/folders                     ║');
  console.log('╚══════════════════════════════════════════════╝');
  console.log('');
});
