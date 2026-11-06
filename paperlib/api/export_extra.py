"""合并后新增的导出：Obsidian（PaperLibrary 能力）。

  GET /api/export/obsidian[?ids=a,b][?limit=n]

契约：docs/合并新增端点契约-20261107.md §10（zip 文件流，
      Content-Disposition: attachment; filename="paperlibrary-obsidian-<date>.zip"）。
zip 内容：每篇一个 .md（frontmatter + 摘要/笔记/AI 总结/图表链接）+ media/ 下引用的图表。
"""
from __future__ import annotations

from paperlib.adapters import obsidian
from paperlib.legacy import obsidian_note  # noqa: F401  （文档指向：笔记拼装规则所在模块）
from paperlib.util import clamp_int, ensure_dir

EXPORT_LIMIT = 2000


def _select_papers(ctx):
    store = ctx.services.db
    papers = store.list_papers()
    raw_ids = str(ctx.query("ids") or "").strip()
    if raw_ids:
        wanted = [piece.strip() for piece in raw_ids.replace("；", ",").split(",") if piece.strip()]
        allowed = set(wanted)
        papers = [paper for paper in papers if str(paper.get("id")) in allowed]
    single = str(ctx.query("paperId") or "").strip()
    if single:
        papers = [paper for paper in papers if str(paper.get("id")) == single]
    limit = clamp_int(ctx.query("limit"), 1, EXPORT_LIMIT, EXPORT_LIMIT)
    return papers[:limit]


def _cleanup(sc_dir, keep=3):
    try:
        files = sorted(sc_dir.glob("paperlibrary-obsidian-*.zip"),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    except OSError:
        return
    for victim in files[keep:]:
        try:
            victim.unlink()
        except OSError:
            pass


def export_obsidian(ctx):
    store = ctx.services.db
    papers = _select_papers(ctx)

    figures_map = {}
    for paper in papers:
        try:
            figures_map[str(paper.get("id"))] = store.repo.list_figures(paper.get("id")) or []
        except Exception:  # noqa: BLE001 - 单篇图片读失败不应让整个导出失败
            figures_map[str(paper.get("id"))] = []

    export_dir = ensure_dir(store.data_dir / "tmp-export")
    filename = obsidian.zip_filename()
    destination = export_dir / filename
    result = obsidian.build_zip(papers, destination, {
        "dataDir": store.data_dir,
        "figures": figures_map,
    })
    if not result.get("ok"):
        return ctx.fail(500, "导出失败：" + str(result.get("error") or "未知原因"))

    _cleanup(export_dir)
    return ctx.send_file(result["path"], download=result.get("filename") or filename,
                         content_type="application/zip", cache="no-store")
