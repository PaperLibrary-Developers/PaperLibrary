# -*- coding: utf-8 -*-
"""论文深度分析：字段化结论 + 五维评分（无 Key 时本地降级）。

移植自 C:\\Users\\wzxle\\Desktop\\WB比赛\\PaperLibrary\\app\\deep_analysis.py
契约名：deep.get / deep.run（GET|POST /api/papers/:id/deep-analysis）

与旧版的差异：
  · 去掉 openai / anthropic 直连与 22 字段长报告渲染：合并后只保留契约 §7 的形状
    （paperId / updatedAt / model / fields / scores / oneLine），模型调用统一走 services.llm；
  · **无 Key 或调用失败一律本地降级并返回 200**（契约 §8 + 离线目标 G2），
    fields 里显式写明「未配置大模型，仅本地分析」，scores 由本地信号启发式给出（1-5 分）；
  · 落库由 api 层走 store.repo.save_analysis(paper_id, "deep", payload)。

本地评分信号（可解释、零网络）：
  创新性   ← 摘要里 novel/first/unprecedented + 高影响力期刊关键词
  严谨性   ← 图表数、正文体量、参考文献数、实验段关键词
  影响力   ← 期刊层级 + 星级/评分 + 年份新近度
  可复现性 ← 有原文、有实验条件（温度/时间/溶剂/电位）、有补充材料
  可读性   ← 摘要是否成段、有无笔记/中文总结、标题长度是否合理
"""
from __future__ import annotations

import json
import re

from paperlib.legacy import compare_engine
from paperlib.util import now_iso

FIELDS = ["研究问题", "方法", "核心结论", "创新点", "证据强度", "局限", "适用场景", "对我的价值"]
SCORE_KEYS = ["创新性", "严谨性", "影响力", "可复现性", "可读性"]
LOCAL_MODEL = "local-heuristic"
LOCAL_NOTE = "未配置大模型，仅本地分析（依据题录、摘要、正文体量与图表计数，不编造数据）"
MAX_TEXT = 26000

_NOVEL = re.compile(r"\b(novel|for the first time|unprecedented|first report|unparalleled)\b", re.I)
_TOP_VENUE = re.compile(r"(nature|science|jacs|journal of the american chemical society|angew|"
                        r"advanced materials|acs nano|chemical reviews|cell|pnas|"
                        r"nature (?:chemistry|physics|materials|energy|catalysis|nanotechnology))", re.I)
_METHOD = re.compile(r"\b(we (?:used|measured|performed|employed|prepared)|dft|nn?mr|xps|xrd|"
                     r"electrochem\w*|spectroscop\w*|simulation)\b", re.I)
_CONDITION = re.compile(r"\b\d+\s*(?:°C|℃|K|h|min|V|mA|nm|M)\b|\bph\s*[=~]?\s*\d", re.I)
_LIMIT = re.compile(r"\b(limitation|drawback|remains? (?:a )?challenge|future work|"
                    r"however,?\s+(?:the|this|it))\b", re.I)
_APPLY = re.compile(r"\b(applicable|application|can be (?:used|applied)|toward|for the (?:detection|"
                    r"synthesis|reduction)|promising)\b", re.I)
_FINDING = re.compile(r"\b(demonstrates?|reveals?|indicates?|shows? that|confirms?|suggests?|"
                      r"we (?:find|found|show))\b", re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")


def _clean(text, limit=0):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit] if limit else value


def _sentences(text, minimum=12):
    return [_clean(s) for s in _SENT_SPLIT.split(str(text or "")) if len(_clean(s)) >= minimum]


def _pick(sentences, regex, fallback=""):
    for sentence in sentences:
        if regex.search(sentence):
            return sentence[:320]
    return fallback


def _clamp(value, low=1, high=5):
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def paper_signals(paper, figures=None, full_text=""):
    """把卡片/图表/正文压成评分信号（本地降级与测试都用它）。"""
    abstract = _clean(paper.get("abstract"))
    text = _clean(full_text)
    venue = str(paper.get("venue") or paper.get("journal") or "")
    refs = paper.get("refs") or []
    attachments = paper.get("attachments") or []
    return {
        "abstractChars": len(abstract),
        "fullTextChars": len(text),
        "figures": len(figures or []),
        "refs": len(refs) if isinstance(refs, list) else 0,
        "attachments": len(attachments) if isinstance(attachments, list) else 0,
        "novelWords": len(_NOVEL.findall(abstract + " " + text[:4000])),
        "topVenue": bool(_TOP_VENUE.search(venue)),
        "hasMethodWords": bool(_METHOD.search(abstract + " " + text[:8000])),
        "hasConditions": bool(_CONDITION.search(abstract + " " + text[:8000])),
        "limits": _pick(_sentences(abstract + " " + text[:6000]), _LIMIT),
        "rating": int(paper.get("rating") or 0),
        "starred": bool(paper.get("starred")),
        "notesChars": len(_clean(paper.get("notes"))),
        "summaryZhChars": len(_clean(paper.get("summaryZh"))),
        "year": paper.get("year"),
    }


