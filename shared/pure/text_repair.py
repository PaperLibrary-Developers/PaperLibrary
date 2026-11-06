"""共享内核 · 文本修复（移植自 Sci-lib shared/pure/text-repair.js）。

契约：PDF 文本层清洗（连字断裂 / 连字符断词 / 破折号空格 / 多余空白），供全文检索与摘要抽取共用。
零 I/O、零全局态。

导出：repair_ligatures / tidy_dashes / tidy_spacing / join_hyphen_breaks / build_word_set /
      repair_text（别名 repair）/ LIGATURES
"""
from __future__ import annotations

import re

# 常见连字组合（按长度从长到短，避免 ffi 被 fi 抢先吃掉）
LIGATURES = ("ffi", "ffl", "ff", "fi", "fl", "ft", "fj", "st")
_LIG_ALT = "|".join(LIGATURES)

# Python 的 re 没有 \p{L}：用「非非字母」的写法近似（字母，排除数字与下划线）
LETTER = r"[^\W\d_]"

# 常见虚词/短词：紧跟其后的「连字」是新词开头，不能把这个空格吃掉
_COMMON_WORD_RE = re.compile(
    r"^(?:the|a|an|of|in|on|at|to|for|with|by|as|is|are|was|were|be|been|being|has|have|had|"
    r"do|does|did|we|our|us|you|your|it|its|this|that|these|those|and|or|but|not|no|can|may|"
    r"might|must|will|would|should|such|than|then|which|who|whose|from|into|over|under|between|"
    r"their|they|them|there|here|also|more|most|other|some|all|both|each|any|so|if|when|while|"
    r"because|however|thus|therefore|only|very|well|two|three|one|new|use|used|using|see|was)$",
    re.IGNORECASE,
)

_LIG_MID_RE = re.compile(r"(" + LETTER + r"+)\s+(" + _LIG_ALT + r")\s+(" + LETTER + r")", re.UNICODE)
_LIG_HEAD_RE = re.compile(
    r'(^|[.;:!?\u2014\u2013(\["' + "'" + r'\u201c])\s*(' + _LIG_ALT + r")\s+(" + LETTER + r")",
    re.UNICODE,
)
_DASH_RE = re.compile(r"(" + LETTER + r"|\d)\s*-\s*(" + LETTER + r"|\d)", re.UNICODE)
_LONG_DASH_RE = re.compile(r"\s*[\u2212\u2013\u2014]\s*")
_SPACES_RE = re.compile(r"\s+")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,.;:!?%)\]])")
_SPACE_AFTER_OPEN_RE = re.compile(r"([(\[])\s+")
_WORD4_RE = re.compile(LETTER + r"{4,}", re.UNICODE)
# 与 JS 一致：连字符必须紧跟前面的词（"con- tainer"），不认 "con - tainer"
_HYPHEN_BREAK_RE = re.compile(r"(" + LETTER + r"+)[-]\s+(" + LETTER + r"+)", re.UNICODE)


def _s(value) -> str:
    return "" if value is None else str(value)


def repair_ligatures(text) -> str:
    """修连字断裂：'e ffi cient' → 'efficient'，'the fi nding' → 'the finding'。"""
    s = _s(text)
    if not s:
        return ""
    for _ in range(3):
        before = s
        # ① 词 + 连字 + 词（前一个碎片若是完整虚词，只接连字与后一个碎片）
        s = _LIG_MID_RE.sub(
            lambda m: (m.group(1) + " " if _COMMON_WORD_RE.match(m.group(1)) else m.group(1))
            + m.group(2)
            + m.group(3),
            s,
        )
        # ② 真正的词首：行首，或紧跟在句末标点/括号/引号之后
        s = _LIG_HEAD_RE.sub(r"\1\3", s)   # JS 的 '$1$3$4' 里 $4 不存在，等价于 起始符+字母
        if s == before:
            break
    return s


def tidy_dashes(text) -> str:
    """修「破折号/连字符两侧的空格」和上下标残渣：'well - defined' → 'well-defined'。"""
    s = _DASH_RE.sub(r"\1-\2", _s(text))
    # 真正的破折号（– — −）统一成两侧带空格的短横，避免把英文单词黏在一起
    return _LONG_DASH_RE.sub(" \u2212 ", s)


def tidy_spacing(text) -> str:
    """收拢空白与标点前的空格。"""
    s = _SPACES_RE.sub(" ", _s(text))
    s = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", s)
    s = _SPACE_AFTER_OPEN_RE.sub(r"\1", s)
    return s.strip()


def build_word_set(text):
    """建「本文里真实出现过的词」清单（只收长度 ≥4 的字母词，小写）。"""
    return set(_WORD4_RE.findall(_s(text).lower()))


def join_hyphen_breaks(text, vocab) -> str:
    """修连字符断行：拼合词在词表里出现过才接（con- tainer → container），否则保留。"""
    if not vocab:
        return _s(text)

    def repl(m):
        head, tail = m.group(1), m.group(2)
        return head + tail if (head + tail).lower() in vocab else head + "-" + tail

    return _HYPHEN_BREAK_RE.sub(repl, _s(text))


def repair_text(text, vocab=None) -> str:
    """一次性做完：连字 → 断词（给了 vocab 才做）→ 破折号 → 空白。"""
    s = repair_ligatures(text)
    if vocab:
        s = join_hyphen_breaks(s, vocab)
    return tidy_spacing(tidy_dashes(s))


# 契约别名（docs §3.5 写的是 repair(text)）
repair = repair_text
