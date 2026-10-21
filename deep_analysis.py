# -*- coding: utf-8 -*-
"""deep_analysis.py — 论文「深度分析」引擎（不只是摘要）

和既有的 `ai_engine.summarize_paper` 的分工：
  · summarize_paper  产出给人快速扫一眼的卡片（背景/方法/发现/创新点）
  · deep_analysis    产出可以当文献笔记归档的长报告：把「作者主张」和「证据支持的判断」
                     分开写，明确标出作者自述的局限与推断出来的风险，最后给五维评分。

为什么用 JSON 而不是让模型直接吐 Markdown：
  Markdown 排版由模型决定 → 每次结构飘、表格列数不一致，没法二次加工。
  这里让模型只负责内容，排版在我们这边，所以同一篇重跑两次排版一模一样。

容错：
  · 模型偶尔会带 ```json 围栏或前后废话 → 剥掉再解析
  · 解析失败时有一次「只重发内容不要围栏」的补正重试
  · 再失败就退化成纯文本报告，不吞掉已经付费买到的内容
"""

import json
import re

from openai import OpenAI
import anthropic

MAX_TEXT_CHARS = 26000

SYSTEM = """你是一位国际顶刊（JACS / Nature 子刊 / ACS Catal. 级别）的资深审稿人，
同时熟悉电化学、有机合成、纳米材料与催化领域。请对给定论文做**可归档的深度分析**。

硬性要求：
1. 严格区分「作者主张」与「证据支持的判断」：作者说的写进 author_claims，
   你根据实验证据判断的写进 evidence_judgement，不得混为一谈。
2. 不得编造实验数据。论文没给的数值、条件、对照，就写空字符串，不要推测填数。
3. 化学细节要精确：催化剂负载量、电解液浓度、电位范围/扫速、参比电极、
   产率/法拉第效率/TOF 等，有就照抄具体数值和单位，没有就留空。
4. 局限分两类：作者自己承认的（limitations_stated）与你读出来的（limitations_inferred）。
5. 只输出 JSON，不要 markdown 围栏，不要任何解释性前后缀。

JSON 结构（键名不得改动，缺内容用 "" 或 []）：
{
  "one_liner": "一句话概括这篇论文做了什么、结论是什么",
  "original_abstract": "原文摘要的关键表述（可为英文原句摘录，不得改写）",
  "zh_overview": "中文概述：准确转述，不添加原文没有的结论",
  "problem": "研究背景、动机、问题定义与适用边界",
  "method_core": "核心方法：组件、流程、关键设计选择及其理由",
  "method_steps": ["步骤1", "步骤2", "步骤3"],
  "formulas": [{"latex": "行内 LaTeX", "meaning": "这个式子的物理/化学含义"}],
  "experiments": {
    "systems": "体系/材料/数据集",
    "baselines": "对照与基线",
    "conditions": "关键实验条件（温度、电位、浓度、时间等）",
    "metrics": "评价指标",
    "main_results": "主要结果，带具体数值",
    "ablations": "消融实验、稳健性或重复性验证"
  },
  "key_points": ["核心要点1", "核心要点2", "核心要点3", "核心要点4"],
  "contributions": ["贡献1", "贡献2"],
  "author_claims": ["作者明确声称的1", "作者明确声称的2"],
  "evidence_judgement": "基于论文展示的证据，你判断哪些结论站得住、哪些证据链偏弱，以及为什么",
  "strengths": ["优势1", "优势2"],
  "limitations_stated": ["作者自述局限1"],
  "limitations_inferred": ["你推断出的局限1"],
  "applicability": {
    "when": "什么条件下可以放心用这篇的方法",
    "when_not": "什么情况不该照搬"
  },
  "related": ["可能需要对比的相关工作或技术路线"],
  "open_questions": ["读完后仍未解决、值得追问的问题"],
  "scores": {
    "novelty":    {"score": 0, "reason": "创新性打分理由"},
    "technical":  {"score": 0, "reason": "技术质量打分理由"},
    "evidence":   {"score": 0, "reason": "证据充分性打分理由"},
    "writing":    {"score": 0, "reason": "写作质量打分理由"},
    "usefulness": {"score": 0, "reason": "实用性打分理由"}
  }
}

评分标准：0-10 的整数。8 以上只给真正突出的；5 是「合格但不惊艳」；3 以下是明显缺陷。
"""

