# -*- coding: utf-8 -*-
"""深度分析的提示词与字段槽位表（从 PaperLibrary 原 SYSTEM JSON 原样搬过来）。

移植自 旧 PaperLibrary 工程\\deep_analysis.py 的 SYSTEM 常量
契约名：deep.get / deep.run（GET|POST /api/papers/:id/deep-analysis）

为什么单独成文件：原提示词有 22 个顶层键（experiments 6 个子槽 + applicability 2 个子槽），
和渲染/降级逻辑放在一起会让单文件超 450 行；这里只放「规格」，纯数据、零 I/O。

与原文的差异（两处，均为契约要求）：
  1. scores 由「0-10 分」改为契约 §7 的五维「1-5 分」，且维度名用契约的中文键
     （原 novelty/technical/evidence/writing/usefulness 对应
       创新性 / 严谨性 / 可复现性 / 可读性 / 影响力），每条仍带 reason；
  2. 其余提示词内容（区分作者主张与证据判断、不编造数据、化学细节精确、局限分自述/推断、
     只输出 JSON）逐句保留。
"""
from __future__ import annotations

LOCAL_MODEL = "local-heuristic"
MAX_TEXT = 26000

# 契约 §7 的五维评分键（顺序即展示顺序）
SCORE_KEYS = ["创新性", "严谨性", "影响力", "可复现性", "可读性"]

# 原 JSON 英文键 → 契约中文评分键（模型若仍按原文英文键输出也能认）
SCORE_ALIASES = {
    "创新性": ["创新性", "novelty"],
    "严谨性": ["严谨性", "technical", "技术质量"],
    "影响力": ["影响力", "usefulness", "实用性"],
    "可复现性": ["可复现性", "evidence", "证据充分性"],
    "可读性": ["可读性", "writing", "写作质量"],
}

# 24 个语义槽位：(中文字段名, 原 JSON 路径, 值类型)
# 文本槽 text；列表槽 list；公式槽 formula（[{latex, meaning}]）
FIELD_SLOTS = [
    ("一句话概括", "one_liner", "text"),
    ("原文摘要", "original_abstract", "text"),
    ("中文概述", "zh_overview", "text"),
    ("研究背景", "problem", "text"),
    ("核心方法", "method_core", "text"),
    ("方法步骤", "method_steps", "list"),
    ("关键公式", "formulas", "formula"),
    ("实验体系", "experiments.systems", "text"),
    ("对照与基线", "experiments.baselines", "text"),
    ("实验条件", "experiments.conditions", "text"),
    ("评价指标", "experiments.metrics", "text"),
    ("主要结果", "experiments.main_results", "text"),
    ("消融与稳健性", "experiments.ablations", "text"),
    ("核心要点", "key_points", "list"),
    ("主要贡献", "contributions", "list"),
    ("作者主张", "author_claims", "list"),
    ("证据判断", "evidence_judgement", "text"),
    ("优势", "strengths", "list"),
    ("作者自述局限", "limitations_stated", "list"),
    ("推断局限", "limitations_inferred", "list"),
    ("适用条件", "applicability.when", "text"),
    ("不适用情形", "applicability.when_not", "text"),
    ("相关工作", "related", "list"),
    ("开放问题", "open_questions", "list"),
]

SLOT_NAMES = [slot[0] for slot in FIELD_SLOTS]

# fields 里应有的全部键：24 个语义槽位 + 评分理由（原 scores.*.reason 的落点）+ 分析模式说明
FIELDS = SLOT_NAMES + ["评分理由", "分析模式"]

LOCAL_NOTE = "未配置大模型，仅本地分析（依据题录、摘要、正文体量与图表计数，不编造数据）"

SYSTEM_PROMPT = """你是一位国际顶刊（JACS / Nature 子刊 / ACS Catal. 级别）的资深审稿人，
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
    "创新性":   {"score": 3, "reason": "创新性打分理由"},
    "严谨性":   {"score": 3, "reason": "技术质量打分理由"},
    "影响力":   {"score": 3, "reason": "影响力打分理由"},
    "可复现性": {"score": 3, "reason": "证据充分性/可复现性打分理由"},
    "可读性":   {"score": 3, "reason": "写作质量打分理由"}
  }
}

评分标准：1-5 的整数（不是 0-10）。5 只给真正突出的；3 是「合格但不惊艳」；2 以下有明显缺陷。
"""