def local_scores(signals):
    """五维本地启发式评分（1-5 整数，可解释）。"""
    novelty = 2
    if signals["novelWords"]:
        novelty += 1
    if signals["topVenue"]:
        novelty += 1
    if signals["figures"] >= 6:
        novelty += 1

    rigor = 1
    if signals["figures"] >= 3:
        rigor += 1
    if signals["fullTextChars"] >= 8000:
        rigor += 1
    if signals["refs"] >= 15:
        rigor += 1
    if signals["hasConditions"]:
        rigor += 1

    impact = 2
    if signals["topVenue"]:
        impact += 1
    if signals["rating"] >= 4 or signals["starred"]:
        impact += 1
    try:
        year = int(signals.get("year") or 0)
    except (TypeError, ValueError):
        year = 0
    if year >= 2021:
        impact += 1

    reproducible = 1
    if signals["fullTextChars"] >= 3000:
        reproducible += 1
    if signals["hasMethodWords"]:
        reproducible += 1
    if signals["hasConditions"]:
        reproducible += 1
    if signals["attachments"] >= 1:
        reproducible += 1

    readable = 2
    if signals["abstractChars"] >= 300:
        readable += 1
    if signals["notesChars"] or signals["summaryZhChars"]:
        readable += 1
    if signals["figures"] >= 2:
        readable += 1

    return {
        "创新性": _clamp(novelty),
        "严谨性": _clamp(rigor),
        "影响力": _clamp(impact),
        "可复现性": _clamp(reproducible),
        "可读性": _clamp(readable),
    }


def analyze_local(paper, figures=None, full_text=""):
    """本地降级分析：字段尽量取原文句子，缺就如实写「未提及」。"""
    abstract = _clean(paper.get("abstract"))
    sentences = _sentences(abstract + " " + _clean(full_text)[:12000])
    signals = paper_signals(paper, figures, full_text)

    question = sentences[0] if sentences else _clean(paper.get("title"), 200)
    method = _pick(sentences, _METHOD, "未提及（摘要里没有方法描述）")
    finding = _pick(sentences, _FINDING, "未提及（摘要里没有结论句）")
    novelty = _pick(sentences, _NOVEL, "未提及（摘要未强调新颖性）")
    limits = signals["limits"] or "未提及（作者未在摘要中陈述局限）"
    apply = _pick(sentences, _APPLY, "未提及")

    evidence = ("图表 %d 张；正文 %d 字；参考文献 %d 条；补充材料 %d 个；条件描述%s"
                % (signals["figures"], signals["fullTextChars"], signals["refs"],
                   signals["attachments"], "有" if signals["hasConditions"] else "无"))

    tags = paper.get("tags") or []
    keywords = paper.get("keywords") or []
    interest = "；".join(str(t) for t in (list(keywords) + list(tags))[:6]) or "未标注关键词/标签"
    value = "建议按「%s」方向归档；可用于与同类工作做横向对比。" % (
        _clean(paper.get("venue") or paper.get("journal") or "未指定期刊", 60))

    scores = local_scores(signals)
    fields = {
        "研究问题": question or "未提及",
        "方法": method,
        "核心结论": finding,
        "创新点": novelty,
        "证据强度": evidence,
        "局限": limits,
        "适用场景": apply,
        "对我的价值": value + ("（关注点：%s）" % interest if interest else ""),
        "分析模式": LOCAL_NOTE,
    }
    one_line = "本地分析：%s（%s）—— 五维均分 %.1f/5" % (
        _clean(paper.get("title") or paper.get("id") or "未命名文献", 80),
        (str(paper.get("year")) + "，" if paper.get("year") else "") +
        (str(paper.get("venue") or paper.get("journal") or "未指定来源")),
        sum(scores.values()) / max(1, len(scores)),
    )
    return {"fields": fields, "scores": scores, "oneLine": one_line}


