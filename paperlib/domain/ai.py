"""领域层 · AI 总结编排。

移植自 Sci-lib server/domain/ai.js（228 行）。
    · summary_zh        中文一句话总结（translate / generate 两种模式）
    · summary_zh_batch  给全库缺中文总结的文献补一遍
    · summarize_stream  AI 总结（SSE 帧：start / delta / done / error）
    · summarize_sync    AI 总结（同步，给脚本与测试用）

约定（与 Node 版一致）：
  · 依赖一律从 services 取（llm / fulltext / library / db），本层不 import api，也不碰
    req/res、不决定 HTTP 状态码——那些是 api 层的事；
  · 校验失败返回 {"ok": False, "status": n, "error": msg}，由 api 层翻成状态码与错误体；
  · SSE：调用方传 stream（有 open() -> send），本层在**所有校验通过之后**才 open()，
    保证「参数/配置错误发生在开流之前」这条契约不变。
"""
from __future__ import annotations

import re

from paperlib.adapters.llm import strip_echoed_material
from paperlib.domain import fullread
from paperlib.util import now_iso

PROMPT_CHARS = 12000          # 送给大模型的正文上限
# 输出预算下限：思考型模型会先花掉一部分预算，用户设置里的 maxTokens（默认 1200）
# 常常不够。结构化总结要写四节 Markdown（实测约 1500–2500 字），给 4000；
# 一句话总结/翻译较短，给 2000。这只是上限，不会强制消耗。
SUMMARY_MIN_MAX_TOKENS = 4000
SUMMARY_ZH_MIN_MAX_TOKENS = 2000
ATT_PART_CHARS = 4000         # 单个补充材料片段上限
ATT_TOTAL_CHARS = 12000       # 补充材料合计上限
ZH_RE = re.compile(r"[\u4e00-\u9fff]")

SYSTEM_SUFFIX = (
    "你是一名严谨的学术助手，只输出中文正文。"
    "涉及公式或化学式时用 LaTeX：行内 $…$、独立成行 $$…$$、化学式 \\ce{…}（例如 $\\ce{Fe^{II}4L6}$）。"
    "数学模式之外不要写 LaTeX 命令：斜体用 *斜体*、加粗用 **加粗**（\\textit{} 只在 $…$ 里才认）。"
)


def build_excerpt(services, paper_id, limit=PROMPT_CHARS):
    """给大模型用的正文节选：优先开头（摘要/引言），再补结尾（结论）。"""
    fulltext = services.fulltext
    fn = getattr(fulltext, "build_prompt_excerpt", None)
    if callable(fn):
        try:
            return fn(paper_id, limit) or ""
        except Exception:  # noqa: BLE001 - 适配器未就绪时退到 read()
            pass
    text = fulltext.read(paper_id) or ""
    if not text:
        return ""
    maximum = int(limit or PROMPT_CHARS)
    if len(text) <= maximum:
        return text
    head = text[: int(maximum * 0.75)]
    tail = text[-int(maximum * 0.25):]
    return head + "\n\n……（中间略）……\n\n" + tail


read_attachment_text = fullread.read_attachment_text


def attachment_parts(services, paper, per_part=ATT_PART_CHARS, total=ATT_TOTAL_CHARS):
    """补充材料的资料段（有 SI 时总结更完整）；每段截前 per_part 字，合计不超过 total。"""
    parts = []
    used = 0
    for attachment in (paper.get("attachments") or []):
        if used >= total:
            break
        text = read_attachment_text(services, paper.get("id"), attachment.get("file"))
        if not text:
            continue
        piece = str(text)[:per_part]
        parts.append("===== 补充材料：" + str(attachment.get("originalName") or "") + " =====\n" + piece)
        used += len(piece)
    return parts


