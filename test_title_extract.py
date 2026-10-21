"""测试从 PDF 提取真实文章标题的两种方案"""
import os, sys, sqlite3, re
import fitz

conn = sqlite3.connect(r'y:\私人\PAPER\paper-library-app\data\papers.db')
rows = conn.execute("SELECT filename, abs_path FROM papers WHERE abs_path != '' LIMIT 8").fetchall()

out = []
for fn, ap in rows:
    if not os.path.exists(ap):
        continue
    out.append('=' * 70)
    out.append('FILE: ' + fn[:80])
    try:
        with fitz.open(ap) as doc:
            # 方案 A: PDF 内嵌元数据
            meta = doc.metadata or {}
            out.append('  [A] metadata title : ' + repr(meta.get('title', ''))[:110])
            out.append('  [A] metadata author: ' + repr(meta.get('author', ''))[:90])

            # 方案 B: 第一页最大字号文本块（标题通常是最大字）
            page = doc[0]
            d = page.get_text('dict')
            spans = []
            for block in d.get('blocks', [])[:40]:
                for line in block.get('lines', []):
                    for span in line.get('spans', []):
                        t = span.get('text', '').strip()
                        if len(t) > 3:
                            spans.append((span.get('size', 0), span.get('bbox', [0,0,0,0])[1], t))
            spans.sort(key=lambda x: -x[0])
            out.append('  [B] top font sizes: ' + ', '.join(f'{s:.1f}' for s, _, _ in spans[:5]))
            for s, y, t in spans[:4]:
                out.append(f'      {s:.1f}pt  {t[:85]}')
    except Exception as e:
        out.append('  ERROR: ' + str(e))
    out.append('')

with open(r'C:\Users\15854\AppData\Local\Temp\title_test.txt', 'w', encoding='utf-8') as f:
    f.write('\n'.join(out))
print('written', len(out), 'lines')
