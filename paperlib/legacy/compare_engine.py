# -*- coding: utf-8 -*-
"""多篇同类文献横向对比（模板 + Map-Reduce + 离线降级）。

移植自 C:\\Users\\wzxle\\Desktop\\WB比赛\\PaperLibrary\\app\\compare_engine.py
契约名：compare.templates / compare.run（GET /api/compare/templates、POST /api/compare）

与旧版的差异：
  · 去掉 ai_engine / openai / anthropic 直连：模型调用统一走 services.llm（T4 的适配器），
    拿不到 Key 或调用失败时**本地降级**（契约 §6 + 离线目标 G2：必须 200，不编造）；
  · 去掉落库缓存（旧版把 Map 结果写 compare_cache）：合并后 analysis 表由 store.repo 管理，
    对比结果按需重算，避免与 T2 的缓存策略冲突；
  · 输出形状改成契约 §6 的 { template, oneLine, sections:[{title, rows:[{paperId, value}]}], usage }。

离线降级算法（本地启发式，零网络）：
  1. 每个维度给一组学术线索正则（产率/条件/表征/机理/局限……）；
  2. 在「题录 + 摘要 + 正文节选（限 20k 字）」里按句切分，取第一个命中且长度合适的句子；
  3. 句子里若含定量数值（% / V / nm / °C …）优先；
  4. 一个维度都没命中就如实写「未提及（本地模式）」——绝不编造数值。
"""
from __future__ import annotations

import json
import re

MAX_LOCAL_TEXT = 20000
MAX_CELL = 300
NEED_LLM_HINT = "未配置大模型，仅本地启发式"

TEMPLATES = [
    {
        "id": "synthesis",
        "name": "合成路线对比",
        "desc": "原料、条件、产率与表征 —— 适合有机合成 / 催化 / 手性材料这类文章",
        "fields": [
            {"key": "substrate", "label": "原料", "hint": "底物/原料类别与目标产物"},
            {"key": "reaction", "label": "反应类型", "hint": "偶联 / 氢化 / 缩聚 / 光催化 …"},
            {"key": "conditions", "label": "条件", "hint": "溶剂、温度、时间、气氛、负载量"},
            {"key": "yield", "label": "产率", "hint": "照抄原文数值与单位"},
            {"key": "selectivity", "label": "选择性", "hint": "ee / dr / 区域选择性数值"},
            {"key": "characterization", "label": "表征", "hint": "NMR / XRD / XPS / SEM / CD 等"},
            {"key": "mechanism", "label": "机理", "hint": "机理假设与证据（对照实验/DFT）"},
            {"key": "limits", "label": "局限", "hint": "作者承认的限制或明显短板"},
        ],
    },
    {
        "id": "electro",
        "name": "电化学性能对比",
        "desc": "电极、电解液、活性与稳定性 —— 适合电催化 / 电池 / 光电化学这类文章",
        "fields": [
            {"key": "system", "label": "电极体系", "hint": "电极材料、工作电极、负载量"},
            {"key": "electrolyte", "label": "电解液", "hint": "电解液浓度与 pH"},
            {"key": "conditions", "label": "测试条件", "hint": "电位窗口、扫速、电流密度"},
            {"key": "performance", "label": "活性指标", "hint": "过电位、法拉第效率、比容量"},
            {"key": "stability", "label": "稳定性", "hint": "循环次数、保持率"},
            {"key": "mechanism", "label": "机理", "hint": "活性位点、决速步、原位表征"},
            {"key": "characterization", "label": "表征", "hint": "XRD / XPS / TEM / 原位 Raman"},
            {"key": "limits", "label": "局限", "hint": "作者承认的限制或比较基准问题"},
        ],
    },
    {
        "id": "material",
        "name": "材料与表征对比",
        "desc": "合成路线、结构与形貌、构效关系 —— 适合 COF/MOF、纳米材料这类文章",
        "fields": [
            {"key": "system", "label": "材料体系", "hint": "材料名称、组成、目标应用"},
            {"key": "synthesis", "label": "合成方法", "hint": "方法名、前驱体、温度/时间"},
            {"key": "structure", "label": "结构与形貌", "hint": "晶型、孔径、维度、比表面积"},
            {"key": "property", "label": "关键性能", "hint": "带数值与单位的核心指标"},
            {"key": "application", "label": "应用", "hint": "吸附 / 催化 / 储能 / 手性识别"},
            {"key": "characterization", "label": "表征", "hint": "实际用到的仪器与数据"},
            {"key": "mechanism", "label": "机理", "hint": "构效关系与证据"},
            {"key": "limits", "label": "局限", "hint": "作者承认的限制"},
        ],
    },
    {
        "id": "general",
        "name": "通用对比",
        "desc": "研究问题、方法、结论、贡献 —— 不限定领域时的兜底模板",
        "fields": [
            {"key": "question", "label": "研究问题", "hint": "这篇要回答什么"},
            {"key": "method", "label": "方法", "hint": "技术路线、样本或数据集"},
            {"key": "findings", "label": "主要结论", "hint": "带关键数值的结论"},
            {"key": "contribution", "label": "贡献", "hint": "相对已有工作的增量"},
            {"key": "limits", "label": "局限", "hint": "作者承认的限制"},
        ],
    },
]

