# -*- coding: utf-8 -*-
"""深度分析的本地降级（无 Key / 调用失败时用，离线目标 G2）。

移植自 C:\\Users\\wzxle\\Desktop\\WB比赛\\PaperLibrary\\app\\deep_analysis.py 的
build_context / _normalize 思路，改成「24 语义槽位 + 五维评分」全部由本地信号填。
契约名：deep.get / deep.run（无 Key 时 200 + 本地结果，fields 里注明未配置大模型）

原则：
  · **能算的算，算不出的写「未提及」**，不编造数值与结论；
  · 每条内容都来自 题录 / 摘要 / 中文总结 / 正文节选 / 图表计数 / 参考文献，
    或明确标注为「本地推断」；
  · 评分是可解释的启发式（1-5 分），理由写进「评分理由」槽位。
"""
from __future__ import annotations

import re

from paperlib.legacy import deep_prompt as P

NOT_MENTIONED = "未提及"
CELL = 320
MAX_STEPS = 4
MAX_POINTS = 4
MAX_REFS = 5

_SENT_SPLIT = re.compile(r"(?<=[.!?。！？])\s+|\n+")

_NOVEL = re.compile(r"\b(novel|for the first time|unprecedented|first report|unparalleled)\b", re.I)
_TOP_VENUE = re.compile(r"(nature|science|jacs|journal of the american chemical society|angew|"
                        r"advanced materials|acs nano|chemical reviews|cell|pnas|"
                        r"nature (?:chemistry|physics|materials|energy|catalysis|nanotechnology))", re.I)
_METHOD = re.compile(r"\b(we (?:used|measured|performed|employed|prepared)|dft|nn?mr|xps|xrd|"
                     r"electrochem\w*|spectroscop\w*|simulation)\b", re.I)
_CONDITION = re.compile(r"\b\d+\s*(?:°C|℃|K|h|min|V|mA|nm|M)\b|\bph\s*[=~]?\s*\d|"
                        r"\b(?:solvent|scan rate|current density|under (?:N2|Ar|air|H2))\b", re.I)
_LIMIT = re.compile(r"\b(limitation|drawback|remains? (?:a )?challenge|future work|"
                    r"however,?\s+(?:the|this|it))\b", re.I)
_APPLY = re.compile(r"\b(applicable|application|can be (?:used|applied)|toward|for the (?:detection|"
                    r"synthesis|reduction)|promising)\b", re.I)
_FINDING = re.compile(r"\b(demonstrates?|reveals?|indicates?|shows? that|confirms?|suggests?|"
                      r"we (?:find|found|show))\b", re.I)
_QUANTITY = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|V|mA|eV|nm|°C|℃|K|h|min|s|M|mg|g)\b")

# 每个「可从句子抽取」的槽位配一组学术线索（命中越多越优先，含数值额外加分）
CUES = {
    "研究背景": [r"\bremains? (?:a )?(?:challenge|unclear|elusive)\b",
                 r"\bgreat (?:interest|attention|importance)\b",
                 r"\battract(?:s|ed)? (?:much|considerable|increasing) attention\b",
                 r"\bdemand\b", r"\bessential\b", r"\burgent\b", r"\b背景", r"\b由于"],
    "核心方法": [r"\bwe (?:used|employed|measured|performed|prepared|synthesi[sz]ed)\b",
                 r"\bmethod\b", r"\btechnique\b", r"\bexperimental\b", r"\b方法", r"\b合成"],
    "方法步骤": [r"\bfirst\b", r"\bthen\b", r"\bfinally\b", r"\bsubsequently\b",
                 r"\bwas added\b", r"\bwere dissolved\b", r"\bstep\b", r"\b步骤"],
    "实验体系": [r"\bsystem\b", r"\bsubstrate\b", r"\bsample\b", r"\belectrode\b",
                 r"\bcatalyst\b", r"\bmaterial\b", r"\bcomposite\b", r"\b体系"],
    "对照与基线": [r"\bcontrol\b", r"\bbaseline\b", r"\bcompared (?:to|with)\b",
                   r"\breference (?:sample|electrode|catalyst)\b", r"\bblank\b", r"\b对照"],
    "实验条件": [r"\b\d+\s*(?:°C|℃|K|h|min)\b", r"\bsolvent\b", r"\bunder (?:N2|Ar|air|H2)\b",
                 r"\bmol\s*%", r"\b\d+\s*M\b", r"\bpotential\b", r"\b条件"],
    "评价指标": [r"\byield\b", r"\bee\b", r"\bdr\b", r"\boverpotential\b", r"\bfaradaic\b",
                 r"\bcapacity\b", r"\bconversion\b", r"\bselectivity\b", r"\btof\b",
                 r"\bsensitivity\b", r"\b指标"],
    "主要结果": [r"\b(demonstrates?|reveals?|indicates?|shows? that|confirms?)\b",
                 r"\b\d{1,3}(?:\.\d+)?\s*%", r"\bresults? (?:show|indicate)\b", r"\b结果"],
    "消融与稳健性": [r"\bablation\b", r"\bcontrol experiment", r"\breproduc\w*",
                     r"\bcycl(?:ing|e)\b", r"\bstability\b", r"\brobust\w*", r"\b重复"],
    "作者主张": [r"\bwe (?:report|show|demonstrate|find|present|describe)\b", r"\bherein\b",
                 r"\bthis work\b", r"\b本文"],
    "作者自述局限": [r"\blimitation", r"\bdrawback", r"\bremains? (?:a )?challenge",
                     r"\bremain(?:s|ed)? (?:limited|narrow|unclear|challenging)\b",
                     r"\blimited\b", r"\bfuture work\b", r"\b局限"],
    "适用条件": [r"\bapplicable\b", r"\bapplication\b", r"\bcan be (?:used|applied)\b",
                 r"\btoward\b", r"\bpromising\b", r"\b适用"],
    "开放问题": [r"\bfuture (?:work|studies|research)\b", r"\bfurther (?:studies|work)\b",
                 r"\bremains? (?:unclear|open|unknown)\b", r"\b未解决"],
}
_CUE_REGEX = {key: re.compile("|".join(patterns), re.I) for key, patterns in CUES.items()}


