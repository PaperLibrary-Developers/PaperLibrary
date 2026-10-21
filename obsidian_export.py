# -*- coding: utf-8 -*-
"""obsidian_export.py — 把库里的文献导出成 Obsidian Vault 笔记

为什么值得单独做（而不是让用户自己复制粘贴）：
  · **document_id 稳定**：同一篇论文重复导出永远落在同一个文件上，
    arxiv 的 v2/v3 也归一到同一个 id，不会生成「标题 (1).md」这种垃圾。
  · **图片走附件目录 + wikilink**：![[assets/<笔记名>/Figure-1.png|800]]
    这样在 Obsidian 里移动/重命名笔记时图片链接不会断（相对路径会断）。
  · **不覆盖**：默认写到已存在的笔记就停下来报「已存在」，除非显式 force。
    旧笔记里可能有你手写的东西，这是不能默默冲掉的。
  · **frontmatter 用 json.dumps 转义**：标题里有冒号、引号、中文书名号也不会
    把 YAML 写坏（这是手拼 YAML 最常见的翻车点）。

目录布局（和 evil-read-arxiv 一致，便于整套 skill 共用）：
    <vault>/20_Research/Papers/<领域>/<安全标题>.md
    <vault>/20_Research/Papers/<领域>/assets/<安全标题>/Figure-1.png
"""

import hashlib
import json
import os
import re
from datetime import datetime

DEFAULT_VAULT = os.environ.get("OBSIDIAN_VAULT_PATH", "")

# 领域推断：先看期刊，再看标题/摘要关键词。命中越靠前优先级越高。
_DOMAIN_RULES = [
    ("电化学", r"(electrochemi|electrocatal|voltamm|impedance|\bbattery|batteries|"
               r"lithium|sodium[- ]ion|anode|cathode|supercapacit|corrosion|"
               r"电化学|电解|电池|锂|钠离子|阳极|阴极|超容|腐蚀|析氢|析氧|氧还原)"),
    ("催化", r"(catalys|catalyt|catalytic|\bcatal\b|photocatal|photoredox|"
             r"催化|光催|photoredox)"),
    ("有机合成", r"(synthesis|synthetic|reaction|reagent|ligand|asymmetric|"
                 r"cross[- ]coupling|cyclization|合成|反应|配体|不对称|偶联|环化)"),
    ("材料", r"(nanomaterial|nanoparticle|mof\b|\bcof\b|perovskite|polymer|"
             r"composite|thin film|membrane|材料|纳米|钙钛矿|聚合物|复合|薄膜|膜)"),
    ("表征分析", r"(spectroscop|\bxrd\b|\bxps\b|\bnmr\b|\bftir\b|infrared|"
                 r"\bms\b|mass spectrom|characteriz|表征|光谱|红外|核磁|质谱)"),
]

_UNSAFE = re.compile(r'[ /\\:*?"<>|\n\r\t]+')


def get_vault(cli_vault=None):
    """vault 路径优先级：显式参数 > 环境变量 > 配置文件 > 空。"""
    if cli_vault:
        return os.path.abspath(cli_vault)
    if DEFAULT_VAULT:
        return os.path.abspath(DEFAULT_VAULT)
    cfg = _load_cfg()
    v = cfg.get("obsidian_vault", "")
    return os.path.abspath(v) if v else ""


def set_vault(path):
    """记住 vault 路径（写进 data/.app_config.json，和 app 其它配置同处）。"""
    cfg = _load_cfg()
    cfg["obsidian_vault"] = os.path.abspath(path) if path else ""
    _save_cfg(cfg)
    return cfg["obsidian_vault"]


def _cfg_file():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "data", ".app_config.json")


def _load_cfg():
    p = _cfg_file()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cfg(cfg):
    p = _cfg_file()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ═══════════════════════════════════════════════════
# 标识与命名
# ═══════════════════════════════════════════════════

