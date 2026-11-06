# -*- coding: utf-8 -*-
"""论文深度分析：22 个语义槽位 + 五维评分（无 Key 时本地降级）。

移植自 C:\\Users\\wzxle\\Desktop\\WB比赛\\PaperLibrary\\app\\deep_analysis.py
契约名：deep.get / deep.run（GET|POST /api/papers/:id/deep-analysis）

保留全部能力（G3：22 字段 + 五维评分），同时遵守契约 §7 的 6 个顶层键：
  { paperId, updatedAt, model, fields, scores, oneLine }
  · fields：24 个语义槽位（原 JSON 的 19 个顶层键，experiments 展开 6 个、applicability 展开 2 个）
    + 「评分理由」（原 scores.*.reason 的落点）+ 「分析模式」说明；
  · scores：契约五维（创新性 / 严谨性 / 影响力 / 可复现性 / 可读性），1-5 整数；
  · 槽位表与系统提示词在 paperlib/legacy/deep_prompt.py（原 SYSTEM 提示词原样搬过来）；
  · 本地降级在 paperlib/legacy/deep_local.py（24 槽位全给值，取不到的写「未提及」）；
  · 模型调用统一走 services.llm（T4 适配器）；无 Key 或调用/解析失败一律本地降级返回 200
    （契约 §8 + 离线目标 G2），不抛错、不编造。
"""
from __future__ import annotations

import json
import re

from paperlib.legacy import compare_engine
from paperlib.legacy import deep_local, deep_prompt
from paperlib.util import now_iso

FIELDS = deep_prompt.FIELDS
SCORE_KEYS = deep_prompt.SCORE_KEYS
LOCAL_MODEL = deep_prompt.LOCAL_MODEL
LOCAL_NOTE = deep_prompt.LOCAL_NOTE
MAX_TEXT = deep_prompt.MAX_TEXT

paper_signals = deep_local.paper_signals
local_scores = deep_local.local_scores
analyze_local = deep_local.analyze_local


def _lookup(obj, path):
    """按 'experiments.systems' 这样的路径取值；取不到返回 None。"""
    current = obj
    for part in str(path or "").split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def _as_text(value, limit=0):
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        text = "；".join(str(item).strip() for item in value if str(item or "").strip())
    elif isinstance(value, dict):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] if limit else text


def _as_formula(value):
    if not isinstance(value, list):
        return _as_text(value)
    lines = []
    for item in value:
        if isinstance(item, dict):
            latex = _as_text(item.get("latex"), 200)
            meaning = _as_text(item.get("meaning"), 200)
            if latex:
                lines.append("$$" + latex + "$$" + (" — " + meaning if meaning else ""))
        elif str(item or "").strip():
            lines.append(_as_text(item, 200))
    return "；".join(lines)


def _map_fields(parsed):
    """把模型 JSON（原英文键结构）映射成 24 个中文字段 + 评分理由 + 分析模式。"""
    fields = {}
    for name, path, kind in deep_prompt.FIELD_SLOTS:
        value = _lookup(parsed, path)
        if value is None:
            value = parsed.get(name)  # 容错：模型忽略嵌套、直接用中文键平铺
        if kind == "formula":
            text = _as_formula(value)
        elif kind == "list":
            text = _as_text(value)
        else:
            text = _as_text(value)
        fields[name] = text.strip() if text and text.strip() else "未提及"
    return fields


def _scale(raw):
    """把模型给的分数压到 1-5：原提示词是 0-10 时按 /2 折算，越界夹紧。"""
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    if number > 5:
        number = number / 2.0
    return max(1, min(5, int(number + 0.5)))  # 四舍五入（不用 round()，避免 4.5→4 的银行家舍入）


def _map_scores(parsed):
    """返回 (scores, 评分理由文本)；模型用英文键（novelty…）也能认。"""
    raw_scores = parsed.get("scores") if isinstance(parsed.get("scores"), dict) else {}
    scores, reasons = {}, []
    for key in SCORE_KEYS:
        slot = None
        for alias in deep_prompt.SCORE_ALIASES.get(key, [key]):
            if alias in raw_scores:
                slot = raw_scores[alias]
                break
        score, reason = None, ""
        if isinstance(slot, dict):
            score = _scale(slot.get("score"))
            reason = _as_text(slot.get("reason"), 300)
        elif slot is not None:
            score = _scale(slot)
        if score is None:
            score = 3
        scores[key] = score
        reasons.append("%s %d%s" % (key, score, ("：" + reason) if reason else ""))
    return scores, "；".join(reasons)


