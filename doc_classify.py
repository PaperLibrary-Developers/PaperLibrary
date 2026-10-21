# -*- coding: utf-8 -*-
"""doc_classify.py — 判断一个 PDF 是不是「期刊发表的文献」

用途：扫描导入前的过滤，避免把自写的总结/笔记（导出成 PDF 的 Word 文档）
      当成文献存进库里。

设计原则（重要）：
  · **偏保守**。期刊补充材料（SI）经常就是 Word 排版导出的，所以绝不能用
    「是不是 Word 导出」来排除 —— 那会把 SI 一起砍掉。
  · **多信号共识**。只有「PDF 生产者是 Word 系」**且**「正文极短」**且**
    「没有任何期刊特征」同时成立，才判为可疑。
  · 判为可疑的默认**只报告、不阻断**（由调用方决定），因为误判代价是丢文献，
    比多存一份笔记严重得多。
"""

import os
import re

import fitz

# Word / WPS / LibreOffice 这类「非排版软件」的产出标记
_WORDISH = re.compile(
    r"(microsoft\s*word|microsoft®\s*word|wps\s*office|libreoffice|openoffice|"
    r"writer|word for|microsoft\s*office)", re.I)

# 期刊/学术出版物的常见特征
_ACADEMIC = re.compile(
    r"(doi|dx\.doi\.org|https?://|issn|isbn|"
    r"received|accepted|published|revised|"
    r"abstract|keywords|introduction|references|bibliography|"
    r"journal|letters|communications|proceedings|transactions|"
    r"vol\.?\s*\d|no\.?\s*\d|pp\.?\s*\d|"
    r"©|\(c\)\s*20|copyright|elsevier|wiley|springer|"
    r"american chemical society|royal society of chemistry|"
    r"nature|science|cell|acs |rsc |angew)", re.I)

# 文件名里像「自写笔记」的词
_NOTE_NAME = re.compile(
    r"(总结|筆記|笔记|综述笔记|读后感|读书笔记|我的|草稿|draft|notes?|summary|"
    r"报告|汇报|汇报材料|ppt|讲稿|课件|作业|论文笔记)", re.I)


def classify(pdf_path: str, filename: str = "") -> dict:
    """返回 {'is_article': bool, 'score': int, 'reasons': [...], 'meta': {...}}

    is_article=True 表示「像期刊文献，可以入库」。
    判为 False 的，reasons 里会写明理由，供界面上展示给用户。
    """
    name = filename or os.path.basename(pdf_path)
    res = {"is_article": True, "score": 0, "reasons": [], "meta": {}}
    try:
        with fitz.open(pdf_path) as doc:
            pages = doc.page_count
            md = doc.metadata or {}
            producer = f"{md.get('producer') or ''} {md.get('creator') or ''}".strip()
            # 前两页文本（封面/首页足够判断）
            text = ""
            for i in range(min(2, pages)):
                text += doc[i].get_text("text") or ""
    except Exception as e:
        return {"is_article": True, "score": 0,
                "reasons": [f"打不开，默认放行：{e}"], "meta": {}}

    res["meta"] = {"pages": pages, "chars": len(text.strip()),
                   "producer": producer[:60], "title": (md.get("title") or "")[:80]}

    wordish = bool(_WORDISH.search(producer))
    academic_hits = len(_ACADEMIC.findall(text[:4000]))
    name_hint = bool(_NOTE_NAME.search(name))
    chars = len(text.strip())

    reasons = []

    # ① 文件名直接暴露（最强信号，但单独不作为否决理由 —— 可能只是命名习惯）
    if name_hint:
        reasons.append("文件名像笔记/总结")
        res["score"] += 2

    # ② 非排版软件产出
    if wordish:
        reasons.append(f"由文字处理软件导出（{producer[:40]}）")
        res["score"] += 1

    # ③ 正文极短（期刊正文首页不可能这么少字）
    if chars < 400:
        reasons.append(f"全文只有 {chars} 个字符，不像论文正文")
        res["score"] += 2

    # ④ 完全没有期刊特征
    if academic_hits == 0:
        reasons.append("找不到任何期刊特征（DOI/期刊名/收稿日期/参考文献等）")
        res["score"] += 2

    # ⑤ 页数与正文都极少
    if pages <= 2 and chars < 1200:
        reasons.append(f"仅 {pages} 页且正文很少")
        res["score"] += 1

    # 判定：优先信任「没有任何期刊特征」这一条 —— 真论文几乎不可能一个都没有
    no_academic = (academic_hits == 0)
    very_short = (chars < 400)
    if no_academic and (very_short or name_hint):
        res["is_article"] = False
    elif name_hint and wordish and chars < 2000:
        # 名字像笔记 + Word 导出 + 内容不多 —— 三条件共识才否决
        res["is_article"] = False
    elif very_short and pages <= 2:
        res["is_article"] = False

    if not res["is_article"]:
        res["reasons"] = reasons
    return res


def classify_many(pdf_infos, progress=None):
    """批量判定。pdf_infos 为 parser.find_pdfs 的返回值。

    返回 (ok_list, skipped_list)，skipped 里每项是 (pdf_info, reasons)。
    """
    ok, skipped = [], []
    total = max(len(pdf_infos), 1)
    for i, pi in enumerate(pdf_infos):
        try:
            r = classify(pi["full_path"], pi["filename"])
        except Exception as e:
            r = {"is_article": True, "reasons": [f"判定异常，默认放行：{e}"]}
        if r["is_article"]:
            ok.append(pi)
        else:
            skipped.append((pi, r.get("reasons", [])))
        if progress:
            progress((i + 1) / total)
    return ok, skipped