def stable_document_id(paper):
    """同一篇论文永远得到同一个 id：arxiv 号（去版本）> DOI > 标题/URL 哈希。"""
    arxiv = (paper.get("arxiv_id") or "").strip()
    if arxiv:
        return re.sub(r"v\d+$", "", arxiv, flags=re.I)
    doi = (paper.get("doi") or "").strip().lower()
    if doi:
        return "doi-" + re.sub(r"[^a-z0-9._-]+", "-", doi)
    basis = (paper.get("title") or paper.get("pdf_url") or
             paper.get("abs_path") or "").strip()
    if not basis:
        return "unknown"
    prefix = "title" if paper.get("title") else "src"
    return f"{prefix}-{hashlib.sha256(basis.encode('utf-8')).hexdigest()[:16]}"


def safe_name(text, fallback="untitled", max_len=110):
    """文件名安全化：Windows 非法字符、首尾点/空格、超长都要处理。"""
    s = _UNSAFE.sub("_", str(text or "")).strip(" ._")
    s = re.sub(r"_{2,}", "_", s)
    if not s:
        s = fallback
    if len(s) > max_len:
        s = s[:max_len].rstrip(" ._")
    return s or fallback


def infer_domain(paper):
    """粗粒度领域，只用来分文件夹；判不出就归「其他」。"""
    hay = " ".join(str(paper.get(k) or "") for k in
                   ("journal", "title", "abstract", "keywords"))
    for name, pat in _DOMAIN_RULES:
        if re.search(pat, hay, re.I):
            return name
    return "其他"


def _yaml(value):
    """永远输出带引号的 YAML 标量 —— 省得跟冒号、井号、引号打架。"""
    return json.dumps(str(value or ""), ensure_ascii=False)


def _list_yaml(key, items):
    items = [i for i in (items or []) if str(i or "").strip()]
    if not items:
        return [f"{key}: []"]
    return [f"{key}:"] + [f"  - {_yaml(i)}" for i in items]


# ═══════════════════════════════════════════════════
# frontmatter
# ═══════════════════════════════════════════════════

def build_frontmatter(paper, document_id, domain, tags, scores=None,
                      source_type="local_pdf", now=None):
    now = now or datetime.now().strftime("%Y-%m-%d")
    arxiv = (paper.get("arxiv_id") or "").strip()
    lines = ["---", f"document_id: {_yaml(document_id)}"]
    if arxiv:
        lines.append(f"arxiv_id: {_yaml(arxiv)}")
    doi = (paper.get("doi") or "").strip()
    if doi:
        lines.append(f"doi: {_yaml(doi)}")
    lines += [
        f"source_type: {_yaml(source_type)}",
        f"title: {_yaml(paper.get('title') or '')}",
        f"authors: {_yaml(paper.get('authors') or '')}",
        f"published: {_yaml(paper.get('year') or '')}",
        f"venue: {_yaml(paper.get('journal') or '')}",
        f"domain: {_yaml(domain)}",
    ]
    pdf_path = (paper.get("abs_path") or "").replace("\\", "/")
    if pdf_path:
        lines.append(f"pdf: {_yaml(pdf_path)}")
    url = (paper.get("url") or paper.get("pdf_url") or "").strip()
    if url:
        lines.append(f"source_url: {_yaml(url)}")

    if scores:
        for k in ("novelty", "technical", "evidence", "writing", "usefulness"):
            if scores.get(k) is not None:
                lines.append(f"score_{k}: {scores[k]}")
        if scores.get("avg") is not None:
            lines.append(f"score_avg: {scores['avg']}")

    lines += _list_yaml("tags", tags)
    lines += _list_yaml("related_papers", [])
    lines += [f"created: {_yaml(now)}", f"updated: {_yaml(now)}",
              'status: "analyzed"', "---"]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════
# 图注 / 图片嵌入
# ═══════════════════════════════════════════════════

