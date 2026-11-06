"""领域层 · 与文献对话 / 识图 / 抄录。

移植自 Sci-lib server/domain/chat.js（318 行）。
三条链路都是 SSE 流式，编排都在这里：
  · chat_stream    组装资料包（题录 / 正文 / SI / 本库相关 / 联网）→ 调模型 → 写回对话历史
  · vision_stream  识图提问（框选图/表或贴图）→ 调多模态模型 → 写回对话历史
  · transcribe     把一页的渲染图抄录成文字（扫描件 OCR 用，不落盘）

约定（与 Node 版一致）：
  · 依赖一律从 services 取（llm / fulltext / library / websearch / external / prefs /
    pick_passages），本层不 import api、不碰 req/res、不决定 HTTP 状态码；
  · 校验失败返回 {"ok": False, "status": n, "error": msg}；SSE 只在校验全通过后 open()。
"""
from __future__ import annotations

import re

from paperlib.domain.ai import build_excerpt, read_attachment_text, safe_extract
from paperlib.util import CJK_RE, now_iso, tokenize

CHAT_SYSTEM_PROMPT = "\n".join([
    "你是这位研究者的文献助手。你可以看到一个固定的资料包：某篇文献的题录、它的正文节选、它的补充材料（SI）节选，",
    "以及可选的「本库相关文献」「网络资料（Exa 检索到的网页正文片段）」「网络深度检索（带引用的答案）」。",
    "",
    "回答要求：",
    "1. 只依据资料包回答；资料里没有的信息，明确说「资料里没有」，不要编造数据、结论或引用；",
    "2. 用中文回答，简洁、专业；需要时用 Markdown 列表或小标题；",
    "3. 引用资料时标明出处，例如「（正文）」「（SI）」「（本库：某标题）」「（网络资料 2）」，方便对方回去核对；引用网络资料时尽量带上编号，必要时附网址；",
    "4. 如果问题与这篇文献无关，直接说明这篇资料里查不到，并建议换个问法；",
    "5. 涉及数字、单位、结论时，原文怎么写的就怎么复述，不要换算或推测；",
    "6. **公式与化学式一律用 LaTeX**：行内用 $…$（例如 $K_a$、$\\Delta G^{\\circ}$），独立成行用 $$…$$（例如 $$\\Delta G = -RT\\ln K$$），",
    "   化学式用 \\ce{…}（例如 $\\ce{Fe^{II}4L6}$）。不要用 Unicode 下标或纯文本去凑公式，前端会把这些 LaTeX 排版成公式。",
    "7. **数学模式之外不要写 LaTeX 命令**：斜体用 Markdown 的 *斜体*（例如 *Cornus officinalis*）、加粗用 **加粗**。",
    "   「\\textit{}」「\\textbf{}」这类命令只在 $…$ 里面才认，裸写会原样显示成 \"\\textit{…}\"。",
])

VISION_SYSTEM_PROMPT = "\n".join([
    "你是严谨的科研助手，能看图片。用户会给你从论文里截出来的插图、表格或照片（可能是电镜/形貌图、晶体结构、示意图、谱图、表格）。",
    "回答要求：",
    "1. 先判断这是什么类型的图，再说内容：图里有什么、坐标轴/比例尺/标注文字分别是什么。",
    "2. 如果是表格，说明列标题与关键数据；如果是电镜/形貌图，描述形貌特征（形状、大小量级、是否聚集、有无孔道或片层）。",
    "3. 只依据图片本身和给出的资料回答。看不清、被裁掉、或图片分辨率不足时，明确说「看不清/不确定」，不要猜数值。",
    "4. 用中文回答，条理清晰，不要客套话。",
    "5. 提到公式或化学式时用 LaTeX：行内 $…$、独立成行 $$…$$、化学式 \\ce{…}（例如 $\\ce{Fe^{II}4L6}$），不要用纯文本凑下标。",
    "6. 数学模式之外不要写 LaTeX 命令：斜体用 *斜体*、加粗用 **加粗**（例如 *Cornus officinalis*）；",
    "   「\\textit{}」「\\textbf{}」只在 $…$ 里才认，裸写会原样显示成 \"\\textit{…}\"。",
])

