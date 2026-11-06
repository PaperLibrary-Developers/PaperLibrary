# -*- coding: utf-8 -*-
"""Obsidian 笔记拼装（只生成文本与媒体清单，不落盘）。

移植自 C:\\Users\\wzxle\\Desktop\\WB比赛\\PaperLibrary\\app\\obsidian_export.py
契约名：export.obsidian（GET /api/export/obsidian → zip 内每篇的 .md）

与旧版的差异：
  · 旧版直接写用户的 vault 目录（存在则拒绝覆盖）—— 合并后改成 **内存笔记 + zip**，
    由 adapters/obsidian.py 统一打包，避免服务器进程写用户任意目录；
  · frontmatter 用 json.dumps 转义（YAML 标量永远带引号），标题含冒号/引号也不会写坏；
  · 图片走 zip 内的 media/<paperId>/ 目录，正文用标准 Markdown 图片链接
    ![](media/<paperId>/fig_001.png)（Obsidian 与普通 Markdown 阅读器都能显示）。

导出规则（契约 §10「只导出有内容的字段」）：
  题录空字段不写；摘要/笔记/AI 总结/中文总结为空则不生成小节；没有图片则不写图片章节。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from paperlib.legacy import figures as legacy_figures

_UNSAFE = re.compile(r'[ /\\:*?"<>|\n\r\t]+')

# 领域推断：先看期刊，再看标题/摘要关键词（沿用 PaperLibrary 的规则）
_DOMAIN_RULES = [
    ("电化学", r"(electrochemi|electrocatal|voltamm|impedance|\bbattery|batteries|"
               r"lithium|sodium[- ]ion|anode|cathode|supercapacit|corrosion|"
               r"电化学|电解|电池|锂|钠离子|阳极|阴极|超容|腐蚀|析氢|析氧|氧还原)"),
    ("催化", r"(catalys|catalyt|catalytic|\bcatal\b|photocatal|photoredox|催化|光催)"),
    ("有机合成", r"(synthesis|synthetic|reaction|reagent|ligand|asymmetric|"
                 r"cross[- ]coupling|cyclization|合成|反应|配体|不对称|偶联|环化)"),
    ("材料", r"(nanomaterial|nanoparticle|mof\b|\bcof\b|perovskite|polymer|"
             r"composite|thin film|membrane|材料|纳米|钙钛矿|聚合物|复合|薄膜|膜)"),
    ("表征分析", r"(spectroscop|\bxrd\b|\bxps\b|\bnmr\b|\bftir\b|infrared|"
                 r"characteriz|表征|光谱|红外|核磁|质谱)"),
]

NOTE_CHARS = 1600


def safe_name(text, fallback="untitled", max_len=110):
    """Windows 文件名安全化：非法字符、首尾点/空格、超长。"""
    value = _UNSAFE.sub("_", str(text or "")).strip(" ._")
    value = re.sub(r"_{2,}", "_", value)
    if not value:
        value = fallback
    return (value[:max_len].rstrip(" ._") or fallback)


def stable_document_id(paper):
    """同一篇论文永远得到同一个 id：arXiv 号（去版本）> DOI > 标题哈希。"""
    arxiv = str(paper.get("arxivId") or paper.get("arxiv_id") or "").strip()
    if arxiv:
        return re.sub(r"v\d+$", "", arxiv, flags=re.I)
    doi = str(paper.get("doi") or "").strip().lower()
    if doi:
        return "doi-" + re.sub(r"[^a-z0-9._-]+", "-", doi)
    basis = str(paper.get("title") or paper.get("pdfFile") or paper.get("url") or "").strip()
    if not basis:
        return "unknown"
    return "title-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


def infer_domain(paper):
    hay = " ".join(str(paper.get(key) or "") for key in
                   ("venue", "journal", "title", "abstract", "keywords"))
    for name, pattern in _DOMAIN_RULES:
        if re.search(pattern, hay, re.I):
            return name
    return "其他"


def _yaml(value):
    return json.dumps(str(value or ""), ensure_ascii=False)


def _authors_text(value):
    if isinstance(value, (list, tuple)):
        return "、".join(str(v) for v in value if str(v).strip())
    return str(value or "").strip()


def build_frontmatter(paper, document_id, domain, tags, now=None):
    now = now or datetime.now().strftime("%Y-%m-%d")
    lines = ["---", "document_id: " + _yaml(document_id)]
    arxiv = str(paper.get("arxivId") or paper.get("arxiv_id") or "").strip()
    if arxiv:
        lines.append("arxiv_id: " + _yaml(arxiv))
    doi = str(paper.get("doi") or "").strip()
    if doi:
        lines.append("doi: " + _yaml(doi))
    for key, value in (("title", paper.get("title")), ("year", paper.get("year")),
                       ("venue", paper.get("venue") or paper.get("journal"))):
        if value not in (None, ""):
            lines.append("%s: %s" % (key, _yaml(value)))
    authors = _authors_text(paper.get("authors"))
    if authors:
        lines.append("authors: " + _yaml(authors))
    lines.append("domain: " + _yaml(domain))
    if tags:
        lines.append("tags:")
        for tag in tags:
            lines.append("  - " + _yaml(tag))
    else:
        lines.append("tags: []")
    lines += ["created: " + _yaml(now), "status: " + _yaml("exported"), "---"]
    return "\n".join(lines)


def build_tags(paper, domain):
    tags = []
    for tag in ["论文笔记", domain] + list(paper.get("tags") or []):
        text = str(tag or "").strip()
        if text and text not in tags:
            tags.append(text)
    return tags


def _media_for_figure(paper_id, fig, data_dir):
    """把图表行变成 zip 内相对路径 + 源文件绝对路径；解析不到返回 None。"""
    image_path = str(fig.get("image_path") or "").strip()
    if not image_path:
        return None
    abs_path = legacy_figures.resolve_image_path(data_dir, image_path) if data_dir else None
    if not abs_path:
        return None
    name = abs_path.replace("\\", "/").rsplit("/", 1)[-1]
    return {"zipPath": "media/%s/%s" % (paper_id, name), "absPath": abs_path,
            "figIndex": fig.get("fig_index") or fig.get("figIndex"), "caption": fig.get("caption") or ""}


def build_note(paper, figures=None, data_dir=None, now=None, note_name=None):
    """返回 {noteName, text, domain, documentId, tags, media:[{zipPath, absPath, ...}]}。"""
    document_id = stable_document_id(paper)
    domain = infer_domain(paper)
    title = str(paper.get("title") or document_id).strip() or document_id
    note_name = note_name or safe_name(title, fallback=document_id)
    tags = build_tags(paper, domain)

    media = []
    media_lines = []
    for fig in (figures or []):
        entry = _media_for_figure(paper.get("id"), fig, data_dir)
        if not entry:
            continue
        media.append(entry)
        caption = re.sub(r"\s+", " ", str(entry["caption"] or "")).strip()
        media_lines.append("![Figure %s](%s)%s" % (
            entry["figIndex"] or "", entry["zipPath"], ("  \n" + caption) if caption else ""))

    body = [build_frontmatter(paper, document_id, domain, tags, now=now), "",
            "# " + title, ""]

    meta = ["## 核心信息"]
    for label, key in (("作者", "authors"), ("年份", "year"),
                       ("期刊/来源", "venue"), ("文档 ID", None)):
        if key is None:
            meta.append("- **文档 ID**：%s" % document_id)
            continue
        value = _authors_text(paper.get(key)) if key == "authors" else paper.get(key)
        if value not in (None, ""):
            meta.append("- **%s**：%s" % (label, value))
    if str(paper.get("doi") or "").strip():
        meta.append("- **DOI**：https://doi.org/%s" % str(paper.get("doi")).strip())
    if len(meta) > 1:
        body += meta + [""]

    abstract = str(paper.get("abstract") or "").strip()
    if abstract:
        body += ["## 摘要", "", abstract, ""]

    notes = str(paper.get("notes") or "").strip()
    if notes:
        body += ["## 我的笔记", "", notes[:20000], ""]

    ai_summary = str(paper.get("aiSummary") or "").strip()
    if ai_summary:
        body += ["## AI 总结", "", ai_summary[:20000], ""]

    summary_zh = str(paper.get("summaryZh") or "").strip()
    if summary_zh:
        body += ["## 中文总结", "", summary_zh[:20000], ""]

    if media_lines:
        body += ["## 图表", ""] + ["\n\n".join(media_lines), ""]

    return {"noteName": note_name, "text": "\n".join(body).rstrip() + "\n",
            "domain": domain, "documentId": document_id, "tags": tags, "media": media}
