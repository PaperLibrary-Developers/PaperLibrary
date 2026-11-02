"""
compare_engine.py — 多篇同类文献横向对比（化学向）

Map-Reduce 两段式：
  Map    单篇论文 → 固定 JSON（每篇只抽一次，落库缓存；换对比组合不再花钱）
  Reduce N 份 JSON → 中文横向综述（一次调用，上下文可控）

为什么不一次喂 N 篇全文：
  化学论文的产率、ee、催化剂负载量大多藏在表格与 Scheme 图注里。
  N 篇全文塞进一次请求必然被截断或被"记混"；拆两段后每段都在安全
  上下文内，且每篇的结构化结果可缓存、可人工核对、可复用到别的组合。
"""

import json

import ai_engine

# ------------------------------------------------------------------
# 维度模板
# ------------------------------------------------------------------

ORGANIC_FIELDS = [
    {"key": "system", "label": "研究对象/体系", "hint": "具体底物类别 + 目标产物或目标材料"},
    {"key": "reaction", "label": "反应类型", "hint": "如 不对称氢化 / 交叉偶联 / 缩聚 / 光催化 等"},
    {"key": "catalyst", "label": "催化体系", "hint": "金属中心、配体、负载量 mol%、助催化剂"},
    {"key": "conditions", "label": "最优条件", "hint": "溶剂、温度、时间、气氛、光源或电位、添加剂"},
    {"key": "yield", "label": "产率/转化率", "hint": "照抄原文数值与单位；区间也照抄"},
    {"key": "selectivity", "label": "选择性", "hint": "ee / dr / 区域选择性的具体数值"},
    {"key": "scope", "label": "底物范围", "hint": "试了多少个底物、容忍哪些官能团、有什么例外"},
    {"key": "mechanism", "label": "机理与证据", "hint": "提出的机理 + 支撑手段（对照实验、DFT、中间体捕获）"},
    {"key": "characterization", "label": "表征手段", "hint": "NMR / XRD / XPS / SEM / TEM / CD / IR 等实际用到的仪器"},
    {"key": "limits", "label": "局限", "hint": "作者承认的限制，或从数据能明显看出的短板"},
    {"key": "novelty", "label": "核心创新点", "hint": "一句话说清与之前工作的差别"},
]

ELECTRO_FIELDS = [
    {"key": "system", "label": "研究对象/体系", "hint": "电极材料、电解液、目标反应"},
    {"key": "electrode", "label": "电极与器件", "hint": "工作电极、对电极、参比电极、负载量"},
    {"key": "conditions", "label": "测试条件", "hint": "电位窗口、扫速、电流密度、电解液浓度与 pH"},
    {"key": "performance", "label": "活性指标", "hint": "过电位、法拉第效率、电流密度等具体数值"},
    {"key": "stability", "label": "稳定性", "hint": "循环次数、保持率、衰减速率"},
    {"key": "mechanism", "label": "机理与证据", "hint": "活性位点、决速步、原位表征或 DFT"},
    {"key": "characterization", "label": "表征手段", "hint": "XRD / XPS / TEM / 原位 Raman / XAS 等"},
    {"key": "limits", "label": "局限", "hint": "作者承认的限制或比较基准问题"},
    {"key": "novelty", "label": "核心创新点", "hint": "一句话说清与之前工作的差别"},
]

MATERIAL_FIELDS = [
    {"key": "system", "label": "材料体系", "hint": "材料名称、组成、目标应用"},
    {"key": "synthesis", "label": "合成方法", "hint": "方法名、前驱体、温度/时间、后处理"},
    {"key": "structure", "label": "结构与形貌", "hint": "晶型、孔径、维度、尺度、比表面积"},
    {"key": "property", "label": "关键性能", "hint": "带数值与单位的核心指标"},
    {"key": "application", "label": "应用场景", "hint": "吸附 / 催化 / 储能 / 手性识别 等"},
    {"key": "characterization", "label": "表征手段", "hint": "实际用到的仪器与数据"},
    {"key": "mechanism", "label": "机理与证据", "hint": "构效关系、机理假设与证据"},
    {"key": "limits", "label": "局限", "hint": "作者承认的限制"},
    {"key": "novelty", "label": "核心创新点", "hint": "一句话说清与之前工作的差别"},
]

