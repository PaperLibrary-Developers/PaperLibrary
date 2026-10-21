# -*- coding: utf-8 -*-
"""
figure_anchor.py — 图文双向快速锚定引擎 (Figure-to-Text Anchor)
功能：
1. 毫秒级提取图注中的核心图表编号（Fig 1, Scheme 2, Table 3 等）；
2. 基于正则与上下文窗口，秒级定位正文中讨论该图表的全部段落与对应 PDF 页码；
3. 支持双引擎模式：
   - 离线模式（无需 API Key）：基于学术谓语规则智能提取关键结论句；
   - 在线模式（配置 API Key）：调用 LLM 深度提炼核心实验机理与定量指标。
"""

import os
import re
import fitz  # PyMuPDF


# 常见学术结论性动词与标记（用于离线精准提取核心句）
CONCLUSION_PATTERNS = [
    r'\b(?:demonstrates?|reveals?|indicates?|shows? that|confirms?|suggests?|proves?)\b',
    r'\b(?:exhibits?|leads? to|results? in|attributed to|as a result)\b',
    r'\b(?:significantly|compared (?:to|with)|highest|lowest|superior)\b',
    r'\b(?:increased?|decreased?|enhanced?|improved?|peak at)\b',
]
_CONCLUSION_REGEX = re.compile('|'.join(CONCLUSION_PATTERNS), re.IGNORECASE)


def normalize_academic_text(text: str) -> str:
    """清理学术 PDF 中常见的合字与非标字符"""
    if not text:
        return ""
    text = text.replace('\ufb01', 'fi').replace('\ufb02', 'fl').replace('\ufb00', 'ff')
    text = text.replace('\ufb03', 'ffi').replace('\ufb04', 'ffl')
    return text


def extract_figure_identity(caption: str, figure_type: str = "") -> dict:
    """
    从图注或类型中识别出图表的标准身份指纹。
    返回: {"prefix": "Fig", "num": "1", "label": "Fig. 1", "search_regex": re.Pattern}
    """
    cap = normalize_academic_text(caption or "").strip()
    ft = normalize_academic_text(figure_type or "").strip()
    
    # 在图注中抓取: 如 "Figure 1.", "Fig. 1a", "Fig. S1", "Figure 1.3", "Scheme 2:", "Table 1"
    # 支持前面带有 (a) 或引文 [1] 的非标情况
    m = re.search(r'\b(Figure|Fig\.?|FIG\.?|Scheme|Chart|Table)\s*\(?([S0-9]+(?:[\.\-][0-9]+)?[a-zA-Z]?)\b', cap, re.IGNORECASE)
    prefix = "Fig"
    num = ""
    
    if m:
        matched_prefix = m.group(1).strip()
        num = m.group(2).strip()
        if "scheme" in matched_prefix.lower():
            prefix = "Scheme"
        elif "table" in matched_prefix.lower():
            prefix = "Table"
        elif "chart" in matched_prefix.lower():
            prefix = "Chart"
        else:
            prefix = "Fig"
    else:
        # 支持省略了 Figure 单词的图注，如 "1.3 Schematic diagram..."
        m_num_lead = re.search(r'^([S0-9]+(?:[\.\-][0-9]+)?[a-zA-Z]?)\s+[A-Z]', cap)
        if m_num_lead:
            num = m_num_lead.group(1).strip()
            prefix = "Scheme" if "scheme" in ft.lower() else "Table" if "table" in ft.lower() else "Fig"
        else:
            # 如果图注没有，从 figure_type 尝试解析，例如 "Figure 3"
            m_ft = re.search(r'([S0-9]+(?:[\.\-][0-9]+)?[a-zA-Z]?)', ft)
            if m_ft:
                num = m_ft.group(1).strip()
                prefix = "Scheme" if "scheme" in ft.lower() else "Table" if "table" in ft.lower() else "Fig"

    if not num:
        return {}

    # 构建安全严格的正文搜索正则，避免匹配到 Fig. 10 这种前缀
    # 支持: Fig. 1, Figure 1, Figs. 1, Fig 1a, (Fig. 1)
    # 转义点号，如 1.3 -> 1\.3
    base_num = re.sub(r'[a-zA-Z]$', '', num)  # 如 1a -> 1
    escaped_base = re.escape(base_num)
    if prefix in ["Scheme", "Table", "Chart"]:
        p_pat = rf'\b{prefix}\s*\(?{escaped_base}(?![0-9])([a-zA-Z]?)\b'
    else:
        p_pat = rf'\b(?:Fig(?:ure)?s?\.?|FIGS?\.?)\s*\(?{escaped_base}(?![0-9])([a-zA-Z]?)\b'

    return {
        "prefix": prefix,
        "num": num,
        "base_num": base_num,
        "label": f"{prefix}. {num}",
        "pattern": re.compile(p_pat, re.IGNORECASE)
    }