# 每个维度的学术线索（离线抽取用；命中越多越优先，含数值额外加分）
CUES = {
    "substrate": [r"\bsubstrate", r"\bsubstrates\b", r"\bstarting material", r"\b底物", r"\b原料"],
    "reaction": [r"\bcoupling\b", r"\bhydro(?:genation|formylation|boration)\b", r"\bcycli[sz]ation\b",
                 r"\boxidation\b", r"\breduction\b", r"\bpolymeri[sz]ation\b", r"\bphotocatal", r"\b反应"],
    "conditions": [r"\b\d+\s*(?:°C|℃|K)\b", r"\b\d+\s*(?:h|min|hours)\b", r"\bsolvent\b",
                   r"\bunder (?:N2|Ar|air|H2)\b", r"\bmol\s*%", r"\b\d+\s*M\b", r"\b条件"],
    "yield": [r"\byield(?:s|ed)?\b", r"\b\d{1,3}(?:\.\d+)?\s*%\s*(?:yield|in )?", r"\b产率"],
    "selectivity": [r"\bee\b", r"\bdr\b", r"\benantio(?:selectiv|meric)\w*", r"\bdiastereo\w*",
                    r"\bselectivity\b", r"\b选择性"],
    "characterization": [r"\bnmr\b", r"\bxrd\b", r"\bxps\b", r"\bsem\b", r"\btem\b", r"\bftir\b",
                         r"\braman\b", r"\buv[- ]?vis\b", r"\bxas\b", r"\bbet\b", r"\btga\b",
                         r"\bcircular dichroism\b", r"\b表征"],
    "mechanism": [r"\bmechanis\w*", r"\bdft\b", r"\bintermediate\b", r"\btransition state\b",
                  r"\bpathway\b", r"\bcontrol experiment", r"\b机理"],
    "limits": [r"\blimitation", r"\bdrawback", r"\bhowever,?\s+(?:the|this|it)", r"\bremains? (?:a )?challenge",
               r"\bfuture work\b", r"\b局限"],
    "system": [r"\belectrode\b", r"\bcatalyst\b", r"\bcomposite\b", r"\bperovskite\b", r"\bcof\b",
               r"\bmof\b", r"\bnanoparticle", r"\bframework\b"],
    "electrolyte": [r"\belectrolyte\b", r"\bkoh\b", r"\bh2so4\b", r"\bhcl\b", r"\bphosphate buffer\b",
                    r"\bph\s*[=~]?\s*\d"],
    "performance": [r"\boverpotential\b", r"\bfaradaic\b", r"\bcurrent density\b", r"\bcapacity\b",
                    r"\bonset potential\b", r"\btafel\b", r"\b\d+(?:\.\d+)?\s*(?:mv|ma|mah)"],
    "stability": [r"\bcycl(?:ing|e)\b", r"\bstability\b", r"\bretention\b", r"\bdurab\w*", r"\b稳定性"],
    "synthesis": [r"\bsynthesi[sz]ed\b", r"\bprepared by\b", r"\bhydrothermal\b", r"\bcalcination\b",
                  r"\banneal", r"\bsolvothermal\b", r"\b合成"],
    "structure": [r"\bmorpholog\w*", r"\bsurface area\b", r"\bpore\b", r"\bcrystal\w*", r"\blattice\b",
                  r"\bstructur\w*", r"\b形貌", r"\b结构"],
    "property": [r"\bconductivity\b", r"\badsorption capacity\b", r"\bsensitivity\b", r"\b\d+(?:\.\d+)?\s*(?:nm|ev|mv|f/g|mah)\b"],
    "application": [r"\bapplication\b", r"\bsensor\b", r"\bbattery\b", r"\badsorption\b", r"\bcatalysis\b",
                    r"\bdrug delivery\b", r"\b应用"],
    "question": [r"\bwe (?:report|show|demonstrate)\b", r"\bherein\b", r"\baim(?:ed)? to\b", r"\b研究"],
    "method": [r"\bwe (?:used|employed|measured|performed)\b", r"\bmethod\b", r"\btechnique\b",
               r"\bexperimental\b", r"\b方法"],
    "findings": [r"\b(?:demonstrates?|reveals?|indicates?|shows? that|confirms?)\b", r"\bresults? (?:show|indicate)",
                 r"\bconclu(?:de|sion)\b", r"\b结论"],
    "contribution": [r"\bnovel\b", r"\bfor the first time\b", r"\bunprecedented\b", r"\bwe report\b",
                     r"\b创新"],
}
_CUE_REGEX = {key: re.compile("|".join(pats), re.IGNORECASE) for key, pats in CUES.items()}
_QUANTITY = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|V|mA|eV|nm|°C|℃|K|h|min|s|M|mg|g)\b")
_TRIM = re.compile(r"\s+")
NOT_MENTIONED = "未提及（本地模式）"