def figures_section(figures, asset_prefix, width=800):
    """figures: [{fig_index, image_path|bytes saved name, caption, figure_type}]
    asset_prefix: 相对笔记目录的附件路径，如 assets/标题
    只接受已经落盘的图片路径；bytes 请调用方先存盘再传路径。"""
    if not figures:
        return ""
    out = ["## 方法架构与图片", ""]
    for f in figures:
        rel = f.get("asset_rel")
        idx = f.get("fig_index", "")
        cap = (f.get("caption") or "").strip().replace("\n", " ")
        ftype = f.get("figure_type") or ""
        label = f"**Figure {idx}**"
        if ftype:
            label += f" · {ftype}"
        out.append(label)
        if rel:
            out.append(f"![[{rel}|{width}]]")
        out.append(cap or "--")
        out.append("")
    return "\n".join(out)


def related_section(related):
    """related: [{'title':..., 'note':...}] → Obsidian 内的双链回填。"""
    if not related:
        return ""
    out = ["## 相关工作与技术路线", ""]
    for r in related:
        title = (r.get("note") or r.get("title") or "").strip()
        if not title:
            continue
        why = (r.get("why") or "").strip()
        out.append(f"- [[{title}]]" + (f" — {why}" if why else ""))
    out.append("")
    return "\n".join(out) if len(out) > 2 else ""


# ═══════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════

def build_note(paper, analysis_md="", figures=None, related=None, tags=None,
               scores=None, domain=None, asset_prefix=None, now=None):
    """拼出完整笔记文本（不落盘）——便于先预览再导出。"""
    document_id = stable_document_id(paper)
    domain = domain or infer_domain(paper)
    title = (paper.get("title") or document_id).strip()
    note_name = safe_name(title, fallback=document_id)

    base_tags = ["论文笔记", domain]
    if (paper.get("arxiv_id") or "").strip():
        base_tags.append("arxiv")
    if (paper.get("doi") or "").strip():
        base_tags.append("doi")
    all_tags = []
    for t in base_tags + list(tags or []):
        t = str(t or "").strip()
        if t and t not in all_tags:
            all_tags.append(t)

    fm = build_frontmatter(paper, document_id, domain, all_tags, scores,
                           now=now)

    body = [fm, "", f"# {title}", ""]

    # 核心信息
    links = []
    if (paper.get("arxiv_id") or "").strip():
        aid = re.sub(r"v\d+$", "", paper["arxiv_id"].strip(), flags=re.I)
        links.append(f"[arXiv](https://arxiv.org/abs/{aid})")
        links.append(f"[PDF](https://arxiv.org/pdf/{aid})")
    if (paper.get("doi") or "").strip():
        links.append(f"[DOI](https://doi.org/{paper['doi'].strip()})")
    if (paper.get("url") or "").strip():
        links.append(f"[原始来源]({paper['url'].strip()})")

    body += ["## 核心信息", "",
             f"- **文档 ID**：{document_id}",
             f"- **来源类型**：{'arxiv' if paper.get('arxiv_id') else 'local_pdf'}",
             f"- **作者**：{paper.get('authors') or '--'}",
             f"- **机构**：--",
             f"- **发布时间**：{paper.get('year') or '--'}",
             f"- **会议/期刊/发布方**：{paper.get('journal') or '--'}",
             f"- **链接**：{' | '.join(links) if links else '--'}",
             f"- **引用**：--", ""]

    if analysis_md:
        # 分析报告本体已经是 Markdown，直接嵌进去；图片章节单独追加
        body += [analysis_md.strip(), ""]
    else:
        body += ["## 摘要与核心要点", "", "### 原文摘要", "",
                 (paper.get("abstract") or "--").strip(), "",
                 "## 我的笔记", "", "%% 在这里添加个人笔记 %%", ""]

    if figures:
        body += [figures_section(figures, asset_prefix or
                                 f"assets/{note_name}"), ""]
    if related:
        rs = related_section(related)
        if rs:
            body += [rs]

    body += ["## 我的笔记", "", "%% 在这里添加个人笔记 %%", "",
             "## 外部资源", "- [代码/项目主页/演示]", ""]
    return "\n".join(body), note_name, domain, document_id


