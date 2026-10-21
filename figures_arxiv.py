# -*- coding: utf-8 -*-
"""figures_arxiv.py — 从 arXiv 源码包取「真正的高清图」

为什么需要它：PDF 里能抠出来的图往往是二次压缩过的位图，或者干脆是矢量图
（PyMuPDF 的 get_images 拿到的是空壳），分辨率不足以看清坐标轴小字。
arXiv 的 e-print 接口能直接拿到作者上传的原始 TeX 源码包，里面的图是原图
（PNG/TIFF 位图，或 PDF 矢量图 —— 后者可以无损渲染成高 DPI 位图）。

策略：
  1. 先确认这篇有 arXiv ID（正文里的 "arXiv:2412.xxxxx"、文件名、DOI 都算）
  2. 拉 e-print 源码包（tar.gz / 单 tex / pdf 三种形态都能处理）
  3. 解析各 \begin{figure} 块里的 \includegraphics 与 \caption
  4. 位图原样取；PDF 矢量图用 PyMuPDF 300 DPI 渲染成 PNG
  5. 任何一步失败都回退 —— 上游继续用 PDF 抠图，不让解析中断
"""

import io
import os
import re
import tarfile
import urllib.error
import urllib.parse
import urllib.request

import fitz  # PyMuPDF

UA = "PaperLibrary/1.0 (figure fetch; mailto:noreply@example.com)"
EPRINT = "https://arxiv.org/e-print/"
TIMEOUT = 45
RENDER_DPI = 300
MIN_BYTES = 3072          # 小于这个尺寸的基本是 logo / 装饰线
RASTER_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
VECTOR_EXT = {".pdf"}
# eps 需要 ghostscript，环境里不保证有，跳过但如实记录
SKIP_EXT = {".eps", ".ps", ".svg"}


# ═══════════════════════════════════════════════════
# arXiv ID 识别
# ═══════════════════════════════════════════════════

# 新式 arXiv ID = YYMM.NNNNN，YY 两位年份、MM 必须 01-12（如 2412.12345 / 1901.00001）。
# 早期写成 (?:19|20)\d{2} 会把 2401/2512 这类全部漏掉——那是 YYMM 不是年份。
_ID = r"(?:0[7-9]|[1-9]\d)(?:0[1-9]|1[0-2])\.\d{4,5}"
# 新式：arXiv:2412.12345 / 2412.12345v2；老式：arXiv:hep-th/9901001
_RE_NEW = re.compile(r"arxiv(?:\.org)?(?:/+abs|/+pdf)?[:\s/_\-.]*(" + _ID + r")(v\d+)?", re.I)
_RE_OLD = re.compile(r"arxiv(?:\.org)?(?:/+abs|/+pdf)?[:\s/_\-.]*([a-z\-]+(?:\.[a-z\-]+)?/\d{7})(v\d+)?", re.I)
# 裸 ID（DOI 里也常见 10.48550/arXiv.2412.12345）
_RE_DOI_ARXIV = re.compile(r"10\.48550/arxiv\.(" + _ID + r")", re.I)


def guess_arxiv_id(full_text="", filename="", doi="", abs_path=""):
    """尽最大努力找出 arXiv ID；找不到返回 ''。

    顺序按可靠性：DOI 前缀 > 正文/文件名里的 arXiv: 标记 > 文件名裸 ID。
    """
    if doi:
        m = _RE_DOI_ARXIV.search(str(doi))
        if m:
            return m.group(1)
    for blob in (str(full_text or "")[:6000], str(filename or ""),
                 os.path.basename(str(abs_path or ""))):
        if not blob:
            continue
        m = _RE_NEW.search(blob)
        if m:
            return m.group(1)
        m = _RE_OLD.search(blob)
        if m:
            return m.group(1)
    # 最后一搏：文件名里就是 2412.12345.pdf
    m = re.search(r"(?<![\d.])(" + _ID + r")(?![\d])", os.path.basename(str(abs_path or "")))
    if m:
        return m.group(1)
    return ""


# ═══════════════════════════════════════════════════
# 下载与解包
# ═══════════════════════════════════════════════════

def fetch_source(arxiv_id, timeout=TIMEOUT):
    """下载 e-print 源码。返回 (data_bytes, kind)，kind ∈ {tar, tex, pdf, unknown}。"""
    aid = str(arxiv_id or "").strip()
    if not aid:
        raise ValueError("缺少 arXiv ID")
    url = EPRINT + urllib.parse.quote(aid, safe="/")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"arXiv 源码 HTTP {e.code} {e.reason}")
    except Exception as e:
        raise RuntimeError(f"{type(e).__name__}: {e}")
    if not data:
        raise RuntimeError("arXiv 源码为空")
    return data, _sniff_kind(data)


def _sniff_kind(data):
    head = data[:5]
    if head.startswith(b"%PDF"):
        return "pdf"
    if data[:2] == b"\x1f\x8b" or data[:262].find(b"ustar") >= 0:
        return "tar"
    # 纯 TeX 源码常见开头
    if re.match(rb"\s*%|\\documentclass|\\input", data[:2000]):
        return "tex"
    return "unknown"


