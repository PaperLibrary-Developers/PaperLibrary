# -*- coding: utf-8 -*-
"""local_rank.py — 本地库的检索相关性排序（Google 式）+ 中英术语对照

要解决两个问题：

**问题一：没有相关性排序。** 原来的 `search_papers` 只是「匹配到就返回」，
按 BM25 或年份排，没有「标题命中优先于正文命中」的概念。

**问题二：中文搜不到英文内容。** FTS5 的 unicode61 分词器不切分中日韩文字，
「循环伏安」在索引里是一个整词，正文写成「循环伏安法」就匹配不上（整库只命中 1 条）。
更麻烦的是**跨语言**：本库的图片字段（caption / ai_description / context_text /
figure_type）**全是英文**，而用户习惯用中文搜（AI 摘要也是中文）——
实测「循环伏安」「机理」在图片检索里**命中 0 张**，就是因为中文词根本不在英文文本里。

本模块的做法：
  1. **分词**：拉丁词按空白/标点切；中文切 bigram（循环伏安 → 循环/环伏/伏安）。
  2. **领域术语对照（GLOSSARY）**：中文术语 ↔ 英文写法双向展开。
     搜「循环伏安」也会去找 cyclic voltammetry / CV；搜 "spin selectivity"
     也会去找 自旋选择性。这是让中文查询能命中英文图片数据的关键。
  3. **概念分组打分**：一个查询词是「一个概念」，它有一组同义写法；
     任一写法命中即算该概念命中。覆盖率 = 命中概念数 / 概念总数。
  4. **字段加权**：标题 ≫ 摘要 > 正文；图注 > AI 描述 > 类型 > 上下文。
     标题命中一定排在只有正文命中的前面，但**正文相关的依然会展示**。
  5. 返回 `relevance`（0–100）与 `matched_fields`，界面据此展示「为什么这条会出来」。

纯函数，不碰数据库，便于离线测试。
"""

import re

# ── 字段权重 ──
# 文献：标题 ≫ 摘要 > 正文。取值原则是「标题里的部分命中要强于正文里的完整命中」。
FIELD_WEIGHTS = {
    "title": 10.0,
    "summary": 4.0,
    "full_text": 1.0,
}
FIELD_LABELS = {
    "title": "标题",
    "summary": "摘要",
    "full_text": "正文",
}

# 图片：图注和 AI 描述最能说明「这张图是什么」；图表类型是短标签；上下文兜底。
FIGURE_WEIGHTS = {
    "caption": 8.0,
    "ai_description": 6.0,
    "figure_type": 3.0,
    "context_text": 1.0,
}
FIGURE_LABELS = {
    "caption": "图注",
    "ai_description": "AI 描述",
    "figure_type": "图表类型",
    "context_text": "上下文",
}

PAPER_FIELDS = {"title": "title", "summary": "summary", "full_text": "full_text"}
FIGURE_FIELDS = {"caption": "caption", "ai_description": "ai_description",
                 "figure_type": "figure_type", "context_text": "context_text"}

# 长字段用轻量归一化（只 lower）：
# 对几 MB 的正文跑标点正则会让打分从 40 ms 涨到 1000 ms（实测）；
# 它们只需要子串包含判断。短字段用完整归一化，
# 这样 "Chirality-Induced Spin Selectivity" 能和查询
# "chirality induced spin selectivity" 通过「短语连续出现」对上。
LIGHT_FIELDS = {"full_text", "ai_description", "context_text"}

# 单字段最高分 = 覆盖率满(1.0) + 短语加分(0.8)
_MAX_PER_FIELD = 1.8

_CJK = r"\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af"
_CJK_RUN = re.compile(f"[{_CJK}]+")
_LATIN_RUN = re.compile(r"[a-z0-9]+")

_STOP = {
    "the", "a", "an", "of", "and", "or", "in", "on", "for", "to", "with", "by",
    "is", "are", "was", "were", "be", "as", "at", "from", "that", "this",
    "的", "了", "和", "与", "在", "是", "为", "对", "及", "等",
}