TRANSCRIBE_SYSTEM_PROMPT = (
    "你是文献数字化助手。把用户给的这一页论文图片**完整抄录**成文字：保留标题、作者、章节小标题、正文、"
    "图表标题与图注；表格用 Markdown 表格输出；"
    "**数学公式与化学式一律用 LaTeX 抄录**：行内用 $…$（例如 $K_a$、$\\Delta G^{\\circ}$、$\\ce{Fe^{II}4L6}$），"
    "独立成行的公式用 $$…$$ 包起来（例如 $$\\Delta G = -RT\\ln K$$），"
    "化学式用 \\ce{…}（例如 $\\ce{C6H4}$、$\\ce{Fe^{II}4L6}$）；不要把公式写成纯文本、不要用 Unicode 下标去凑。"
    "不要翻译、不要总结、不要添加任何解释或前后缀。如果这一页确实没有文字（纯图片页），只回复：（本页无文字）。"
)

WEB_TRIGGER_RE = re.compile(
    r"相关研究|相关工作|别人|其他研究|最新|进展|review|综述|文献|有没有人|similar|related", re.I)
IMAGE_RE = re.compile(r"^data:image/", re.I)

# 关键词里太常见的词，命中一片反而没信息量
STOP_WORDS = frozenset("""
the and for with that this from are was were have has had not but you your can will would should
what which when where how why does did been into than then they them their there about also such
some any all one two use used using between
""".split())


# ---------------- 片段挑选（T1 的 pick_passages 未就绪时的本地等价实现） ----------------
def _anchor_patterns(question):
    out = []
    seen = set()
    for match in re.finditer(
            r"(?:^|[^A-Za-z0-9])([A-Za-z]{1,3})\s*[-–−—]?\s*(\d{1,4})(?!\d)", str(question or "")):
        letter, digits = match.group(1), match.group(2)
        key = letter.lower() + digits
        if key in seen:
            continue
        seen.add(key)
        out.append(re.compile(re.escape(letter) + r"\s*[-–−—]?\s*" + digits + r"(?!\d)", re.I))
    return out


def _keywords(question):
    out = []
    seen = set()
    for token in tokenize(str(question or "")):
        if len(token) < 2:
            continue
        is_cjk = bool(CJK_RE.search(token))
        if not is_cjk and (len(token) < 3 or token in STOP_WORDS):
            continue
        if token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _find_hits(text, question, limit=40):
    lower = text.lower()
    hits = set()
    anchors = 0
    for pattern in _anchor_patterns(question):
        for count, match in enumerate(pattern.finditer(text)):
            if count >= 200:
                break
            hits.add(match.start())
            anchors += 1
    for keyword in _keywords(question):
        needle = keyword.lower()
        start = 0
        for _ in range(200):
            at = lower.find(needle, start)
            if at < 0:
                break
            hits.add(at)
            start = at + len(needle)
    return sorted(hits)[: int(limit or 40)], anchors


def _merge_windows(hits, radius):
    spans = []
    for at in hits:
        start = max(0, at - radius)
        end = at + radius
        if spans and start <= spans[-1][1] + 200:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