def _open_tar(data):
    for mode in ("r:gz", "r:xz", "r:bz2", "r:"):
        try:
            return tarfile.open(fileobj=io.BytesIO(data), mode=mode)
        except Exception:
            continue
    return None


def read_source_files(data, kind=None):
    """把源码包读成 {相对路径: bytes}；只取 TeX 与图片，跳过其余。"""
    kind = kind or _sniff_kind(data)
    files = {}

    if kind == "tex":
        files["main.tex"] = data
        return files

    if kind == "pdf":
        # 作者上传的就是 PDF（.pdf 只可能是一张图或排版好的正文），没有可用源码
        return {}

    tar = _open_tar(data)
    if tar is None:
        # 单文件 gzip（只有一个 .tex 被 gzip 的情况）
        try:
            import gzip
            files["main.tex"] = gzip.decompress(data)
            return files
        except Exception:
            return {}

    keep = {".tex", ".bbl"} | RASTER_EXT | VECTOR_EXT
    with tar:
        for m in tar.getmembers():
            if not m.isfile():
                continue
            name = m.name.lstrip("./")
            # 防目录穿越
            if ".." in name.split("/") or name.startswith("/"):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext not in keep:
                continue
            if m.size > 40 * 1024 * 1024:      # 单文件 40MB 上限，防止异常包
                continue
            try:
                f = tar.extractfile(m)
                if f is None:
                    continue
                files[name] = f.read()
            except Exception:
                continue
    return files


# ═══════════════════════════════════════════════════
# TeX 解析：图块 → (图片路径, 图注)
# ═══════════════════════════════════════════════════

_FIG_BLOCK_RE = re.compile(r"\\begin\{(figure\*?|wrapfigure\*?|subfigure)\}(.*?)\\end\{\1\}",
                           re.S)
_INCLUDE_RE = re.compile(r"\\includegraphics\s*(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_CAPTION_RE = re.compile(r"\\caption\s*(?:\[[^\]]*\])?\s*\{")


def _match_brace(text, start):
    """从 text[start] == '{' 起找到配对的 '}'，返回内部内容（支持嵌套）。"""
    if start >= len(text) or text[start] != "{":
        return ""
    depth = 0
    i = start
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start + 1:i]
        i += 1
    return text[start + 1:]