def safe_extract(services, paper):
    """自动补抽 PDF 正文（失败不抛错，把结果塞进 SSE 的 autoExtract）。"""
    fn = getattr(services, "extract_paper_fulltext", None)
    if not callable(fn):
        return None
    try:
        return fn(paper)
    except NotImplementedError:
        return {"ok": False, "error": "PDF 正文抽取引擎还没就绪"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def _model_name(services):
    settings = getattr(services.llm, "settings", None)
    if not isinstance(settings, dict):
        return ""
    return str(settings.get("model") or "")


def _meta_line(paper):
    return (
        "标题：" + str(paper.get("title") or "（无）")
        + "\n作者：" + (", ".join(paper.get("authors") or []) or "（未填）")
        + "\n年份：" + str(paper.get("year") or "（未填）")
        + "\n期刊/会议：" + str(paper.get("venue") or "（未填）")
    )


# ---------------------------------------------------------------- 全文总结
SUMMARY_MAP_SYSTEM = (
    "你是严谨的学术助手。下面给你同一篇论文（正文或补充材料 SI）的一个片段。"
    "请提炼这个片段中可用于撰写中文总结的事实：研究问题、方法、关键实验条件与数据"
    "（数字与单位照抄）、结论、局限。只依据本片段，不要编造；"
    "本片段没有有用信息时只输出「本段无有效信息」。"
)

SUMMARY_FULL_NOTE = (
    "【本次输入】下面不是节选：正文与全部补充材料（SI）已完整读入；"
    "超过单次上下文的部分已按段提炼并汇总，覆盖全文。不要声称内容被截断。"
)


def _summary_map_user(paper, chunk, index, total):
    return (
        "论文标题：" + str(paper.get("title") or "（无）")
        + "\n片段来源：" + str(chunk.get("label") or "")
        + "（第 %d/%d 段）" % (index, total)
        + "\n\n" + str(chunk.get("text") or "")
    )


def _summary_prepare(services, paper, use_fulltext):
    if not use_fulltext:
        coverage = {
            "mode": "none", "bodyChars": 0, "siChars": 0, "totalChars": 0,
            "attachCount": 0, "attachments": [], "missing": [], "chunks": 0,
            "processedChunks": 0, "chunkChars": 0, "directLimit": fullread.DIRECT_LIMIT,
        }
        return {"mode": "none", "materials": [], "chunks": [], "totalChars": 0,
                "coverage": coverage}
    return fullread.prepare(services, paper)


def _output_budget(llm, minimum):
    """请求的 max_tokens：用户配置与「本次调用的下限」取大者。"""
    try:
        configured = int(llm.settings.get("maxTokens") or 1200)
    except (TypeError, ValueError):
        configured = 1200
    return max(int(minimum), configured)


def _run_summary(services, paper, options, emit=None, on_delta=None, auto_extract=None):
    """完整读入正文 + 全部 SI 后总结；返回 {"text","usage","coverage"}。"""
    llm = services.llm
    use_fulltext = options.get("useFulltext") is not False
    prepared = _summary_prepare(services, paper, use_fulltext)
    coverage = prepared["coverage"]
    total = prepared["totalChars"]

    if emit:
        emit({"type": "start", "model": _model_name(services),
              "usedFulltext": bool(total), "chars": total,
              "autoExtract": auto_extract, "fullRead": coverage})

    if total:
        if prepared["mode"] == "direct":
            material = fullread.direct_text(prepared)
            label = "全文（正文 + 全部补充材料 SI，未截断）"
        else:
            def progress(index, count, chunk):
                if emit:
                    emit({"type": "progress", "stage": "map", "done": index - 1,
                          "total": count, "label": chunk.get("label")})

            notes = fullread.map_chunks(
                llm, prepared["chunks"], SUMMARY_MAP_SYSTEM,
                lambda chunk, index, count: _summary_map_user(paper, chunk, index, count),
                on_progress=progress,
            )
            coverage["processedChunks"] = len(notes)
            material = ("【全文分段提炼，覆盖正文与全部 SI】\n\n"
                        + "\n\n".join("[" + str(n["label"]) + "]\n" + n["text"] for n in notes))
            label = "全文分段提炼（覆盖正文与全部补充材料 SI）"
            if emit:
                emit({"type": "progress", "stage": "map", "done": len(notes),
                      "total": len(notes), "label": ""})
    else:
        material = ""
        label = ""

    system = str(llm.settings.get("promptTemplate") or "")
    if material:
        note = SUMMARY_FULL_NOTE
        missing = coverage.get("missing") or []
        if missing:
            note += "\n注意：以下 SI 没有提取到文字，无法阅读：" + "、".join(missing)
        system = system + ("\n\n" if system else "") + note
    user = (llm.build_user_message(paper, material, label=label) if material
            else llm.build_user_message(paper, ""))
    result = llm.stream(on_delta, {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        # 总结比翻译长得多；给足输出预算，避免思考型模型把预算用光后截断/空回复。
        "maxTokens": _output_budget(llm, SUMMARY_MIN_MAX_TOKENS),
    })
    text = strip_echoed_material(str((result or {}).get("text") or ""))
    return {"text": text, "usage": (result or {}).get("usage") or None, "coverage": coverage}


def summary_zh(services, paper, options=None):
    """中文一句话总结。有英文摘要→忠实翻译（translate）；没有→依据题录/总结/正文写（generate）。

    结果写进 summaryZh 字段，所以只做一次：前端来回切「中文 / 原文」不再调模型。
    """
    opts = options or {}
    library = services.library
    llm = services.llm
    paper_id = paper.get("id")
    source = str(paper.get("summary") or "").strip()
    source_is_chinese = bool(ZH_RE.search(source))
    mode = str(opts.get("mode") or ("translate" if source and not source_is_chinese else "generate"))

    # 原文本来就是中文：直接用原文，不调模型
    if mode == "translate" and source_is_chinese:
        saved = library.update(paper_id, {
            "summaryZh": source,
            "summaryZhAt": now_iso(),
            "summaryZhModel": "（原文已是中文）",
        })
        return {"ok": True, "paper": saved, "translated": False, "reason": "原文已经是中文"}

    readiness = llm.readiness()
    if not readiness["ready"]:
        return {"ok": False, "status": 400,
                "error": "还没有配置大模型：" + str(readiness.get("reason") or "") + "（到「设置 → 大模型」里填写）"}

    # 组装资料：
    #   translate 只发「原文摘要」（翻译用不着正文，塞正文会被整段抄回输出）；
    #   generate（没有摘要）才需要 AI 总结与正文节选当素材。
    parts = []
    if source:
        parts.append("===== 原文摘要 =====\n" + source[:4000])
    if mode != "translate":
        if paper.get("aiSummary"):
            parts.append("===== 已生成的中文总结 =====\n" + str(paper.get("aiSummary"))[:3000])
        excerpt = build_excerpt(services, paper_id, 3000)
        if excerpt:
            parts.append("===== 正文节选 =====\n" + excerpt)
    rich_material = bool(parts)
    if not parts:
        if not str(paper.get("title") or "").strip():
            return {"ok": False, "status": 400, "error": "这条文献还没有标题，无法生成总结"}
        parts.append("（这篇文献没有摘要、也没有 PDF 正文，只有上面的题录信息）")

    if mode == "translate":
        instruction = ("把「原文摘要」忠实翻译成简洁、通顺的学术中文：保留专业术语、化学式、缩写与数字，"
                       "不增删信息，只输出译文本身，不要写解释或前后缀。")
    elif rich_material:
        instruction = ("根据下面提供的资料，写一段中文的一句话总结（1–3 句，60–160 字）："
                       "说明这篇文献解决了什么问题、用了什么方法、主要结论是什么。"
                       "只输出总结本身，不要标题、不要分点、不要解释。")
    else:
        instruction = ("这篇文献只有题录信息。请只依据标题、作者、期刊与年份，写一句保守的中文概括（40–100 字）："
                       "说明它大致研究什么主题即可，**绝对不要编造研究方法、数据或结论**；"
                       "信息确实不足时，就写成「（仅据标题）本文涉及……」这样的形式，只输出这一句。")

    collected = []
    try:
        result = llm.stream(lambda chunk: collected.append(chunk), {
            "messages": [
                {"role": "system", "content": SYSTEM_SUFFIX + instruction},
                {"role": "user", "content": _meta_line(paper) + "\n\n" + "\n\n".join(parts)},
            ],
            "timeoutMs": 120000,
            # 推理模型先输出思考内容，1200 常常不够一句话总结/翻译 + 思考；
            # 给足预算，避免空回复或半截译文。
            "maxTokens": _output_budget(llm, SUMMARY_ZH_MIN_MAX_TOKENS),
        })
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 502,
                "error": ("翻译" if mode == "translate" else "生成") + "失败：" + str(exc)}

    text = str((result or {}).get("text") or "".join(collected))
    produced = strip_echoed_material(text)
    if not produced:
        return {"ok": False, "status": 502, "error": "模型没有返回内容，稍后再试"}
    saved = library.update(paper_id, {
        "summaryZh": produced,
        "summaryZhAt": now_iso(),
        "summaryZhModel": _model_name(services),
    })
    return {"ok": True, "paper": saved, "translated": mode == "translate", "mode": mode,
            "chars": len(produced)}