GENERAL_FIELDS = [
    {"key": "question", "label": "研究问题", "hint": "这篇要回答什么"},
    {"key": "method", "label": "方法与数据", "hint": "技术路线、样本或数据集、关键实验设计"},
    {"key": "findings", "label": "主要结论", "hint": "带关键数值的结论"},
    {"key": "contribution", "label": "贡献", "hint": "相对已有工作的增量"},
    {"key": "limits", "label": "局限", "hint": "作者承认的限制"},
]

PRESETS = {
    "organic": {
        "label": "🧪 有机合成 / 催化",
        "desc": "底物、催化剂、最优条件、产率与选择性 —— 适合羰基还原、Cu 催化、手性聚合物合成这类文章",
        "fields": ORGANIC_FIELDS,
        "metrics": ["产率", "选择性"],
    },
    "electro": {
        "label": "🔋 电化学 / 能源器件",
        "desc": "电极、电解液、活性与稳定性 —— 适合电催化、电池、CISS 电化学这类文章",
        "fields": ELECTRO_FIELDS,
        "metrics": ["过电位", "法拉第效率"],
    },
    "material": {
        "label": "🧱 材料 / 结构表征",
        "desc": "合成路线、结构与形貌、构效关系 —— 适合 COF/MOF、纳米材料这类文章",
        "fields": MATERIAL_FIELDS,
        "metrics": ["性能指标"],
    },
    "general": {
        "label": "🌐 通用（跨领域兜底）",
        "desc": "研究问题、方法、结论、贡献 —— 不限定化学子领域时用这个",
        "fields": GENERAL_FIELDS,
        "metrics": ["关键数值"],
    },
}

DEFAULT_MAX_TEXT = 16000


# ------------------------------------------------------------------
# 模板工具
# ------------------------------------------------------------------

def preset_ids():
    return list(PRESETS.keys())


def get_preset(preset_id):
    return PRESETS.get(preset_id) or PRESETS["general"]


def build_fields(preset_id, extra_labels=None):
    """模板字段 + 用户自定义维度（自定义维度排在最后，key 用 custom_N 固定下来）。"""
    fields = [dict(f) for f in get_preset(preset_id)["fields"]]
    for i, name in enumerate(extra_labels or []):
        name = (name or "").strip()
        if not name:
            continue
        fields.append({"key": "custom_%d" % (i + 1), "label": name,
                       "hint": "用户自定义维度：" + name})
    return fields


def cache_key(preset_id, extra_labels=None):
    """缓存键：模板 + 自定义维度。改了维度就要重新抽 —— 字段不同，旧结果不可复用。"""
    extra = [(x or "").strip() for x in (extra_labels or []) if (x or "").strip()]
    return preset_id if not extra else preset_id + "|" + ",".join(extra)


def paper_label(index):
    return "P%d" % (index + 1)


# ------------------------------------------------------------------
# 单篇抽取（Map）
# ------------------------------------------------------------------

def _paper_text(paper, figures=None, max_chars=DEFAULT_MAX_TEXT):
    """题录 + 图注 + 正文节选。图注对化学论文至关重要：条件表、产率常印在 Scheme/Table 上。"""
    parts = []
    parts.append("# 题录")
    parts.append("- 标题: " + str(paper.get("title") or paper.get("filename") or "（无标题）"))
    if paper.get("journal"):
        parts.append("- 期刊: " + str(paper.get("journal")))
    if paper.get("year"):
        parts.append("- 年份: " + str(paper.get("year")))
    if paper.get("doi"):
        parts.append("- DOI: " + str(paper.get("doi")))

    if figures:
        lines = []
        for f in figures[:40]:
            cap = (f.get("caption") or "").strip()
            if not cap:
                continue
            lines.append("- [Figure %s] %s" % (f.get("fig_index", "?"), cap[:300]))
        if lines:
            parts.append("\n# 图表图注")
            parts.extend(lines)

    full = (paper.get("full_text") or "")
    if full:
        parts.append("\n# 正文节选")
        parts.append(full[:max_chars])

    return "\n".join(parts)


