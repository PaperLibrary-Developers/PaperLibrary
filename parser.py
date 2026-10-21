"""
parser.py — PDF 多模态解析模块
功能:
  1. 递归扫描本地 PDF 文件夹，SHA256 去重
  2. 使用 PyMuPDF 提取全文文本 + 元数据（标题/DOI/年份）
  3. 从 PDF 每一页提取嵌入的图片/矢量图，自动匹配 Figure Caption
  4. 返回结构化数据供 database.py 写入 SQLite
"""
import os
import re
import json
import hashlib
import fitz
from title_extract import extract_title
from caption_matcher import match_page_images_with_captions

PAPER_ROOT = os.path.dirname(__file__)
IMAGES_DIR = os.path.join(PAPER_ROOT, 'data', 'images')
SKIP_DIRS = {'node_modules', 'data', 'scripts', 'public', '.git', '__pycache__', '.claude'}
SKIP_PREFIXES = ('.', '~')


def sha256_file(filepath):
    h = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def find_pdfs(root_dir):
    """递归扫描所有 PDF 文件，返回列表 [{'full_path', 'folder', 'filename'}]"""
    results = []
    for dirpath, dirnames, filenames in os.walk(root_dir):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(SKIP_PREFIXES)]
        for fname in filenames:
            if fname.startswith(SKIP_PREFIXES):
                continue
            if fname.lower().endswith('.pdf'):
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(dirpath, root_dir)
                results.append({
                    'full_path': full,
                    'folder': '根目录' if rel == '.' else rel.replace(os.sep, '/'),
                    'filename': fname,
                })
    return results


def _extract_year_robust(full_text: str, filename: str, meta: dict):
    """多级稳健年份提取，避免页码/编号误捕获 (如 2074, 2054, 1929)"""
    current_year = 2026

    # 1. 出版标记关联的年份（置信度最高）
    head = full_text[:4000] if full_text else ''
    pub_patterns = [
        r'(?:received|accepted|published|online|copyright|©)\s*[^0-9\n]{0,35}\b(19\d{2}|20\d{2})\b',
        r'\b(19\d{2}|20\d{2})\b\s+(?:the authors|elsevier|american chemical society|wiley|springer|nature|rsc|acs|iop)',
        r'doi:?\s*10\.\d{4,9}/[^\s]+?\b(19\d{2}|20\d{2})\b'
    ]
    for pat in pub_patterns:
        m = re.search(pat, head, re.I)
        if m:
            y = int(m.group(1))
            if 1970 <= y <= current_year:
                return y

    # 2. 文件名中的年份（很多学者命名习惯：2024_Shen... 或 Author - 2023 - Title）
    if filename:
        m = re.search(r'\b(19[7-9]\d|20[0-2]\d)\b', filename)
        if m:
            y = int(m.group(1))
            if 1970 <= y <= current_year:
                return y

    # 3. PDF 内嵌元数据中的 creationDate (形如 D:20230514...)
    if meta:
        cdate = meta.get('creationDate', '') or meta.get('modDate', '') or ''
        m = re.search(r'D:(\d{4})', cdate)
        if m:
            y = int(m.group(1))
            if 1970 <= y <= current_year:
                return y

    # 4. 文本前部的合理年份兜底
    for m in re.finditer(r'\b(19[7-9]\d|20[0-2]\d)\b', head[:2000]):
        y = int(m.group(1))
        if 1970 <= y <= current_year:
            return y

    return None


def extract_text_and_metadata(pdf_path, filename=''):
    """PyMuPDF 提取全文 + 元数据"""
    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return {'full_text': '', 'doi': '', 'year': None, 'page_count': 0, 'authors': []}

    parts = []
    for page in doc:
        t = page.get_text('text')
        if t:
            parts.append(t)
    full_text = '\n'.join(parts)

    meta = doc.metadata or {}
    doi = meta.get('doi', '') or ''
    if not doi:
        m = re.search(r'10\.\d{4,9}/[-._;()/:A-Za-z0-9]+', full_text[:3500] or '', re.I)
        doi = m.group(0).rstrip(';,.)') if m else ''

    # 尝试从 metadata 读取作者
    authors = []
    raw_author = meta.get('author', '') or ''
    if raw_author and not re.search(r'(microsoft|word|latex|adobe|unknown|untitled|\.pdf)', raw_author, re.I):
        # 常见分割符
        spl = re.split(r'[,;、&]|\band\b', raw_author)
        authors = [a.strip() for a in spl if len(a.strip()) > 1 and len(a.strip()) < 50][:10]

    year = _extract_year_robust(full_text, filename or os.path.basename(pdf_path), meta)

    pc = doc.page_count
    doc.close()
    return {'full_text': full_text, 'doi': doi, 'year': year, 'page_count': pc, 'authors': authors}