SCORE_LABELS = [
    ("novelty", "创新性"),
    ("technical", "技术质量"),
    ("evidence", "证据充分性"),
    ("writing", "写作质量"),
    ("usefulness", "实用性"),
]


# ═══════════════════════════════════════════════════
# 客户端
# ═══════════════════════════════════════════════════

def _deepseek(api_key, base_url=None):
    return OpenAI(api_key=api_key,
                  base_url=base_url or "https://api.deepseek.com")


def _claude(api_key):
    return anthropic.Anthropic(api_key=api_key)


def _strip_fence(raw):
    """剥掉 ```json 围栏与前后废话，返回最外层 {...} 内容。"""
    t = str(raw or "").strip()
    t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = re.sub(r"\s*```$", "", t).strip()
    if t.startswith("{") and t.endswith("}"):
        return t
    i, j = t.find("{"), t.rfind("}")
    return t[i:j + 1] if (i >= 0 and j > i) else t


def _parse_json(raw):
    for cand in (_strip_fence(raw), str(raw or "").strip()):
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue
    return None


# ═══════════════════════════════════════════════════
# 上下文构造（复用 ai_engine 的论文背景拼装思路，但更省 token）
# ═══════════════════════════════════════════════════

def build_context(paper, figures=None, max_chars=MAX_TEXT_CHARS):
    parts = ["# 题录"]
    for label, key in (("标题", "title"), ("作者", "authors"), ("期刊", "journal"),
                       ("年份", "year"), ("DOI", "doi")):
        val = paper.get(key)
        if val:
            parts.append(f"- {label}: {val}")

    if figures:
        parts.append("\n# 图表清单（按序，含图注）")
        for f in figures[:30]:
            cap = (f.get("caption") or "").strip().replace("\n", " ")
            ftype = f.get("figure_type") or ""
            line = f"- [Figure {f.get('fig_index', '')}]"
            if ftype:
                line += f" ({ftype})"
            if cap:
                line += f" 图注: {cap[:220]}"
            parts.append(line)

    text = (paper.get("full_text") or "").strip()
    if text:
        # 优先给「引言 + 实验 + 结论」的结构信息：头尾各取一段，中间按预算截断
        if len(text) > max_chars:
            head = text[: int(max_chars * 0.62)]
            tail = text[-int(max_chars * 0.30):]
            body = head + "\n\n……（中段省略）……\n\n" + tail
        else:
            body = text
        parts.append(f"\n# 正文\n{body}")
    else:
        parts.append("\n# 正文\n（本地没有抽取到正文文本，请仅基于图表清单与题录做保守分析）")
    return "\n".join(parts)


def _ask(prompt, provider, api_key, model, base_url, max_tokens=4500):
    if provider == "deepseek":
        client = _deepseek(api_key, base_url)
        resp = client.chat.completions.create(
            model=model or "deepseek-chat",
            messages=[{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
        )
        return resp.choices[0].message.content or ""
    if provider == "claude":
        client = _claude(api_key)
        resp = client.messages.create(
            model=model or "claude-sonnet-5",
            max_tokens=max_tokens,
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text or ""
    raise ValueError(f"Unknown provider: {provider}")


# ═══════════════════════════════════════════════════
# 主入口
# ═══════════════════════════════════════════════════

def analyze_paper(paper, figures=None, provider="deepseek", api_key="", model=None,
                  base_url=None, max_chars=MAX_TEXT_CHARS):
    """返回 {'ok', 'data'(dict|None), 'markdown', 'error', 'raw'}。绝不抛异常。"""
    ctx = build_context(paper, figures, max_chars)
    prompt = ("请深度分析下面这篇论文，严格按系统消息里的 JSON 结构输出。\n\n" + ctx)

    raw = ""
    try:
        raw = _ask(prompt, provider, api_key, model, base_url)
    except Exception as e:
        return {"ok": False, "data": None, "markdown": "", "raw": "",
                "error": f"模型调用失败：{type(e).__name__}: {e}"}

    data = _parse_json(raw)
    if data is None:
        # 补正重试一次：只要求它把 JSON 修好
        try:
            raw2 = _ask(prompt + "\n\n注意：上一次输出不是合法 JSON。这次只输出 JSON 本体。",
                        provider, api_key, model, base_url)
            data = _parse_json(raw2)
            raw = raw2 or raw
        except Exception:
            data = None

    if data is None:
        # 退化成纯文本报告 —— 已经花掉的调用不能白花
        return {"ok": True, "data": None, "markdown": _fallback_markdown(raw, paper),
                "raw": raw, "error": "模型返回不是严格 JSON，已按纯文本报告保存"}

    _normalize(data)
    return {"ok": True, "data": data, "markdown": render_markdown(data, paper),
            "raw": raw, "error": ""}


def _as_list(v, limit=20):
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x or "").strip()][:limit]
    if isinstance(v, str) and v.strip():
        return [v.strip()]
    return []


def _as_text(v):
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v or "").strip()


