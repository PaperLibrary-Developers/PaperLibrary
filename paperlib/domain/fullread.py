"""全文材料收集与分段覆盖（AI 总结 / 与文献对话）。

旧实现只给模型 1.2–2.4 万字的「节选」：正文取头尾、SI 每段取前 4000 字/合计 12000 字，
模型看不到实验细节和大部分 SI。这里把正文与**全部** SI 收集起来：
  · 总量 <= DIRECT_LIMIT：单次调用，完整送入，不截断；
  · 超过：按 CHUNK_CHARS 分段，每段都调用模型提炼（map），再汇总（reduce）；
  · 任一段读取/调用失败 -> FullReadError，由上层发错误帧，绝不静默跳过或声称已完整读入。
"""
from __future__ import annotations

import os

DIRECT_LIMIT = 40000      # 单次完整送入的字符上限（保守值，兼容 64k 上下文模型）
CHUNK_CHARS = 48000       # 分段上限（每段都完整送给模型）
MAP_MAX_TOKENS = 1000     # 单段提炼的输出上限


class FullReadError(RuntimeError):
    """全文读取/分段提炼失败：上层必须报错，不能降级成“已完整读入”。"""


def _response_text(response):
    if isinstance(response, dict):
        for key in ("text", "content", "message", "answer"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value
    if isinstance(response, str):
        return response
    return ""


def read_attachment_text(services, paper_id, file):
    """读 SI 文本：优先 fulltext.read_attachment，退到 store 的 att-<文件名> 键。"""
    fulltext = services.fulltext
    fn = getattr(fulltext, "read_attachment", None)
    if callable(fn):
        try:
            text = fn(paper_id, file) or ""
            if text:
                return str(text)
        except Exception:  # noqa: BLE001
            pass
    base = os.path.basename(str(file or ""))
    if not base:
        return ""
    store = services.db
    for key in ("att-" + base, "att-" + base + ".txt", "att-" + str(file)):
        try:
            text = store.get_text(key) or ""
        except Exception:  # noqa: BLE001
            text = ""
        if text:
            return str(text)
    return ""


def _attachment_path(services, file):
    store = services.db
    fn = getattr(store, "path_for_attachment", None)
    if callable(fn):
        try:
            return fn(file) or ""
        except Exception:  # noqa: BLE001
            return ""
    return ""


def _try_extract_attachment(services, paper, attachment):
    """旧数据/异常附件的兜底：重新抽一次 SI 文本，成功则顺手存进 store。"""
    file = str(attachment.get("file") or "")
    fn = getattr(services, "extract_attachment_text", None)
    if not file or not callable(fn):
        return ""
    path = _attachment_path(services, file)
    if not path or not os.path.isfile(path):
        return ""
    try:
        result = fn(path, os.path.splitext(file)[1].lower())
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(result, dict) or not result.get("ok"):
        return ""
    text = str(result.get("text") or "")
    if text:
        saver = getattr(services.fulltext, "save_attachment_text", None)
        if callable(saver):
            try:
                saver(paper.get("id"), file, text)
            except Exception:  # noqa: BLE001
                pass
    return text


def collect_materials(services, paper):
    """收集正文 + 全部 SI 的文本（不截断），返回材料、覆盖信息与缺失清单。"""
    paper_id = paper.get("id")
    body = ""
    try:
        body = str(services.fulltext.read(paper_id) or "")
    except Exception:  # noqa: BLE001
        body = ""
    materials = []
    missing = []
    attachment_info = []
    if body.strip():
        materials.append({"kind": "body", "name": "正文", "text": body})
    for attachment in (paper.get("attachments") or []):
        file = str(attachment.get("file") or "")
        name = str(attachment.get("originalName") or file or "SI")
        text = read_attachment_text(services, paper_id, file)
        if not text.strip() and file:
            text = _try_extract_attachment(services, paper, attachment)
        chars = len(text)
        attachment_info.append({"name": name, "file": file, "chars": chars})
        if text.strip():
            materials.append({"kind": "si", "name": name, "file": file, "text": text})
        else:
            missing.append(name)
    body_chars = len(body)
    si_chars = sum(len(m["text"]) for m in materials if m["kind"] == "si")
    return {
        "materials": materials,
        "bodyChars": body_chars,
        "siChars": si_chars,
        "totalChars": body_chars + si_chars,
        "attachments": attachment_info,
        "missing": missing,
    }


def split_text(text, limit):
    """把文本切成每段 <= limit 的片段；优先在段落/句子边界切。纯函数，便于测试。"""
    src = str(text or "")
    if not src:
        return []
    limit = max(1, int(limit or CHUNK_CHARS))
    out = []
    while len(src) > limit:
        floor = int(limit * 0.6)
        cut = limit
        for sep in ("\n\n", "\n", "。", ". "):
            start = min(floor, limit - len(sep))
            if start < 0:
                continue
            idx = src.rfind(sep, start, limit - len(sep) + 1)
            if idx > 0:
                cut = idx + len(sep)
                break
        out.append(src[:cut])
        src = src[cut:]
    if src:
        out.append(src)
    return out


def build_chunks(materials, chunk_chars=CHUNK_CHARS):
    """按材料逐段切片；标签带来源（正文 / SI 文件名）与段号，方便引用。"""
    chunks = []
    for material in materials:
        pieces = split_text(material.get("text") or "", chunk_chars)
        for index, piece in enumerate(pieces, 1):
            label = material["name"]
            if len(pieces) > 1:
                label += "（第 %d/%d 段）" % (index, len(pieces))
            chunks.append({
                "kind": material["kind"],
                "name": material["name"],
                "label": label,
                "index": index,
                "count": len(pieces),
                "chars": len(piece),
                "text": piece,
            })
    return chunks


def prepare(services, paper):
    """返回 {mode, materials, chunks, totalChars, coverage}；mode: none/direct/map-reduce。"""
    collected = collect_materials(services, paper)
    total = collected["totalChars"]
    if total <= 0:
        mode = "none"
        chunks = []
    elif total <= DIRECT_LIMIT:
        mode = "direct"
        chunks = []
    else:
        mode = "map-reduce"
        chunks = build_chunks(collected["materials"], CHUNK_CHARS)
    coverage = {
        "mode": mode,
        "bodyChars": collected["bodyChars"],
        "siChars": collected["siChars"],
        "totalChars": total,
        "attachCount": len(collected["attachments"]),
        "attachments": collected["attachments"],
        "missing": collected["missing"],
        "chunks": len(chunks) if mode == "map-reduce" else (1 if total else 0),
        "processedChunks": 1 if mode == "direct" else 0,
        "chunkChars": CHUNK_CHARS if mode == "map-reduce" else total,
        "directLimit": DIRECT_LIMIT,
    }
    collected.update({"mode": mode, "chunks": chunks, "coverage": coverage})
    return collected


def direct_text(prepared):
    """单次调用的完整材料（正文 + 全部 SI），带来源标题。"""
    parts = []
    for material in prepared.get("materials") or []:
        title = "论文正文" if material["kind"] == "body" else ("补充材料：" + material["name"])
        parts.append("===== " + title + " =====\n" + material["text"])
    return "\n\n".join(parts)


def coverage_text(coverage):
    """给模型/日志看的覆盖说明（不省略任何来源）。"""
    if not coverage or not coverage.get("totalChars"):
        return "没有找到 PDF 正文或 SI 文字。"
    if coverage.get("mode") == "direct":
        text = ("正文 %d 字 + SI %d 字（%d 个文件），合计 %d 字；单次完整送入，未截断。"
                % (coverage["bodyChars"], coverage["siChars"], coverage["attachCount"], coverage["totalChars"]))
    else:
        text = ("正文 %d 字 + SI %d 字（%d 个文件），合计 %d 字；按 %d 段完整处理（每段 <= %d 字）。"
                % (coverage["bodyChars"], coverage["siChars"], coverage["attachCount"],
                   coverage["totalChars"], coverage["chunks"], coverage["chunkChars"]))
    missing = coverage.get("missing") or []
    if missing:
        text += " 未提取到文字的 SI %d 个：%s（这些没有读入）。" % (len(missing), "、".join(missing))
    return text


def map_chunks(llm, chunks, system_prompt, build_user, max_tokens=MAP_MAX_TOKENS, on_progress=None):
    """逐段调用模型提炼；任一段失败（重试一次后仍失败）抛 FullReadError。"""
    notes = []
    total = len(chunks)
    for index, chunk in enumerate(chunks, 1):
        if on_progress:
            try:
                on_progress(index, total, chunk)
            except Exception:  # noqa: BLE001 - 进度帧失败不影响主线
                pass
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": build_user(chunk, index, total)},
        ]
        text = ""
        last_error = None
        for _attempt in (1, 2):
            try:
                text = _response_text(llm.chat(messages, {"maxTokens": max_tokens, "temperature": 0.1})).strip()
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                text = ""
            if text:
                break
        if not text:
            raise FullReadError("第 %d/%d 段（%s）读取失败：%s"
                                % (index, total, chunk.get("label"), last_error or "模型没有返回内容"))
        notes.append({"label": chunk.get("label"), "text": text})
    return notes