def local_pick_passages(text, question, budget=12000, head_chars=2500, tail_chars=800, radius=700):
    """开头 + 与问题相关的片段 + 结尾（总长仍在预算内）。"""
    src = "" if text is None else str(text)
    budget = max(1000, int(budget or 12000))
    if not src:
        return {"text": "", "used": 0, "passages": 0, "anchors": 0, "truncated": False}
    if len(src) <= budget:
        return {"text": src, "used": len(src), "passages": 1, "anchors": 0, "truncated": False}
    head_chars = max(200, min(int(head_chars or 2500), budget // 2))
    tail_chars = max(0, min(int(800 if tail_chars is None else tail_chars), budget // 4))
    radius = max(200, int(radius or 700))
    head = src[:head_chars]
    tail = src[-tail_chars:] if tail_chars else ""
    hits, anchors = _find_hits(src, question, 60)
    spends_left = budget - len(head) - len(tail)
    chunks = []
    if spends_left > radius:
        for start, end in _merge_windows(hits, radius):
            if start < head_chars:
                continue
            piece = src[start:end]
            if len(piece) > spends_left:
                chunks.append(piece[: max(0, spends_left)])
                spends_left = 0
                break
            chunks.append(piece)
            spends_left -= len(piece)
    segs = [head.strip()]
    if chunks:
        segs.append("……（以下是与问题相关的片段）……\n" + "\n……\n".join(c.strip() for c in chunks))
    if tail:
        segs.append("……（以下为文档结尾）……\n" + tail.strip())
    out = "\n\n".join([seg for seg in segs if seg])
    return {"text": out, "used": len(out), "passages": len(chunks),
            "anchors": anchors, "truncated": len(out) < len(src)}


def _normalize_picked(value):
    """把 T1 pick_passages 的返回（dict 或 list[dict]）规整成 {text, used, passages, anchors}。"""
    if isinstance(value, dict) and isinstance(value.get("text"), str):
        text = value["text"]
        return {"text": text, "used": int(value.get("used") or len(text)),
                "passages": int(value.get("passages") or 0), "anchors": int(value.get("anchors") or 0)}
    if isinstance(value, (list, tuple)):
        pieces = []
        for item in value:
            if isinstance(item, dict):
                piece = item.get("text") or item.get("content") or item.get("snippet") or ""
            else:
                piece = item or ""
            if str(piece).strip():
                pieces.append(str(piece))
        if pieces:
            text = "\n……\n".join(pieces)
            return {"text": text, "used": len(text), "passages": len(pieces), "anchors": 0}
    if isinstance(value, str) and value.strip():
        return {"text": value, "used": len(value), "passages": 1, "anchors": 0}
    return None


def pick_passages(services, text, question, budget, head_chars, tail_chars):
    """优先用 shared.pure.passages（T1），不可用或返回形状不符时退到本地实现。"""
    fn = getattr(services, "pick_passages", None)
    if callable(fn):
        try:
            picked = _normalize_picked(fn(text, question, {
                "budget": budget, "headChars": head_chars, "tailChars": tail_chars}))
            if picked:
                return picked
        except Exception:  # noqa: BLE001
            pass
    return local_pick_passages(text, question, budget=budget,
                               head_chars=head_chars, tail_chars=tail_chars)


# ---------------- 资料包 ----------------
def build_chat_context(services, paper, question, options=None):
    """把「题录 + 正文 + SI + 本库相关 + 联网」拼成资料包；返回 {context, sources, hasBody}。"""
    opts = options or {}
    paper_id = paper.get("id")
    parts = []
    used = []

    # 1. 题录
    meta = [
        "===== 文献题录 =====",
        "标题：" + str(paper.get("title") or "(无)"),
        "作者：" + (", ".join(paper.get("authors") or []) or "(未填)"),
        "年份：" + str(paper.get("year") or "(未填)"),
        "期刊/会议：" + str(paper.get("venue") or "(未填)"),
        "DOI：" + str(paper.get("doi") or "(未填)"),
        ("我写的一句话总结：" + str(paper.get("summary"))) if paper.get("summary") else "",
        ("原文关键词：" + ", ".join(paper.get("keywords") or [])) if paper.get("keywords") else "",
    ]
    parts.append("\n".join([line for line in meta if line]))

    # 2. 正文：按问题挑片段（开头 + 命中处 + 结尾），而不是无脑取头尾
    body_budget = services.prefs.limit("chatBodyChars")
    body_text = services.fulltext.read(paper_id) or ""
    if body_text:
        body_material = pick_passages(services, body_text, question, body_budget, 3000, 1200)["text"]
    else:
        body_material = build_excerpt(services, paper_id, body_budget)
    if body_material:
        parts.append("===== 这篇文献的正文节选 =====\n" + body_material)
        used.append("正文")

    # 3. 补充材料（SI）：同样按问题检索（问「S55 那一页」时不能只给开头）
    att_budget = services.prefs.limit("chatAttachChars")
    att_total = services.prefs.limit("chatAttachTotalChars")
    att_chars = 0
    for attachment in (paper.get("attachments") or []):
        if att_chars >= att_total:
            break
        text = read_attachment_text(services, paper_id, attachment.get("file"))
        if not text:
            continue
        budget = min(att_budget, att_total - att_chars)
        picked = pick_passages(services, text, question, budget, 2500, 800)
        if not picked["text"]:
            continue
        name = str(attachment.get("originalName") or "")
        parts.append("===== 补充材料：" + name + " =====\n" + picked["text"])
        att_chars += picked["used"]
        used.append("SI:" + name + ("（按问题检索到 %d 个片段）" % picked["passages"]
                                    if picked["passages"] else ""))

    # 4. 本库相关文献
    if opts.get("useLocal") is not False and question:
        try:
            hits = services.library.query({"q": question, "pageSize": 4, "all": False})
            items = [i for i in (hits.get("items") or []) if i.get("id") != paper_id]
            if items:
                lines = []
                for item in items:
                    lines.append("- %s（%s，%s）%s" % (
                        item.get("title"), item.get("year") or "年份未知",
                        item.get("venue") or "出处未知",
                        (" doi:" + str(item.get("doi"))) if item.get("doi") else ""))
                parts.append("===== 本库中可能相关的其它文献 =====\n" + "\n".join(lines))
                used.append("本库检索")
        except Exception:  # noqa: BLE001 - 检索索引未就绪时不影响对话
            pass

    # 5. 联网检索
    #    勾了「联网搜索」→ Exa（付费，最便宜的基础档）；没勾 → 免费的学术检索（OpenAlex 等）
    if opts.get("useExternal") and question and WEB_TRIGGER_RE.search(question):
        websearch = services.websearch
        exa_ready = (opts.get("web") is True
                     and websearch.settings.get("useInChat") is not False
                     and websearch.readiness()["ready"])
        if exa_ready:
            web = websearch.search(question, {"numResults": websearch.settings.get("numResults") or 4})
            if web.get("ok") and web.get("items"):
                lines = []
                for index, item in enumerate(web["items"]):
                    snippet = " … ".join(item.get("highlights") or [])[:600]
                    when = ("，" + str(item.get("published"))) if item.get("published") else ""
                    lines.append("[%d] %s（%s%s）\n    %s\n    片段：%s" % (
                        index + 1, item.get("title"), item.get("domain"), when,
                        item.get("url"), snippet or "（无正文片段）"))
                parts.append("===== 网络资料（Exa 检索，仅供参考，引用时请标注来源编号与网址）=====\n"
                             + "\n".join(lines))
                used.append("网络资料")
            elif web.get("error"):
                parts.append("（网络检索失败：" + str(web["error"]) + "）")
        else:
            try:
                found = services.external.search(question, {"limit": 5, "page": 1})
                doi = str(paper.get("doi") or "").lower()
                items = [i for i in (found.get("items") or [])
                         if not doi or str(i.get("doi") or "").lower() != doi]
                if items:
                    lines = []
                    for item in items:
                        tail = ("，被引 " + str(item.get("citations"))) if item.get("citations") is not None else ""
                        lines.append("- %s（%s，%s%s）%s" % (
                            item.get("title"), item.get("year") or "年份未知",
                            item.get("venue") or "出处未知", tail,
                            (" doi:" + str(item.get("doi"))) if item.get("doi") else ""))
                    parts.append("===== 联网检索到的相关文献（仅供参考，不是本库内容）=====\n"
                                 + "\n".join(lines))
                    used.append("联网检索")
            except Exception as exc:  # noqa: BLE001
                parts.append("（联网检索失败：" + str(exc) + "）")

    return {"context": "\n\n".join(parts), "sources": used, "hasBody": bool(body_text)}


# ---------------- 对话 ----------------
def _readiness_error(services, suffix="（到「设置 → 大模型」里填写）"):
    readiness = services.llm.readiness()
    if readiness["ready"]:
        return None
    return {"ok": False, "status": 400,
            "error": "还没有配置大模型：" + str(readiness.get("reason") or "") + suffix}


def _auto_extract(services, paper):
    if services.fulltext.has(paper.get("id")) or not paper.get("pdfFile"):
        return None
    return safe_extract(services, paper)


def _append_chat(services, paper, messages):
    services.library.update(paper.get("id"), {"chat": list(messages)})
    fresh = services.library.get(paper.get("id")) or {}
    return list(fresh.get("chat") or [])


def chat_stream(services, paper, body, stream):
    """POST /api/papers/:id/chat：资料包 → 模型 → 问答都写进这篇文献的对话历史。"""
    options = body or {}
    question = str(options.get("question") or "").strip()
    if not question:
        return {"ok": False, "status": 400, "error": "请输入问题"}
    failed = _readiness_error(services)
    if failed:
        return failed

    auto_extract = _auto_extract(services, paper)
    built = build_chat_context(services, paper, question, {
        "useLocal": options.get("useLocal") is not False,
        "useExternal": options.get("useExternal") is not False,
        # 只有用户勾了「联网搜索」才走 Exa（付费）；否则用免费学术检索
        "web": options.get("web") is True,
    })

    fresh = services.library.get(paper.get("id")) or paper
    current = list(fresh.get("chat") or [])
    history = current[-12:]
    _append_chat(services, paper, current + [
        {"role": "user", "content": question, "at": now_iso()},
    ])

    llm = services.llm
    send = stream.open()
    send({
        "type": "start",
        "model": str(llm.settings.get("model") or ""),
        "sources": built["sources"],
        "hasBody": built["hasBody"],
        "autoExtract": auto_extract,
    })
    collected = []

    def on_delta(chunk):
        collected.append(chunk)
        send({"type": "delta", "text": chunk})

    try:
        messages = [
            {"role": "system", "content": CHAT_SYSTEM_PROMPT},
            {"role": "system", "content": "资料包如下：\n\n" + built["context"]},
        ]
        for message in history:
            messages.append({"role": message.get("role") or "user",
                             "content": str(message.get("content") or "")})
        messages.append({"role": "user", "content": question})
        result = llm.chat_stream(on_delta, {"messages": messages})
        answer = str((result or {}).get("text") or "".join(collected)).strip()
        if answer:
            latest = services.library.get(paper.get("id")) or paper
            history_now = list(latest.get("chat") or [])
            _append_chat(services, paper, history_now + [{
                "role": "assistant",
                "content": answer,
                "at": now_iso(),
                "model": str(llm.settings.get("model") or ""),
                "sources": built["sources"],
            }])
        send({"type": "done", "text": answer, "sources": built["sources"],
              "usage": (result or {}).get("usage") or None})
    except Exception as exc:  # noqa: BLE001
        send({"type": "error", "message": str(exc)})
    return {"ok": True}


def vision_stream(services, paper, body, stream):
    """POST /api/papers/:id/vision：识图提问。图片本身不入库（只记「含 N 张图」）。"""
    options = body or {}
    question = str(options.get("question") or "").strip() or "解释这张图/表说明了什么。"
    raw_images = options.get("images") if isinstance(options.get("images"), list) else []
    images = [str(u) for u in raw_images if IMAGE_RE.match(str(u))][:4]
    if not images:
        return {"ok": False, "status": 400, "error": "没有收到图片"}
    failed = _readiness_error(services)
    if failed:
        return failed

    llm = services.llm
    bits = [
        "标题：" + str(paper.get("title") or "（无）"),
        "期刊/会议：" + str(paper.get("venue") or "（未填）") + "　年份：" + str(paper.get("year") or "（未填）"),
    ]
    if paper.get("summaryZh"):
        bits.append("中文摘要：" + str(paper.get("summaryZh"))[:800])
    elif paper.get("summary"):
        bits.append("摘要：" + str(paper.get("summary"))[:800])
    if paper.get("aiSummary"):
        bits.append("已有总结（节选）：" + str(paper.get("aiSummary"))[:800])
    excerpt = build_excerpt(services, paper.get("id"), 1500)
    label = str(options.get("label") or "")

    send = stream.open()
    send({"type": "start", "model": str(llm.settings.get("model") or ""),
          "images": len(images), "label": label})

    collected = []

    def on_delta(chunk):
        collected.append(chunk)
        send({"type": "delta", "text": chunk})

    content = [{
        "type": "text",
        "text": "我给的图片是从这篇文献里截出来的。" + question
                + (("\n\n（可供参考的正文节选，不一定与图片直接相关）\n" + excerpt) if excerpt else ""),
    }] + [{"type": "image_url", "image_url": {"url": url}} for url in images]

    usage = None
    try:
        result = llm.stream(on_delta, {
            "messages": [
                {"role": "system", "content": VISION_SYSTEM_PROMPT
                                                + "\n\n【这篇文献的背景资料】\n" + "\n".join(bits)},
                {"role": "user", "content": content},
            ],
            "maxTokens": 1600,
            "timeoutMs": 180000,
        })
        usage = (result or {}).get("usage") or None
    except Exception as exc:  # noqa: BLE001
        send({"type": "error", "message": str(exc)})
        return {"ok": True}

    answer = "".join(collected).strip()
    fresh = services.library.get(paper.get("id")) or paper
    messages = list(fresh.get("chat") or []) + [
        {"role": "user", "content": question + (("\n（" + label + "）") if label else ""),
         "at": now_iso(), "images": len(images)},
        {"role": "assistant", "content": answer, "at": now_iso(),
         "model": str(llm.settings.get("model") or ""),
         "sources": ["图片%d张" % len(images)], "usage": usage},
    ]
    saved = _append_chat(services, paper, messages)
    send({"type": "done", "messages": saved})
    return {"ok": True}


def transcribe(services, paper, body):
    """POST /api/papers/:id/transcribe：把一页渲染图抄录成文字（逐页调用，不落盘）。"""
    options = body or {}
    image = str(options.get("image") or "")
    if not IMAGE_RE.match(image):
        return {"ok": False, "status": 400, "error": "没有收到图片"}
    failed = _readiness_error(services, "")
    if failed:
        return failed
    page = int(options.get("page") or 0)
    try:
        collected = []
        result = services.llm.stream(lambda chunk: collected.append(chunk), {
            "messages": [
                {"role": "system", "content": TRANSCRIBE_SYSTEM_PROMPT},
                {"role": "user", "content": [
                    {"type": "text", "text": "请抄录这一页（第 %d 页）。" % (page or 1)},
                    {"type": "image_url", "image_url": {"url": image}},
                ]},
            ],
            "maxTokens": 3000,
            "timeoutMs": 180000,
        })
        text = str((result or {}).get("text") or "".join(collected))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "status": 502, "error": "识别失败：" + str(exc)}
    return {"ok": True, "page": page, "text": text.strip()}
