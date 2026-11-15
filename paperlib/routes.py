"""路由表 —— 端点的唯一事实来源（对应 Node server/routes.js，58 条记录）。

字段：
  name     端点名（日志/契约测试用）
  methods  ('GET','HEAD') 之类；'*' 表示任何方法（设置类端点自己判 405）
  pattern  '/api/papers/:id/pdf'，':name' 匹配一段路径（不做 URL 解码）
  handler  (ctx) -> None

顺序即优先级：静态段排在 :id 前面。
"""
from __future__ import annotations

from paperlib.api import (
    ai,
    annotations,
    chat,
    copilot,
    folders,
    compare,
    deep,
    export_extra,
    figures,
    files,
    import_,
    papers,
    presence,
    search,
    settings,
    system,
    tags,
    terms,
    update,
)

ROUTES = [
    # ---------------- 基础 / 运维 ----------------
    {"name": "health", "methods": ("GET",), "pattern": "/api/health", "handler": system.health},
    {"name": "version", "methods": ("GET",), "pattern": "/api/version", "handler": system.version},
    {"name": "update-check", "methods": ("GET",), "pattern": "/api/update/check", "handler": update.check},
    {"name": "update-download", "methods": ("POST",), "pattern": "/api/update/download", "handler": update.download},
    {"name": "update-status", "methods": ("GET",), "pattern": "/api/update/status", "handler": update.status},
    {"name": "update-apply", "methods": ("POST",), "pattern": "/api/update/apply", "handler": update.apply},
    {"name": "stats", "methods": ("GET",), "pattern": "/api/stats", "handler": system.stats},
    {"name": "shutdown-status", "methods": ("GET",), "pattern": "/api/shutdown/status", "handler": system.shutdown_status},
    {"name": "shutdown", "methods": ("POST",), "pattern": "/api/shutdown", "handler": system.shutdown_now},
    {"name": "release-build", "methods": ("POST",), "pattern": "/api/release/build", "handler": system.release_build},
    # 文件夹分类（库内分类，类似资源管理器）：树 / 新建 / 重命名 / 删除 / 移动文献
    {"name": "folders-list", "methods": ("GET",), "pattern": "/api/folders", "handler": folders.list_folders},
    {"name": "folders-create", "methods": ("POST",), "pattern": "/api/folders", "handler": folders.create_folder},
    {"name": "folders-update", "methods": ("PATCH",), "pattern": "/api/folders", "handler": folders.rename_folder},
    {"name": "folders-delete", "methods": ("DELETE",), "pattern": "/api/folders", "handler": folders.delete_folder},
    {"name": "folders-move", "methods": ("POST",), "pattern": "/api/folders/move", "handler": folders.move_papers},

    # 全库智能问答助手：概览（非流式）+ 提问（SSE）
    {"name": "copilot-overview", "methods": ("GET",), "pattern": "/api/copilot/overview", "handler": copilot.overview},
    {"name": "copilot-ask", "methods": ("POST",), "pattern": "/api/copilot/ask", "handler": copilot.ask},

    # 在线监听：页面挂一条 SSE，关掉页面 30 秒后服务自动停（--idle-exit，0 = 关闭该行为）
    {"name": "presence", "methods": ("GET",), "pattern": "/api/presence", "handler": presence.stream},
    {"name": "presence-status", "methods": ("GET",), "pattern": "/api/presence/status", "handler": presence.status},

    # ---------------- 设置（方法不匹配时自己回 405） ----------------
    {"name": "prefs", "methods": ("*",), "pattern": "/api/prefs", "handler": settings.prefs},
    {"name": "ai-settings", "methods": ("*",), "pattern": "/api/ai/settings", "handler": settings.ai_settings},
    {"name": "sources-settings", "methods": ("*",), "pattern": "/api/sources/settings", "handler": settings.sources_settings},
    {"name": "websearch-settings", "methods": ("*",), "pattern": "/api/websearch/settings", "handler": settings.websearch_settings},

    # ---------------- 标签 ----------------
    {"name": "tags-list", "methods": ("GET",), "pattern": "/api/tags", "handler": tags.list_tags},
    {"name": "tags-rename", "methods": ("POST",), "pattern": "/api/tags/rename", "handler": tags.rename},
    {"name": "tags-delete", "methods": ("POST",), "pattern": "/api/tags/delete", "handler": tags.remove},

    # ---------------- 文献：检索 / 建卡 / 整理 ----------------
    {"name": "papers-list", "methods": ("GET",), "pattern": "/api/papers", "handler": papers.list_papers},
    {"name": "papers-create", "methods": ("POST",), "pattern": "/api/papers", "handler": papers.create},
    {"name": "papers-bulk", "methods": ("POST",), "pattern": "/api/papers/bulk", "handler": papers.bulk},
    {"name": "papers-scan-pdfs", "methods": ("GET",), "pattern": "/api/papers/scan/pdfs", "handler": papers.scan_pdfs},
    {"name": "papers-associate-pdf", "methods": ("POST",), "pattern": "/api/papers/associate-pdf", "handler": papers.associate_pdf},
    {"name": "papers-cleanup-duplicates", "methods": ("POST",), "pattern": "/api/papers/cleanup-duplicates", "handler": papers.cleanup_duplicates},
    {"name": "papers-tidy-pdf-names", "methods": ("POST",), "pattern": "/api/papers/tidy-pdf-names", "handler": papers.tidy_pdf_names},
    {"name": "facets", "methods": ("GET",), "pattern": "/api/facets", "handler": papers.facets},

    # ---------------- 图表（合并后新增） ----------------
    {"name": "figures-list", "methods": ("GET",), "pattern": "/api/figures", "handler": figures.list_figures},
    {"name": "figure-delete", "methods": ("DELETE",), "pattern": "/api/figures/:id", "handler": figures.delete_figure},
    {"name": "figure-image", "methods": ("GET", "HEAD"), "pattern": "/api/figures/:id/image", "handler": figures.figure_image},
    {"name": "compare-templates", "methods": ("GET",), "pattern": "/api/compare/templates", "handler": compare.templates},
    {"name": "compare-run", "methods": ("POST",), "pattern": "/api/compare", "handler": compare.run},
    {"name": "terms-search", "methods": ("GET",), "pattern": "/api/terms/search", "handler": terms.search_terms},

    # ---------------- 文献：原文 / 全文 / 补充材料 ----------------
    {"name": "papers-upload", "methods": ("POST",), "pattern": "/api/papers/upload", "handler": files.upload},
    {"name": "papers-fulltext-batch", "methods": ("POST",), "pattern": "/api/papers/fulltext/batch", "handler": files.fulltext_batch},

    # ---------------- AI：中文总结 / AI 总结 ----------------
    {"name": "ai-test", "methods": ("POST",), "pattern": "/api/ai/test", "handler": ai.test},
    {"name": "ai-summarize", "methods": ("POST",), "pattern": "/api/ai/summarize", "handler": ai.summarize},
    {"name": "ai-summarize-sync", "methods": ("POST",), "pattern": "/api/ai/summarize-sync", "handler": ai.summarize_sync},
    {"name": "papers-summary-zh-batch", "methods": ("POST",), "pattern": "/api/papers/summary-zh/batch", "handler": ai.summary_zh_batch},

    # ---------------- 外部源 / 网络检索 / 题录抓取 ----------------
    {"name": "external-sources", "methods": ("GET",), "pattern": "/api/external/sources", "handler": search.external_sources},
    {"name": "external-search", "methods": ("GET",), "pattern": "/api/external/search", "handler": search.external_search},
    {"name": "sources-backfill", "methods": ("POST",), "pattern": "/api/sources/abstracts/backfill", "handler": search.sources_backfill},
    {"name": "sources-openalex-test", "methods": ("POST",), "pattern": "/api/sources/openalex/test", "handler": search.openalex_test},
    {"name": "websearch-test", "methods": ("POST",), "pattern": "/api/websearch/test", "handler": search.websearch_test},
    {"name": "websearch-search", "methods": ("GET",), "pattern": "/api/websearch/search", "handler": search.websearch_search},
    {"name": "websearch-answer", "methods": ("GET",), "pattern": "/api/websearch/answer", "handler": search.websearch_answer},
    {"name": "fetch-doi", "methods": ("POST",), "pattern": "/api/fetch/doi", "handler": search.fetch_doi},
    {"name": "fetch-title", "methods": ("POST",), "pattern": "/api/fetch/title", "handler": search.fetch_title},

    # ---------------- 导入 ----------------
    {"name": "import-pdf-preflight", "methods": ("POST",), "pattern": "/api/import/pdf", "handler": import_.pdf_preflight},
    {"name": "import-pdf-abandon", "methods": ("DELETE",), "pattern": "/api/import/pdf", "handler": import_.pdf_abandon},
    {"name": "import-pdf-confirm", "methods": ("POST",), "pattern": "/api/import/pdf/confirm", "handler": import_.pdf_confirm},
    {"name": "import-json", "methods": ("POST",), "pattern": "/api/import/json", "handler": import_.json_import},

    # ---------------- 导出 ----------------
    {"name": "export-obsidian", "methods": ("GET",), "pattern": "/api/export/obsidian", "handler": export_extra.export_obsidian},
    {"name": "export", "methods": ("GET",), "pattern": "/api/export/:kind", "handler": papers.export_by_kind},

    # ---------------- 文献：按 id 的端点（静态段已排在上面） ----------------
    {"name": "papers-get", "methods": ("GET",), "pattern": "/api/papers/:id", "handler": papers.get_one},
    {"name": "papers-update", "methods": ("PUT", "PATCH"), "pattern": "/api/papers/:id", "handler": papers.update},
    {"name": "papers-delete", "methods": ("DELETE",), "pattern": "/api/papers/:id", "handler": papers.remove},

    {"name": "papers-fulltext-get", "methods": ("GET",), "pattern": "/api/papers/:id/fulltext", "handler": files.fulltext_get},
    {"name": "papers-fulltext-post", "methods": ("POST",), "pattern": "/api/papers/:id/fulltext", "handler": files.fulltext_extract},
    {"name": "papers-fulltext-text", "methods": ("POST",), "pattern": "/api/papers/:id/fulltext/text", "handler": files.fulltext_save_text},
    {"name": "papers-summary-zh", "methods": ("POST",), "pattern": "/api/papers/:id/summary-zh", "handler": ai.summary_zh},

    {"name": "papers-figures-get", "methods": ("GET",), "pattern": "/api/papers/:id/figures", "handler": figures.paper_figures},
    {"name": "papers-figures-extract", "methods": ("POST",), "pattern": "/api/papers/:id/figures/extract", "handler": figures.extract_figures},
    {"name": "papers-deep-get", "methods": ("GET",), "pattern": "/api/papers/:id/deep-analysis", "handler": deep.get_analysis},
    {"name": "papers-deep-run", "methods": ("POST",), "pattern": "/api/papers/:id/deep-analysis", "handler": deep.run_analysis},

    {"name": "papers-attachments-get", "methods": ("GET",), "pattern": "/api/papers/:id/attachments", "handler": files.attachments_list},
    {"name": "papers-attachments-post", "methods": ("POST",), "pattern": "/api/papers/:id/attachments", "handler": files.attachments_upload},
    {"name": "papers-attachment-download", "methods": ("GET", "HEAD"), "pattern": "/api/papers/:id/attachments/:file", "handler": files.attachment_download},
    {"name": "papers-attachment-delete", "methods": ("DELETE",), "pattern": "/api/papers/:id/attachments/:file", "handler": files.attachment_remove},

    {"name": "papers-chat-get", "methods": ("GET",), "pattern": "/api/papers/:id/chat", "handler": chat.chat_get},
    {"name": "papers-chat-delete", "methods": ("DELETE",), "pattern": "/api/papers/:id/chat", "handler": chat.chat_delete},
    {"name": "papers-chat-post", "methods": ("POST",), "pattern": "/api/papers/:id/chat", "handler": chat.chat_post},
    {"name": "papers-vision", "methods": ("POST",), "pattern": "/api/papers/:id/vision", "handler": chat.vision},
    {"name": "papers-transcribe", "methods": ("POST",), "pattern": "/api/papers/:id/transcribe", "handler": chat.transcribe},

    # PDF 批注（荧光笔 / 便签）：整表读写，坐标是每页 0~1 的归一化值
    {"name": "papers-annotations-get", "methods": ("GET",), "pattern": "/api/papers/:id/annotations", "handler": annotations.get_annotations},
    {"name": "papers-annotations-put", "methods": ("PUT",), "pattern": "/api/papers/:id/annotations", "handler": annotations.put_annotations},

    {"name": "papers-cite", "methods": ("GET",), "pattern": "/api/papers/:id/cite", "handler": files.cite},
    {"name": "papers-pdf", "methods": ("GET", "HEAD"), "pattern": "/api/papers/:id/pdf", "handler": files.pdf},
]