def _clamp_score(v):
    try:
        n = int(round(float(v)))
    except Exception:
        return None
    return max(0, min(10, n))


def _normalize(d):
    """把模型可能漏掉的键、写歪的类型补齐（保证渲染不 KeyError）。"""
    for k in ("one_liner", "original_abstract", "zh_overview", "problem",
              "method_core", "evidence_judgement"):
        d[k] = _as_text(d.get(k))
    for k in ("method_steps", "key_points", "contributions", "author_claims",
              "strengths", "limitations_stated", "limitations_inferred",
              "related", "open_questions"):
        d[k] = _as_list(d.get(k))

    f = d.get("formulas")
    d["formulas"] = [{"latex": _as_text(x.get("latex")), "meaning": _as_text(x.get("meaning"))}
                     for x in (f if isinstance(f, list) else []) if isinstance(x, dict)]

    exp = d.get("experiments") if isinstance(d.get("experiments"), dict) else {}
    d["experiments"] = {k: _as_text(exp.get(k)) for k in
                        ("systems", "baselines", "conditions", "metrics",
                         "main_results", "ablations")}

    app = d.get("applicability") if isinstance(d.get("applicability"), dict) else {}
    d["applicability"] = {"when": _as_text(app.get("when")),
                          "when_not": _as_text(app.get("when_not"))}

    sc = d.get("scores") if isinstance(d.get("scores"), dict) else {}
    clean = {}
    for key, _label in SCORE_LABELS:
        slot = sc.get(key)
        if isinstance(slot, dict):
            clean[key] = {"score": _clamp_score(slot.get("score")),
                          "reason": _as_text(slot.get("reason"))}
        else:
            clean[key] = {"score": _clamp_score(slot), "reason": ""}
    d["scores"] = clean
    return d


# ═══════════════════════════════════════════════════
# 渲染
# ═══════════════════════════════════════════════════

def _bullets(items, empty="--"):
    items = [i for i in (items or []) if i]
    return "\n".join(f"- {i}" for i in items) if items else f"- {empty}"