def _field_list_text(fields):
    return "\n".join("- %s（%s）：%s" % (f["key"], f["label"], f.get("hint", ""))
                     for f in fields)


def _json_template(fields):
    obj = {}
    for f in fields:
        obj[f["key"]] = "字符串"
    first_key = fields[0]["key"] if fields else "system"
    skeleton = {
        "one_line": "一句话概括这篇做了什么（20 字内）",
        "values": obj,
        "metrics": [{"name": "指标名", "value": "数值",
                     "unit": "单位", "condition": "对该数值生效的条件"}],
        "evidence": {first_key: "原文短句（英文照抄）"},
    }
    return json.dumps(skeleton, ensure_ascii=False, indent=2)


EXTRACT_SYSTEM = """你是化学领域的文献信息抽取助手。你的唯一任务是把一篇论文里**原文明确写到**的信息，填进固定的字段结构。

硬性规则：
1. 数值必须照抄原文，保留单位与有效数字；原文写区间就照抄区间。不要换算、不要四舍五入、不要取整。
2. 原文没有写到的字段，值统一写"未提及"。禁止根据领域常识补全，禁止写"据推测"。
3. 每个字段控制在 80 字以内，但产率、ee、负载量、温度这类关键数值必须完整保留，不因为压字数而丢数字。
4. metrics 只放可比较的量化指标（产率、ee、过电位、比表面积……），每条带 condition；没有量化指标就给空数组。
5. evidence 里为最多 3 个最关键的字段附上原文英文短句（20 词以内，照抄不改写），用来人工核对。
6. 只输出 JSON 对象本身：不要 markdown 代码块，不要前后解释。"""


def build_extract_prompt(paper, figures, fields):
    return (
        "请抽取下面这篇论文的信息。\n\n"
        "## 要填的字段（JSON 的 values 里，键名必须与括号外的标识完全一致）\n"
        + _field_list_text(fields)
        + "\n\n## 输出 JSON 结构\n" + _json_template(fields)
        + "\n\n## 论文内容\n" + _paper_text(paper, figures)
    )


def _strip_fence(raw):
    raw = (raw or "").strip()
    tick = chr(96) * 3
    if raw.startswith(tick):
        body = raw[len(tick):]
        if body.startswith("json"):
            body = body[4:]
        if body.rstrip().endswith(tick):
            body = body.rstrip()[:-len(tick)]
        return body.strip()
    return raw


def parse_json_object(raw):
    """尽量从模型回复里抠出一个 JSON 对象；抠不出来返回 None（调用方负责报错，不静默吞）。"""
    txt = _strip_fence(raw)
    try:
        return json.loads(txt)
    except Exception:
        pass
    start = txt.find("{")
    end = txt.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(txt[start:end + 1])
        except Exception:
            return None
    return None


def _call_llm(provider, api_key, model, base_url, system, user,
              max_tokens=2000, json_mode=False, temperature=0.2):
    """统一的单轮调用。json_mode 只对 OpenAI 兼容端点开启；失败自动降级重试一次。"""
    attempts = [True, False] if json_mode else [False]
    last_err = None
    for attempt_json in attempts:
        try:
            if provider in ("deepseek", "openai_compatible", "openai"):
                client = (ai_engine._deepseek_client(api_key, base_url)
                          if provider == "deepseek" else ai_engine._openai_client(api_key, base_url))
                default_model = "deepseek-chat" if provider == "deepseek" else "gpt-4o-mini"
                kwargs = {}
                if attempt_json:
                    kwargs["response_format"] = {"type": "json_object"}
                resp = client.chat.completions.create(
                    model=model or default_model,
                    messages=[{"role": "system", "content": system},
                              {"role": "user", "content": user}],
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kwargs
                )
                return resp.choices[0].message.content or ""
            elif provider == "claude":
                client = ai_engine._claude_client(api_key)
                resp = client.messages.create(
                    model=model or "claude-sonnet-5",
                    max_tokens=max_tokens,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                )
                return resp.content[0].text or ""
            else:
                raise ValueError("未知 provider: %s" % provider)
        except Exception as e:
            last_err = e
            if attempt_json:
                continue
            raise
    raise last_err if last_err else RuntimeError("调用失败")