def find_figure_mentions_in_paper(pdf_path: str, caption: str, figure_type: str = "",
                                  max_mentions: int = 8) -> list:
    """
    在指定 PDF 全文正文中毫秒级检索提及该图表的所有段落，并提取对应页码与上下文片段。
    返回列表: [{"page": 3, "snippet": "...", "match": "Fig. 1b"}, ...]
    """
    if not pdf_path or not os.path.isfile(pdf_path):
        return []

    ident = extract_figure_identity(caption, figure_type)
    if not ident:
        return []

    pattern = ident["pattern"]
    mentions = []
    seen_texts = set()

    try:
        doc = fitz.open(pdf_path)
    except Exception:
        return []

    try:
        for page_idx in range(len(doc)):
            page = doc[page_idx]
            page_num = page_idx + 1
            text = page.get_text("text")
            if not text:
                continue

            # 规范化连续空白符与学术合字
            clean_text = re.sub(r'[ \t]+', ' ', text)
            clean_text = normalize_academic_text(clean_text)
            
            # 分句处理，保留完整标点
            sentences = re.split(r'([.。?!；;\n\r]{1,2})', clean_text)
            full_sentences = []
            for i in range(0, len(sentences) - 1, 2):
                full_sentences.append(sentences[i] + sentences[i+1])
            if len(sentences) % 2 == 1:
                full_sentences.append(sentences[-1])

            for s_idx, sent in enumerate(full_sentences):
                s_strip = sent.strip()
                if len(s_strip) < 15:
                    continue
                # 跳过图注自身所在的句子（避免把当前页图注当作正文讨论）
                if caption and len(caption) > 20 and caption[:30] in s_strip:
                    continue

                m = pattern.search(s_strip)
                if m:
                    # 获取该句及其后一句作为自然讨论语境
                    ctx = s_strip
                    if s_idx + 1 < len(full_sentences):
                        next_sent = full_sentences[s_idx + 1].strip()
                        if 15 < len(next_sent) < 300:
                            ctx += " " + next_sent

                    # 清理连字符折行
                    ctx = re.sub(r'(\w+)-\s+(\w+)', r'\1\2', ctx)
                    ctx = re.sub(r'\s+', ' ', ctx).strip()

                    # 避免在同一页重复收录雷同句子
                    sim_key = f"{page_num}:{ctx[:60]}"
                    if sim_key in seen_texts:
                        continue
                    seen_texts.add(sim_key)

                    mentions.append({
                        "page": page_num,
                        "snippet": ctx[:320] + ("…" if len(ctx) > 320 else ""),
                        "match": m.group(0),
                    })

                    if len(mentions) >= max_mentions:
                        break
            if len(mentions) >= max_mentions:
                break
    finally:
        doc.close()

    return mentions


def extract_offline_conclusion(mentions: list, caption: str = "") -> str:
    """
    离线模式（无需任何 API Key）：
    通过材料/化学学术谓语规则匹配，自动挖掘出最具有结论性的正文讨论句。
    """
    if not mentions:
        return "原文正文中未检测到明显讨论段落，请参考原作者图注。"

    best_sentence = ""
    highest_score = 0

    for m in mentions:
        s = m.get("snippet", "")
        # 统计包含多少个学术结论指示词
        matches = _CONCLUSION_REGEX.findall(s)
        score = len(matches)
        # 包含定量数值或百分比加分
        if re.search(r'\b\d+(?:\.\d+)?\s*(?:%|V|mA|eV|nm|°C|K|h|min|s)\b', s):
            score += 1
        if score > highest_score:
            highest_score = score
            best_sentence = s

    if not best_sentence:
        best_sentence = mentions[0].get("snippet", "")

    return f"💡 **【正文核心论断】**：\n> “{best_sentence}”\n\n*(注：本结论由本地学术语义规则从正文自动提炼，无需连接在线大模型)*"


def summarize_figure_with_llm(caption: str, mentions: list, fig_label: str,
                              api_provider: str, api_key: str, api_model: str) -> str:
    """
    在线模式（用户配置了 API Key）：
    调用 LLM 深度梳理图注与正文讨论，提炼出关键实验结论与机理。
    """
    import ai_engine

    snippets_text = "\n\n".join([f"- [第 {m['page']} 页]: {m['snippet']}" for m in mentions[:5]])
    prompt = f"""你是一位严谨的材料化学与纳米能源领域的顶级审稿专家。
请根据以下从论文正文中提取出的关于【{fig_label}】的关键讨论片段和图注，用2~3句精炼、专业的学术中文，提炼总结出作者通过该图得出的【核心实验结论与反应机理】（包括关键性能数据、峰位/形貌归属、对比结论等）：

【原图注 Caption】：
{caption or "无独立图注"}

【正文关键讨论片段】：
{snippets_text or "无直接正文片段"}

要求：直接给出清晰凝练的结论要点，不要废话，专业术语准确。"""

    try:
        reply = ai_engine.copilot_chat(
            system_prompt="你是一位专业的学术论文图表与实验机理解析专家。",
            history_messages=[],
            user_message=prompt,
            api_provider=api_provider,
            api_key=api_key,
            api_model=api_model
        )
        return reply.strip()
    except Exception as e:
        # 在线调用若遇网络波动，优雅自动降级为离线模式
        fallback = extract_offline_conclusion(mentions, caption)
        return f"{fallback}\n\n*(在线模型调用异常: {e}，已自动降级为离线提炼)*"


def save_figure_conclusion(fig_id: int, conclusion: str):
    """
    保存图表提炼结论（带自动建列与强容错，直接操作 DB，避免热重载缓存问题）
    """
    import database
    try:
        if hasattr(database, "update_figure_conclusion"):
            database.update_figure_conclusion(fig_id, conclusion)
            return
    except Exception:
        pass

    conn = database.get_conn()
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(figures)").fetchall()]
        if 'ai_conclusion' not in cols:
            conn.execute("ALTER TABLE figures ADD COLUMN ai_conclusion TEXT DEFAULT ''")
        conn.execute("UPDATE figures SET ai_conclusion=? WHERE id=?", (conclusion, fig_id))
        conn.commit()
    finally:
        conn.close()

