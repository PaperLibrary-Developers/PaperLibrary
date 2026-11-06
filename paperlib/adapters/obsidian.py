# -*- coding: utf-8 -*-
"""Obsidian 导出（PaperLibrary 能力；移植自 app/obsidian_export.py）。

契约名：export.obsidian（GET /api/export/obsidian）

产出物是一个 zip（不写用户 vault 目录，也不依赖 Streamlit）：
    paperlibrary-obsidian-<date>.zip
      ├── <安全标题>.md              每篇一个笔记（frontmatter + 摘要/笔记/AI 总结/图表链接）
      └── media/<paperId>/<图片>     正文引用到的图表

约定：
  · 数据由调用方注入（papers + {paperId: figures 行} + dataDir），本模块不碰 SQL；
  · 笔记文本的拼装规则在 paperlib/legacy/obsidian_note.py（纯函数，可单测）；
  · 同名笔记自动加 paperId 后缀，避免 zip 内互相覆盖。
"""
from __future__ import annotations

import zipfile
from pathlib import Path

from paperlib.legacy import obsidian_note
from paperlib.util import ensure_dir, now_iso


def zip_filename(now=None):
    """契约 §10 的下载文件名：paperlibrary-obsidian-<date>.zip。"""
    stamp = str(now or now_iso())[:10]
    return "paperlibrary-obsidian-%s.zip" % stamp


def build_zip(papers, destination, options=None):
    """把 papers 打包成 Obsidian zip。

    options:
      dataDir  数据目录（解析 figures 行的相对图片路径；缺省则不导出图片）
      figures  {paperId: [figures 行]}；行字段同 store.repo.list_figures
      now      时间戳（默认 util.now_iso()）
    返回 {ok, path, filename, notes, media, bytes}；失败返回 {ok: False, error}。
    """
    options = dict(options or {})
    data_dir = options.get("dataDir") or options.get("data_dir")
    figures_by_paper = options.get("figures") or {}
    now = options.get("now")
    dest = Path(destination)
    filename = dest.name

    try:
        ensure_dir(dest.parent)
        used_names = set()
        notes = 0
        media = 0
        with zipfile.ZipFile(str(dest), "w", zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for paper in papers or []:
                paper_id = str(paper.get("id") or "")
                figures = figures_by_paper.get(paper_id) or []
                note = obsidian_note.build_note(paper, figures, data_dir, now=now)
                name = note["noteName"]
                if name in used_names:
                    name = "%s (%s)" % (name, paper_id[:6] or str(notes + 1))
                used_names.add(name)
                archive.writestr(name + ".md", note["text"])
                notes += 1
                for entry in note["media"]:
                    try:
                        archive.write(entry["absPath"], entry["zipPath"])
                        media += 1
                    except OSError:
                        continue
        size = dest.stat().st_size if dest.is_file() else 0
        return {"ok": True, "path": str(dest), "filename": filename,
                "notes": notes, "media": media, "bytes": size}
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {"ok": False, "path": str(dest), "filename": filename,
                "notes": 0, "media": 0, "bytes": 0, "error": str(exc)}