# ═══════════════════════════════════════════════════
# 领域术语对照表（中文 → 英文同义写法）
#   · 只收本领域（电化学 / 材料表征 / 有机合成 / 手性自旋）常见词
#   · 英文写法可以是多词短语，按子串匹配（归一化后空格分隔）
#   · 反向（英文→中文）由 _REVERSE 自动生成
# ═══════════════════════════════════════════════════
GLOSSARY = {
    # 电化学方法
    "循环伏安": ["cv", "cyclic voltammetry", "voltammetry", "voltammogram"],
    "伏安": ["voltammetry", "voltammogram"],
    "线性扫描": ["lsv", "linear sweep"],
    "极化曲线": ["polarization curve", "lsv", "polarization"],
    "阻抗谱": ["eis", "impedance", "impedance spectroscopy"],
    "交流阻抗": ["eis", "impedance"],
    "计时电流": ["chronoamperometry", "i-t"],
    "法拉第效率": ["faradaic efficiency", "faradaic", "fe"],
    "电流密度": ["current density"],
    "过电位": ["overpotential"],
    "起始电位": ["onset potential"],
    "塔菲尔": ["tafel", "tafel slope"],
    "塔菲尔斜率": ["tafel slope", "tafel"],
    "氧化峰": ["oxidation peak"],
    "还原峰": ["reduction peak"],
    "还原电位": ["reduction potential"],
    "电化学": ["electrochemical", "electrochemistry"],
    "电催化": ["electrocatalytic", "electrocatalysis"],
    "电解": ["electrolysis", "electrolyte"],
    "电极": ["electrode"],
    "电解液": ["electrolyte"],
    "析氧反应": ["oxygen evolution", "oer"],
    "析氢反应": ["hydrogen evolution", "her"],
    "氧还原": ["oxygen reduction", "orr"],
    "二氧化碳还原": ["co2 reduction", "co2rr", "carbon dioxide reduction"],
    "氮还原": ["nitrogen reduction", "nrr"],
    # 表征
    "射线衍射": ["xrd", "x-ray diffraction", "diffraction"],
    "图谱": ["spectrum", "spectra", "pattern"],
    "光谱": ["spectroscopy", "spectrum", "spectra"],
    "光电子能谱": ["xps", "photoelectron spectroscopy"],
    "电子显微镜": ["sem", "tem", "microscopy", "micrograph"],
    "扫描电镜": ["sem", "scanning electron microscopy"],
    "透射电镜": ["tem", "transmission electron microscopy"],
    "拉曼": ["raman"],
    "红外": ["ftir", "infrared", "ir spectrum"],
    "核磁": ["nmr", "nuclear magnetic resonance"],
    "荧光": ["fluorescence", "photoluminescence"],
    "紫外": ["uv-vis", "ultraviolet"],
    "电子顺磁共振": ["epr", "electron paramagnetic resonance", "esr"],
    "吸附": ["adsorption", "adsorbed"],
    "比表面积": ["surface area", "bet"],
    "晶体结构": ["crystal structure", "crystallographic"],
    "形貌": ["morphology", "morphological"],
    "纳米片": ["nanosheet"],
    "纳米颗粒": ["nanoparticle", "nanoparticles"],
    "纳米材料": ["nanomaterial", "nanomaterials", "nanostructure"],
    "单原子": ["single-atom", "single atom", "sac"],
    # 结构 / 反应
    "反应机理": ["mechanism", "mechanistic"],
    "机理": ["mechanism", "mechanistic"],
    "催化循环": ["catalytic cycle", "catalytic"],
    "反应路径": ["pathway", "reaction pathway"],
    "中间体": ["intermediate", "intermediates"],
    "自由基": ["radical", "radicals"],
    "底物": ["substrate", "substrates"],
    "底物适用范围": ["substrate scope", "substrate"],
    "产率": ["yield", "yields"],
    "选择性": ["selectivity", "selective"],
    "催化剂": ["catalyst", "catalytic", "catalysts"],
    "配体": ["ligand", "ligands"],
    "官能团": ["functional group", "functional groups"],
    "羰基": ["carbonyl", "carbonyls"],
    "羰基还原": ["carbonyl reduction", "carbonyl"],
    "醛": ["aldehyde", "aldehydes"],
    "酮": ["ketone", "ketones"],
    "醇": ["alcohol", "alcohols"],
    "烯烃": ["alkene", "alkenes", "olefin"],
    "偶联": ["coupling", "cross-coupling"],
    "还原": ["reduction", "reductive"],
    "氧化": ["oxidation", "oxidative"],
    "不对称催化": ["asymmetric catalysis", "asymmetric"],
    "对映选择性": ["enantioselectivity", "enantioselective"],
    # 手性 / 自旋
    "手性": ["chiral", "chirality"],
    "手性诱导": ["chirality induced", "chiral-induced", "chirality-induced", "ciss"],
    "自旋": ["spin"],
    "自旋极化": ["spin polarization", "spin-polarized", "spin polarized"],
    "自旋选择性": ["spin selectivity", "spin-selective"],
    "自旋过滤": ["spin filter", "spin filtering"],
    "效应": ["effect", "effects"],
    "金属有机框架": ["mof", "metal-organic framework", "metal organic framework"],
    "共价有机框架": ["cof", "covalent organic framework"],
    "磁各向异性": ["magnetic anisotropy"],
    "单分子磁体": ["single-molecule magnet", "single molecule magnet"],
    "圆二色": ["circular dichroism", "cd spectra"],
    # 图形与通用术语（图片检索常用；刻意不收「数据」「时间」这类过度泛化的词，
    # 否则任何含 data 的图都会凭一个泛词抬高覆盖率）
    "曲线": ["curve", "curves", "plot"],
    "图像": ["image", "images", "micrograph"],
    "示意图": ["schematic", "illustration", "diagram"],
    "示意图解": ["schematic", "illustration"],
    "结构": ["structure", "structural"],
    "分布": ["distribution"],
    "峰": ["peak", "peaks"],
    "峰位": ["peak position", "peak"],
    "对比": ["comparison", "compared"],
    "性能": ["performance"],
    "活性": ["activity", "active"],
    "稳定性": ["stability", "stable"],
    "效率": ["efficiency"],
    "电位": ["potential"],
    "电流": ["current"],
    "电压": ["voltage"],
    "扫描速率": ["scan rate"],
    "浓度": ["concentration"],
    "表面": ["surface"],
    "界面": ["interface"],
    "薄膜": ["film", "thin film"],
    "晶体": ["crystal", "crystalline"],
    "衍射": ["diffraction"],
    "散射": ["scattering"],
    "吸收": ["absorption", "absorbance"],
    "发射": ["emission"],
    "强度": ["intensity"],
    "模拟": ["simulation", "simulated"],
    "计算": ["calculation", "computational", "dft"],
    "实验": ["experimental", "experiment"],
    "理论": ["theoretical", "theory"],
    "能带": ["band structure", "band"],
    "信号": ["signal", "signals"],
    "谱图": ["spectrum", "spectra", "spectroscopic"],
    "峰形": ["peak shape"],
    "响应": ["response", "responsive"],
}