def _clean(text, limit=0):
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value[:limit] if limit else value


def _sentences(text, minimum=12):
    return [_clean(part) for part in _SENT_SPLIT.split(str(text or ""))
            if len(_clean(part)) >= minimum]


def _clamp(value, low=1, high=5):
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return low
    return max(low, min(high, number))


def abstract_of(paper):
    """原文摘要：合并后的卡片字段叫 summary（见 data-model.md §卡片），老数据可能叫 abstract。"""
    for key in ("abstract", "summary"):
        text = _clean(paper.get(key))
        if text:
            return text
    return ""


def paper_signals(paper, figures=None, full_text=""):
    """把卡片/图表/正文压成评分信号（本地降级与测试共用）。"""
    abstract = abstract_of(paper)
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


def _score_reasons(signals, scores):
    """把每个分数的本地依据写成一句话（原 scores.*.reason 的本地版）。"""
    reasons = {
        "创新性": ("摘要/正文含新颖性表述" if signals["novelWords"] else "摘要未见新颖性表述")
                  + ("；期刊层级高" if signals["topVenue"] else "")
                  + ("；图表充足" if signals["figures"] >= 6 else ""),
        "严谨性": ("图表 %d 张" % signals["figures"])
                  + ("；正文 %d 字" % signals["fullTextChars"] if signals["fullTextChars"] else "；无正文")
                  + ("；参考文献 %d 条" % signals["refs"] if signals["refs"] else "")
                  + ("；有实验条件描述" if signals["hasConditions"] else "；缺实验条件描述"),
        "影响力": ("期刊层级高" if signals["topVenue"] else "期刊层级未识别")
                  + ("；用户评分 %d" % signals["rating"] if signals["rating"] else "")
                  + ("；%s 年发表" % signals["year"] if signals.get("year") else ""),
        "可复现性": ("有正文" if signals["fullTextChars"] else "无正文")
                    + ("；有方法描述" if signals["hasMethodWords"] else "；缺方法描述")
                    + ("；有实验条件" if signals["hasConditions"] else "；缺实验条件")
                    + ("；有补充材料" if signals["attachments"] else "；无补充材料"),
        "可读性": ("摘要 %d 字" % signals["abstractChars"]) if signals["abstractChars"] else "无摘要",
    }
    return {key: "%s %d：%s" % (key, scores[key], reasons.get(key, "本地信号不足"))
            for key in P.SCORE_KEYS}


_LIMIT_SLOTS = ("作者自述局限", "开放问题")


def _pick(sentences, key, fallback=NOT_MENTIONED, limit=CELL):
    regex = _CUE_REGEX.get(key)
    best, best_score = "", 0
    for sentence in sentences:
        if not regex or not regex.search(sentence):
            continue
        # 局限/展望句不该被当成背景、体系、条件：除非本槽位就是抽局限
        if key not in _LIMIT_SLOTS and _LIMIT.search(sentence):
            continue
        score = len(regex.findall(sentence)) + (2 if _QUANTITY.search(sentence) else 0)
        if score > best_score:
            best, best_score = sentence, score
    return (best[:limit].strip() or fallback)


def _pick_list(sentences, key, limit=MAX_POINTS):
    regex = _CUE_REGEX.get(key)
    picked = []
    for sentence in sentences:
        if regex and regex.search(sentence) and sentence[:CELL] not in picked:
            picked.append(sentence[:CELL])
        if len(picked) >= limit:
            break
    return "；".join(picked) if picked else NOT_MENTIONED


