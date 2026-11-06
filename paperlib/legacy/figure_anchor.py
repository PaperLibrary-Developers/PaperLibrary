# -*- coding: utf-8 -*-
"""图文双向锚定：图表身份识别 + 正文讨论定位 + 离线结论提炼。

移植自 C:\\Users\\wzxle\\Desktop\\WB比赛\\PaperLibrary\\app\\figure_anchor.py
契约名：figure_anchor（extract_figure_identity / find_figure_mentions_in_paper /
        extract_offline_conclusion）

与旧版的差异（去 Streamlit / 去直连 sqlite3 / 去在线依赖）：
  · 删除 summarize_figure_with_llm 与 save_figure_conclusion（在线调用归 services.llm，
    落库归 store.repo.save_analysis / insert_figures，由 api 层决定）；
  · 保留「离线学术谓语规则」提炼核心论断 —— 这是无 Key 时（G2 离线目标）唯一可用的路径。
"""
from __future__ import annotations

import re

try:  # PyMuPDF 发行版同时提供 pymupdf 与 fitz 两个入口
    import pymupdf as fitz
except ImportError:  # pragma: no cover - 兼容老版本 PyMuPDF
    import fitz  # type: ignore

# 常见学术结论性动词与标记（离线提取核心句用）
CONCLUSION_PATTERNS = [
    r"\b(?:demonstrates?|reveals?|indicates?|shows? that|confirms?|suggests?|proves?)\b",
    r"\b(?:exhibits?|leads? to|results? in|attributed to|as a result)\b",
    r"\b(?:significantly|compared (?:to|with)|highest|lowest|superior)\b",
    r"\b(?:increased?|decreased?|enhanced?|improved?|peak at)\b",
]
_CONCLUSION_REGEX = re.compile("|".join(CONCLUSION_PATTERNS), re.IGNORECASE)
_QUANTITY_REGEX = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|V|mA|eV|nm|°C|K|h|min|s)\b")

MAX_MENTIONS = 8
SNIPPET_CHARS = 320


def normalize_academic_text(text: str) -> str:
    """清理学术 PDF 中常见的合字与非标字符（fi/fl/ff 等连字）。"""
    if not text:
        return ""
    text = text.replace("\ufb01", "fi").replace("\ufb02", "fl").replace("\ufb00", "ff")
    return text.replace("\ufb03", "ffi").replace("\ufb04", "ffl")


def extract_figure_identity(caption: str, figure_type: str = "") -> dict:
    """从图注/类型里识别图表身份：{'prefix','num','base_num','label','pattern'}；识别不出返回 {}。"""
    cap = normalize_academic_text(caption or "").strip()
    ftype = normalize_academic_text(figure_type or "").strip()
    prefix, num = "Fig", ""

    m = re.search(
        r"\b(Figure|Fig\.?|FIG\.?|Scheme|Chart|Table)\s*\(?([S0-9]+(?:[\.\-][0-9]+)?[a-zA-Z]?)\b",
        cap, re.IGNORECASE,
    )
    if m:
        matched, num = m.group(1).strip(), m.group(2).strip()
        low = matched.lower()
        prefix = ("Scheme" if "scheme" in low else
                  "Table" if "table" in low else
                  "Chart" if "chart" in low else "Fig")
    else:
        # 省略 Figure 单词的图注，如 "1.3 Schematic diagram ..."
        lead = re.search(r"^([S0-9]+(?:[\.\-][0-9]+)?[a-zA-Z]?)\s+[A-Z]", cap)
        if lead:
            num = lead.group(1).strip()
            prefix = ("Scheme" if "scheme" in ftype.lower() else
                      "Table" if "table" in ftype.lower() else "Fig")
        else:
            m_ft = re.search(r"([S0-9]+(?:[\.\-][0-9]+)?[a-zA-Z]?)", ftype)
            if m_ft:
                num = m_ft.group(1).strip()
                prefix = ("Scheme" if "scheme" in ftype.lower() else
                          "Table" if "table" in ftype.lower() else "Fig")

    if not num:
        return {}

    base_num = re.sub(r"[a-zA-Z]$", "", num)  # 1a → 1
    escaped = re.escape(base_num)
    if prefix in ("Scheme", "Table", "Chart"):
        pattern = re.compile(
            r"\b" + prefix + r"\s*\(?" + escaped + r"(?![0-9])([a-zA-Z]?)\b", re.IGNORECASE)
    else:
        pattern = re.compile(
            r"\b(?:Fig(?:ure)?s?\.?|FIGS?\.?)\s*\(?" + escaped + r"(?![0-9])([a-zA-Z]?)\b",
            re.IGNORECASE)

    return {"prefix": prefix, "num": num, "base_num": base_num,
            "label": prefix + ". " + num, "pattern": pattern}


