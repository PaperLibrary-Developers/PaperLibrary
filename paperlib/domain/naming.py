"""领域层 · 文件命名（移植自 server/domain/naming.js）。

原文 PDF 与补充材料（SI）在盘上叫什么、记录里的 file 指向谁；命名规则的唯一依据。
"""
from __future__ import annotations

import os
import re
import time

from paperlib.util import sanitize_file_name


def display_file_name(file):
    return re.sub(r"^[a-f0-9]{8,}-", "", str(file or ""), flags=re.I)


def attachment_base_name(paper):
    label = sanitize_file_name(paper.get("title"), 60) or "paper"
    return str(paper.get("id")) + "-" + label + " SI"


def pick_attachment_name(base, ext, taken, current=None):
    def free(name):
        return name == current or name not in taken

    if free(base + ext):
        return base + ext
    n = 2
    while n < 1000:
        candidate = base + " (" + str(n) + ")" + ext
        if free(candidate):
            return candidate
        n += 1
    return base + " (" + str(int(time.time() * 1000)) + ")" + ext


def next_attachment_name(paper, att, index, taken):
    ext = os.path.splitext(str(att.get("originalName") or att.get("file") or ""))[1].lower() or ".bin"
    base = attachment_base_name(paper)
    preferred = base + " (" + str(index + 1) + ")" + ext if index > 0 else base + ext
    if index > 0 and preferred not in taken:
        return preferred
    return pick_attachment_name(base, ext, taken, preferred if index > 0 else None)


def find_attachment_on_disk(directory, paper):
    paper_id = str(paper.get("id") or "")
    if not paper_id:
        return ""
    try:
        for name in sorted(os.listdir(directory)):
            if name.startswith(paper_id + "-"):
                return name
    except OSError:
        return ""
    return ""