# 英文写法 → 中文术语（自动反向）
_REVERSE = {}
for _cn, _ens in GLOSSARY.items():
    for _en in _ens:
        _REVERSE.setdefault(normalize_key := _en.lower(), []).append(_cn)


def normalize(text: str) -> str:
    """归一化：小写、把标点与空白统一成单空格，便于子串比较。"""
    s = str(text or "").lower()
    s = re.sub(r"[\s\u3000]+", " ", s)
    s = re.sub(f"[^{_CJK}a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def cjk_bigrams(run: str):
    """把一个中文串切成 bigram；长度 1 时返回该字本身。"""
    if len(run) == 1:
        return [run]
    return [run[i:i + 2] for i in range(len(run) - 1)]


def _cn_forms(run: str):
    """一个中文串的全部中文写法：整串 + bigram + 对照表补充的中文词。"""
    forms = [run] + cjk_bigrams(run)
    return forms


def _en_forms_for_cn(run: str):
    """中文串对应的英文写法：整串命中对照表，或任一 bigram 命中对照表。"""
    out = []
    if run in GLOSSARY:
        out.extend(GLOSSARY[run])
    for bg in cjk_bigrams(run):
        if bg in GLOSSARY:
            out.extend(GLOSSARY[bg])
    return out


def _cn_forms_for_en(word: str):
    """英文词对应的中文写法（反向查表）"""
    return list(_REVERSE.get(word.lower(), []))


def parse_concepts(query: str):
    """把查询解析成**概念分组**：每个概念有一组同义写法，任一命中即算命中。

    返回 [[写法, 写法, ...], ...]。这是打分的单位 —— 用「概念」而不是「词」，
    中英对照才有意义：「循环伏安」和 "cyclic voltammetry" 是同一个概念。
    """
    q = normalize(query)
    if not q:
        return []
    concepts = []

    # 中文串
    for run in _CJK_RUN.findall(q):
        if run in _STOP:
            continue
        forms = _cn_forms(run) + _en_forms_for_cn(run)
        # 反向：中文写法也补上（例如整串是 bigram 的扩展时）
        seen, uniq = set(), []
        for f in forms:
            f = normalize(f)
            if f and f not in seen:
                seen.add(f)
                uniq.append(f)
        if uniq:
            concepts.append(uniq)

    # 拉丁词
    for w in _LATIN_RUN.findall(q):
        if len(w) < 2 or w in _STOP:
            continue          # 单字母拉丁词是噪声（「g 因子」的 g 会匹配一切）
        forms = [w] + [normalize(x) for x in _cn_forms_for_en(w)]
        # 英文短语本身也作为写法保留（多词按子串匹配）
        seen, uniq = set(), []
        for f in forms:
            if f and f not in seen:
                seen.add(f)
                uniq.append(f)
        if uniq:
            concepts.append(uniq)

    return concepts


def all_forms(query: str):
    """查询涉及的全部写法（扁平、去重）—— 给 SQL 预筛用。

    必须包含对照表展开的写法，否则英文的 cyclic voltammetry 根本进不了候选集。
    """
    out, seen = [], set()
    for group in parse_concepts(query):
        for f in group:
            if f not in seen:
                seen.add(f)
                out.append(f)
    return out


def tokenize(query: str):
    """基础分词（不含对照表展开）：返回 (词表, 短语)。

    保留这个函数是因为「生成片段」只需要最朴素的原词，
    用展开后的英文写法去找中文片段的位置反而会找错。
    """
    q = normalize(query)
    if not q:
        return [], ""
    terms = []
    for w in _LATIN_RUN.findall(q):
        if len(w) >= 2 and w not in _STOP:
            terms.append(w)
    for run in _CJK_RUN.findall(q):
        for bg in cjk_bigrams(run):
            if bg not in _STOP:
                terms.append(bg)
    seen, uniq = set(), []
    for t in terms:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq, q


def score_fields(concepts, phrase, fields, weights=None):
    """给一条记录的多个字段打分。

    concepts: parse_concepts() 的结果（概念分组）
    fields:   {'title': str, ...}
    weights:  字段权重表；默认 FIELD_WEIGHTS，图片检索传 FIGURE_WEIGHTS
    返回 (总分, 命中字段列表, 明细)
    """
    if not concepts:
        return 0.0, [], {}
    w_map = weights if weights is not None else FIELD_WEIGHTS
    total = 0.0
    matched = []
    detail = {}
    # 遍历**实际传入的字段**（而不是遍历权重表）：
    # 否则字段名和权重表对不上时会静默返回 0 —— 不报错但什么都搜不到，极难排查。
    # 权重表只负责「相对重要性」，缺项按 1.0 处理。
    for name, raw in fields.items():
        weight = float(w_map.get(name, 1.0))
        if not raw:
            continue
        if name in LIGHT_FIELDS:
            text = str(raw).lower()
        else:
            text = normalize(raw)
        if not text:
            continue
        # 概念命中数：一个概念里任一写法出现即算命中
        hits = sum(1 for group in concepts if any(f in text for f in group))
        if hits == 0:
            continue
        coverage = hits / len(concepts)
        s = coverage
        if phrase and len(phrase) >= 2 and phrase in text:
            s += 0.8                      # 整串连续出现：质量最高
        elif hits == len(concepts):
            s += 0.3                      # 所有概念都在，但不是连续短语
        total += weight * s
        matched.append(name)
        detail[name] = {"hits": hits, "of": len(concepts),
                        "coverage": round(coverage, 3),
                        "score": round(weight * s, 3)}
    return total, matched, detail


def rank_items(items, query, field_map=None, weights=None, labels=None):
    """按相关性排序，并写入 relevance / matched_fields / match_reason。

    items 必须已经带了要打分的字段（调用方从库里取）。
    field_map: {打分字段名: items 里的键名}；默认文献三字段，图片检索传 FIGURE_FIELDS。
    返回新列表（不改原对象），只保留相关度 > 0 的条目，按分数降序。
    """
    concepts = parse_concepts(query)
    if not concepts:
        return []
    phrase = normalize(query)

    fmap = field_map if field_map is not None else PAPER_FIELDS
    w_map = weights if weights is not None else FIELD_WEIGHTS
    lab = labels if labels is not None else FIELD_LABELS
    max_total = sum(w_map.values()) * _MAX_PER_FIELD or 1.0

    out = []
    for it in items:
        fields = {name: (it.get(key) or "") for name, key in fmap.items()}
        total, matched, detail = score_fields(concepts, phrase, fields, weights=w_map)
        if total <= 0:
            continue
        rec = dict(it)
        rec["_score"] = total
        rec["matched_fields"] = matched
        rec["match_detail"] = detail
        rec["relevance"] = round(min(100.0, 100.0 * total / max_total), 1)
        rec["match_reason"] = " + ".join(lab.get(m, m) for m in matched) + "命中"
        out.append(rec)

    out.sort(key=lambda r: (-r["_score"], -int(r.get("year") or 0)))
    return out


def explain(query: str) -> dict:
    """调试/展示用：说明查询被如何理解（含对照表展开）。"""
    concepts = parse_concepts(query)
    terms, phrase = tokenize(query)
    return {
        "flow": str(query or ""),
        "terms": terms,
        "phrase": phrase,
        "term_count": len(concepts),
        "concepts": concepts,
        "forms": all_forms(query),
        "expanded": [g for g in concepts if len(g) > 1],
    }


def make_snippet(text: str, terms, width: int = 90, max_len: int = 240) -> str:
    """截取包含查询词的片段，让用户看到「为什么这条相关」。

    找到第一个命中的词，从它前面一点开始截取，前后各留上下文。
    找不到就把开头截一段。
    """
    raw = str(text or "")
    if not raw:
        return ""
    light = raw.lower()
    pos = -1
    for t in terms or []:
        i = light.find(t)
        if i >= 0 and (pos < 0 or i < pos):
            pos = i
    if pos < 0:
        s = re.sub(r"\s+", " ", raw).strip()
        return (s[:max_len] + "…") if len(s) > max_len else s
    start = max(0, pos - width // 3)
    end = min(len(raw), start + max_len)
    s = re.sub(r"\s+", " ", raw[start:end]).strip()
    return ("…" if start > 0 else "") + s + ("…" if end < len(raw) else "")