def _split_sentences(text: str):
    parts = re.split(r"([.。?!；;\n\r]{1,2})", text)
    sentences = []
    for i in range(0, len(parts) - 1, 2):
        sentences.append(parts[i] + parts[i + 1])
    if len(parts) % 2 == 1:
        sentences.append(parts[-1])
    return sentences


def find_figure_mentions_in_paper(pdf_path: str, caption: str, figure_type: str = "",
                                  max_mentions: int = MAX_MENTIONS) -> list:
    """在全文中检索讨论该图表的段落：返回 [{'page','snippet','match'}, ...]。"""
    import os

    if not pdf_path or not os.path.isfile(pdf_path):
        return []
    ident = extract_figure_identity(caption, figure_type)
    if not ident:
        return []

    pattern = ident["pattern"]
    mentions = []
    seen = set()
    try:
        doc = fitz.open(pdf_path)
    except Exception:  # noqa: BLE001
        return []
    try:
        for page_idx in range(len(doc)):
            text = doc[page_idx].get_text("text")
            if not text:
                continue
            clean = normalize_academic_text(re.sub(r"[ \t]+", " ", text))
            sentences = _split_sentences(clean)
            for s_idx, sentence in enumerate(sentences):
                stripped = sentence.strip()
                if len(stripped) < 15:
                    continue
                # 跳过图注自身（避免把图注当正文讨论）
                if caption and len(caption) > 20 and caption[:30] in stripped:
                    continue
                match = pattern.search(stripped)
                if not match:
                    continue
                ctx = stripped
                if s_idx + 1 < len(sentences):
                    nxt = sentences[s_idx + 1].strip()
                    if 15 < len(nxt) < 300:
                        ctx += " " + nxt
                ctx = re.sub(r"(\w+)-\s+(\w+)", r"\1\2", ctx)  # 连字符折行
                ctx = re.sub(r"\s+", " ", ctx).strip()
                key = "%d:%s" % (page_idx + 1, ctx[:60])
                if key in seen:
                    continue
                seen.add(key)
                mentions.append({
                    "page": page_idx + 1,
                    "snippet": ctx[:SNIPPET_CHARS] + ("…" if len(ctx) > SNIPPET_CHARS else ""),
                    "match": match.group(0),
                })
                if len(mentions) >= max_mentions:
                    break
            if len(mentions) >= max_mentions:
                break
    finally:
        doc.close()
    return mentions


def extract_offline_conclusion(mentions: list, caption: str = "") -> str:
    """离线提炼核心论断：学术谓语规则打分，取最高分句（无 Key 也可用）。"""
    if not mentions:
        return "原文正文中未检测到明显讨论段落，请参考原作者图注。"

    best, best_score = "", 0
    for mention in mentions:
        snippet = mention.get("snippet", "")
        score = len(_CONCLUSION_REGEX.findall(snippet))
        if _QUANTITY_REGEX.search(snippet):
            score += 1
        if score > best_score:
            best, best_score = snippet, score
    if not best:
        best = mentions[0].get("snippet", "")
    return ("💡 **【正文核心论断】**：\n> “" + best + "”\n\n"
            "*(注：本结论由本地学术语义规则从正文自动提炼，无需连接在线大模型)*")


def anchor_figure(pdf_path: str, caption: str, figure_type: str = "") -> dict:
    """组合入口（api 层只用这个）：返回 {identity, mentions, conclusion}；失败不抛异常。"""
    try:
        ident = extract_figure_identity(caption, figure_type)
        mentions = find_figure_mentions_in_paper(pdf_path, caption, figure_type)
        conclusion = extract_offline_conclusion(mentions, caption) if mentions else ""
        return {"identity": ident, "mentions": mentions, "conclusion": conclusion}
    except Exception as exc:  # noqa: BLE001
        return {"identity": {}, "mentions": [], "conclusion": "", "error": str(exc)}