def templates_public():
    """契约 §5 形状：id / name / desc / fields（字段显示名数组）。"""
    return [{"id": t["id"], "name": t["name"], "desc": t["desc"],
             "fields": [f["label"] for f in t["fields"]]} for t in TEMPLATES]


def get_template(template_id):
    target = str(template_id or "").strip().lower()
    for template in TEMPLATES:
        if template["id"] == target:
            return template
    return TEMPLATES[-1]


def _sentences(text):
    parts = re.split(r"(?<=[.!?。！？；;])\s+|\n+", str(text or ""))
    return [re.sub(r"\s+", " ", s).strip() for s in parts if s and len(s.strip()) >= 12]


def _paper_blob(paper, full_text="", figures=None):
    """离线抽取的素材：题录 + 摘要 + 正文节选 + 图注。"""
    chunks = []
    for key in ("title", "abstract", "keywords", "summary", "notes"):
        value = paper.get(key)
        if isinstance(value, list):
            value = "、".join(str(v) for v in value)
        if value:
            chunks.append(str(value))
    for fig in (figures or [])[:20]:
        caption = str(fig.get("caption") or "").strip()
        if caption:
            chunks.append(caption)
    if full_text:
        chunks.append(str(full_text)[:MAX_LOCAL_TEXT])
    return "\n".join(chunks)


def extract_local_values(blob, fields):
    """本地启发式：每个维度取线索分最高的句子；没有命中就写「未提及（本地模式）」。"""
    sentences = _sentences(blob)
    values = {}
    for field in fields:
        key = field["key"]
        regex = _CUE_REGEX.get(key)
        best, best_score = "", 0
        for sentence in sentences:
            if not regex or not regex.search(sentence):
                continue
            score = len(regex.findall(sentence))
            if _QUANTITY.search(sentence):
                score += 2
            if score > best_score:
                best, best_score = sentence, score
        if not best and key in ("question", "findings") and sentences:
            best = sentences[0]
        if not best and key == "contribution" and len(sentences) > 1:
            best = sentences[1]
        values[key] = (best[:MAX_CELL].strip() or NOT_MENTIONED)
    return values


def _llm_text(response):
    """把 T4 的 LLM.chat 返回值统一成文本（字符串 / {content|text|message}）。"""
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "text", "message", "answer"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
        choices = response.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict) and isinstance(message.get("content"), str):
                return message["content"]
    return ""


def _llm_usage(response):
    usage = response.get("usage") if isinstance(response, dict) else None
    if not isinstance(usage, dict):
        return {"prompt_tokens": 0, "completion_tokens": 0}
    def _int(key):
        try:
            return int(usage.get(key) or 0)
        except (TypeError, ValueError):
            return 0
    return {"prompt_tokens": _int("prompt_tokens") or _int("promptTokens"),
            "completion_tokens": _int("completion_tokens") or _int("completionTokens")}


def _parse_json_object(raw):
    text = str(raw or "").strip()
    text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
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