def _clean_tex(s):
    """把图注里的 TeX 噪声压成可读文本。"""
    t = str(s or "")
    t = re.sub(r"\\[a-zA-Z@]+\*?", " ", t)          # 命令名
    t = t.replace("\\\\", " ").replace("~", " ")
    t = re.sub(r"[{}$]", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _resolve_asset(path, files):
    """把 \\includegraphics 里的路径对到源码包里的真实文件。"""
    p = str(path or "").strip().strip('"').replace("\\", "/")
    if not p:
        return None
    p = p.lstrip("./")
    cands = [p] if os.path.splitext(p)[1].lower() else \
        [p + e for e in (".png", ".jpg", ".jpeg", ".pdf", ".tif", ".tiff", ".eps")]
    keys = {k.replace("\\", "/"): k for k in files}
    for c in cands:
        if c in keys:
            return keys[c]
    # 大小写/子目录容错：按 basename 唯一匹配
    base = os.path.basename(p).lower()
    hits = [k for k in keys if os.path.basename(k).lower() == base]
    if len(hits) == 1:
        return keys[hits[0]]
    if not os.path.splitext(base)[1]:
        hits = [k for k in keys if os.path.splitext(os.path.basename(k).lower())[0] == base]
        if len(hits) == 1:
            return keys[hits[0]]
    return None


def parse_tex_figures(files):
    """解析出 [{'asset': 路径, 'caption': 文本}]，保持文档顺序。"""
    tex_names = [n for n in files if n.lower().endswith(".tex")]
    if not tex_names:
        return []

    def score(n):
        try:
            txt = files[n].decode("utf-8", "replace")
        except Exception:
            return -1
        return txt.count("\\includegraphics") * 3 + txt.count("\\documentclass") * 5

    main = max(tex_names, key=score)
    raw = files[main].decode("utf-8", "replace")

    out = []
    for m in _FIG_BLOCK_RE.finditer(raw):
        body = m.group(2)
        incs = _INCLUDE_RE.findall(body)
        if not incs:
            continue
        cap = ""
        cm = _CAPTION_RE.search(body)
        if cm:
            cap = _clean_tex(_match_brace(body, cm.end() - 1))[:400]
        for p in incs:
            out.append({"asset": p, "caption": cap})
    if out:
        return out

    # 兜底：没有 figure 环境（作者直接排图），按 includegraphics 顺序取
    for m in _INCLUDE_RE.finditer(raw):
        out.append({"asset": m.group(1), "caption": ""})
    return out


# ═══════════════════════════════════════════════════
# 落盘
# ═══════════════════════════════════════════════════

def _vector_to_png(data, dpi=RENDER_DPI):
    """PDF 矢量图 → 高 DPI PNG（多页取第一页，绝大多数插图只有一页）。"""
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        if doc.page_count <= 0:
            return b"", 0, 0
        pix = doc[0].get_pixmap(dpi=dpi)
        return pix.tobytes("png"), pix.width, pix.height
    finally:
        doc.close()


def extract_figures(arxiv_id, out_dir, max_figures=30, timeout=TIMEOUT):
    """主入口：返回 (figures, info)。

    figures: [{'fig_index', 'image_path', 'caption', 'width', 'height',
               'figure_type', 'context_text'}]
    info:    {'ok', 'kind', 'reason', 'skipped': [(asset, reason)], 'sourceFile'}
    """
    info = {"ok": False, "kind": "", "reason": "", "skipped": [], "sourceFile": ""}
    try:
        data, kind = fetch_source(arxiv_id, timeout=timeout)
    except Exception as e:
        info["reason"] = str(e)
        return [], info
    info["kind"] = kind

    if kind == "pdf":
        info["reason"] = "作者只上传了 PDF，没有可用的 TeX 源码"
        return [], info

    files = read_source_files(data, kind)
    if not files:
        info["reason"] = "源码包内没有可取的文件"
        return [], info

    parsed = parse_tex_figures(files)
    if not parsed:
        info["reason"] = "源码里没找到 \\includegraphics"
        return [], info

    os.makedirs(out_dir, exist_ok=True)
    figures = []
    used = set()
    idx = 0
    for entry in parsed:
        if idx >= max_figures:
            break
        key = _resolve_asset(entry["asset"], files)
        if not key or key in used:
            if key is None:
                info["skipped"].append((entry["asset"], "源码包里找不到该图片文件"))
            continue
        blob = files.get(key) or b""
        if len(blob) < MIN_BYTES:
            info["skipped"].append((entry["asset"], "文件过小，判定为装饰图"))
            continue
        ext = os.path.splitext(key)[1].lower()
        if ext in SKIP_EXT:
            info["skipped"].append((entry["asset"], f"{ext} 需要 ghostscript，已跳过"))
            continue

        idx += 1
        fname = f"fig_{idx:03d}.png"
        fpath = os.path.join(out_dir, fname)
        try:
            if ext in VECTOR_EXT:
                png, w, h = _vector_to_png(blob)
                if not png:
                    idx -= 1
                    info["skipped"].append((entry["asset"], "矢量图渲染失败"))
                    continue
                with open(fpath, "wb") as f:
                    f.write(png)
            else:
                # 位图原样保留字节（不重新编码，避免二次损失）
                real_ext = ".png" if ext == ".png" else (".jpg" if ext in (".jpg", ".jpeg") else ".png")
                fname = f"fig_{idx:03d}{real_ext}"
                fpath = os.path.join(out_dir, fname)
                with open(fpath, "wb") as f:
                    f.write(blob)
                try:
                    pix = fitz.Pixmap(io.BytesIO(blob))
                    w, h = pix.width, pix.height
                except Exception:
                    w = h = 0
        except Exception as e:
            idx -= 1
            info["skipped"].append((entry["asset"], f"写出失败：{e}"))
            continue

        used.add(key)
        figures.append({
            "fig_index": idx,
            "image_path": fpath,
            "caption": entry.get("caption") or "",
            "width": w, "height": h,
            "figure_type": "vector_render" if ext in VECTOR_EXT else "source_raster",
            "context_text": "",
        })

    info["ok"] = bool(figures)
    if not figures:
        info["reason"] = info["reason"] or "解析出图块但一张都没取成功"
    elif not info["sourceFile"]:
        info["sourceFile"] = "arxiv-source"
    return figures, info


def try_upgrade(src_pdf, full_text="", filename="", doi="", out_root=None, timeout=TIMEOUT):
    """给现有文献尝试一次「换成源码高清图」。

    返回 {'ok', 'arxivId', 'figures', 'info', 'reason'}；失败不抛异常。
    """
    aid = guess_arxiv_id(full_text=full_text, filename=filename, doi=doi, abs_path=src_pdf)
    if not aid:
        return {"ok": False, "arxivId": "", "figures": [],
                "reason": "这篇不是 arXiv 预印本（正文/文件名/DOI 里都没有 arXiv ID）"}
    out_root = out_root or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "data", "images_arxiv")
    out_dir = os.path.join(out_root, aid.replace("/", "_"))
    figures, info = extract_figures(aid, out_dir, timeout=timeout)
    return {"ok": bool(figures), "arxivId": aid, "figures": figures,
            "info": info, "reason": "" if figures else (info.get("reason") or "未知原因")}


if __name__ == "__main__":
    import sys
    aid = sys.argv[1] if len(sys.argv) > 1 else "2212.09748"
    figs, info = extract_figures(aid, os.path.join("data", "_figtest", aid))
    print("[kind]", info.get("kind"), "[ok]", info.get("ok"), "[reason]", info.get("reason"))
    print("[figs]", len(figs))
    for f in figs[:5]:
        print("   ", os.path.basename(f["image_path"]), f["width"], "x", f["height"],
              (f["caption"] or "")[:60])
    for s in info.get("skipped", [])[:5]:
        print("  skip:", s)