def _llm_prompt(paper, figures, full_text):
    parts = ["# 题录"]
    for label, key in (("标题", "title"), ("作者", "authors"), ("期刊", "venue"),
                       ("年份", "year"), ("DOI", "doi")):
        value = paper.get(key)
        if isinstance(value, list):
            value = "、".join(str(v) for v in value)
        if value:
            parts.append("- %s：%s" % (label, value))
    if figures:
        parts.append("\n# 图表清单")
        for fig in figures[:20]:
            caption = _clean(fig.get("caption"), 200)
            if caption:
                parts.append("- [Figure %s] %s" % (fig.get("fig_index"), caption))
    body = _clean(full_text)[:MAX_TEXT]
    if body:
        parts.append("\n# 正文节选\n" + body)
    else:
        abstract = _clean(paper.get("abstract"))
        if abstract:
            parts.append("\n# 摘要\n" + abstract)
    parts.append("\n# 任务\n只依据上面的材料，输出 JSON："
                 '{"fields": {"研究问题": "", "方法": "", "核心结论": "", "创新点": "",'
                 ' "证据强度": "", "局限": "", "适用场景": "", "对我的价值": ""},'
                 ' "scores": {"创新性": 1-5, "严谨性": 1-5, "影响力": 1-5,'
                 ' "可复现性": 1-5, "可读性": 1-5}, "oneLine": ""}。'
                 "数值必须来自原文，没有就写「未提及」，不要编造。")
    return "\n".join(parts)


def _parse_json_object(raw):
    text = str(raw or "").strip()
    tick = chr(96) * 3
    if text.startswith(tick):
        text = text[len(tick):]
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
        if text.rstrip().endswith(tick):
            text = text.rstrip()[:-len(tick)]
        text = text.strip()
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except ValueError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start:end + 1])
            return obj if isinstance(obj, dict) else None
        except ValueError:
            return None
    return None


def analyze_with_llm(services, paper, figures, full_text):
    """返回 (fields, scores, oneLine, model)；没有可用模型/失败时返回 None。"""
    llm = getattr(services, "llm", None)
    if not compare_engine.llm_ready(llm):
        return None
    messages = [
        {"role": "system", "content": "你是严谨的化学/材料领域审稿人，只输出合法 JSON。"},
        {"role": "user", "content": _llm_prompt(paper, figures, full_text)},
    ]
    try:
        response = llm.chat(messages, {"maxTokens": 1600, "temperature": 0.2, "json": True})
    except (NotImplementedError, Exception):  # noqa: BLE001 - 在线失败必须降级
        return None
    text = response if isinstance(response, str) else ""
    if isinstance(response, dict):
        for key in ("content", "text", "message", "answer"):
            if isinstance(response.get(key), str):
                text = response[key]
                break
    parsed = _parse_json_object(text)
    if not parsed:
        return None

    raw_fields = parsed.get("fields") if isinstance(parsed.get("fields"), dict) else {}
    if not raw_fields:
        # 模型有时忽略嵌套直接平铺：仍按契约字段名认领
        raw_fields = {name: parsed.get(name) for name in FIELDS if name in parsed}
    fields = {}
    for name in FIELDS:
        value = raw_fields.get(name)
        if isinstance(value, (list, tuple)):
            value = "；".join(str(v) for v in value)
        fields[name] = _clean(value, 800) or "未提及"

    raw_scores = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}
    scores = {key: _clamp(raw_scores.get(key, 3)) for key in SCORE_KEYS}
    one_line = _clean(parsed.get("oneLine") or parsed.get("one_line"), 300)
    if not one_line:
        one_line = "模型分析：%s" % _clean(paper.get("title"), 80)
    model = ""
    try:
        model = str((llm.settings or {}).get("model") or "")
    except Exception:  # noqa: BLE001
        model = ""
    return {"fields": fields, "scores": scores, "oneLine": one_line, "model": model or "llm"}


def run(services, paper, figures=None, full_text=""):
    """契约 §7/§8 的 analysis 对象（6 个键，一个不多一个不少）。"""
    paper_id = paper.get("id")
    result = analyze_with_llm(services, paper, figures, full_text)
    if result:
        fields, scores, one_line = result["fields"], result["scores"], result["oneLine"]
        model = result.get("model") or "llm"
    else:
        local = analyze_local(paper, figures, full_text)
        fields, scores, one_line = local["fields"], local["scores"], local["oneLine"]
        model = LOCAL_MODEL
    return {
        "paperId": paper_id,
        "updatedAt": now_iso(),
        "model": model,
        "fields": fields,
        "scores": scores,
        "oneLine": one_line,
    }
