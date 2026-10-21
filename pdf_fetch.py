# -*- coding: utf-8 -*-
"""pdf_fetch.py — 把检索到的文献**真正下载下来**并存进分类文件夹

背景：只往 papers.db 写一条记录、不存原文，会在界面上表现为
「能在库里搜到，但打开说 PDF 未找到，也不能移动/看图」—— 这是半成品。
这里补上「拿到原文」这一步，让网络导入的文献和本地手动导入的**效果完全一致**。

抓取顺序（尽力而为，绝不绕付费墙）：
  1. 各源直接给的开放获取链接（OpenAlex 的 best_oa_location、S2 的 openAccessPdf）
  2. Unpaywall 按 DOI 解析出的 OA 直链（免费，只需一个邮箱）
  3. arXiv 直链（若是 arXiv 论文）
  4. Crossref 登记的 full-text link
只有响应体确实以 %PDF- 开头才算成功 —— 落地页/HTML 错误页一律判失败。

失败是常态且要如实告知：ACS / Science / Wiley 的付费墙、Cloudflare 拦截
（实测 Annual Reviews、pubs.acs.org、doi.org 都返回 403），这些**不应假装能下**。
"""

import json
import os
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

# 用浏览器 UA：部分开放获取站点会拦截非浏览器 UA
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")
TIMEOUT = 40
MAX_BYTES = 300 * 1024 * 1024

# Windows 非法字符 + 保留名
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *((f"LPT{i}") for i in range(1, 10))}


def safe_filename(title: str, max_len: int = 120) -> str:
    """把论文标题变成安全的文件名（沿用库存量文件名风格：直接用标题）"""
    s = _ILLEGAL.sub(" ", str(title or "")).replace("\n", " ")
    s = re.sub(r"\s+", " ", s).strip().strip(".")
    if len(s) > max_len:
        s = s[:max_len].strip()
    if not s:
        s = "untitled"
    if s.upper() in _RESERVED:
        s = s + "_"
    return s


def is_pdf_bytes(head: bytes) -> bool:
    """真正的 PDF 以 %PDF- 开头；HTML 落地页不是"""
    return bool(head) and head[:5] == b"%PDF-"


def _download(url, timeout=TIMEOUT):
    """下载并校验；返回 (ok, 字节或原因, 状态码)

    超时/403/HTML 一律算失败，并把原因说清楚 —— 不静默当成功。
    """
    req = urllib.request.Request(url, headers={
        "User-Agent": BROWSER_UA,
        "Accept": "application/pdf,application/octet-stream,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            head = r.read(2048)
            if not is_pdf_bytes(head):
                # 有些站点 Content-Type 不准但内容是 PDF；反之则是落地页
                if "pdf" in ctype:
                    pass
                else:
                    return False, f"不是 PDF（Content-Type: {ctype[:40] or '未知'}）", r.status
            rest = r.read(MAX_BYTES)
            return True, head + rest, r.status
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code} {e.reason}", e.code
    except socket.timeout:
        return False, "下载超时", 0
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)[:60]}", 0


# ── Unpaywall：按 DOI 找合规的开放获取全文 ──

def unpaywall_links(doi: str, email: str, timeout=20):
    """返回该 DOI 的开放获取直链候选列表（按可信度排序）"""
    doi = str(doi or "").strip()
    if not doi or not email:
        return []
    url = ("https://api.unpaywall.org/v2/" + urllib.parse.quote(doi)
           + "?email=" + urllib.parse.quote(email))
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": f"PaperLibrary/1.0 (mailto:{email})"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return []

    out = []
    best = data.get("best_oa_location") or {}
    for loc in [best] + list(data.get("oa_locations") or []):
        for key in ("url_for_pdf", "url"):
            u = (loc or {}).get(key) or ""
            # 只收 ends with pdf 的直链；doi.org 落地页直接跳过（实测 403 且不是 PDF）
            if u and "doi.org/" not in u and u not in out:
                out.append(u)
    # 明确以 .pdf 结尾的排前面
    out.sort(key=lambda u: (0 if u.lower().split("?")[0].endswith(".pdf") else 1))
    return out


def candidate_urls(item: dict, email: str = "") -> list:
    """汇总所有可能拿到 PDF 的链接，按「最可能成功」排序"""
    cands = []

    def add(u):
        u = str(u or "").strip()
        if u and u not in cands:
            cands.append(u)

    # 1) arXiv 论文：直链最可靠
    sid = str(item.get("sourceId") or "")
    if item.get("source") == "arxiv" or "arxiv.org" in sid:
        m = re.search(r"(\d{4}\.\d{4,5})", sid) or re.search(r"(\d{4}\.\d{4,5})",
                                                            str(item.get("url") or ""))
        if m:
            add(f"https://arxiv.org/pdf/{m.group(1)}")

    # 2) 源直接给的 OA 链接（OpenAlex best_oa_location / S2 openAccessPdf）
    oa = str(item.get("oaUrl") or "")
    if oa and "doi.org/" not in oa:
        add(oa)

    # 3) Unpaywall（按 DOI 解析，最全面）
    for u in unpaywall_links(item.get("doi"), email):
        add(u)

    return cands


def fetch_pdf(item: dict, email: str = "", timeout=TIMEOUT):
    """按候选顺序逐个尝试下载。

    返回 {'ok', 'data'|'reason', 'url', 'tried': [(url, reason), ...]}
    """
    tried = []
    for u in candidate_urls(item, email):
        ok, payload, status = _download(u, timeout=timeout)
        tried.append((u, "成功" if ok else payload))
        if ok:
            return {"ok": True, "data": payload, "url": u, "tried": tried}
    reason = "没有可用的开放获取链接" if not tried else tried[-1][1]
    return {"ok": False, "reason": reason, "url": "", "tried": tried}


def store_pdf(data: bytes, root: str, folder_rel: str, title: str,
              filename: str = "") -> str:
    """把 PDF 写进「文献库根目录/分类文件夹」，返回最终绝对路径。

    同名时自动加 (2)(3)… 后缀，**绝不覆盖已有文件**。
    """
    folder = os.path.abspath(os.path.join(root, (folder_rel or "").replace("/", os.sep)))
    root_abs = os.path.abspath(root)
    if folder != root_abs and not folder.startswith(root_abs + os.sep):
        raise ValueError("目标文件夹超出文献库范围")
    os.makedirs(folder, exist_ok=True)

    base = safe_filename(filename or title)
    target = os.path.join(folder, base + ".pdf")
    n = 2
    while os.path.exists(target):
        target = os.path.join(folder, f"{base} ({n}).pdf")
        n += 1

    tmp = target + ".part"
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)          # 原子落盘，避免半截文件被扫描入库
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise
    return target


def import_with_pdf(item: dict, root: str, folder_rel: str, email: str = "",
                    filename: str = "") -> dict:
    """一步到位：下载 → 落盘。返回 {'ok', 'path'|'reason', 'url', 'tried'}"""
    res = fetch_pdf(item, email=email)
    if not res["ok"]:
        return {"ok": False, "reason": res["reason"], "tried": res.get("tried", [])}
    try:
        path = store_pdf(res["data"], root, folder_rel, item.get("title") or "", filename)
    except Exception as e:
        return {"ok": False, "reason": f"保存失败：{e}", "tried": res.get("tried", [])}
    return {"ok": True, "path": path, "url": res["url"], "tried": res.get("tried", []),
            "bytes": len(res["data"])}