def extract_profile(paper, figures, fields, provider, api_key, model=None, base_url=None,
                    max_chars=DEFAULT_MAX_TEXT):
    """抽一篇。返回 values / metrics / evidence / one_line / raw_ok。"""
    trimmed = dict(paper)
    trimmed["full_text"] = (paper.get("full_text") or "")
    prompt = build_extract_prompt(trimmed, figures, fields)
    raw = _call_llm(provider, api_key, model, base_url, EXTRACT_SYSTEM, prompt,
                    max_tokens=2200, json_mode=True, temperature=0.1)
    obj = parse_json_object(raw)
    if obj is None:
        return {"values": {}, "metrics": [], "evidence": {}, "one_line": "",
                "raw_ok": False, "raw": (raw or "")[:400]}

    values = obj.get("values") if isinstance(obj.get("values"), dict) else {}
    clean = {}
    for f in fields:
        v = values.get(f["key"], "未提及")
        if isinstance(v, (list, tuple)):
            v = "；".join(str(x) for x in v)
        elif isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False)
        v = (str(v) if v is not None else "").strip()
        clean[f["key"]] = v if v else "未提及"

    metrics = obj.get("metrics") if isinstance(obj.get("metrics"), list) else []
    metrics = [m for m in metrics if isinstance(m, dict) and (m.get("name") or m.get("value"))]

    evidence_raw = obj.get("evidence") if isinstance(obj.get("evidence"), dict) else {}
    evidence = {}
    for k, v in evidence_raw.items():
        if v:
            evidence[str(k)] = str(v)[:300]

    return {"values": clean, "metrics": metrics, "evidence": evidence,
            "one_line": str(obj.get("one_line") or "")[:120], "raw_ok": True}


# ------------------------------------------------------------------
# 横向综合（Reduce）
# ------------------------------------------------------------------

SYNTH_SYSTEM = """你是化学领域的资深综述作者与审稿人。用户给你的是**多篇同类论文的结构化抽取结果**（不是全文），
你的任务是写一篇横向对比综述，帮助读者看清这批工作之间的关系。

硬性规则：
1. 只依据给出的抽取结果。不得引入外部文献、不得补领域常识、不得替作者圆场。
2. 论文一律用编号引用（[P1]、[P2]），不要写作者名。
3. 凡是要说"差异"的地方，必须并排列出双方的具体数值或条件；只说"存在差异"没有任何价值。
4. 原文未报告的量，明确写"未报告"，不要用"约""左右""大致"糊过去。
5. 不要逐篇复述摘要 —— 读者要的是横向关系：谁的体系更好、条件更温和、数据更完整。
6. 用中文写，Markdown 格式。"""


def build_synthesis_prompt(profiles, fields, focus=""):
    rows = []
    for p in profiles:
        rows.append({
            "编号": p["label"],
            "标题": (p.get("paper") or {}).get("title", "")[:120],
            "年份": (p.get("paper") or {}).get("year"),
            "期刊": (p.get("paper") or {}).get("journal", ""),
            "字段": p.get("values", {}),
            "关键数值": p.get("metrics", []),
        })
    body = json.dumps(rows, ensure_ascii=False, indent=1)
    dims = "、".join(f["label"] for f in fields)
    return (
        "## 对比维度\n" + dims + "\n\n"
        "## 用户关注点\n" + (focus.strip() or "（未特别指定：请按下面结构给出全面横向对比）") + "\n\n"
        "## 结构化数据（共 %d 篇）\n" % len(profiles) + body
    )