def export_note(paper, analysis_md="", figures=None, related=None, tags=None,
                scores=None, domain=None, vault=None, force=False,
                language="zh", now=None):
    """落盘。返回 dict：{ok, status, path, note_name, domain, document_id, message}

    status: 'created' | 'exists' | 'error'
    图片 dict 里若带 'image_bytes'，会先写到附件目录再把 asset_rel 填好。
    """
    vault_root = get_vault(vault)
    if not vault_root:
        return {"ok": False, "status": "error", "path": "", "note_name": "",
                "domain": "", "document_id": "",
                "message": "还没有设置 Obsidian Vault 路径，请先在设置里选一个文件夹。"}
    if not os.path.isdir(vault_root):
        return {"ok": False, "status": "error", "path": "", "note_name": "",
                "domain": "", "document_id": "",
                "message": f"Vault 目录不存在：{vault_root}"}

    text, note_name, domain, document_id = build_note(
        paper, analysis_md=analysis_md, figures=None, related=related,
        tags=tags, scores=scores, domain=domain, now=now)

    note_dir = os.path.join(vault_root, "20_Research", "Papers", domain)
    note_path = os.path.join(note_dir, f"{note_name}.md")
    if os.path.exists(note_path) and not force:
        return {"ok": False, "status": "exists", "path": note_path,
                "note_name": note_name, "domain": domain,
                "document_id": document_id,
                "message": f"笔记已存在，没有覆盖：{note_path}"}

    try:
        os.makedirs(note_dir, exist_ok=True)

        # 图片附件：先落盘，再把 wikilink 插入正文
        saved = []
        if figures:
            asset_dir = os.path.join(note_dir, "assets", note_name)
            os.makedirs(asset_dir, exist_ok=True)
            for i, f in enumerate(figures, 1):
                idx = f.get("fig_index", i)
                rel = f.get("asset_rel")
                if not rel:
                    blob = f.get("image_bytes")
                    if blob:
                        fname = f"Figure-{idx}.png"
                        with open(os.path.join(asset_dir, fname), "wb") as fh:
                            fh.write(blob)
                        rel = f"assets/{note_name}/{fname}"
                    elif f.get("image_path") and os.path.exists(f["image_path"]):
                        # 已在别处的图片：复制进附件目录，避免链接将来失效
                        import shutil
                        ext = os.path.splitext(f["image_path"])[1] or ".png"
                        fname = f"Figure-{idx}{ext}"
                        shutil.copyfile(f["image_path"],
                                        os.path.join(asset_dir, fname))
                        rel = f"assets/{note_name}/{fname}"
                if rel:
                    saved.append({**f, "asset_rel": rel})

        if saved:
            text2, _n, _d, _i = build_note(
                paper, analysis_md=analysis_md, figures=saved, related=related,
                tags=tags, scores=scores, domain=domain, now=now)
            text = text2

        with open(note_path, "w", encoding="utf-8") as fh:
            fh.write(text)
    except OSError as e:
        return {"ok": False, "status": "error", "path": note_path,
                "note_name": note_name, "domain": domain,
                "document_id": document_id, "message": f"写入失败：{e}"}

    return {"ok": True, "status": "created", "path": note_path,
            "note_name": note_name, "domain": domain,
            "document_id": document_id,
            "message": f"已导出：{note_path}"}


def preview(paper, analysis_md="", figures=None, related=None, tags=None,
            scores=None, domain=None):
    """不落盘预览（给「导出前先看看」按钮用）。"""
    text, note_name, dom, doc_id = build_note(
        paper, analysis_md=analysis_md, figures=figures, related=related,
        tags=tags, scores=scores, domain=domain)
    return {"text": text, "note_name": note_name, "domain": dom,
            "document_id": doc_id}


if __name__ == "__main__":
    demo = {"title": "A Cu-Based Catalyst: \"CO2\" Reduction?", "authors": "A, B",
            "year": 2024, "journal": "JACS", "doi": "10.1021/jacs.4b00001",
            "arxiv_id": "2401.00001v3"}
    print(preview(demo, analysis_md="## 摘要\n测试")["text"][:700])