CAPTION_RE = re.compile(
    r'(?:Figure|Fig\.?)\s*\d+[a-z]?[\.\s:](.+?)(?=\n\n|\n(?:Figure|Fig|Scheme|Table)|$)',
    re.IGNORECASE | re.DOTALL
)
CN_CAPTION_RE = re.compile(
    r'图\s*\d+[\.\s:：](.+?)(?=\n\n|\n(?:图|表|Figure)|$)',
    re.DOTALL
)


def extract_figures(pdf_path, paper_hash):
    """提取 PDF 中嵌入的图片，并尝试匹配图注"""
    figures = []
    paper_img_dir = os.path.join(IMAGES_DIR, paper_hash)
    os.makedirs(paper_img_dir, exist_ok=True)

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return figures

    # 预先收集每一页的图注
    page_captions = {}
    for i in range(len(doc)):
        text = doc[i].get_text('text') or ''
        caps = CAPTION_RE.findall(text) + CN_CAPTION_RE.findall(text)
        page_captions[i] = [c.strip().replace('\n', ' ')[:300] for c in caps]

    fig_index = 0
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text('text') or ''
        image_list = page.get_images(full=True)

        # 优先使用空间几何近邻（Bounding Box）匹配本页图注
        try:
            geo_caps = match_page_images_with_captions(page)
        except Exception:
            geo_caps = {}

        for img_info in image_list:
            xref = img_info[0]
            try:
                base = doc.extract_image(xref)
                img_bytes = base.get('image', b'')
                ext = base.get('ext', 'png')

                if len(img_bytes) < 3072:   # skip tiny icons / logos
                    continue

                fig_index += 1
                fname = f"fig_{fig_index:03d}.{ext}"
                fpath = os.path.join(paper_img_dir, fname)
                with open(fpath, 'wb') as wf:
                    wf.write(img_bytes)

                # 优先匹配空间近邻图注；若未命中则按页面图注顺序兜底
                cap = geo_caps.get(xref, '')
                if not cap:
                    caps = page_captions.get(page_num, [])
                    img_idx_in_page = image_list.index(img_info) if img_info in image_list else 0
                    if img_idx_in_page < len(caps):
                        cap = caps[img_idx_in_page]

                figures.append({
                    'fig_index': fig_index,
                    'image_path': os.path.join('data', 'images', paper_hash, fname),
                    'caption': cap,
                    'context_text': page_text[:500] if page_text else '',
                    'ai_description': '',
                    'figure_type': '',
                })
            except Exception:
                continue

    doc.close()
    return figures


def parse_one(pdf_info):
    """解析单篇 PDF，返回 (paper_dict, figures_list)"""
    h = sha256_file(pdf_info['full_path'])
    td = extract_text_and_metadata(pdf_info['full_path'], filename=pdf_info['filename'])

    # 提取真实论文标题（3级智能降级：metadata -> 最大字号 -> 文件名清洗）
    real_title, _ = extract_title(pdf_info['full_path'], pdf_info['filename'])
    title = real_title or pdf_info['filename'].replace('.pdf', '')

    paper = {
        'id': h,
        'filename': pdf_info['filename'],
        'title': title,
        'authors': json.dumps(td.get('authors', []), ensure_ascii=False),
        'doi': td['doi'],
        'year': td['year'],
        'folder': pdf_info['folder'],
        'abs_path': pdf_info['full_path'],
        'full_text': td['full_text'],
        'summary': '',
        'parsed_at': '',
    }
    figs = extract_figures(pdf_info['full_path'], h)
    return paper, figs


if __name__ == '__main__':
    pdfs = find_pdfs(PAPER_ROOT)
    print(f'Found {len(pdfs)} PDFs')
    if pdfs:
        p, fs = parse_one(pdfs[0])
        print(f"  ID: {p['id'][:16]}...")
        print(f"  Text: {len(p['full_text'])} chars, {len(fs)} figures")
        for f in fs[:3]:
            print(f"    [{f['fig_index']}] {f['caption'][:80]}")