def build_llm_prompt(paper, fields, blob):
    lines = ["请只依据下面这篇论文的内容，按 JSON 抽取以下维度。",
             "规则：数值照抄原文（含单位）；原文没写就写「未提及」；不要编造。",
             "输出形如 {" + ", ".join('"%s": "..."' % f["key"] for f in fields) + "} 的 JSON 对象。",
             "", "# 维度"]
    for field in fields:
        lines.append("- %s（%s）：%s" % (field["key"], field["label"], field.get("hint", "")))
    lines += ["", "# 论文", blob[:12000]]
    return "\n".join(lines)


def _extract_with_llm(services, paper, fields, blob):
    """返回 (values, usage)；失败/无 Key 返回 (None, 空用量)。"""
    llm = getattr(services, "llm", None)
    if llm is None:
        return None, None
    try:
        if not llm.ready():
            return None, None
    except NotImplementedError:
        return None, None
    except Exception:  # noqa: BLE001
        return None, None

    messages = [
        {"role": "system", "content": "你是化学/材料领域的文献信息抽取助手，只输出合法 JSON。"},
        {"role": "user", "content": build_llm_prompt(paper, fields, blob)},
    ]
    try:
        response = llm.chat(messages, {"maxTokens": 1400, "temperature": 0.1, "json": True})
    except NotImplementedError:
        return None, None
    except Exception:  # noqa: BLE001 - 在线失败必须降级，不能 502
        return None, None

    parsed = _parse_json_object(_llm_text(response))
    if not parsed:
        return None, None
    values = {}
    for field in fields:
        value = parsed.get(field["key"])
        if isinstance(value, (list, tuple)):
            value = "；".join(str(v) for v in value)
        elif isinstance(value, dict):
            value = json.dumps(value, ensure_ascii=False)
        text = str(value or "").strip()
        values[field["key"]] = text[:MAX_CELL] if text else "未提及"
    return values, _llm_usage(response)


def _one_line(papers, template, mode, first_values=None):
    titles = [str((p.get("title") or p.get("id") or "")).strip() for p in papers]
    short = "；".join(t[:40] for t in titles[:3])
    if len(titles) > 3:
        short += " 等 %d 篇" % len(titles)
    if mode == "local":
        return ("未配置大模型：以下按「%s」模板做本地启发式对比（共 %d 篇：%s）。"
                "未命中维度如实标注「未提及」，不编造数值。"
                % (template["name"], len(papers), short))
    focus = ""
    if first_values:
        for key in ("question", "findings", "system"):
            value = first_values.get(key)
            if value and "未提及" not in value:
                focus = value[:80]
                break
    return "共对比 %d 篇（%s），按「%s」模板抽取。%s" % (
        len(papers), short, template["name"], ("共同关注：" + focus) if focus else "")


def _load_extra(paper_id, services):
    """正文与图表：全文走 store（paper_text 表），图表走 store.repo。"""
    full_text, figures = "", []
    try:
        store = services.db
        full_text = store.get_text(str(paper_id)) or ""
    except Exception:  # noqa: BLE001
        full_text = ""
    try:
        figures = services.db.repo.list_figures(str(paper_id)) or []
    except Exception:  # noqa: BLE001
        figures = []
    return full_text, figures


def run(services, papers, template_id):
    """契约 §6：返回 { template, oneLine, sections, usage }（绝不抛异常）。"""
    template = get_template(template_id)
    fields = template["fields"]

    profiles = []
    mode = "local"
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0}

    for paper in papers:
        full_text, figures = _load_extra(paper.get("id"), services)
        blob = _paper_blob(paper, full_text, figures)
        values, usage = _extract_with_llm(services, paper, fields, blob)
        if values is None:
            values = extract_local_values(blob, fields)
        else:
            mode = "llm"
            if usage:
                total_usage["prompt_tokens"] += usage["prompt_tokens"]
                total_usage["completion_tokens"] += usage["completion_tokens"]
        profiles.append({"paperId": paper.get("id"), "values": values})

    sections = []
    for field in fields:
        rows = [{"paperId": profile["paperId"],
                 "value": profile["values"].get(field["key"]) or NOT_MENTIONED}
                for profile in profiles]
        sections.append({"title": field["label"], "rows": rows})

    first_values = profiles[0]["values"] if profiles else {}
    return {
        "template": template["id"],
        "oneLine": _one_line(papers, template, mode, first_values),
        "sections": sections,
        "usage": total_usage,
    }