def render_markdown(d, paper=None):
    """把分析结果排版成 Markdown（纯本地排版，重跑两次结果一致）。"""
    title = (paper or {}).get("title") or "论文"
    L = []
    L.append(f"# {title} — 深度分析")
    L.append("")

    if d.get("one_liner"):
        L += [f"> {d['one_liner']}", ""]

    L += ["## 摘要与核心要点", "", "### 原文摘要", "",
          d.get("original_abstract") or "--", "", "### 中文概述", "",
          d.get("zh_overview") or "--", "", "### 核心要点", "",
          _bullets(d.get("key_points")), ""]

    L += ["## 研究背景与问题", "", d.get("problem") or "--", ""]

    L += ["## 方法概述", "", "### 核心方法", "", d.get("method_core") or "--", ""]
    if d.get("method_steps"):
        L += ["### 流程", "", "\n".join(f"{i + 1}. {s}"
                                        for i, s in enumerate(d["method_steps"])), ""]
    if d.get("formulas"):
        L += ["### 关键公式", ""]
        for f in d["formulas"]:
            if not f.get("latex"):
                continue
            L.append(f"- $${f['latex']}$$" + (f" — {f['meaning']}" if f.get("meaning") else ""))
        L.append("")

    exp = d.get("experiments") or {}
    L += ["## 实验与证据", "", "| 项目 | 内容 |", "|---|---|"]
    for label, key in (("体系/材料", "systems"), ("对照与基线", "baselines"),
                       ("关键条件", "conditions"), ("评价指标", "metrics"),
                       ("主要结果", "main_results"), ("消融与稳健性", "ablations")):
        L.append(f"| {label} | {(exp.get(key) or '--').replace('|', '/')} |")
    L.append("")

    L += ["## 深度分析", "", "### 核心贡献与创新", "",
          _bullets(d.get("contributions")), "",
          "### 作者明确主张", "", _bullets(d.get("author_claims")), "",
          "### 基于证据的判断", "", d.get("evidence_judgement") or "--", "",
          "### 优势", "", _bullets(d.get("strengths")), "",
          "### 作者自述的局限", "", _bullets(d.get("limitations_stated")), "",
          "### 读出来的局限与风险", "", _bullets(d.get("limitations_inferred")), ""]

    app = d.get("applicability") or {}
    L += ["### 适用场景", "",
          f"- **可以用**：{app.get('when') or '--'}",
          f"- **不要照搬**：{app.get('when_not') or '--'}", ""]

    if d.get("related"):
        L += ["## 相关工作与技术路线", "", _bullets(d["related"]), ""]
    if d.get("open_questions"):
        L += ["## 仍未解决的问题", "", _bullets(d["open_questions"]), ""]

    L += ["## 综合评价", "", "| 维度 | 分数 | 理由 |", "|---|---:|---|"]
    for key, label in SCORE_LABELS:
        slot = (d.get("scores") or {}).get(key) or {}
        score = slot.get("score")
        score_txt = f"{score}/10" if score is not None else "--"
        reason = (slot.get("reason") or "--").replace("|", "/").replace("\n", " ")
        L.append(f"| {label} | {score_txt} | {reason} |")
    L.append("")

    avg = [s.get("score") for s in (d.get("scores") or {}).values()
           if isinstance(s, dict) and s.get("score") is not None]
    if avg:
        L += [f"**综合均分：{sum(avg) / len(avg):.1f}/10**", ""]

    L += ["---", "", "*本报告由 AI 辅助生成，结论需回到原文核对后使用。*"]
    return "\n".join(L)


def _fallback_markdown(raw, paper=None):
    title = (paper or {}).get("title") or "论文"
    return (f"# {title} — 深度分析（纯文本回退）\n\n"
            "> 模型这次没有按 JSON 结构输出，以下为原始分析文本，未经结构化排版。\n\n"
            f"{str(raw or '').strip() or '（模型未返回内容）'}\n\n---\n\n"
            "*本报告由 AI 辅助生成，结论需回到原文核对后使用。*")


def extract_scores(markdown_or_data):
    """从已存的分析里把五维分数抠出来（用于列表页排序/筛选）。"""
    if isinstance(markdown_or_data, dict):
        data = markdown_or_data
    else:
        try:
            obj = _parse_json(markdown_or_data)
        except Exception:
            obj = None
        data = obj
    if not isinstance(data, dict) or not isinstance(data.get("scores"), dict):
        return {}
    out = {}
    for key, _label in SCORE_LABELS:
        slot = data["scores"].get(key)
        if isinstance(slot, dict) and slot.get("score") is not None:
            out[key] = slot["score"]
    if out:
        out["avg"] = round(sum(out.values()) / len(out), 2)
    return out


if __name__ == "__main__":
    demo = {
        "title": "Demo Paper on CO2 Reduction",
        "journal": "JACS", "year": 2024, "doi": "10.1021/demo",
        "authors": '["A. Author", "B. Author"]',
        "full_text": "Abstract: we report a copper-based catalyst ... " * 40,
    }
    print(render_markdown(_normalize({}), demo)[:600])