def analyze_local(paper, figures=None, full_text=""):
    """本地降级分析：24 语义槽位 + 评分理由 + 分析模式，全部给值（没有就「未提及」）。"""
    signals = paper_signals(paper, figures, full_text)
    abstract = abstract_of(paper)
    text = _clean(full_text)
    sentences = _sentences(abstract + " " + text[:12000])
    scores = local_scores(signals)
    title = _clean(paper.get("title") or paper.get("id") or "未命名文献", 120)
    venue_year = "，".join(str(x) for x in (paper.get("year"),
                                            paper.get("venue") or paper.get("journal")) if x)

    abstract_sentences = _sentences(abstract)
    # 摘要可能整段都不到 minimum 长度（用户随手写「待补充」），此时回退到原文前 120 字；
    # 否则 [0] 会 IndexError，把「无 Key 本地降级必须 200」的契约打成 500。
    first = abstract_sentences[0] if abstract_sentences else (_clean(abstract)[:120] if abstract else "")
    one_line = "%s%s —— %s" % (title, ("（%s）" % venue_year) if venue_year else "",
                               first[:120] if first else "本地未抽取到摘要，仅依据题录")

    refs = paper.get("refs") or []
    ref_lines = []
    for item in (refs if isinstance(refs, list) else [])[:MAX_REFS]:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            ref_lines.append(str(item["text"]).strip()[:200])
    related = "；".join("%d. %s" % (index + 1, line) for index, line in enumerate(ref_lines)) \
        if ref_lines else NOT_MENTIONED

    advantages = []
    if signals["topVenue"]:
        advantages.append("期刊层级高")
    if signals["figures"] >= 3:
        advantages.append("图表充足（%d 张）" % signals["figures"])
    if signals["fullTextChars"] >= 8000:
        advantages.append("正文完整（%d 字）" % signals["fullTextChars"])
    if signals["hasConditions"]:
        advantages.append("实验条件可检索")
    if signals["attachments"]:
        advantages.append("有补充材料 %d 个" % signals["attachments"])

    inferred_limits = []
    if not signals["fullTextChars"]:
        inferred_limits.append("本地没有正文，实验细节无法核实")
    if signals["figures"] < 2:
        inferred_limits.append("图表少于 2 张，证据链偏弱")
    if signals["refs"] and signals["refs"] < 10:
        inferred_limits.append("参考文献仅 %d 条" % signals["refs"])
    if not signals["hasConditions"]:
        inferred_limits.append("摘要未给出可复现的实验条件")

    evidence = ("图表 %d 张；正文 %d 字；参考文献 %d 条；补充材料 %d 个；实验条件描述%s"
                % (signals["figures"], signals["fullTextChars"], signals["refs"],
                   signals["attachments"], "有" if signals["hasConditions"] else "无"))

    fields = {
        "一句话概括": one_line[:CELL],
        "原文摘要": abstract[:1200] or "未提及（本地没有抽取到摘要）",
        "中文概述": _clean(paper.get("summaryZh"))[:1200] or "未提及（本地未生成中文概述）",
        "研究背景": _pick(sentences, "研究背景"),
        "核心方法": _pick(sentences, "核心方法"),
        "方法步骤": _pick_list(sentences, "方法步骤", MAX_STEPS),
        "关键公式": "未提及（本地模式不解析公式；原文公式请在阅读器查看）",
        "实验体系": _pick(sentences, "实验体系"),
        "对照与基线": _pick(sentences, "对照与基线"),
        "实验条件": _pick(sentences, "实验条件"),
        "评价指标": _pick(sentences, "评价指标"),
        "主要结果": _pick(sentences, "主要结果"),
        "消融与稳健性": _pick(sentences, "消融与稳健性"),
        "核心要点": _pick_list(sentences, "主要结果", MAX_POINTS),
        "主要贡献": _pick_list(sentences, "作者主张", 3),
        "作者主张": _pick_list(sentences, "作者主张", MAX_POINTS),
        "证据判断": evidence,
        "优势": "；".join(advantages) if advantages else "未提及（本地信号不足）",
        "作者自述局限": _pick(sentences, "作者自述局限"),
        "推断局限": "；".join(inferred_limits) if inferred_limits else NOT_MENTIONED,
        "适用条件": _pick(sentences, "适用条件"),
        "不适用情形": "未提及（本地模式未抽取适用边界）",
        "相关工作": related,
        "开放问题": _pick(sentences, "开放问题"),
        "评分理由": "；".join(_score_reasons(signals, scores).values()),
        "分析模式": P.LOCAL_NOTE,
    }
    return {
        "fields": fields,
        "scores": scores,
        "oneLine": ("本地分析：%s —— 五维均分 %.1f/5；%s"
                    % (title[:80], sum(scores.values()) / max(1, len(scores)), P.LOCAL_NOTE)),
    }