def _llm_prompt(paper, figures, full_text):
    parts = ["# 题录"]
    for label, key in (("标题", "title"), ("作者", "authors"), ("期刊", "venue"),
                       ("年份", "year"), ("DOI", "doi"), ("标签", "tags")):
        value = paper.get(key)
        if isinstance(value, (list, tuple)):
            value = "、".join(str(v) for v in value if str(v).strip())
        if value not in (None, ""):
            parts.append("- %s：%s" % (label, value))

    if figures:
        parts.append("\n# 图表清单（按序，含图注）")
        for figure in figures[:30]:
            caption = re.sub(r"\s+", " ", str(figure.get("caption") or "")).strip()
            line = "- [Figure %s]" % (figure.get("fig_index") or figure.get("figIndex") or "")
            figure_type = str(figure.get("figure_type") or figure.get("figureType") or "")
            if figure_type:
                line += " (%s)" % figure_type
            if caption:
                line += " 图注: " + caption[:220]
            parts.append(line)

    text = str(full_text or "").strip()
    if text:
        if len(text) > MAX_TEXT:
            head = text[: int(MAX_TEXT * 0.62)]
            tail = text[-int(MAX_TEXT * 0.30):]
            text = head + "\n\n……（中段省略）……\n\n" + tail
        parts.append("\n# 正文\n" + text)
    else:
        abstract = str(paper.get("abstract") or paper.get("summary") or "").strip()
        if abstract:
            parts.append("\n# 摘要\n" + abstract[:6000])
        else:
            parts.append("\n# 正文\n（本地没有抽取到正文与摘要，请仅基于题录与图表清单做保守分析）")

    parts.append("\n请深度分析这篇论文，严格按系统消息里的 JSON 结构输出。")
    return "\n".join(parts)


def _parse_json_object(raw):
    text = str(raw or "").strip()
    tick = chr(96) * 3
    if text.startswith(tick):
        text = re.sub(r"^" + re.escape(tick) + r"[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*" + re.escape(tick) + r"$", "", text).strip()
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


def _call_llm(services, messages):
    llm = getattr(services, "llm", None)
    try:
        response = llm.chat(messages, {"maxTokens": 2400, "temperature": 0.2})
    except (NotImplementedError, Exception):  # noqa: BLE001 - 在线失败必须降级
        return ""
    text = response if isinstance(response, str) else ""
    if isinstance(response, dict):
        for key in ("content", "text", "message", "answer"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                text = value
                break
    return text


def analyze_with_llm(services, paper, figures, full_text):
    """返回 {fields, scores, oneLine, model}；无可用模型或解析失败返回 None。"""
    llm = getattr(services, "llm", None)
    if not compare_engine.llm_ready(llm):
        return None

    messages = [
        {"role": "system", "content": deep_prompt.SYSTEM_PROMPT},
        {"role": "user", "content": _llm_prompt(paper, figures, full_text)},
    ]
    texts = [_call_llm(services, messages)]
    parsed = _parse_json_object(texts[0])
    if parsed is None:
        # 原实现的补正重试：只要求它把 JSON 修好，最多一次
        retry = list(messages)
        retry[-1] = {"role": "user",
                     "content": messages[-1]["content"] +
                     "\n\n注意：上一次输出不是合法 JSON。这次只输出 JSON 本体，不要围栏与解释。"}
        texts.append(_call_llm(services, retry))
        parsed = _parse_json_object(texts[-1])
    if parsed is None:
        return None

    fields = _map_fields(parsed)
    scores, reasons = _map_scores(parsed)
    fields["评分理由"] = reasons
    try:
        model = str((llm.settings or {}).get("model") or "llm")
    except Exception:  # noqa: BLE001
        model = "llm"
    fields["分析模式"] = "大模型分析（model=%s）" % model

    one_line = (_as_text(parsed.get("one_liner"), 300) or fields.get("一句话概括")
                or ("模型分析：" + _as_text(paper.get("title"), 80)))
    return {"fields": fields, "scores": scores, "oneLine": one_line, "model": model}


def run(services, paper, figures=None, full_text=""):
    """契约 §7/§8 的 analysis 对象（6 个顶层键，一个不多一个不少）。"""
    result = analyze_with_llm(services, paper, figures, full_text)
    if result:
        fields, scores, one_line, model = (result["fields"], result["scores"],
                                          result["oneLine"], result["model"])
    else:
        local = analyze_local(paper, figures, full_text)
        fields = local["fields"]
        scores = local["scores"]
        one_line = local["oneLine"]
        model = LOCAL_MODEL
    return {
        "paperId": paper.get("id"),
        "updatedAt": now_iso(),
        "model": model,
        "fields": fields,
        "scores": scores,
        "oneLine": one_line,
    }