def summary_zh_batch(services):
    """给全库还没有中文总结的文献补一份（逐条走同一套逻辑）。"""
    llm = services.llm
    readiness = llm.readiness()
    if not readiness["ready"]:
        return {"ok": False, "status": 400,
                "error": "还没有配置大模型：" + str(readiness.get("reason") or "") + "（到「设置 → 大模型」里填写）"}
    pending = [p for p in services.db.list_papers() if not str(p.get("summaryZh") or "").strip()]
    results = []
    for paper in pending:
        fresh = services.library.get(paper.get("id")) or paper
        try:
            one = summary_zh(services, fresh, {})
            results.append({"id": paper.get("id"), "title": paper.get("title"),
                            "ok": bool(one.get("ok")), "error": one.get("error") or ""})
        except Exception as exc:  # noqa: BLE001
            results.append({"id": paper.get("id"), "title": paper.get("title"),
                            "ok": False, "error": str(exc)})
    return {
        "ok": True,
        "total": len(pending),
        "succeeded": len([r for r in results if r["ok"]]),
        "failed": len([r for r in results if not r["ok"]]),
        "results": results,
    }


def summarize_stream(services, paper, body, stream):
    """AI 总结（SSE）：完整读入正文 + 全部 SI 后总结；start → delta* → done / error。"""
    options = body or {}
    llm = services.llm
    library = services.library
    paper_id = paper.get("id")
    readiness = llm.readiness()
    if not readiness["ready"]:
        return {"ok": False, "status": 400,
                "error": "还没有配置大模型：" + str(readiness.get("reason") or "") + "（到「设置 → 大模型」里填写）"}

    # 有 PDF 但还没提取过正文时先自动抽一次，否则会退化成「仅依据摘要」
    auto_extract = None
    if options.get("useFulltext") is not False and not services.fulltext.has(paper_id) and paper.get("pdfFile"):
        auto_extract = safe_extract(services, paper)

    send = stream.open()
    collected = []

    def on_delta(chunk):
        collected.append(chunk)
        send({"type": "delta", "text": chunk})

    try:
        result = _run_summary(services, paper, options, emit=send,
                              on_delta=on_delta, auto_extract=auto_extract)
        text = result["text"]
        saved = bool(text) and options.get("save") is not False
        if saved:
            library.update(paper_id, {
                "aiSummary": text,
                "aiSummaryAt": now_iso(),
                "aiSummaryModel": _model_name(services),
            })
        send({"type": "done", "text": text, "saved": saved,
              "usage": result.get("usage"), "fullRead": result["coverage"]})
    except Exception as exc:  # noqa: BLE001
        send({"type": "error", "message": str(exc)})
    return {"ok": True}


def summarize_sync(services, paper, body):
    """AI 总结（同步）：完整读入正文 + 全部 SI；{"ok","text","usedFulltext","usage","fullRead"}。"""
    options = body or {}
    llm = services.llm
    paper_id = paper.get("id")
    readiness = llm.readiness()
    if not readiness["ready"]:
        return {"ok": False, "status": 400,
                "error": "还没有配置大模型：" + str(readiness.get("reason") or "")}

    auto_extract = None
    if options.get("useFulltext") is not False and not services.fulltext.has(paper_id) and paper.get("pdfFile"):
        auto_extract = safe_extract(services, paper)
    try:
        result = _run_summary(services, paper, options, emit=None,
                              on_delta=None, auto_extract=auto_extract)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 502, "error": str(exc)}

    text = result["text"]
    if options.get("save") is not False and text:
        services.library.update(paper_id, {
            "aiSummary": text,
            "aiSummaryAt": now_iso(),
            "aiSummaryModel": _model_name(services),
        })
    return {"ok": True, "text": text, "usedFulltext": bool(result["coverage"]["totalChars"]),
            "usage": result.get("usage"), "fullRead": result["coverage"]}