SYNTH_OUTLINE = """
请严格按这个结构写：

## 一、这批工作共同在解决什么
（3-5 句，说清共同的问题背景与共同的评价标准）

## 二、共识
（至少 3 条，每条结尾标出来自哪几篇，如 [P1][P3]；说清"共识"具体共识在什么条件/数值上）

## 三、分歧与矛盾
（每条都给出双方的具体数值或条件对比；如果这批工作其实分歧很小，也要说明"分歧很小，集中在 X"）

## 四、方法与条件的演进
（按年份排，讲清楚条件是在往更温和/更便宜/更绿色走，还是反过来；给出具体条件对比）

## 五、关键指标横向对照
（一个 markdown 表格：行 = 论文编号，列 = 3-5 个最重要的可比指标；缺失写"未报告"）

## 六、研究空白与可执行的下一步
（3-5 条。每条必须写成"做什么实验 / 和谁比 / 预期看到什么"，而不是"有待深入研究"）

## 七、一句话结论
（一句话：这批工作整体到了什么程度，最大的缺口在哪）
"""


def synthesize(profiles, fields, focus, provider, api_key, model=None, base_url=None):
    prompt = build_synthesis_prompt(profiles, fields, focus) + "\n" + SYNTH_OUTLINE
    return _call_llm(provider, api_key, model, base_url, SYNTH_SYSTEM, prompt,
                     max_tokens=3500, json_mode=False, temperature=0.3)


# ------------------------------------------------------------------
# 表格与导出（纯函数，便于单测）
# ------------------------------------------------------------------

def matrix_rows(profiles, fields):
    """行 = 维度，列 = 各篇论文。用于并排阅读同一维度上不同论文的取值。"""
    rows = []
    for f in fields:
        row = {"维度": f["label"], "key": f["key"]}
        for p in profiles:
            row[p["label"]] = (p.get("values") or {}).get(f["key"], "未提及")
        rows.append(row)
    meta = {"维度": "题录", "key": "_meta"}
    for p in profiles:
        pp = p.get("paper") or {}
        meta[p["label"]] = " · ".join(x for x in [str(pp.get("year") or ""),
                                                  (pp.get("journal") or "")[:20],
                                                  str(pp.get("doi") or "")[:40]] if x)
    rows.append(meta)
    return rows


def _md_cell(v, limit=0):
    s = "" if v is None else str(v)
    if limit and len(s) > limit:
        s = s[:limit] + "…"
    s = s.replace("|", chr(92) + "|").replace("\n", " ")
    return s.strip() or "—"


def to_markdown(rows, profiles, cell_limit=0):
    if not rows:
        return ""
    labels = [p["label"] for p in profiles]
    head = "| 维度 | " + " | ".join(labels) + " |"
    sep = "| --- | " + " | ".join("---" for _ in labels) + " |"
    lines = [head, sep]
    for r in rows:
        lines.append("| **%s** | " % _md_cell(r["维度"]) +
                     " | ".join(_md_cell(r.get(l, ""), cell_limit) for l in labels) + " |")
    return "\n".join(lines)


def to_csv(rows):
    import csv
    import io
    if not rows:
        return ""
    cols = list(rows[0].keys())
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=cols)
    w.writeheader()
    for r in rows:
        w.writerow({c: ("" if r.get(c) is None else str(r.get(c)).replace("\n", " "))
                    for c in cols})
    return buf.getvalue()


def full_markdown(profiles, fields, focus, report, stamp=""):
    """导出用：题录 + 矩阵 + 综合报告。"""
    out = ["# 文献横向对比：%s" % (focus.strip() or "（未指定关注点）"), ""]
    out.append("生成时间：%s ｜ 共 %d 篇 ｜ 维度 %d 项" % (stamp or "—", len(profiles), len(fields)))
    out.append("")
    out.append("## 一、参与对比的文献")
    for p in profiles:
        pp = p.get("paper") or {}
        out.append("- **[%s]** %s（%s, %s）" % (
            p["label"], pp.get("title") or pp.get("filename") or "（无标题）",
            pp.get("journal") or "—", pp.get("year") or "—"))
    out.append("")
    out.append("## 二、结构化对比矩阵")
    out.append(to_markdown(matrix_rows(profiles, fields), profiles))
    out.append("")
    out.append("## 三、AI 综合对比报告")
    out.append(report or "（尚未生成）")
    return "\n".join(out)


def estimate_tokens(text):
    """粗估：中英混排按 1.6 字/token，用于给用户成本预期，不追求精确。"""
    return int(len(text or "") / 1.6) + 1
