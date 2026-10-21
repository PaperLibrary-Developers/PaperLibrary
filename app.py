"""
app.py — 文献管理检索系统 Streamlit 界面

页签 1: 文献库概览与详情（论文卡片 + AI 结构化总结 + 图表画廊 + 站内 PDF 阅读）
页签 2: 图片/图注精确检索（自然语言 → 图表匹配）

支持增量更新：新增 PDF 放入文件夹 → 点「更新文献库」→ 仅导入新文件（SHA256 去重）。

说明：本文件是重建版，刻意**不含**任何自定义注入 CSS，页面使用 Streamlit 原生外观，
     以保证侧边栏、页签等组件稳定。原美化样式（hero 区 / 卡片版式 / 夜间模式）
     保存在项目根目录的 _css_backup.txt，将来需要时可小步、单独引入。
"""

import os, sys, json, time, hashlib, urllib.parse, webbrowser, sqlite3
import html as html_mod
import fitz  # PyMuPDF — 站内 PDF 阅读器渲染用
import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import database
import importlib
try:
    importlib.reload(database)
except Exception:
    pass
import parser as paper_parser
import ai_engine
import library_fs
import doc_classify
import search_sources
import search_aggregate as agg
import pdf_fetch
import pdf_index
import thumbs
import local_rank
import citation
import metadata_enricher
import folder_picker
import importer
import comparator
import figure_anchor
import figures_arxiv
import deep_analysis
import obsidian_export

APP_DIR = os.path.dirname(os.path.abspath(__file__))
APP_CONFIG_FILE = os.path.join(APP_DIR, "data", ".app_config.json")

st.set_page_config(page_title="文献管理检索系统", page_icon="📚", layout="wide")

# 启动本地文件服务器（端口 8502，仅本机可访问，用于浏览器内嵌 PDF 预览）。
# 在 Streamlit 进程内以线程运行：独立子进程容易被系统/沙箱清理掉，导致 PDF 打不开。
# 模块级的 _server_started 标志保证只启动一次；端口被占用时会打印提示（不再静默）。
try:
    import file_server as _fs
    _fs.start_file_server()
except Exception as _e:
    print(f"[app] 文件服务器启动失败（不影响其它功能）: {_e}")


# ═══════════════════════════════════════════════════
# 全站紧凑高密度学术版式 CSS 注入
# ═══════════════════════════════════════════════════

def inject_compact_academic_css():
    """注入高级现代学术风（Linear / Raycast / ReadCube 质感）高信息密度版式 CSS"""
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

    /* 全局字体与平滑渲染 */
    html, body, [class*="css"] {
        font-family: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "PingFang SC", "Microsoft YaHei", sans-serif !important;
        -webkit-font-smoothing: antialiased;
        -moz-osx-font-smoothing: grayscale;
    }
    code, pre {
        font-family: 'JetBrains Mono', Consolas, Monaco, monospace !important;
    }

    /* 1. 紧凑页面顶部与主容器内边距 */
    .block-container {
        padding-top: 0.8rem !important;
        padding-bottom: 2.0rem !important;
        padding-left: 1.5rem !important;
        padding-right: 1.5rem !important;
        max-width: 98% !important;
    }

    /* 顶栏品牌 Header Banner */
    .sc-top-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 8px 14px;
        background: linear-gradient(135deg, rgba(30, 41, 59, 0.4) 0%, rgba(15, 23, 42, 0.25) 100%);
        border: 1px solid rgba(148, 163, 184, 0.15);
        border-radius: 10px;
        margin-bottom: 12px;
        backdrop-filter: blur(8px);
    }
    .sc-brand {
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .sc-logo-icon {
        font-size: 24px;
        line-height: 1;
        filter: drop-shadow(0 2px 4px rgba(99, 102, 241, 0.4));
    }
    .sc-app-title {
        font-size: 16px;
        font-weight: 700;
        letter-spacing: -0.02em;
        background: linear-gradient(135deg, #e2e8f0 0%, #cbd5e1 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
    }
    .sc-badge-pro {
        font-size: 10px;
        font-weight: 700;
        padding: 1px 5px;
        border-radius: 4px;
        background: linear-gradient(135deg, #6366f1 0%, #4f46e5 100%);
        color: #ffffff !important;
        -webkit-text-fill-color: #ffffff !important;
        vertical-align: middle;
        margin-left: 4px;
    }
    .sc-app-subtitle {
        font-size: 12px;
        color: #94a3b8;
        margin-left: 8px;
    }
    .sc-designed-by {
        font-size: 11px;
        font-weight: 600;
        letter-spacing: 0.04em;
        color: #818cf8;
        margin-left: 8px;
        padding-left: 8px;
        border-left: 1px solid rgba(148, 163, 184, 0.35);
        white-space: nowrap;
    }
    .sc-status-chips {
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .sc-chip {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-size: 11px;
        font-weight: 500;
        padding: 3px 9px;
        border-radius: 20px;
        background: rgba(148, 163, 184, 0.08);
        border: 1px solid rgba(148, 163, 184, 0.15);
        color: #94a3b8;
    }
    .sc-dot {
        width: 6px;
        height: 6px;
        border-radius: 50%;
    }
    .dot-green { background: #10b981; box-shadow: 0 0 6px rgba(16, 185, 129, 0.6); }
    .dot-blue { background: #38bdf8; box-shadow: 0 0 6px rgba(56, 189, 248, 0.6); }
    .dot-purple { background: #818cf8; box-shadow: 0 0 6px rgba(129, 140, 248, 0.6); }

    /* 2. 紧凑垂直与水平间距 */
    div[data-testid="stVerticalBlock"] > div {
        margin-bottom: 0.25rem !important;
    }
    div[data-testid="stHorizontalBlock"] {
        gap: 0.45rem !important;
        align-items: center;
    }

    /* 3. 高级感卡片容器（Hover 微抬升与光晕） */
    div[data-testid="stContainer"][border="true"] {
        padding: 0.6rem 0.85rem !important;
        border-radius: 10px !important;
        margin-bottom: 0.4rem !important;
        border: 1px solid rgba(148, 163, 184, 0.18) !important;
        background: rgba(30, 41, 59, 0.18) !important;
        transition: all 0.2s cubic-bezier(0.16, 1, 0.3, 1) !important;
    }
    div[data-testid="stContainer"][border="true"]:hover {
        border-color: rgba(99, 102, 241, 0.4) !important;
        box-shadow: 0 4px 18px -2px rgba(99, 102, 241, 0.12), 0 2px 6px -1px rgba(0, 0, 0, 0.08) !important;
        transform: translateY(-1px) !important;
    }

    /* 4. 标题紧凑化 */
    h1, h2, h3, h4, h5, h6 {
        margin-top: 0.1rem !important;
        margin-bottom: 0.25rem !important;
        padding: 0 !important;
        line-height: 1.28 !important;
        letter-spacing: -0.015em !important;
    }

    /* 5. 统计指标微卡片（Linear 仪表板质感） */
    div[data-testid="stMetric"] {
        padding: 6px 12px !important;
        background: linear-gradient(135deg, rgba(30, 41, 59, 0.35) 0%, rgba(15, 23, 42, 0.25) 100%) !important;
        border-radius: 8px !important;
        border: 1px solid rgba(148, 163, 184, 0.16) !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05) !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 19px !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
        color: #f1f5f9 !important;
    }
    div[data-testid="stMetricLabel"] {
        font-size: 11.5px !important;
        font-weight: 500 !important;
        color: #94a3b8 !important;
    }

    /* 6. 高级感按钮与主色调 */
    div[data-testid="stButton"] > button {
        padding: 3px 12px !important;
        min-height: 32px !important;
        font-size: 12.5px !important;
        font-weight: 500 !important;
        border-radius: 7px !important;
        transition: all 0.18s ease-in-out !important;
    }
    div[data-testid="stButton"] > button[kind="primary"] {
        background: linear-gradient(135deg, #4f46e5 0%, #6366f1 100%) !important;
        border: 1px solid rgba(255, 255, 255, 0.15) !important;
        box-shadow: 0 2px 8px rgba(79, 70, 229, 0.32) !important;
        color: #ffffff !important;
    }
    div[data-testid="stButton"] > button[kind="primary"]:hover {
        background: linear-gradient(135deg, #4338ca 0%, #4f46e5 100%) !important;
        box-shadow: 0 4px 14px rgba(79, 70, 229, 0.45) !important;
        transform: translateY(-1px) !important;
    }
    div[data-testid="stLinkButton"] > a {
        padding: 3px 12px !important;
        min-height: 32px !important;
        font-size: 12.5px !important;
        font-weight: 500 !important;
        border-radius: 7px !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
    }

    /* 7. 折叠面板紧凑化 */
    div[data-testid="stExpander"] {
        margin-bottom: 0.3rem !important;
    }
    div[data-testid="stExpander"] details {
        border-radius: 8px !important;
        border: 1px solid rgba(148, 163, 184, 0.15) !important;
    }
    div[data-testid="stExpander"] details summary {
        padding: 6px 12px !important;
        font-size: 12.5px !important;
        font-weight: 500 !important;
    }

    /* 8. 页签 Tabs 现代质感 */
    div[data-testid="stTabs"] {
        margin-bottom: 0.4rem !important;
    }
    div[data-testid="stTabs"] button {
        padding: 4px 14px !important;
        font-size: 13.5px !important;
        font-weight: 600 !important;
        letter-spacing: -0.01em !important;
        border-radius: 6px 6px 0 0 !important;
    }

    /* 9. 输入框紧凑化 */
    div[data-testid="stTextInput"] input {
        padding: 6px 12px !important;
        font-size: 13px !important;
        border-radius: 7px !important;
        border: 1px solid rgba(148, 163, 184, 0.22) !important;
    }

    /* 10. 分割线收紧 */
    hr {
        margin: 0.4rem 0 !important;
        border-color: rgba(148, 163, 184, 0.12) !important;
    }

    /* 11. 聊天对话气泡精修（Claude/ChatGPT 风格） */
    div[data-testid="stChatMessage"] {
        padding: 10px 14px !important;
        margin-bottom: 8px !important;
        border-radius: 10px !important;
        border: 1px solid rgba(148, 163, 184, 0.12) !important;
        background: rgba(30, 41, 59, 0.25) !important;
    }

    /* 12. 摘要紧凑展示（带品牌色左边条） */
    .compact-summary {
        display: -webkit-box;
        -webkit-line-clamp: 2;
        -webkit-box-orient: vertical;
        overflow: hidden;
        text-overflow: ellipsis;
        font-size: 12px;
        line-height: 1.45;
        color: #94a3b8;
        padding-left: 9px;
        border-left: 2.5px solid rgba(99, 102, 241, 0.5);
        margin: 4px 0 6px 0;
    }

    /* 13. 胶囊徽章 Pill Badges 系统 */
    .meta-pills-row {
        display: flex;
        flex-wrap: wrap;
        gap: 5px;
        margin: 3px 0 5px 0;
        align-items: center;
    }
    .meta-pill {
        display: inline-flex;
        align-items: center;
        gap: 3px;
        font-size: 11.5px;
        line-height: 1.35;
        padding: 2px 7px;
        border-radius: 12px;
        font-weight: 500;
        white-space: nowrap;
        max-width: 280px;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    .meta-pill-author { background: rgba(99, 102, 241, 0.12); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.25); }
    .meta-pill-journal { background: rgba(16, 185, 129, 0.12); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.25); font-style: italic; }
    .meta-pill-year { background: rgba(245, 158, 11, 0.12); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.25); }
    .meta-pill-folder { background: rgba(148, 163, 184, 0.10); color: #94a3b8; border: 1px solid rgba(148, 163, 184, 0.22); }
    .meta-pill-doi { background: rgba(14, 165, 233, 0.12); color: #38bdf8; border: 1px solid rgba(14, 165, 233, 0.25); }

    /* 兼容老 class */
    .compact-badges {
        font-size: 12px;
        color: #94a3b8;
        margin-bottom: 3px;
    }

    /* 缩略图卡片微美化 */
    .thumb-preview-wrap {
        border-radius: 6px;
        overflow: hidden;
        border: 1px solid rgba(148, 163, 184, 0.2);
        box-shadow: 0 2px 6px rgba(0, 0, 0, 0.15);
    }

    /* ═══ 模块间清晰分区与规整图注卡片系统 ═══ */

    /* 14. 规整图注卡片框 (整齐对齐，告别杂乱) */
    .caption-card-box {
        background: rgba(15, 23, 42, 0.55);
        border: 1px solid rgba(99, 102, 241, 0.28);
        border-left: 3.5px solid #6366f1;
        border-radius: 7px;
        padding: 8px 12px;
        margin: 6px 0;
    }
    .caption-card-box .cap-label {
        font-size: 11px;
        font-weight: 700;
        color: #818cf8;
        margin-bottom: 4px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        display: flex;
        align-items: center;
        gap: 4px;
    }
    .caption-card-box .cap-body {
        font-size: 12.5px;
        line-height: 1.48;
        color: #e2e8f0;
        word-break: break-word;
    }

    /* 15. AI 视觉机理解读卡片框 */
    .ai-desc-card-box {
        background: rgba(16, 185, 129, 0.06);
        border: 1px solid rgba(16, 185, 129, 0.25);
        border-left: 3.5px solid #10b981;
        border-radius: 7px;
        padding: 8px 12px;
        margin: 6px 0;
    }
    .ai-desc-card-box .ai-label {
        font-size: 11px;
        font-weight: 700;
        color: #34d399;
        margin-bottom: 4px;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        display: flex;
        align-items: center;
        gap: 4px;
    }
    .ai-desc-card-box .ai-body {
        font-size: 12px;
        line-height: 1.45;
        color: #a7f3d0;
        word-break: break-word;
    }

    /* 16. 工作台面板头部条 (各区块泾渭分明) */
    .workbench-panel-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 6px 12px;
        background: linear-gradient(135deg, rgba(30, 41, 59, 0.5) 0%, rgba(15, 23, 42, 0.4) 100%);
        border: 1px solid rgba(148, 163, 184, 0.2);
        border-radius: 8px;
        margin-bottom: 8px;
        font-size: 13.5px;
        font-weight: 600;
        color: #f1f5f9;
        letter-spacing: -0.01em;
    }

    /* 16.1 并排研读对齐：带边框容器的第一个子元素顶格，消除两侧首个元素的上外边距差异，
       保证「PDF 原文视窗」与「AI 问答顾问」的顶边落在同一条水平线上 */
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:first-child,
    div[data-testid="stVerticalBlockBorderWrapper"] div[data-testid="stVerticalBlock"] > div[data-testid="stElementContainer"]:first-child > div {
        margin-top: 0 !important;
        padding-top: 0 !important;
    }
    div[data-testid="stIFrame"] {
        display: block;
        margin: 0 !important;
    }

    /* 16.2 并排研读两栏等高齐平：含 .wb-eq-h-anchor（AI 顾问面板）的那一行，
       两列容器一起拉伸到同一高度 → 右侧 AI 顾问底板与左侧 PDF 原文视窗底边齐平；
       AI 内容超出时在框内滚动，不会把页面顶长 */
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"] .wb-eq-h-anchor) > div[data-testid="stColumn"] {
        display: flex;
        flex-direction: column;
    }
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"] .wb-eq-h-anchor) > div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] {
        display: flex;
        flex-direction: column;
        flex: 1 1 auto;
        min-height: 0;
    }
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"] .wb-eq-h-anchor) > div[data-testid="stColumn"] > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
        flex: 1 1 auto;
        min-height: 0;
    }
    div[data-testid="stHorizontalBlock"]:has(> div[data-testid="stColumn"] .wb-eq-h-anchor) > div[data-testid="stColumn"]:last-child > div[data-testid="stVerticalBlock"] > div[data-testid="stVerticalBlockBorderWrapper"] {
        overflow-y: auto;
    }

    /* 17. 顶部工具条专用框 */
    .toolbar-container-box {
        background: rgba(30, 41, 59, 0.28);
        border: 1px solid rgba(148, 163, 184, 0.22);
        border-radius: 9px;
        padding: 6px 10px;
        margin-bottom: 10px;
    }

    /* 18. 纯净现代侧边栏（白底黑字，高对比度，清晰易读） */
    section[data-testid="stSidebar"],
    section[data-testid="stSidebar"] > div {
        background-color: #ffffff !important;
        background: #ffffff !important;
        border-right: 1px solid #e2e8f0 !important;
    }
    section[data-testid="stSidebar"] div.block-container {
        padding-top: 1.0rem !important;
        padding-left: 0.85rem !important;
        padding-right: 0.85rem !important;
    }
    /* 侧边栏所有正文、段落、列表、代码标签强制深黑，彻底消除灰字发白 */
    section[data-testid="stSidebar"] p,
    section[data-testid="stSidebar"] span,
    section[data-testid="stSidebar"] label,
    section[data-testid="stSidebar"] li,
    section[data-testid="stSidebar"] div,
    section[data-testid="stSidebar"] code {
        color: #0f172a !important;
    }
    /* 侧边栏所有说明文字 (Caption)：清晰中深灰，绝不发灰难辨 */
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"],
    section[data-testid="stSidebar"] [data-testid="stCaptionContainer"] * {
        color: #475569 !important;
        font-size: 12px !important;
    }
    /* 侧边栏所有标题与加粗文本：100% 极深纯黑 */
    section[data-testid="stSidebar"] h1,
    section[data-testid="stSidebar"] h2,
    section[data-testid="stSidebar"] h3,
    section[data-testid="stSidebar"] h4,
    section[data-testid="stSidebar"] strong,
    section[data-testid="stSidebar"] b {
        color: #000000 !important;
        font-weight: 700 !important;
    }
    /* 侧边栏卡片容器（白底、浅灰边框、微弱投影） */
    .sb-card {
        background: #f8fafc !important;
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
        padding: 10px 12px !important;
        margin-bottom: 10px !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05) !important;
    }
    .sb-card-header {
        font-size: 12px !important;
        font-weight: 700 !important;
        color: #0f172a !important;
        margin-bottom: 6px !important;
        display: flex !important;
        align-items: center !important;
        gap: 6px !important;
        text-transform: uppercase !important;
        letter-spacing: 0.05em !important;
    }
    /* 侧边栏品牌卡片（白底黑字、清爽学术感） */
    .sb-brand-card {
        background: #f1f5f9 !important;
        border: 1.5px solid #cbd5e1 !important;
        border-radius: 8px !important;
        padding: 10px 12px !important;
        margin-bottom: 12px !important;
        box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04) !important;
    }
    .sb-brand-top {
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
        margin-bottom: 4px !important;
    }
    .sb-brand-title {
        font-size: 16px !important;
        font-weight: 700 !important;
        letter-spacing: -0.02em !important;
        color: #0f172a !important;
        display: flex !important;
        align-items: center !important;
        gap: 8px !important;
    }
    .sb-brand-badge {
        font-size: 9.5px !important;
        font-weight: 700 !important;
        background: #0f172a !important;
        color: #ffffff !important;
        padding: 2px 6px !important;
        border-radius: 4px !important;
        letter-spacing: 0.06em !important;
        text-transform: uppercase !important;
    }
    .sb-brand-sub {
        font-size: 11.5px !important;
        color: #475569 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
    }
    .sb-brand-sub span {
        color: #334155 !important;
    }
    .sb-live-indicator {
        display: inline-flex !important;
        align-items: center !important;
        gap: 5px !important;
        font-size: 11px !important;
        color: #059669 !important;
        font-weight: 600 !important;
    }
    .sb-live-dot {
        width: 6.5px !important;
        height: 6.5px !important;
        background: #10b981 !important;
        border-radius: 50% !important;
        box-shadow: 0 0 4px #10b981 !important;
    }
    .sb-path-box {
        font-family: 'JetBrains Mono', Consolas, monospace !important;
        font-size: 11px !important;
        color: #0f172a !important;
        background: #e2e8f0 !important;
        border: 1px solid #cbd5e1 !important;
        padding: 6px 9px !important;
        border-radius: 6px !important;
        word-break: break-all !important;
        margin: 4px 0 8px 0 !important;
    }
    /* 侧边栏折叠面板（白底纯净） */
    section[data-testid="stSidebar"] [data-testid="stExpander"] {
        border: 1px solid #cbd5e1 !important;
        border-radius: 8px !important;
        background: #f8fafc !important;
        margin-bottom: 8px !important;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] details summary {
        font-size: 12.5px !important;
        font-weight: 600 !important;
        color: #0f172a !important;
        padding: 8px 10px !important;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] details summary:hover {
        color: #1e40af !important;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] details summary svg {
        color: #475569 !important;
    }
    section[data-testid="stSidebar"] hr {
        margin: 10px 0 !important;
        border-color: #e2e8f0 !important;
    }

    /* 19. 侧边栏智能助手 (Copilot) 白底深字 */
    .sb-copilot-user {
        background: #e0f2fe !important;
        border: 1px solid #7dd3fc !important;
        border-radius: 6px !important;
        padding: 6px 9px !important;
        font-size: 12px !important;
        color: #0369a1 !important;
        margin: 6px 0 4px 0 !important;
        font-weight: 600 !important;
    }
    .sb-copilot-bot {
        background: #ffffff !important;
        border: 1.5px solid #cbd5e1 !important;
        border-left: 3.5px solid #0f172a !important;
        border-radius: 0 6px 6px 0 !important;
        padding: 8px 10px !important;
        font-size: 12.5px !important;
        color: #0f172a !important;
        margin: 4px 0 10px 0 !important;
        line-height: 1.55 !important;
    }
    .sb-copilot-bot * {
        color: #0f172a !important;
    }
    .sb-copilot-bot strong, .sb-copilot-bot b {
        color: #000000 !important;
        font-weight: 700 !important;
    }
    /* 侧边栏 Metric 标签与数值提亮（深黑大字） */
    section[data-testid="stSidebar"] [data-testid="stMetricLabel"] {
        color: #475569 !important;
        font-size: 11.5px !important;
        font-weight: 600 !important;
    }
    section[data-testid="stSidebar"] [data-testid="stMetricValue"] {
        color: #000000 !important;
        font-size: 18px !important;
        font-weight: 700 !important;
        font-family: 'JetBrains Mono', Consolas, monospace !important;
    }
    /* 侧边栏 Checkbox、Radio 选项文字深黑 */
    section[data-testid="stSidebar"] [data-testid="stCheckbox"] label span,
    section[data-testid="stSidebar"] [data-testid="stRadio"] label span {
        color: #0f172a !important;
        font-size: 12.5px !important;
        font-weight: 500 !important;
    }
    /* 侧边栏输入框文字与白底 */
    section[data-testid="stSidebar"] input {
        background: #ffffff !important;
        color: #000000 !important;
        border: 1px solid #cbd5e1 !important;
    }
    /* 侧边栏 Expander 内部段落、列表、代码 */
    section[data-testid="stSidebar"] [data-testid="stExpander"] p,
    section[data-testid="stSidebar"] [data-testid="stExpander"] li,
    section[data-testid="stSidebar"] [data-testid="stExpander"] span {
        color: #0f172a !important;
    }
    section[data-testid="stSidebar"] [data-testid="stExpander"] code {
        color: #0f172a !important;
        background: #e2e8f0 !important;
        border: 1px solid #cbd5e1 !important;
        padding: 2px 5px !important;
        border-radius: 4px !important;
    }
    /* 侧边栏按钮样式统一加深 */
    section[data-testid="stSidebar"] button {
        color: #0f172a !important;
        border: 1px solid #cbd5e1 !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"] {
        background: #0f172a !important;
        border: 1px solid #0f172a !important;
        color: #ffffff !important;
    }
    section[data-testid="stSidebar"] button[kind="primary"] * {
        color: #ffffff !important;
    }

    /* ═══════════════════════════════════════════════════
       20. Google 首页风格超显著学术搜索栏 (Google Hero Search)
       ═══════════════════════════════════════════════════ */
    .google-hero-header {
        text-align: center;
        margin-top: 8px;
        margin-bottom: 12px;
    }
    .google-academic-logo {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        gap: 1.5px;
        font-size: 28px;
        font-weight: 700;
        letter-spacing: -0.5px;
        font-family: 'Plus Jakarta Sans', -apple-system, sans-serif;
    }
    .g-blue   { color: #4285F4; }
    .g-red    { color: #EA4335; }
    .g-yellow { color: #FBBC05; }
    .g-green  { color: #34A853; }
    .google-logo-sub {
        font-size: 13px;
        font-weight: 700;
        background: #4285F4;
        color: #ffffff !important;
        padding: 2px 8px;
        border-radius: 12px;
        margin-left: 8px;
        letter-spacing: 0.02em;
        text-shadow: none;
        vertical-align: middle;
    }
    .google-hero-tagline {
        font-size: 12.5px;
        color: #64748b;
        margin-top: 4px;
        font-weight: 500;
    }

    /* 谷歌搜索核心药丸外框 (Hero Search Pill Container) */
    .google-search-pill-card {
        background: #ffffff !important;
        border: 1.5px solid #dfe1e5 !important;
        border-radius: 36px !important;
        box-shadow: 0 2px 7px rgba(32, 33, 36, 0.18), 0 0 0 1px rgba(0,0,0,0.02) !important;
        padding: 4px 16px !important;
        margin: 6px auto 14px auto !important;
        max-width: 860px !important;
        transition: all 0.22s cubic-bezier(0.4, 0, 0.2, 1) !important;
    }
    .google-search-pill-card:hover {
        border-color: #cbd5e1 !important;
        box-shadow: 0 4px 14px 1px rgba(64, 60, 67, 0.22) !important;
        transform: translateY(-1px);
    }
    .google-search-pill-card:focus-within {
        border-color: #4285F4 !important;
        box-shadow: 0 4px 18px 2px rgba(66, 133, 244, 0.35) !important;
    }

    /* 穿透注入 Streamlit 的输入框，消除原有黑边，直接融入白色药丸 */
    .google-search-pill-card div[data-testid="stTextInput"] {
        margin-bottom: 0px !important;
    }
    .google-search-pill-card div[data-testid="stTextInput"] > div {
        border: none !important;
        background: transparent !important;
        box-shadow: none !important;
    }
    .google-search-pill-card div[data-testid="stTextInput"] input {
        background: transparent !important;
        border: none !important;
        outline: none !important;
        box-shadow: none !important;
        font-size: 15.5px !important;
        color: #1e293b !important;
        font-weight: 500 !important;
        padding: 6px 2px !important;
        height: 42px !important;
    }
    .google-search-pill-card div[data-testid="stTextInput"] input::placeholder {
        color: #94a3b8 !important;
        font-size: 14px !important;
        font-weight: 400 !important;
    }

    /* 谷歌搜索快捷按键徽章 */
    .google-kbd-badge {
        font-family: 'JetBrains Mono', Consolas, monospace;
        font-size: 11px;
        background: #f1f5f9;
        color: #64748b;
        border: 1px solid #cbd5e1;
        border-radius: 4px;
        padding: 3px 6px;
        display: inline-block;
        user-select: none;
    }

    /* 谷歌风格热搜 Chips 胶囊 */
    .google-chip-label {
        font-size: 12px;
        color: #64748b;
        font-weight: 600;
        margin-right: 6px;
    }

    /* ═══════════════════════════════════════════════════
       21. 多图横向对比幕布专用样式 (Figure Comparator)
       ═══════════════════════════════════════════════════ */
    .compare-tray-banner {
        background: #ffffff !important;
        border: 1.5px solid #059669 !important;
        border-radius: 12px !important;
        padding: 10px 16px !important;
        margin-bottom: 14px !important;
        box-shadow: 0 4px 16px rgba(5, 150, 105, 0.08) !important;
        display: flex !important;
        align-items: center !important;
        justify-content: space-between !important;
    }
    .compare-tray-title {
        font-size: 14.5px !important;
        font-weight: 700 !important;
        color: #065f46 !important;
        display: flex !important;
        align-items: center !important;
        gap: 8px !important;
    }
    .compare-tray-badge {
        background: #ecfdf5 !important;
        color: #059669 !important;
        border: 1px solid #a7f3d0 !important;
        padding: 2px 8px !important;
        border-radius: 20px !important;
        font-size: 11.5px !important;
        font-weight: 700 !important;
        font-family: 'JetBrains Mono', monospace !important;
    }
    .compare-column-card {
        background: #ffffff !important;
        border: 1.5px solid #e2e8f0 !important;
        border-radius: 10px !important;
        padding: 10px !important;
        margin-bottom: 8px !important;
        box-shadow: 0 2px 8px rgba(0,0,0,0.03) !important;
        transition: all 0.2s ease !important;
    }
    .compare-column-card:hover {
        border-color: #059669 !important;
        box-shadow: 0 4px 14px rgba(5, 150, 105, 0.12) !important;
    }
    .compare-img-box {
        background: #f8fafc !important;
        border: 1px solid #e2e8f0 !important;
        border-radius: 8px !important;
        padding: 6px !important;
        text-align: center !important;
        margin-bottom: 8px !important;
        min-height: 180px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }
    </style>
    """, unsafe_allow_html=True)

inject_compact_academic_css()


# ═══════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════

def resolve_media_path(path_value: str) -> str:
    """把库里存的图片路径解析成绝对路径。

    数据库里的 image_path 是相对 app 目录存的（data/images/...），只有工作目录
    恰好是 app 目录时 os.path.exists(相对路径) 才为真（start_all.py 会 chdir，
    但 launcher_gui / PyInstaller 启动时不保证），所以统一在这里解析。
    """
    if not path_value:
        return ""
    if os.path.isabs(path_value):
        return path_value
    return os.path.normpath(os.path.join(APP_DIR, path_value))


def get_offline_assistant_reply(query: str, stats: dict, scan_root: str) -> str:
    """系统内置的离线问答规则引擎，无需 API Key 也能精准解答系统操作与库藏情况。"""
    q = query.lower().strip()
    pc = stats.get("paper_count", 0)
    fc = stats.get("figure_count", 0)
    auc = stats.get("with_authors", 0)
    capc = stats.get("with_caption", 0)

    if any(k in q for k in ["选择文件夹", "文件夹", "路径", "windows", "文件管理", "资源管理器"]):
        return (
            f"📂 **文件夹选择与文献分类指引**：\n\n"
            f"1. **像 Windows 一样选择文件夹**：\n"
            f"   展开侧边栏「⚙️ 设定文献总文件夹与分类」，点击 **「🖥️ 弹出 Windows 文件管理器选择…」**，即可在 Windows 屏幕前弹出原生浏览窗口，鼠标点选确定即可秒级切换！也可以使用内置的网页目录树逐级点选。\n"
            f"2. **大文件夹相同，分类可独立定制**：\n"
            f"   电脑上的总文件夹与网站完全相同，包含所有文献；分类一般默认与电脑子目录相同，但也可以在网站为文献设定独立分类标签。\n"
            f"3. **导入时指定分类**：\n"
            f"   每次导入文献（网页拖入上传或扫描同步）时，都可下拉选择要存入哪个分类，并支持勾选「电脑同步移动归档」（默认开启，电脑与网站保持一致）。"
        )
    elif any(k in q for k in ["导入", "增加", "新增", "怎么加", "放哪里", "上传", "分类", "更新", "pdf"]):
        return (
            f"📥 **文献导入与分类指引**：\n\n"
            f"1. **直接网页上传导入**：在侧边栏或主界面「📥 导入新文献」，直接拖拽 PDF，选择目标分类（如 `电催化`、`手性材料` 或新建分类），点击导入后自动保存至对应电脑子目录并即刻解析入库！\n"
            f"2. **电脑文件同步导入**：放入电脑大文件夹后，点击「🔄 同步入库电脑新文献」，可选择【按电脑子目录自动分类（默认）】或【统一归入指定分类】。\n"
            f"3. **随时调整分类**：在文献卡片或研读工作台点击「📁 换分类」，支持电脑物理移动与纯网站分类双模式。"
        )
    elif any(k in q for k in ["有无", "区别", "配置ai", "没配", "没有key", "api key", "离线", "锚定", "正文讨论", "提炼"]):
        return (
            f"🎯 **图文双向锚定与结论提炼：有无配置 AI 的区别**：\n\n"
            f"1. **图文双向快速锚定（秒级跳页）**：\n"
            f"   • **无论是否配置 AI 均可秒级使用**：基于本地毫秒级几何与正则拓扑算法，直接扫描提取正文中讨论该图的全部段落与对应 PDF 页码，点击「📍 跳至该页」即可在阅读器中直达对应页面，完全不消耗任何网络与费用！\n\n"
            f"2. **提炼图表核心结论的双引擎机制**：\n"
            f"   • **💡 离线模式（未配置 API Key 时）**：\n"
            f"     完全免费、零网络依赖、秒级即享！系统内置化学/材料学术语义规则库，自动扫描提取正文中包含特征机理与对比动词的句子（如 demonstrates、reveals、increases by 等）作为核心论断。\n"
            f"   • **🤖 在线模式（配置了 DeepSeek / Claude / OpenAI API Key 时）**：\n"
            f"     调用大模型融会贯通正文与实验数据，用 2~3 句精炼的学术中文总结反应机理、活性提升倍数与特征峰位归属；\n"
            f"   • **按需点选 + 永久存库**：看哪张点哪张，提炼一次永久存入本地数据库，下次打开秒现，绝不重复花钱！"
        )
    elif any(k in q for k in ["对比", "并排", "比对", "横向", "多图"]):
        return (
            f"🔬 **多图横向对比幕布 (Figure Comparator) 使用指引**：\n\n"
            f"1. **添加对比**：在「🔬 图片检索」Tab 或文献研读工作台的插图画廊中，点击任意图片下方的 **「🔬 加入横向对比」**（最多可同时比对 4 张）；\n"
            f"2. **并排对照**：顶部会自动升起 **「多图横向对比幕布」**，等宽并排展示多张高清实验图谱、期刊出处与原作者详细图注；\n"
            f"3. **一键研读**：对比发现某篇论文机理特别关键时，点击卡片下方 **「📖 研读本文献 ➡」** 即可秒级直达该文献的双栏工作台；\n"
            f"4. **灵活移除与清空**：随时点击右上角「✕ 移出」或「🗑 清空对比栏」，操作毫无负担！"
        )
    elif any(k in q for k in ["图表", "图片", "插图", "cv", "xrd", "sem", "tem", "图注", "caption", "figure"]):
        return (
            f"🔬 **插图与图注检索秘籍**：\n\n"
            f"1. **切换到「🔬 图片检索」Tab**：输入您关注的表征或测试类型，如 `CV 曲线`、`XRD 谱图`、`SEM 图像`、`反应机理`。\n"
            f"2. **跨语言自动展开**：输入中文（如“循环伏安”），系统会自动拆分并检索 `cyclic voltammetry` 与 `CV`。\n"
            f"3. **就近图注匹配**：采用几何 Bbox 空间邻近绑定，精准对齐插图与图注，点击「进入研读工作台」可直达原文对应页面。"
        )
    elif any(k in q for k in ["引文", "引用", "bibtex", "gb/t", "gbt", "apa", "markdown", "导出"]):
        return (
            f"📑 **引用导出技巧**：\n\n"
            f"1. **单篇导出**：在文献卡片点击「📋 引用」标签页，支持直接复制 `BibTeX`、`GB/T 7714-2015`、`APA 7th` 与 `Markdown` 四种格式。\n"
            f"2. **全库批量导出**：在主界面顶部点击「📚 批量导出全库/筛选文献引用」，可一键将当前筛选的所有论文导出为完整 `.bib` 文件供 Zotero / LaTeX 使用。"
        )
    elif any(k in q for k in ["作者", "期刊", "补全", "年份", "crossref", "openalex", "题录"]):
        return (
            f"🚀 **权威题录智能补全**：\n\n"
            f"1. 系统无缝对接全球开放学术图谱（Crossref 与 OpenAlex）。\n"
            f"2. 刚导入的 PDF 若作者或期刊缺失，只需点击侧边栏 **「🚀 批量智能补全题录」**，即可全自动秒级补齐官方作者名录与顶级刊物名称（如 *Nature*, *JACS*, *Angewandte* 等）。"
        )
    elif any(k in q for k in ["多少", "统计", "概况", "数据", "总览", "库里"]):
        return (
            f"📊 **当前文献库最新数据**：\n\n"
            f"- 📄 **文献总数**：{pc} 篇\n"
            f"- 🖼️ **高分辨率图表**：{fc} 张\n"
            f"- 📝 **就近提取图注**：{capc} 条\n"
            f"- 👤 **已补全作者题录**：{auc} 篇\n\n"
            f"全部文献均已建立全文与图注高速倒排检索索引！"
        )
    elif any(k in q for k in ["阅读", "pdf", "全屏", "划线", "复制", "双栏", "工作台"]):
        return (
            f"📖 **沉浸式研读工作台**：\n\n"
            f"1. 点击任意文献卡片的 **「📖 进入研读工作台 ➡」** 即可切换到专属双栏阅读模式。\n"
            f"2. **左栏**：嵌入式 HTML5 原生阅读器，支持自由划线选词、文本复制，绝不刷新页面；\n"
            f"3. **右栏**：专属单篇 ChatPDF 助手，随时向文献提问核心实验机理与反应条件。"
        )
    else:
        return (
            f"💡 **PaperLibrary 智能助手**：\n\n"
            f"您好！我是您的学术文献管理 Copilot。您可以问我：\n"
            f"- 📥 *怎么导入新下载的论文？*\n"
            f"- 🔬 *怎么检索具体的图表（如 CV 曲线）？*\n"
            f"- 📑 *如何复制 BibTeX 引用？*\n"
            f"- 🚀 *怎么批量补全作者与期刊？*\n"
            f"- 📊 *当前库藏统计概况*\n\n"
            f"*(提示：若您在下方填入 DeepSeek 或 Claude 的 API Key，还可以进行深度学术问答与论文联想！)*"
        )


def render_relevance(item: dict, snippet_key: str = "snippet",
                     snippet_len: int = 220) -> None:
    """显示「这条为什么出来、凭什么排这么前」的证据。

    文献检索和图片检索共用同一套徽章，避免两处样式漂移。
    """
    if item.get("relevance") is None:
        return
    rel = float(item.get("relevance") or 0)
    color = "#235648" if rel >= 60 else "#8a6d1f" if rel >= 25 else "#75726a"
    bg = "#e4efea" if rel >= 60 else "#fbf3dd" if rel >= 25 else "#f0efea"
    reason = html_mod.escape(str(item.get("match_reason") or ""))
    st.markdown(
        f'<div style="display:flex;gap:10px;align-items:center;'
        f'margin:.1rem 0 .45rem">'
        f'<span style="background:{bg};color:{color};'
        f'border-radius:6px;padding:2px 9px;font-size:.78rem;'
        f'font-weight:700">相关性 {rel:g}</span>'
        f'<span style="color:#75726a;font-size:.78rem">{reason}</span></div>',
        unsafe_allow_html=True)
    text = item.get(snippet_key)
    if text:
        st.markdown(
            f'<div style="border-left:2px solid #e2dfd5;'
            f'padding-left:10px;color:#5b584e;font-size:.83rem;'
            f'line-height:1.7;margin-bottom:.4rem">'
            f'{html_mod.escape(str(text)[:snippet_len])}</div>',
            unsafe_allow_html=True)


def render_figure_comparator_section():
    """多图横向对比幕布 (Figure Side-by-Side Comparator) 渲染器
    设计原则：极简、高容错、易上手、直观并排。
    """
    basket = comparator.get_basket(st.session_state)
    if not basket:
        return

    n = len(basket)
    is_open = comparator.is_compare_open(st.session_state)

    # 1. 顶层轻量对比托盘条 (Compare Tray Banner)
    st.markdown(f"""
    <div class="compare-tray-banner">
        <div class="compare-tray-title">
            <span>🔬</span>
            <span>多图横向对比幕布</span>
            <span class="compare-tray-badge">已选 {n}/{comparator.MAX_COMPARE_COUNT} 张</span>
        </div>
        <div style="font-size:12.5px; color:#047857; font-weight:500;">
            并排比对不同论文的核心实验曲线、机理图与图注
        </div>
    </div>
    """, unsafe_allow_html=True)

    c_b1, c_b2, c_b3 = st.columns([2.5, 5, 2.5])
    with c_b1:
        toggle_label = "✕ 收起对比幕布" if is_open else f"👀 展开并排对比 ({n} 张)"
        if st.button(toggle_label, key="btn_toggle_cmp_stage", type="primary" if not is_open else "secondary", use_container_width=True):
            comparator.set_compare_open(st.session_state, not is_open)
            st.rerun()
    with c_b3:
        if st.button("🗑 清空对比栏", key="btn_clear_cmp_stage", use_container_width=True):
            comparator.clear_basket(st.session_state)
            comparator.set_compare_open(st.session_state, False)
            st.rerun()

    # 2. 对比幕布主体展开区域
    if is_open:
        if n == 1:
            st.info(
                f"💡 当前对比栏已有 1 张图表（来自《{basket[0].get('paper_title', '未知文献')[:35]}》）。\n\n"
                f"👉 **请在下方搜索结果或文献画廊中，为第二张图点击「🔬 加入横向对比」，即可立即在此处 2~4 栏并排比对！**"
            )
        else:
            with st.container(border=True):
                st.markdown(f"""
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px; padding-bottom:8px; border-bottom:1px solid #e2e8f0;">
                    <span style="font-weight:700; color:#0f172a; font-size:14px;">📊 正在并排比对 {n} 篇文献的实验图表与关键图注：</span>
                    <span style="font-size:12px; color:#64748b;">左右横向并排对照研读</span>
                </div>
                """, unsafe_allow_html=True)

                cols = st.columns(n)
                for c_idx, item in enumerate(basket):
                    with cols[c_idx]:
                        with st.container(border=True):
                            # a. 顶栏：图号与移除按钮
                            h1, h2 = st.columns([3, 1.2])
                            with h1:
                                ft = item.get("figure_type") or "Figure"
                                st.markdown(f"<span style='font-weight:700; color:#059669; font-size:13px;'>图 {c_idx+1} · {html_mod.escape(ft)}</span>", unsafe_allow_html=True)
                            with h2:
                                if st.button("✕ 移出", key=f"cmp_rm_{item['id']}_{c_idx}", use_container_width=True, help="从对比幕布移出本图"):
                                    comparator.remove_from_basket(st.session_state, item["id"])
                                    st.rerun()

                            # b. 高清图片展示
                            ip = resolve_media_path(item.get("image_path", ""))
                            if ip and os.path.isfile(ip):
                                st.image(ip, use_container_width=True)
                            else:
                                st.caption("⚠️ 图片文件缺失或正在加载")

                            # c. 出处信息
                            p_title = item.get("paper_title") or "未知文献"
                            journal = item.get("journal") or ""
                            year = item.get("year") or ""
                            page_num = item.get("page_number") or ""

                            meta_pills = []
                            if journal:
                                meta_pills.append(f"<span class='meta-pill meta-pill-journal'>🏛️ {html_mod.escape(journal)}</span>")
                            if year:
                                meta_pills.append(f"<span class='meta-pill meta-pill-year'>📅 {year}</span>")
                            if page_num:
                                meta_pills.append(f"<span class='meta-pill meta-pill-author'>📑 P.{page_num}</span>")
                            if meta_pills:
                                st.markdown(f"<div class='meta-pills-row' style='margin:4px 0;'>{' '.join(meta_pills)}</div>", unsafe_allow_html=True)

                            st.markdown(f"<div style='font-size:12.5px; font-weight:600; color:#1e293b; margin:4px 0; line-height:1.3;' title='{html_mod.escape(p_title)}'>📄 {html_mod.escape(p_title[:55])}</div>", unsafe_allow_html=True)

                            # d. 原文图注
                            cap = (item.get("caption") or "").strip()
                            if cap:
                                st.markdown(f"""
                                <div class="caption-card-box" style="margin-top:6px; max-height:160px; overflow-y:auto;">
                                  <div class="cap-label">📝 提取图注 (Caption)</div>
                                  <div class="cap-body" style="font-size:11.5px; line-height:1.4;">{html_mod.escape(cap)}</div>
                                </div>
                                """, unsafe_allow_html=True)
                            else:
                                st.markdown("""
                                <div class="caption-card-box" style="margin-top:6px; border-left-color:#94a3b8;">
                                  <div class="cap-label" style="color:#94a3b8;">📝 图注 (Caption)</div>
                                  <div class="cap-body" style="color:#94a3b8; font-style:italic; font-size:11.5px;">（原文未就近提取到独立图注）</div>
                                </div>
                                """, unsafe_allow_html=True)

                            # e. 跳转研读工作台
                            p_id = item.get("paper_id")
                            if p_id:
                                if st.button("📖 研读本文献 ➡", key=f"cmp_to_wb_{item['id']}_{c_idx}", use_container_width=True):
                                    st.session_state["selected_paper_id"] = p_id
                                    st.rerun()


def show_figures(paper_id: str, limit: int = 4, per_row: int = 2, caption_len: int = 60,
                 thumbs_only: bool = True, figs=None):
    """在当前位置用若干列并排展示某篇文献的图片，返回该文献图片总数。

    性能两点：
      · figs 可由调用方预取（一次查完所有文献的图片），避免每篇各开一次数据库连接
        （140 篇实测 1011 ms → 预取后只需一次查询）
      · 默认用缩略图（480×360 的 JPEG 缓存）而不是原图。库里 4566 张图共 882 MB，
        首屏 537 张原图就是 114 MB —— 这是页面加载慢的主因。
    缩略图生成不上时自动回退原图，不会因此丢图。
    """
    if figs is None:
        figs = database.get_paper_figures(paper_id)
    items = []
    for f in figs:
        p = resolve_media_path(f.get("image_path", ""))
        if p and os.path.isfile(p):
            items.append((f, p))
        if len(items) >= limit:
            break
    for r in range(0, len(items), per_row):
        row = items[r:r + per_row]
        cols = st.columns(len(row))
        for ci, (f, p) in enumerate(row):
            with cols[ci]:
                shown = p
                if thumbs_only:
                    t = thumbs.make_thumb(p, APP_DIR)
                    if t:
                        shown = t
                st.image(shown, width="stretch")
                cap = (f.get("caption") or "").strip().replace("\n", " ")
                if cap:
                    st.caption(cap[:caption_len])
    return len(figs)


@st.cache_data(show_spinner=False, ttl=300)
def _figures_map(paper_ids: tuple):
    """一次取回这批文献的全部图片（按 ids 记忆 5 分钟）。

    原先列表页对每篇文献各开一次数据库连接查 figures，140 篇实测 1011 ms；
    预取后只需一次查询。ttl 保证新增/移动文献后最多 5 分钟内会刷新。
    """
    try:
        return database.get_figures_map(list(paper_ids))
    except Exception:
        return {}


# ── 站内 PDF 阅读器 ──
@st.cache_data(show_spinner=False)
def _pdf_page_count(abs_path: str, mtime: float) -> int:
    """返回 PDF 页数（按文件修改时间缓存）"""
    try:
        with fitz.open(abs_path) as doc:
            return doc.page_count
    except Exception:
        return 0


@st.cache_data(show_spinner=False)
def _pdf_page_image(abs_path: str, page: int, mtime: float, dpi: int = 120) -> bytes:
    """渲染 PDF 单页为 PNG 字节（缓存）"""
    try:
        with fitz.open(abs_path) as doc:
            if page < 0 or page >= doc.page_count:
                return b""
            return doc[page].get_pixmap(dpi=dpi).tobytes("png")
    except Exception:
        return b""


# 站内 PDF 阅读器：可选视窗高度（数值越大，一屏能完整呈现的页面越大越清晰）
PDF_VIEWER_HEIGHTS = [1280, 1080, 1440, 1680, 1920]
PDF_VIEWER_DEFAULT_H = 1280
PDF_HEIGHT_AUTO = "🖥️ 跟随浏览器铺满（一屏一页）"


def _render_pdf_iframe(url: str, height: int = 1080):
    """安全渲染 iframe，优先使用现代 Streamlit st.iframe API"""
    if hasattr(st, "iframe"):
        try:
            st.iframe(url, height=height, width="stretch")
        except TypeError:
            st.iframe(url, height=height)
    else:
        components.iframe(url, height=height, scrolling=True)


def _pdf_fill_viewport_css(fill_height: bool = True):
    """阅读器铺满网页：宽度 100%；fill_height=True 时高度跟随浏览器视窗（CSS 覆盖 px 高度）。

    Streamlit 通过 iframe 的 height 属性设置高度，属性优先级低于任何 CSS 规则，
    因此这里的 !important 规则可以把阅读器拉成整窗大小，一屏正好一页。
    """
    height_rule = ""
    if fill_height:
        height_rule = """
          div[data-testid="stIFrame"] iframe,
          iframe.stCustomComponentV1 {
              height: max(calc(100vh - 190px), 820px) !important;
          }
        """
    st.markdown(f"""
        <style>
          /* 阅读器横向彻底铺满：去掉圆角留白与容器左右内边距 */
          div[data-testid="stIFrame"] {{ width: 100% !important; margin: 0 !important; }}
          div[data-testid="stIFrame"] iframe,
          iframe.stCustomComponentV1 {{
              width: 100% !important;
              max-width: 100% !important;
              display: block !important;
              border: 0 !important;
              border-radius: 10px !important;
              background: #0d1017 !important;
          }}
          {height_rule}
          div[data-testid="stElementContainer"]:has(> div[data-testid="stIFrame"]) {{
              padding-left: 0 !important;
              padding-right: 0 !important;
          }}
        </style>
        """, unsafe_allow_html=True)


def show_pdf_reader(abs_path: str, key_prefix: str):
    """站内 PDF 阅读器：现代 HTML5 流式阅读（基于 Mozilla PDF.js，全兼容 Edge/Chrome，彻底免疫 iframe 拦截）
    + 原生插件直连 + 经典逐页渲染降级
    """
    with st.expander("📖 站内阅读 PDF（无需离开网页）", expanded=False):
        try:
            if not os.path.isfile(abs_path):
                st.error("❌ PDF 文件不存在，可能已被移动或删除")
                return

            enc_path = urllib.parse.quote(abs_path, safe="")
            html5_url = f"http://localhost:8502/view?path={enc_path}&fit=page"
            raw_url = f"http://localhost:8502/?path={enc_path}"

            mode_key = f"pdf_mode_{key_prefix}"
            reader_mode = st.radio("阅读模式",
                                   ["🌐 HTML5 流式阅读（全兼容 Edge / Chrome，抗拦截）",
                                    "⚡ 原生插件直连",
                                    "🖼️ 逐页图像预览"],
                                   horizontal=True, key=mode_key, label_visibility="collapsed")

            if "HTML5" in reader_mode:
                _render_pdf_iframe(html5_url, height=960)
            elif "原生" in reader_mode:
                _render_pdf_iframe(raw_url, height=960)
            else:
                mt = os.path.getmtime(abs_path)
                npages = _pdf_page_count(abs_path, mt)
                if npages <= 0:
                    st.error("⚠️ PDF 解析失败，请用上方「浏览器打开」")
                    return
                key = f"pdfpg_{key_prefix}"
                st.session_state.setdefault(key, 1)
                b1, b2, b3, _ = st.columns([1, 1, 1.2, 3])
                with b1:
                    if st.button("⬅ 上一页", key=f"pdfprev_{key_prefix}", width="stretch"):
                        st.session_state[key] = max(1, st.session_state[key] - 1)
                        st.rerun()
                with b2:
                    if st.button("下一页 ➡", key=f"pdfnext_{key_prefix}", width="stretch"):
                        st.session_state[key] = min(npages, st.session_state[key] + 1)
                        st.rerun()
                with b3:
                    pg = int(st.number_input("页码", 1, npages, key=key))
                with st.spinner("🖨️ 渲染页面中…"):
                    img = _pdf_page_image(abs_path, pg - 1, mt)
                if img:
                    st.image(img, width="stretch")
                    st.caption(f"第 {pg} / {npages} 页")
                else:
                    st.warning("⚠️ 页面渲染失败，请用上方「浏览器打开」")
        except Exception as e:
            st.warning(f"⚠️ 站内阅读暂不可用: {e}")


def render_delete_button(paper: dict, key_suffix: str = "default"):
    """渲染带二次确认气泡框的删除按钮 (Popover)"""
    pid = paper["id"]
    if hasattr(st, "popover"):
        pop = st.popover("🗑️ 删除", help="从文献库中移除本条文献")
    else:
        pop = st.expander("🗑️ 删除选项", expanded=False)

    with pop:
        st.markdown("**⚠️ 确认删除此项？**")
        p_name = paper.get("title") or paper.get("filename") or pid
        st.caption(f"文献：*{p_name[:75]}*")

        ap = paper.get("abs_path", "")
        has_file = bool(ap and os.path.exists(ap))
        del_file = False
        if has_file:
            del_file = st.checkbox("同时彻底删除本地原 PDF 文件",
                                   key=f"del_chk_{pid[:12]}_{key_suffix}",
                                   help="默认不勾选：仅从文献库移除索引与图表缓存，保留磁盘上的原文件。若勾选则会将本地硬盘上的 PDF 也一并删除。")
        else:
            st.caption("（注：本条记录无本地 PDF 文件，将仅清理元数据与图表缓存）")

        btn_label = "💥 彻底删除文件及记录" if del_file else "🗑️ 从文献库移除"
        if st.button(btn_label, key=f"del_btn_{pid[:12]}_{key_suffix}", type="primary", use_container_width=True):
            ok, msg = database.delete_paper(pid, delete_source_file=del_file)
            if ok:
                st.success(f"✅ {msg}")
                time.sleep(0.6)
                st.rerun()
            else:
                st.error(f"❌ {msg}")


def _trigger_paper_chat(paper, history, user_query, provider, api_key, model, chat_key):
    pid = paper["id"]
    p_full = database.get_paper_by_id(pid) or paper
    figs = database.get_paper_figures(pid) or []

    history.append({"role": "user", "content": user_query})
    with st.spinner("🧠 论文助手正在研读论文全文与高精度图注，生成学术解答中…"):
        try:
            ans = ai_engine.chat_with_paper(
                paper=p_full,
                figures=figs,
                history=history[:-1],
                user_query=user_query,
                provider=provider or "deepseek",
                api_key=api_key,
                model=model or None
            )
            history.append({"role": "assistant", "content": ans})
        except Exception as e:
            history.append({"role": "assistant", "content": f"⚠️ 调用失败：{e}"})
    st.session_state[chat_key] = history
    st.rerun()


def render_paper_chat(paper: dict, api_provider: str, api_key: str, api_model: str,
                      standalone: bool = False, compact: bool = False):
    """文献专属端到端 AI 对话助手 (ChatPDF)：结合全文正文与全篇高精度图注进行深度学术解答

    compact=True 时按窄栏（并排右栏）排版：快捷芯片单列、输入框独占一行。
    """
    pid = paper["id"]
    chat_key = f"chat_history_{pid}"
    st.session_state.setdefault(chat_key, [])
    history = st.session_state[chat_key]

    def _render_chat_content():
        st.caption("基于本篇论文的**完整正文**与**提取的高精度图注清单**进行深度学术问答，支持前驱体配比、催化机理、谱图比对等提问。")

        # 检查 API Key
        active_key = api_key or st.session_state.get("custom_chat_api_key", "")
        if not active_key:
            st.info("💡 请在**左侧边栏**填写 DeepSeek / Claude 的 API Key，或在下方快速输入：")
            quick_key = st.text_input("临时 API Key", type="password", key=f"quick_key_{pid[:12]}", placeholder="sk-...")
            if quick_key:
                active_key = quick_key
                st.session_state["custom_chat_api_key"] = quick_key

        # 快捷科研提问芯片 Prompt Chips：默认折叠，需要时再展开，把纵向空间让给对话与阅读
        _chips = [
            ("syn", "🧪 提炼实验配比与合成条件",
             "请全面提炼本文的实验条件、化学前驱体、摩尔比、溶剂、温度以及关键合成步骤。"),
            ("fig", "📊 对比核心图表 (Figures) 实验数据",
             "请梳理本文所有核心图表（特别是 Figure 1, 2, 3 等），对比不同催化剂/样品的测试结果（如 CV/XRD/SEM）及性能差异。"),
            ("mech", "💡 剖析核心反应机理与决速步",
             "请详细解析本文提出的反应机理（Scheme）、关键反应路径与决速步（RDS）。"),
            ("ec", "⚡ 总结电化学测试条件与指标",
             "请总结本文涉及的电化学测试参数（工作电极、参比电极、电解质、扫速、过电位、Tafel斜率、法拉第效率等）。"),
        ]
        with st.expander(f"🔬 常用科研深度提问快捷指令（{len(_chips)} 条，点击展开）", expanded=False):
            if compact:
                for _cid, _label, _q in _chips:
                    if st.button(_label, key=f"chip_{_cid}_{pid[:12]}", width="stretch", disabled=not active_key):
                        _trigger_paper_chat(paper, history, _q, api_provider, active_key, api_model, chat_key)
            else:
                p_col1, p_col2 = st.columns(2)
                _chip_cols = [p_col1, p_col2]
                for _i, (_cid, _label, _q) in enumerate(_chips):
                    with _chip_cols[_i % 2]:
                        if st.button(_label, key=f"chip_{_cid}_{pid[:12]}", width="stretch", disabled=not active_key):
                            _trigger_paper_chat(paper, history, _q, api_provider, active_key, api_model, chat_key)

        st.divider()

        # 对话历史展示
        if history:
            for msg in history:
                role = msg.get("role", "user")
                with st.chat_message("user" if role == "user" else "assistant"):
                    st.markdown(msg.get("content", ""))
        else:
            st.caption("（暂无对话历史，点击上方快捷芯片或在下方输入问题开始与论文对话）")

        # 提问输入框（并排窄栏时输入框独占一行、发送键下置）
        if compact:
            user_input = st.text_input("对本文提问", key=f"chat_in_{pid[:12]}",
                                       placeholder="例如：合成催化剂的摩尔比是多少？",
                                       label_visibility="collapsed")
            send_click = st.button("🚀 发送提问", key=f"chat_send_{pid[:12]}", width="stretch", disabled=not active_key)
        else:
            in_col1, in_col2 = st.columns([5, 1])
            with in_col1:
                user_input = st.text_input("对本文提问", key=f"chat_in_{pid[:12]}",
                                           placeholder="例如：合成催化剂的摩尔比是多少？对比 Figure 3a 和 3b 的电化学差异？",
                                           label_visibility="collapsed")
            with in_col2:
                send_click = st.button("🚀 发送", key=f"chat_send_{pid[:12]}", width="stretch", disabled=not active_key)

        if send_click and user_input.strip():
            _trigger_paper_chat(paper, history, user_input.strip(), api_provider, active_key, api_model, chat_key)

        if history:
            if st.button("🗑️ 清空当前对话历史", key=f"clear_chat_{pid[:12]}"):
                st.session_state[chat_key] = []
                st.rerun()

    if standalone:
        _render_chat_content()
    else:
        with st.expander("💬 论文专属 AI 对话助手 (Chat with Paper / ChatPDF)", expanded=bool(history)):
            _render_chat_content()


def render_paper_detail_page(paper_id: str, api_provider: str, api_key: str, api_model: str):
    """独立的文献全屏研读工作台（子页面）：
    提供顶栏返回、完整题录信息、权威引文、左右双栏（现代 HTML5 流式阅读器 + 专属 AI 对话助手）、底栏图表全景画廊
    """
    p = database.get_paper_by_id(paper_id)
    if not p:
        st.error("⚠️ 未找到该文献数据，可能已被移除")
        if st.button("⬅ 返回文献目录", type="primary"):
            st.session_state["selected_paper_id"] = None
            st.rerun()
        return

    ap = p.get("abs_path", "")
    pdf_exists = bool(ap and os.path.exists(ap))
    enc_path = urllib.parse.quote(ap, safe="") if pdf_exists else ""
    raw_pdf_url = f"http://localhost:8502/?path={enc_path}" if pdf_exists else ""
    html5_pdf_url = f"http://localhost:8502/view?path={enc_path}" if pdf_exists else ""

    # 1. 顶栏返回与快捷操作工具条（外加边框隔离容器，避免与内容混杂）
    with st.container(border=True):
        top_c1, top_c2, top_c3, top_c4, top_c5, top_c6, top_c7 = st.columns([1.8, 2.5, 1.6, 1.6, 1.4, 1.6, 1.2])
        with top_c1:
            if st.button("⬅ 返回文献目录", key="btn_back_to_catalog", type="primary", use_container_width=True):
                st.session_state["selected_paper_id"] = None
                st.rerun()
        with top_c2:
            cur_cat = p.get('folder') or '根目录'
            with st.popover(f"📁 {cur_cat} ▾", use_container_width=True):
                st.markdown("**🔀 调整文献所属分类**")
                st.caption("大文件夹保持一致，分类可独立定制：")
                avail_cats = importer.get_existing_categories(get_scan_root())
                cat_idx = avail_cats.index(cur_cat) if cur_cat in avail_cats else 0
                chosen_cat = st.selectbox("选择目标分类", avail_cats + ["➕ 新建分类..."], index=cat_idx, key=f"wb_sel_cat_{p['id'][:8]}")
                new_cat_txt = ""
                if chosen_cat == "➕ 新建分类...":
                    new_cat_txt = st.text_input("新分类名称", placeholder="例如：电催化 / 手性材料", key=f"wb_new_cat_{p['id'][:8]}")
                final_wb_cat = new_cat_txt.strip() if chosen_cat == "➕ 新建分类..." and new_cat_txt.strip() else chosen_cat

                sync_file_pc = st.checkbox("电脑同步移动物理文件（保持一致）", value=True, key=f"wb_sync_pc_{p['id'][:8]}",
                                           help="勾选后在电脑大文件夹中真正移动该 PDF 文件到对应子目录；若取消勾选，则仅修改网站中的分类标签")
                if st.button("确认修改分类", key=f"wb_btn_save_cat_{p['id'][:8]}", type="primary", use_container_width=True):
                    if sync_file_pc and p.get("abs_path") and os.path.isfile(p["abs_path"]):
                        try:
                            dest_rel = "" if final_wb_cat == "根目录" else final_wb_cat
                            new_abs = library_fs.move_pdf(get_scan_root(), p["abs_path"], dest_rel)
                            sync_paper_path(p["abs_path"], new_abs)
                            database.update_paper_folder(p["id"], final_wb_cat)
                            st.success(f"已同步移动至电脑「{final_wb_cat}」并更新分类！")
                            time.sleep(0.5)
                            st.rerun()
                        except Exception as e:
                            st.error(f"移动失败: {e}")
                    else:
                        ok, msg = database.update_paper_folder(p["id"], final_wb_cat)
                        if ok:
                            st.success(f"网站分类已更新为「{final_wb_cat}」！")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error(msg)
        with top_c3:
            if pdf_exists:
                st.link_button("📖 新标签全屏", raw_pdf_url, help="在新标签页全屏打开 PDF 原文", use_container_width=True)
            else:
                st.button("📖 新标签全屏", disabled=True, use_container_width=True)
        with top_c4:
            if pdf_exists:
                if st.button("🖥️ 外部打开", key="dt_sys_open", help="使用电脑默认 PDF 阅读器打开", use_container_width=True):
                    webbrowser.open(ap)
            else:
                st.button("🖥️ 外部打开", disabled=True, use_container_width=True)
        with top_c5:
            if pdf_exists:
                st.download_button("⬇ 下载", data=open(ap, "rb"), file_name=os.path.basename(ap),
                                   mime="application/pdf", key="dt_dl_btn", use_container_width=True)
            else:
                st.button("⬇ 下载", disabled=True, use_container_width=True)
        with top_c6:
            if st.button("🔄 智能补全", key="dt_enrich_btn", help="从 Crossref/OpenAlex 补齐作者与期刊", use_container_width=True):
                with st.spinner("检索权威元数据中…"):
                    ok, msg = metadata_enricher.enrich_and_update_db(os.path.join(APP_DIR, "data", "papers.db"), p["id"])
                    if ok:
                        st.success(f"✅ {msg}")
                        time.sleep(0.6)
                        st.rerun()
                    else:
                        st.warning(f"⚠️ {msg}")
        with top_c7:
            render_delete_button(p, key_suffix="dt_detail")

    # 2. 文献头部信息（Hero Header - 现代面包屑 + 标题 + 胶囊徽章）
    title = str(p.get("title") or p.get("filename") or "未命名文献")
    folder_name = p.get('folder') or '根目录'
    st.markdown(f"""
    <div style="font-size: 12px; color: #94a3b8; margin-bottom: 4px; display: flex; align-items: center; gap: 6px;">
      <span>🏠 文献库</span>
      <span style="opacity:0.4;">/</span>
      <span>📁 {html_mod.escape(folder_name)}</span>
      <span style="opacity:0.4;">/</span>
      <span style="color: #cbd5e1; font-weight: 600;">📖 研读工作台</span>
    </div>
    <div style="font-size:19px; font-weight:700; line-height:1.32; margin-bottom:5px; letter-spacing:-0.015em; color:#f8fafc;">{html_mod.escape(title)}</div>
    """, unsafe_allow_html=True)

    # 题录元数据胶囊徽章行
    authors = citation.parse_authors(p.get("authors"))
    authors_str = "、".join(authors) if authors else "未知作者"
    journal = (p.get("journal") or "").strip()
    year = p.get("year") or "未知年份"
    doi = (p.get("doi") or "").strip()

    pills = [f"<span class='meta-pill meta-pill-author' title='作者列表'>👤 {html_mod.escape(authors_str)}</span>"]
    if journal:
        pills.append(f"<span class='meta-pill meta-pill-journal' title='出版期刊'>🏛️ {html_mod.escape(journal)}</span>")
    if year:
        pills.append(f"<span class='meta-pill meta-pill-year' title='发表年份'>📅 {year}</span>")
    if doi:
        pills.append(f"<span class='meta-pill meta-pill-doi' title='数字对象唯一标识符 DOI'>🔗 {html_mod.escape(doi)}</span>")
    st.markdown(f"<div class='meta-pills-row' style='margin-bottom:8px;'>{' '.join(pills)}</div>", unsafe_allow_html=True)

    # 3. 权威学术引文面板（紧凑展开层）
    with st.expander("📑 复制本篇文献学术引文（BibTeX / GB/T 7714 / APA / Markdown）", expanded=False):
        c_tab1, c_tab2, c_tab3, c_tab4 = st.tabs(["BibTeX", "GB/T 7714", "APA 7th", "Markdown"])
        with c_tab1:
            st.code(citation.format_bibtex(p), language="bibtex")
        with c_tab2:
            st.code(citation.format_gbt7714(p), language="text")
        with c_tab3:
            st.code(citation.format_apa(p), language="text")
        with c_tab4:
            st.code(citation.format_markdown_ref(p), language="markdown")

    # 4. 核心研读分栏区与视窗控制（支持沉浸巨幕铺满与黄金双栏）
    wb_ctl1, wb_ctl2, wb_ctl3 = st.columns([2.4, 1.4, 1.2])
    with wb_ctl1:
        wb_layout = st.radio("工作台版式", ["⚖️ 黄金双栏研读（PDF 原文 + AI 顾问并排）", "🖥️ 沉浸巨幕阅读（PDF 全宽 / AI 折叠下方）"],
                             horizontal=True, key=f"wb_layout2_{p['id'][:12]}", label_visibility="collapsed")
    with wb_ctl2:
        wb_fit = st.radio("页面适配", ["📄 适应整页（完整一页）", "↔️ 视窗撑满（横向铺满）"],
                          horizontal=True, key=f"wb_fit_{p['id'][:12]}", label_visibility="collapsed")
    with wb_ctl3:
        wb_height = st.selectbox("视窗高度",
                                 [PDF_HEIGHT_AUTO] + PDF_VIEWER_HEIGHTS,
                                 index=0,
                                 format_func=lambda h: h if h == PDF_HEIGHT_AUTO else f"📐 高度 {h}px",
                                 key=f"wb_h_{p['id'][:12]}", label_visibility="collapsed",
                                 help="「跟随浏览器铺满」= 阅读器高度自动等于窗口高度，一屏看完整页；也可固定 1080~1920px")

    # 视窗铺满：宽度始终 100%，高度按选项跟随浏览器（一屏一页）
    wb_fill_viewport = (wb_height == PDF_HEIGHT_AUTO)
    if wb_fill_viewport:
        wb_height = PDF_VIEWER_DEFAULT_H   # 兜底 px 高度，会被 CSS 覆盖为视窗高度
    _pdf_fill_viewport_css(wb_fill_viewport)

    fit_flag = "page" if "整页" in wb_fit else "width"
    html5_pdf_url_with_fit = f"{html5_pdf_url}&fit={fit_flag}" if "?" in html5_pdf_url else f"{html5_pdf_url}?fit={fit_flag}"

    def _draw_pdf_panel():
        with st.container(border=True):
            st.markdown("""
            <div class="workbench-panel-header">
              <span>⚡ 文献 PDF 原文视窗</span>
              <span style="font-size:11.5px; opacity:0.8;">支持划词复制 · 自由缩放 · P/W 快捷适配</span>
            </div>
            """, unsafe_allow_html=True)
            if pdf_exists:
                mode_key = f"dt_pdf_mode_{p['id'][:12]}"
                k_pending_mode = f"pending_pdf_mode_{p['id'][:12]}"
                if k_pending_mode in st.session_state:
                    st.session_state[mode_key] = st.session_state.pop(k_pending_mode)

                r_mode = st.radio("阅读器模式",
                                  ["🌐 HTML5 流畅流式（全兼容 Edge / Chrome，抗拦截）",
                                   "⚡ 浏览器原生插件",
                                   "🖼️ 逐页图像预览"],
                                  horizontal=True, key=mode_key, label_visibility="collapsed")
                if "HTML5" in r_mode:
                    _render_pdf_iframe(html5_pdf_url_with_fit, height=wb_height)
                elif "原生" in r_mode:
                    _render_pdf_iframe(raw_pdf_url, height=wb_height)
                else:
                    mt = os.path.getmtime(ap)
                    npages = _pdf_page_count(ap, mt)
                    if npages > 0:
                        k_pg = f"dt_pdf_page_{p['id'][:12]}"
                        k_pending_pg = f"pending_pdf_page_{p['id'][:12]}"
                        if k_pending_pg in st.session_state:
                            target_v = int(st.session_state.pop(k_pending_pg))
                            st.session_state[k_pg] = max(1, min(npages, target_v))
                        else:
                            st.session_state.setdefault(k_pg, 1)

                        p_b1, p_b2, p_b3 = st.columns([1, 1, 2])
                        with p_b1:
                            if st.button("⬅ 上一页", key=f"dt_prev_{p['id'][:12]}", use_container_width=True):
                                st.session_state[k_pending_pg] = max(1, st.session_state.get(k_pg, 1) - 1)
                                st.rerun()
                        with p_b2:
                            if st.button("下一页 ➡", key=f"dt_next_{p['id'][:12]}", use_container_width=True):
                                st.session_state[k_pending_pg] = min(npages, st.session_state.get(k_pg, 1) + 1)
                                st.rerun()
                        with p_b3:
                            pg = int(st.number_input("页码", 1, npages, key=k_pg))
                        img = _pdf_page_image(ap, pg - 1, mt)
                        if img:
                            st.image(img, use_container_width=True)
                            st.caption(f"第 {pg} / {npages} 页")
            else:
                st.info("⚠️ 当前文献没有本地 PDF 文件，可在上方操作栏尝试补抓原文。")

    def _draw_chat_panel(compact: bool = True):
        # 常驻展开：不再用 expander 折叠，AI 顾问始终可见；容器与左侧 PDF 面板等高（CSS 拉伸，见 16.2）
        with st.container(border=True):
            st.markdown('<span class="wb-eq-h-anchor"></span>', unsafe_allow_html=True)
            st.markdown("""
            <div class="workbench-panel-header">
              <span>💬 论文专属 AI 问答顾问</span>
              <span style="font-size:11.5px; opacity:0.8;">全文正文 + 空间图注融合</span>
            </div>
            """, unsafe_allow_html=True)
            render_paper_chat(p, api_provider, api_key, api_model, standalone=True, compact=compact)

    if "沉浸" in wb_layout:
        _draw_pdf_panel()
        _draw_chat_panel(compact=False)
    else:
        # 并排研读：左侧文献 PDF 原文视窗 + 右侧论文专属 AI 问答顾问（两栏边框等高齐平）
        col_pdf, col_chat = st.columns([2.15, 1.15], gap="medium")
        with col_pdf:
            _draw_pdf_panel()
        with col_chat:
            _draw_chat_panel(compact=True)

    # 5. 底栏：结构化摘要与全部提取图表（各独立卡片面板）
    with st.container(border=True):
        st.markdown('<div class="workbench-panel-header"><span>📝 结构化全文摘要与深度解读</span></div>', unsafe_allow_html=True)
        sum_txt = database._readable_summary(p.get("summary")) or p.get("full_text", "")
        if sum_txt:
            st.markdown(sum_txt[:2500] + ("…" if len(sum_txt) > 2500 else ""))
        else:
            st.caption("（暂无摘要或总结）")

    figs = database.get_paper_figures(p["id"])
    # 4.5 深度分析报告 / arXiv 高清图表升级 / Obsidian 导出
    render_deep_analysis_panel(p, api_provider, api_key, api_model)
    render_figure_comparator_section()
    with st.container(border=True):
        gal_h1, gal_h2 = st.columns([3, 1.2])
        with gal_h1:
            st.markdown(f'<div class="workbench-panel-header" style="border:none; margin:0; padding:0;"><span>🖼️ 全篇图表图注全景画廊</span><span style="font-size:11.5px; opacity:0.8;">共提取出 {len(figs)} 张核心图表</span></div>', unsafe_allow_html=True)
        with gal_h2:
            gal_view_mode = st.radio("画廊排版", ["📖 精读并排", "🖼️ 紧凑矩阵"], horizontal=True, key=f"gal_view_{p['id'][:8]}", label_visibility="collapsed")

        if not figs:
            st.caption("本篇文献尚未提取图表。")
        elif "精读" in gal_view_mode:
            # 模式 A: 宽幅双栏精读卡片（图在左，论据在右，层次分明，绝不拥挤错位）
            for idx, fig in enumerate(figs):
                with st.container(border=True):
                    c_fig_left, c_fig_right = st.columns([1, 2.85], gap="medium")
                    ft = fig.get("figure_type") or "Figure"
                    cap = (fig.get("caption") or "").strip()
                    pg_num = fig.get("page_number") or ""

                    with c_fig_left:
                        img_path = resolve_media_path(fig.get("image_path", ""))
                        has_img = bool(img_path and os.path.isfile(img_path))
                        if has_img:
                            # 图列只占约 1/4 宽度：图片按列宽等比缩小，把横向空间让给右侧图注与论证
                            st.image(img_path, use_container_width=True)
                        else:
                            st.caption("⚠️ 图片暂未缓存")

                        # 操作区：对比键独占整行；「看原图 / 跳页」短标签并排，窄栏也不截断
                        in_wb_cmp = comparator.is_in_basket(st.session_state, fig["id"])
                        if in_wb_cmp:
                            if st.button("✅ 移出对比", key=f"wb_cmp_l_{fig['id']}", use_container_width=True):
                                comparator.remove_from_basket(st.session_state, fig["id"])
                                st.rerun()
                        else:
                            if st.button("🔬 加入对比", key=f"wb_cmp_l_{fig['id']}", use_container_width=True):
                                ok, msg = comparator.toggle_basket(st.session_state, {
                                    "id": fig["id"],
                                    "image_path": fig.get("image_path", ""),
                                    "caption": fig.get("caption", ""),
                                    "figure_type": ft,
                                    "paper_id": p["id"],
                                    "paper_title": p.get("title", ""),
                                    "journal": p.get("journal", ""),
                                    "year": p.get("year", ""),
                                    "page_number": pg_num,
                                    "paper_abs_path": ap,
                                })
                                if not ok:
                                    st.warning(msg)
                                else:
                                    comparator.set_compare_open(st.session_state, True)
                                    st.rerun()

                        b_l1, b_l2 = st.columns([1, 1])
                        with b_l1:
                            if has_img:
                                st.link_button(
                                    "🔍 看原图",
                                    "http://localhost:8502/?path=" + urllib.parse.quote(img_path, safe=""),
                                    use_container_width=True,
                                    help="在新标签页按原始分辨率查看该图（不占用本页空间）",
                                )
                        with b_l2:
                            if pg_num:
                                if st.button(f"📑 第 {pg_num} 页", key=f"jump_p_l_{fig['id']}", use_container_width=True, help=f"跳转上方阅读器至图表所在原页（第 {pg_num} 页）"):
                                    st.session_state[f"pending_pdf_mode_{p['id'][:12]}"] = "🖼️ 逐页图像预览"
                                    st.session_state[f"pending_pdf_page_{p['id'][:12]}"] = int(pg_num)
                                    st.toast(f"📍 阅读器已就绪定位至第 {pg_num} 页")
                                    st.rerun()

                    with c_fig_right:
                        # 1. 顶部标题徽章行
                        meta_badges = [f"<span class='meta-pill meta-pill-journal' style='font-size:12px; font-weight:700;'>🏷️ {html_mod.escape(ft)}</span>"]
                        if pg_num:
                            meta_badges.append(f"<span class='meta-pill meta-pill-year' style='font-size:12px;'>📑 PDF 第 {pg_num} 页</span>")
                        st.markdown(f"<div style='margin-bottom:8px;'>{' '.join(meta_badges)}</div>", unsafe_allow_html=True)

                        # 2. 原文图注 Caption（带优雅折叠保护）
                        if cap:
                            if len(cap) > 220:
                                st.markdown(f"""
                                <div class="caption-card-box" style="margin-bottom:8px;">
                                  <div class="cap-label">📝 论文图注 (Caption)</div>
                                  <div class="cap-body">{html_mod.escape(cap[:220])}…</div>
                                </div>
                                """, unsafe_allow_html=True)
                                with st.expander("展开完整图注全文", expanded=False):
                                    st.write(cap)
                            else:
                                st.markdown(f"""
                                <div class="caption-card-box" style="margin-bottom:8px;">
                                  <div class="cap-label">📝 论文图注 (Caption)</div>
                                  <div class="cap-body">{html_mod.escape(cap)}</div>
                                </div>
                                """, unsafe_allow_html=True)

                        # 3. 🎯 图文双向锚定：正文讨论与页码
                        with st.expander("💬 正文关键讨论与页码定位", expanded=False):
                            mentions = figure_anchor.find_figure_mentions_in_paper(ap, cap, ft)
                            if mentions:
                                st.caption(f"🎯 毫秒级锁定正文 **{len(mentions)} 处** 讨论：")
                                for m_idx, m_item in enumerate(mentions):
                                    m_b1, m_b2 = st.columns([3.2, 0.8])
                                    with m_b1:
                                        st.markdown(f"""
                                        <div style="font-size:12px; line-height:1.5; color:#1e293b; background:#f8fafc; border-left:3px solid #2563eb; padding:6px 8px; border-radius:4px; margin-bottom:4px;">
                                          <div style="font-weight:700; color:#1d4ed8; font-size:11px;">📑 第 {m_item['page']} 页 · 引用 <code>{html_mod.escape(m_item['match'])}</code></div>
                                          <div>{html_mod.escape(m_item['snippet'])}</div>
                                        </div>
                                        """, unsafe_allow_html=True)
                                    with m_b2:
                                        if st.button("📍 跳至该页", key=f"jmp_split_{fig['id']}_{m_idx}_{m_item['page']}", use_container_width=True):
                                            st.session_state[f"pending_pdf_mode_{p['id'][:12]}"] = "🖼️ 逐页图像预览"
                                            st.session_state[f"pending_pdf_page_{p['id'][:12]}"] = m_item['page']
                                            st.toast(f"📍 阅读器已就绪定位至第 {m_item['page']} 页")
                                            st.rerun()
                            else:
                                st.caption("正文中暂未检测到明显针对该图号的独立句式，可参考原图注。")

                        # 4. ✨ 核心实验结论提炼模块
                        ai_conc = fig.get("ai_conclusion") or ""
                        if ai_conc:
                            st.markdown(f"""
                            <div style="background:#f0fdf4; border:1px solid #86efac; border-radius:6px; padding:8px 10px; margin-top:6px; font-size:12.5px; color:#14532d; line-height:1.55;">
                              <div style="font-weight:700; color:#166534; font-size:11.5px; margin-bottom:4px;">✨ 图表核心实验结论</div>
                              <div>{html_mod.escape(ai_conc)}</div>
                            </div>
                            """, unsafe_allow_html=True)
                            c_btn1, c_btn2 = st.columns([1, 1])
                            with c_btn1:
                                if st.button("🔄 重新提炼", key=f"re_conc_s_{fig['id']}", use_container_width=True, help="重新提炼正文结论并覆盖更新"):
                                    with st.spinner("正在提炼正文结论..."):
                                        mentions = figure_anchor.find_figure_mentions_in_paper(ap, cap, ft)
                                        ident = figure_anchor.extract_figure_identity(cap, ft)
                                        fig_lbl = ident.get("label", "此图")
                                        if api_key and api_key.strip():
                                            new_conc = figure_anchor.summarize_figure_with_llm(cap, mentions, fig_lbl, api_provider, api_key, api_model)
                                        else:
                                            new_conc = figure_anchor.extract_offline_conclusion(mentions, cap)
                                        figure_anchor.save_figure_conclusion(fig["id"], new_conc)
                                        st.toast("✅ 核心结论已更新并持久化！")
                                        st.rerun()
                            with c_btn2:
                                if st.button("🗑 清除结论", key=f"del_conc_s_{fig['id']}", use_container_width=True):
                                    figure_anchor.save_figure_conclusion(fig["id"], "")
                                    st.rerun()
                        else:
                            has_key = bool(api_key and api_key.strip())
                            btn_label = "✨ 提炼正文结论 (AI在线深度)" if has_key else "✨ 提炼正文结论 (离线规则秒提)"
                            if st.button(btn_label, key=f"gen_conc_s_{fig['id']}", use_container_width=True, help="配置API Key享受AI深度机理提炼，无Key亦可秒级提取正文核心论据"):
                                with st.spinner("正在提取正文并提炼结论..."):
                                    mentions = figure_anchor.find_figure_mentions_in_paper(ap, cap, ft)
                                    ident = figure_anchor.extract_figure_identity(cap, ft)
                                    fig_lbl = ident.get("label", "此图")
                                    if has_key:
                                        new_conc = figure_anchor.summarize_figure_with_llm(cap, mentions, fig_lbl, api_provider, api_key, api_model)
                                    else:
                                        new_conc = figure_anchor.extract_offline_conclusion(mentions, cap)
                                    figure_anchor.save_figure_conclusion(fig["id"], new_conc)
                                    st.toast("✅ 结论已提炼并永久存库！")
                                    st.rerun()
        else:
            # 模式 B: 紧凑三列网格（极简干净看图，不塞大段文字，高低对齐）
            f_cols = st.columns(3)
            for idx, fig in enumerate(figs):
                col_target = f_cols[idx % 3]
                with col_target:
                    with st.container(border=True):
                        img_path = resolve_media_path(fig.get("image_path", ""))
                        if img_path and os.path.isfile(img_path):
                            st.image(img_path, use_container_width=True)
                        ft = fig.get("figure_type") or "Figure"
                        st.markdown(f"**🏷️ {html_mod.escape(ft)}** · 第 {fig.get('page_number', '?')} 页")
                        cap = (fig.get("caption") or "").strip()
                        if cap:
                            st.caption(cap[:90] + ("…" if len(cap) > 90 else ""))
                        in_wb_cmp = comparator.is_in_basket(st.session_state, fig["id"])
                        if in_wb_cmp:
                            if st.button("✅ 移出对比", key=f"wb_cmp_m_{fig['id']}", use_container_width=True):
                                comparator.remove_from_basket(st.session_state, fig["id"])
                                st.rerun()
                        else:
                            if st.button("🔬 加入对比", key=f"wb_cmp_m_{fig['id']}", use_container_width=True):
                                comparator.toggle_basket(st.session_state, {
                                    "id": fig["id"],
                                    "image_path": fig.get("image_path", ""),
                                    "caption": fig.get("caption", ""),
                                    "figure_type": ft,
                                    "paper_id": p["id"],
                                    "paper_title": p.get("title", ""),
                                    "journal": p.get("journal", ""),
                                    "year": p.get("year", ""),
                                    "page_number": fig.get("page_number", ""),
                                    "paper_abs_path": ap,
                                })
                                comparator.set_compare_open(st.session_state, True)
                                st.rerun()


# ── 会话状态 ──

for _key, _default in [("processing", False), ("scan_result", None), ("ai_all", False),
                       ("enrich_all", False), ("scan_root_just_changed", False), ("scan_skipped", None),
                       ("selected_paper_id", None), ("sb_copilot_messages", [])]:
    st.session_state.setdefault(_key, _default)


# ── 应用配置 ──

def load_app_config():
    if os.path.exists(APP_CONFIG_FILE):
        try:
            with open(APP_CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_app_config(updates: dict):
    cfg = load_app_config()
    cfg.update(updates)
    os.makedirs(os.path.dirname(APP_CONFIG_FILE), exist_ok=True)
    with open(APP_CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def get_scan_root():
    """当前 PDF 扫描根目录（用户自定义优先，其次【文献存放处】子目录，否则 app 目录）"""
    cfg = load_app_config()
    path = cfg.get("pdf_scan_root", "")
    if path and os.path.isdir(path):
        return os.path.abspath(path)
    default_papers_dir = os.path.join(APP_DIR, "【文献存放处】")
    if os.path.isdir(default_papers_dir):
        return default_papers_dir
    return APP_DIR


def get_scan_state():
    cfg = load_app_config()
    return {"last_scan": cfg.get("last_scan"),
            "total_papers": cfg.get("total_papers", 0)}


def sync_paper_path(old_abs: str, new_abs: str):
    """文献被移动后，同步 papers.db 里的 abs_path 与 folder。

    folder 用「相对于文献库根目录的路径」表示，与 parser.find_pdfs 的写法一致
    （如 "羰基还原"、"MOF&COF CISS 纳米材料/手性螺旋聚合物"；根目录为 "根目录"）。
    必须先改数据库再移动文件：反过来的话中途失败就会两处不一致，而先改库则只需回滚 SQL。
    """
    conn = database.get_conn()
    try:
        conn.execute("UPDATE papers SET abs_path=?, filename=?, folder=? WHERE abs_path=?",
                     (new_abs, os.path.basename(new_abs),
                      library_fs.folder_of(get_scan_root(), new_abs), old_abs))
        conn.commit()
    finally:
        conn.close()


def count_new_pdfs():
    """检测文件夹里有多少 PDF 尚未入库

    性能关键：不再每次刷新都把全库 139 个 PDF（771 MB）重算 SHA256（实测 525 ms），
    改为走 pdf_index 的缓存（只 stat 文件，实测 5.5 ms，快约 95 倍）。
    文件大小/修改时间变了才重算那一个，删除的文件会自动清出索引。
    """
    root = get_scan_root()
    try:
        pdfs = paper_parser.find_pdfs(root)
    except Exception:
        return 0, 0
    if not pdfs:
        return 0, 0

    # 用一个连接批量查，避免每个文件都开关一次数据库（原先这样多花 170 ms）
    try:
        conn = database.get_conn()
        known = {r[0] for r in conn.execute("SELECT id FROM papers")}
        conn.close()
    except Exception:
        known = set()

    try:
        new_count, total, _stats = pdf_index.count_new(
            pdfs, APP_DIR, lambda pid: pid in known)
        return new_count, total
    except Exception:
        # 缓存层出问题也不能让侧边栏瘫掉：退回「不报待导入数」
        return 0, len(pdfs)


# ═══════════════════════════════════════════════════
# 侧边栏
# ═══════════════════════════════════════════════════

with st.sidebar:
    # ── 1. Brand & System Status Card ──
    try:
        stats_quick = database.get_stats()
    except Exception:
        stats_quick = {}
    p_cnt = stats_quick.get("paper_count", 0)
    f_cnt = stats_quick.get("figure_count", 0)
    au_cnt = stats_quick.get("with_authors", 0)

    st.markdown(f"""
    <div class="sb-brand-card">
      <div class="sb-brand-top">
        <div class="sb-brand-title">
          <span>📚</span>
          <span>PaperLibrary</span>
          <span class="sb-brand-badge">PRO</span>
        </div>
        <div class="sb-live-indicator">
          <span class="sb-live-dot"></span>
          <span>8502 在线</span>
        </div>
      </div>
      <div class="sb-brand-sub">
        <span>个人专属学术文献库</span>
        <span style="font-family:'JetBrains Mono'; font-weight:700; color:#0f172a;">{p_cnt} 篇 · {f_cnt} 图</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    # ── 2. Library Navigation & Folder Filter ──
    current_root = get_scan_root()
    try:
        folders_data = database.get_folders()
        total_paper_num = sum(f["count"] for f in folders_data)
        folder_display_map = {"全部": "全部"}
        folder_display_list = [f"全部 ({total_paper_num})"]
        for f in folders_data:
            disp = f"{f['folder']} ({f['count']})"
            folder_display_map[disp] = f["folder"]
            folder_display_list.append(disp)
    except Exception:
        folders_data = []
        folder_display_map = {"全部": "全部"}
        folder_display_list = ["全部"]

    st.markdown('<div class="sb-card-header">📂 文献分类与导航</div>', unsafe_allow_html=True)
    sel_disp = st.selectbox("分类筛选", folder_display_list, label_visibility="collapsed")
    selected_folder = folder_display_map.get(sel_disp, "全部")

    # 分类操作工具条：新建分类 & 管理分类
    cat_tool1, cat_tool2 = st.columns(2)
    with cat_tool1:
        with st.popover("➕ 新建分类", use_container_width=True):
            st.markdown("**➕ 新建分类文件夹**")
            st.caption("创建后将出现在筛选和导入目标中：")
            new_cat_name = st.text_input("分类名称", placeholder="例如：电催化 / 钙钛矿", key="sb_pop_new_cat_name")
            sync_create_pc = st.checkbox("电脑同步建立文件夹", value=True, key="sb_pop_sync_pc_create",
                                         help="勾选后在电脑大文件夹中自动创建对应的物理子目录；若取消勾选，则仅在网站建立该分类标签")
            if st.button("确认创建", key="sb_pop_btn_create_cat", type="primary", use_container_width=True):
                nm_clean = new_cat_name.strip()
                if nm_clean:
                    if sync_create_pc:
                        try:
                            library_fs.create_folder(current_root, nm_clean)
                        except Exception as e:
                            st.warning(f"电脑文件夹创建提示: {e}")
                    ok, msg = database.add_custom_category(nm_clean)
                    if ok:
                        st.success(f"已创建分类「{nm_clean}」！")
                        time.sleep(0.4)
                        st.rerun()
                    else:
                        st.error(msg)
                else:
                    st.warning("请输入分类名称")

    with cat_tool2:
        with st.popover("⚙️ 管理分类", use_container_width=True):
            st.markdown("**⚙️ 网站分类文件夹管理**")
            m_tab1, m_tab2, m_tab3 = st.tabs(["✏️ 改名", "🔀 合并", "🗑️ 删除"])

            # 1. 重命名
            with m_tab1:
                st.caption("修改分类名称（大文件夹相同，默认同步改名）：")
                renamable_cats = [f["folder"] for f in folders_data if f["folder"] and f["folder"] != "根目录"]
                if renamable_cats:
                    old_c = st.selectbox("选择要修改的分类", renamable_cats, key="sb_mgr_ren_old")
                    new_c = st.text_input("输入新名称", value=old_c, key="sb_mgr_ren_new")
                    ren_sync_pc = st.checkbox("电脑同步重命名文件夹", value=True, key="sb_mgr_ren_sync_pc",
                                              help="勾选后在电脑磁盘上也重命名该文件夹；不勾选则仅修改网站分类标签")
                    if st.button("确认更名", key="sb_mgr_btn_ren", type="primary", use_container_width=True):
                        old_pfx = None
                        new_pfx = None
                        if ren_sync_pc:
                            try:
                                new_abs_dir = library_fs.rename_folder(current_root, old_c, new_c.strip())
                                old_abs_dir = library_fs.safe_join(current_root, old_c)
                                old_pfx = old_abs_dir
                                new_pfx = new_abs_dir
                            except Exception as e:
                                st.error(f"电脑重命名失败: {e}")
                                old_pfx = None
                                new_pfx = None
                        ok, msg = database.rename_category_in_db(old_c, new_c.strip(), old_pfx, new_pfx)
                        if ok:
                            st.success(f"已更名为「{new_c.strip()}」！")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error(msg)
                else:
                    st.caption("暂无可修改的自定义分类")

            # 2. 合并
            with m_tab2:
                st.caption("将某一分类的全部文献批量转移合并到另一分类：")
                all_raw_cats = [f["folder"] for f in folders_data if f["folder"]]
                if len(all_raw_cats) >= 2:
                    from_c = st.selectbox("源分类（将被合并清空）", [c for c in all_raw_cats if c != "根目录"], key="sb_mgr_mrg_from")
                    to_c = st.selectbox("目标分类（合并入）", [c for c in all_raw_cats if c != from_c], key="sb_mgr_mrg_to")
                    if st.button("确认合并文献", key="sb_mgr_btn_mrg", use_container_width=True):
                        ok, msg = database.merge_category_in_db(from_c, to_c)
                        if ok:
                            st.success(msg)
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error(msg)
                else:
                    st.caption("至少需要两个分类才能合并")

            # 3. 删除
            with m_tab3:
                st.caption("删除网站分类：")
                del_cats = [f["folder"] for f in folders_data if f["folder"] and f["folder"] != "根目录"]
                if del_cats:
                    target_del = st.selectbox("选择要删除的分类", del_cats, key="sb_mgr_del_target")
                    found_f = next((f for f in folders_data if f["folder"] == target_del), None)
                    p_num = found_f["count"] if found_f else 0
                    if p_num > 0:
                        st.info(f"💡 该分类下现有 **{p_num}** 篇文献，删除分类后将自动归入「根目录」，文献不会丢失。")
                    del_sync_pc = st.checkbox("电脑同步删除空文件夹", value=False, key="sb_mgr_del_sync_pc",
                                              help="仅在电脑该文件夹为空时才会删除；若非空则保留电脑文件")
                    if st.button("确认删除该分类", key="sb_mgr_btn_del", type="primary", use_container_width=True):
                        if del_sync_pc:
                            try:
                                library_fs.delete_folder(current_root, target_del)
                            except Exception as e:
                                st.warning(f"电脑文件夹删除提示: {e}")
                        ok, msg = database.delete_custom_category(target_del, reassign_to="根目录")
                        if ok:
                            st.success(f"分类「{target_del}」已删除！")
                            time.sleep(0.5)
                            st.rerun()
                        else:
                            st.error(msg)
                else:
                    st.caption("暂无可删除的分类")

    # ── 导入新文献 + 设定文献库总大文件夹：两个独立入口合并成同一个侧边栏面板 ──
    with st.expander("📥 导入新文献 (上传并选择分类)  ·  ⚙️ 设定文献库总大文件夹", expanded=False):
        # ① 导入新文献（原「📥 导入新文献 (上传并选择分类)」折叠框的全部功能）
        st.caption("拖入或选择 PDF 文献，指定存入的分类：")
        sb_files = st.file_uploader("拖入或选择 PDF", type=["pdf"], accept_multiple_files=True,
                                    key="sb_upload_pdfs", label_visibility="collapsed")
        sb_cats = importer.get_existing_categories(current_root)
        sb_chosen = st.selectbox("选择导入的文件夹/分类：", sb_cats + ["➕ 新建分类..."],
                                 key="sb_import_cat")
        sb_new_txt = ""
        if sb_chosen == "➕ 新建分类...":
            sb_new_txt = st.text_input("输入新分类名称", placeholder="例如：最新文献 / 钙钛矿",
                                       key="sb_import_new_cat")
        sb_target = sb_new_txt.strip() if sb_chosen == "➕ 新建分类..." and sb_new_txt.strip() else sb_chosen
        sb_sync = st.checkbox("电脑同步归档（存入对应子目录）", value=True, key="sb_import_sync_disk",
                              help="默认电脑与网站在同一分类子目录下；取消勾选则文件存入根目录，仅网站打上分类标签")
        if st.button("🚀 开始导入上传文献", key="sb_btn_do_import", type="primary",
                     use_container_width=True, disabled=not sb_files):
            sb_ok = 0
            with st.spinner(f"正在导入 {len(sb_files)} 篇文献并解析图表…"):
                for _uf in sb_files:
                    _ok, _ = importer.ingest_uploaded_pdf(
                        _uf.getvalue(), _uf.name, current_root,
                        target_folder=sb_target, sync_to_disk_folder=sb_sync
                    )
                    if _ok:
                        sb_ok += 1
            if sb_ok > 0:
                st.success(f"✅ 成功导入 {sb_ok} 篇文献至「{sb_target}」！")
                time.sleep(0.6)
                st.rerun()
            else:
                st.warning("⚠️ 没有文件被导入（可能已入库或不是有效 PDF）")

        st.markdown('<hr style="margin:12px 0 10px 0; border:none; border-top:1px dashed #cbd5e1;">',
                    unsafe_allow_html=True)

        # ② 设定文献库总大文件夹（原「⚙️ 设定文献库总大文件夹」折叠框的全部功能）
        st.markdown("""
        <div style="font-size:11.5px; color:#475569; font-weight:700; margin-bottom:4px;">
          ⚙️ 设定文献库总大文件夹
        </div>
        """, unsafe_allow_html=True)
        st.markdown(f"""
        <div style="font-size:11.5px; color:#475569; margin-bottom:4px; font-weight:600;">
          📍 当前文献库大文件夹（电脑与网站根目录一致）：
        </div>
        <div class='sb-path-box'>📁 {html_mod.escape(current_root.replace(chr(92), '/'))}</div>
        <div style="font-size:11px; color:#64748b; margin-top:-4px; margin-bottom:8px;">
          💡 电脑与网站的大文件夹相同（总文献相同）；各自分类可独立，默认一致。
        </div>
        """, unsafe_allow_html=True)

        # 方式: 弹出 Windows 文件夹管理器窗口选择
        if st.button("🖥️ 弹出 Windows 文件管理器选择…", key="btn_pick_win_folder", type="primary", use_container_width=True, help="在屏幕前弹出 Windows 原生文件夹浏览窗口，点击选择即可自动切换"):
            with st.spinner("等待在 Windows 窗口中选择文件夹…"):
                chosen_dir = folder_picker.pick_folder_native(current_root)
                if chosen_dir and os.path.isdir(chosen_dir):
                    save_app_config({"pdf_scan_root": os.path.abspath(chosen_dir)})
                    st.session_state.scan_root_just_changed = True
                    st.success(f"已成功切换为：{chosen_dir}")
                    time.sleep(0.5)
                    st.rerun()
                elif chosen_dir is not None:
                    st.warning("选择的路径不存在")

        # 辅助文本输入与恢复默认
        st.caption("手动输入路径备用：")
        new_root = st.text_input("手动粘贴路径", value="", placeholder="输入绝对路径…", key="new_scan_root", label_visibility="collapsed")
        c_p1, c_p2 = st.columns([1.5, 1])
        with c_p1:
            if st.button("确认应用", width="stretch", disabled=not new_root):
                if os.path.isdir(new_root):
                    save_app_config({"pdf_scan_root": os.path.abspath(new_root)})
                    st.session_state.scan_root_just_changed = True
                    st.success("已切换")
                    time.sleep(0.4)
                    st.rerun()
                else:
                    st.error("路径不存在")
        with c_p2:
            if current_root != APP_DIR and st.button("恢复默认", width="stretch"):
                save_app_config({"pdf_scan_root": ""})
                st.session_state.scan_root_just_changed = True
                st.rerun()

    # ── 3. Data Pipeline & Sync Center ──
    new_cnt, total_cnt = count_new_pdfs()
    scan_state = get_scan_state()

    st.markdown('<div class="sb-card-header" style="margin-top:14px;">⚡ 数据流水线与同步中枢</div>', unsafe_allow_html=True)

    sync_c1, sync_c2 = st.columns(2)
    with sync_c1:
        if new_cnt > 0:
            st.markdown(f"""
            <div style="background:#fee2e2; border:1px solid #fca5a5; border-radius:6px; padding:6px 8px; text-align:center;">
              <div style="font-size:11px; color:#991b1b; font-weight:700;">待导入文献</div>
              <div style="font-size:16px; font-weight:700; color:#b91c1c; font-family:'JetBrains Mono';">{new_cnt} <span style="font-size:11px;">篇</span></div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#dcfce7; border:1px solid #86efac; border-radius:6px; padding:6px 8px; text-align:center;">
              <div style="font-size:11px; color:#166534; font-weight:700;">文献库状态</div>
              <div style="font-size:13px; font-weight:700; color:#15803d; margin-top:2px;">✅ 全部已入库</div>
            </div>
            """, unsafe_allow_html=True)
    with sync_c2:
        au_ratio = f"{au_cnt}/{p_cnt}" if p_cnt else "0/0"
        st.markdown(f"""
        <div style="background:#e0e7ff; border:1px solid #a5b4fc; border-radius:6px; padding:6px 8px; text-align:center;">
          <div style="font-size:11px; color:#3730a3; font-weight:700;">作者/期刊补全率</div>
          <div style="font-size:15px; font-weight:700; color:#4338ca; font-family:'JetBrains Mono'; margin-top:1px;">{au_ratio}</div>
        </div>
        """, unsafe_allow_html=True)

    all_categories = importer.get_existing_categories(current_root)

    # 扫描与同步电脑新文献
    if new_cnt > 0:
        with st.expander(f"🔄 电脑检测到 {new_cnt} 个待入库文件", expanded=True):
            scan_strategy = st.radio("分类策略：",
                                     ["⚡ 保持与电脑文件夹相同（默认推荐）", "📁 统一归入指定分类"],
                                     key="sb_scan_strategy")
            scan_cat_override = "AUTO"
            if "统一归入" in scan_strategy:
                scan_cat_override = st.selectbox("统一归入哪个分类：", all_categories, key="sb_scan_cat_override")

            if st.button(f"🔄 确认同步入库 ({new_cnt} 篇)", key="sb_btn_scan_sync", type="primary", use_container_width=True):
                st.session_state["scan_target_folder"] = scan_cat_override
                st.session_state.processing = True
                st.rerun()
    else:
        if st.button("🔄 扫描刷新文献库", key="sb_btn_rescan", use_container_width=True):
            st.session_state["scan_target_folder"] = "AUTO"
            st.session_state.processing = True
            st.rerun()

    if st.button("🚀 批量智能补全题录 (Crossref/OpenAlex)", width="stretch", help="全自动补充缺失的作者名单、权威期刊与发表年份"):
        st.session_state.enrich_all = True
        st.rerun()

    st.checkbox("只导入期刊文献（自动跳过自写笔记等）", value=True, key="scan_filter")

    _sk = st.session_state.get("scan_skipped")
    if _sk:
        with st.expander(f"⚠️ 查看上次跳过的 {len(_sk)} 个非学术文件", expanded=False):
            for _item in _sk:
                st.markdown(f"**{_item['filename']}**")
                st.caption(f"位置：{_item['folder']} ｜ 理由：" + "；".join(_item["reasons"]))

    # ── 4. Advanced Toolboxes & AI Settings ──
    st.markdown('<div class="sb-card-header" style="margin-top:14px;">🛠️ 高级配置与维护</div>', unsafe_allow_html=True)

    with st.expander("🤖 AI 问答与摘要配置", expanded=False):
        api_provider = st.selectbox("API 提供商", ["deepseek", "claude"])
        api_key = st.text_input("API Key", type="password", help="填入 DeepSeek 或 Claude 的 API Key")
        api_model = st.text_input("模型名称", placeholder="deepseek-chat / claude-sonnet-5")
        if st.button("🧠 运行全文 AI 摘要分析", width="stretch", disabled=not api_key):
            st.session_state.ai_all = True
            st.rerun()
        st.caption("注：前台已聚焦原始图文，此处仅对论文正文提炼结构化摘要。")

    with st.expander("⚡ 缩略图缓存与性能", expanded=False):
        _ts = thumbs.cache_stats(APP_DIR)
        st.caption(f"当前缓存：{_ts['files']} 张，占用 {_ts['bytes']/1024/1024:.1f} MB")
        c_th1, c_th2 = st.columns(2)
        with c_th1:
            if st.button("🗑 清空缓存", key="th_clear", width="stretch"):
                n = thumbs.clear_cache(APP_DIR)
                st.success(f"已清空 {n} 张")
                time.sleep(0.3)
                st.rerun()
        with c_th2:
            if st.button("♻️ 重建缓存", key="th_warm", width="stretch"):
                with st.spinner("生成缩略图中…"):
                    _papers = database.get_all_papers()
                    _fm = database.get_figures_map([x["id"] for x in _papers])
                    _n = 0
                    for _x in _papers:
                        for _f in (_fm.get(_x["id"]) or [])[:4]:
                            _ip = resolve_media_path(_f.get("image_path", ""))
                            if os.path.isfile(_ip) and thumbs.make_thumb(_ip, APP_DIR):
                                _n += 1
                st.success(f"已重建 {_n} 张")
                time.sleep(0.3)
                st.rerun()

    # ── 4. PaperLibrary Copilot (系统与全库智能问答助手) ──
    st.markdown('<div class="sb-card-header" style="margin-top:14px;">💬 智能问答助手 (Copilot)</div>', unsafe_allow_html=True)
    with st.expander("🤖 打开系统与全库问答顾问", expanded=False):
        st.caption("基于全库知识与系统操作手册，即问即答：")

        q_cols = st.columns(2)
        preset_q = None
        with q_cols[0]:
            if st.button("📥 怎么导入文献", key="sb_pq1", use_container_width=True):
                preset_q = "怎么导入新下载的论文？"
            if st.button("📑 如何导出BibTeX", key="sb_pq2", use_container_width=True):
                preset_q = "如何导出BibTeX引用？"
            if st.button("🔬 怎么搜图表", key="sb_pq3", use_container_width=True):
                preset_q = "怎么检索具体的实验图表如CV曲线？"
        with q_cols[1]:
            if st.button("🎯 有无配置AI区别", key="sb_pq5", use_container_width=True):
                preset_q = "有无配置AI有什么区别？"
            if st.button("📊 库藏数据概况", key="sb_pq4", use_container_width=True):
                preset_q = "当前文献库最新统计数据如何？"
            if st.button("🔬 多图横向对比", key="sb_pq6", use_container_width=True):
                preset_q = "多图横向对比幕布怎么用？"

        sb_user_query = st.text_input("向 Copilot 提问", value=preset_q or "",
                                      placeholder="输入使用问题或库藏咨询…", key="sb_copilot_input")

        c_sub, c_clr = st.columns([3, 1])
        with c_sub:
            send_clicked = st.button("📨 发送提问", key="sb_copilot_send", type="primary", use_container_width=True)
        with c_clr:
            if st.button("🗑 清空", key="sb_copilot_clr", use_container_width=True):
                st.session_state["sb_copilot_latest"] = None
                st.session_state["sb_copilot_messages"] = []
                st.rerun()

        active_query = preset_q or (sb_user_query.strip() if send_clicked and sb_user_query.strip() else None)
        if active_query:
            with st.spinner("Copilot 正在解答…"):
                if api_key:
                    try:
                        sys_prompt = f"""你是由 Google DeepMind 专家级团队打造的 PaperLibrary 学术文献管理与科研 Assistant。
你熟悉当前文献库的一切：
- 本地文献库路径: {current_root}
- 篇目总数: {p_cnt} 篇，图表总数: {f_cnt} 张，图注总数: {stats_quick.get('with_caption', 0)} 条，官方作者补全数: {au_cnt} 篇。
- 系统核心功能: 增量文件夹扫描与期刊过滤、中英双模态插图检索(Figure级与Bbox几何图注临近绑定)、HTML5嵌入式双栏研读工作台(选词复制划线与单篇ChatPDF)、多格式引文一键导出(BibTeX/GB/T 7714/APA)、Crossref与OpenAlex题录自动补全。
请用专业、亲切、干练的学术中文回答用户的任何使用疑问或科研检索建议。排版规范，要点清晰。"""
                        ai_ans = ai_engine.copilot_chat(sys_prompt, [],
                                                        active_query, api_provider, api_key, model=api_model or None)
                    except Exception as e:
                        ai_ans = get_offline_assistant_reply(active_query, stats_quick, current_root)
                else:
                    ai_ans = get_offline_assistant_reply(active_query, stats_quick, current_root)
            # 每次提问直接覆盖上一个问答，保持左栏紧凑整洁不拉长
            st.session_state["sb_copilot_latest"] = {
                "question": active_query,
                "answer": ai_ans
            }
            st.rerun()

        # 展示当前最新的一问一答（覆盖模式）
        latest_qa = st.session_state.get("sb_copilot_latest")
        if latest_qa:
            st.markdown(f'<div class="sb-copilot-user">👤 问：{html_mod.escape(latest_qa["question"])}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="sb-copilot-bot">{latest_qa["answer"]}</div>', unsafe_allow_html=True)
            if st.button("✕ 收起当前回答", key="sb_copilot_dismiss", use_container_width=True):
                st.session_state["sb_copilot_latest"] = None
                st.rerun()
        else:
            st.caption("💡 提示：点击上方快捷提问或输入问题，回答将即时呈现在此；每次提问自动刷新覆盖，不堆叠拉长侧栏。")

    # ── 5. Detailed User Guide (详细新手指南) ──
    with st.expander("📖 详细新手指南与操作手册", expanded=False):
        st.markdown(f"""
### 📁 一、 文献存放与入库规则
1. **存放位置**：
   `{current_root.replace(chr(92), '/')}`
2. **多级子目录自动归类**：
   支持按研究课题建立子文件夹（例如 `电催化/`、`手性化学/`），系统会自动以文件夹名作为分类标签。
3. **期刊论文自动筛选（防杂质）**：
   默认开启「只导入期刊文献」，系统会自动分析两栏排版、DOI、Abstract 等学术发表特征，自写的 Word 笔记导出 PDF 不会被误收录。

---

### 🔍 二、 跨语言双模态极速检索
1. **文献全文检索 (Tab 1)**：
   支持标题、正文、作者、期刊名、年份模糊检索，中英文自由混搜。
2. **论文插图级检索 (Tab 2 - 核心亮点)**：
   业内领先的 **Figure 插图级精准对齐检索**！
   - 输入实验表征类型（如 `CV`、`循环伏安`、`XRD`、`SEM`、`TEM`、`反应机理`）；
   - 算法自动跨语言映射中英文专有名词，并通过几何 Bbox 空间邻近算法，直接锁定包含该图的页面与原图、就近图注；
   - 点击「📖 进入研读工作台」可直达原文并同步浏览该文献的全景图表画廊。
3. **学术网络发现 (Tab 3)**：
   聚合 Crossref / OpenAlex / ArXiv 全球学术网络，一键检索最新论文并支持直接加入本地库。

---

### 📖 三、 沉浸式双栏研读工作台
1. **原生嵌入式阅读器**：
   进入文献工作台后，采用 HTML5 嵌入式引擎，**支持自由划线选词、文本高亮复制、全功能放大缩小**，绝不会因翻页导致全屏刷新，同时完美防止浏览器拦截。
2. **单篇文献 ChatPDF 深度问答**：
   在工作台右栏可直接向该文献提问，内置化学/材料/机理专属提示词，一键提炼实验条件、反应步骤与关键结论。
3. **全格式引文一键导出**：
   支持 4 种国际标准学术引用（`BibTeX`、`GB/T 7714-2015`、`APA 7th`、`Markdown`），点击右上角一键复制到剪贴板，支持全库/筛选批量打包下载 `.bib` 文件。
4. **🔬 多图横向对比幕布**：
   在画廊或图片检索中点击「🔬 加入横向对比」，即可在幕布中等宽并排比对多篇文献的 XRD/SEM/TEM/CV 实验图谱。

---

### 🎯 四、 图文双向快速锚定与正文结论提炼（有无配置 AI 的区别）
1. **看图找正文（秒级跳页）**：
   在工作台画廊或图片检索结果中，展开「💬 原文正文讨论与页码定位」，本地正则拓扑引擎可在毫秒级扫描全文，精准抓取作者讨论该图的全部上下文句段与具体页码。点击「📍 跳至该页」，阅读器自动翻到对应页，告别痛苦的滚轮翻页！
2. **提炼图表结论（有无配置 AI 的机制区别）**：
   - **💡 离线模式 (未配置 API Key 时)**：
     * **完全免费 · 零网络依赖 · 零延迟**；
     * 系统内置独创的**化学/材料学术语义规则库**，自动扫描正文中带有机理与对比动词的句子（如 demonstrates、reveals、confirms、increases by 等）作为核心论断呈现，随时可用！
   - **🤖 在线模式 (在侧边栏填入 DeepSeek / Claude / OpenAI Key 时)**：
     * 自动调用大语言模型，融会贯通图注与分散在正文多处的讨论，提炼出 2~3 句精炼的中文实验机理、性能提升倍数与特征峰位归属；
     * **按需点击提炼**：看哪张图点哪张，不浪费 Token；
     * **结果永久存库**：提炼后自动写入本地数据库持久化，以后打开瞬间呈现，**绝不重复花钱调用二次**！

---

### 🚀 五、 权威题录智能补全
- 刚下载的 PDF 往往只有粗糙的文件名、缺少作者名录？
- 点击侧边栏 **「🚀 批量智能补全题录」**，系统自动连线全球开放学术图谱，秒级补齐作者、权威期刊（*Nature*, *JACS* 等）与准确年份！

---

### ❓ 六、 常见问题与技巧 (FAQ)
- **Q: 为什么有些 PDF 提示跳过？**
  A: 系统默认过滤了自写笔记等非学术文档，可在侧边栏取消勾选「只导入期刊文献」以强制全部导入。
- **Q: 怎么更换文献库路径？**
  A: 在侧边栏「⚙️ 文献库路径与分类管理」中输入新路径即可无缝切换，原数据库索引依然完整保留。
""")

    # ── 5. Bottom Overview Metrics ──
    st.markdown('<div class="sb-card-header" style="margin-top:14px;">📊 库藏数据总览</div>', unsafe_allow_html=True)
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("📄 论文", stats_quick.get("paper_count", 0))
    m2.metric("🖼️ 图表", stats_quick.get("figure_count", 0))
    m3.metric("👤 作者", stats_quick.get("with_authors", 0))
    m4.metric("📝 图注", stats_quick.get("with_caption", 0))


# ═══════════════════════════════════════════════════
# 扫描流水线（增量）
# ═══════════════════════════════════════════════════

if st.session_state.processing:
    st.sidebar.info("⏳ 正在扫描 PDF 文件夹…")
    database.init_db()
    scan_root = get_scan_root()
    pdfs = paper_parser.find_pdfs(scan_root)

    new_count = skip_count = fail_count = 0
    cls_skipped = []
    prog = st.sidebar.progress(0, "解析中…")
    status_text = st.sidebar.empty()

    total_pdfs = max(len(pdfs), 1)
    for i, pi in enumerate(pdfs):
        status_text.text(f"[{i+1}/{len(pdfs)}] {pi['filename'][:45]}")

        # ── 只导入期刊文献：不像论文的（自写笔记导出成的那类 PDF）跳过并记录理由 ──
        if st.session_state.get("scan_filter", True):
            try:
                _cls = doc_classify.classify(pi["full_path"], pi["filename"])
            except Exception:
                _cls = {"is_article": True, "reasons": []}
            if not _cls.get("is_article", True):
                cls_skipped.append({"filename": pi["filename"],
                                    "folder": pi.get("folder", ""),
                                    "reasons": _cls.get("reasons", [])})
                prog.progress((i + 1) / total_pdfs)
                continue

        try:
            h = hashlib.sha256()
            with open(pi["full_path"], "rb") as f:
                for chunk in iter(lambda: f.read(65536), b""):
                    h.update(chunk)
            pid = h.hexdigest()
        except Exception:
            fail_count += 1
            continue

        conn = database.get_conn()
        exists = conn.execute("SELECT 1 FROM papers WHERE id=?", (pid,)).fetchone()
        conn.close()
        if exists:
            skip_count += 1
            prog.progress((i + 1) / total_pdfs)
            continue

        try:
            scan_override = st.session_state.get("scan_target_folder", "AUTO")
            if scan_override and scan_override != "AUTO":
                pi["folder"] = scan_override
            paper, figs = paper_parser.parse_one(pi)
            paper["parsed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            database.upsert_paper(paper)
            database.clear_figures(paper["id"])
            for f in figs:
                database.insert_figure(paper["id"], f)
            new_count += 1
        except Exception:
            fail_count += 1

        prog.progress((i + 1) / total_pdfs)

    prog.empty()
    status_text.empty()

    save_app_config({"last_scan": time.strftime("%Y-%m-%d %H:%M:%S"),
                     "total_papers": database.get_stats()["paper_count"]})
    st.session_state.scan_result = {"new": new_count, "skipped": skip_count,
                                    "failed": fail_count, "total": len(pdfs)}
    st.session_state.scan_skipped = cls_skipped
    st.session_state.processing = False

    if new_count > 0:
        st.sidebar.success(f"✅ 新增 {new_count} 篇，跳过 {skip_count} 篇（已入库）")
    else:
        st.sidebar.info(f"✅ 文献库已是最新（{skip_count} 篇均已入库）")
    if cls_skipped:
        st.sidebar.warning(f"⚠️ {len(cls_skipped)} 个文件不像期刊文献，已跳过（清单见侧边栏）")
    if fail_count:
        st.sidebar.warning(f"⚠️ {fail_count} 篇解析失败")
    time.sleep(1)
    st.rerun()


# ═══════════════════════════════════════════════════
# AI 总结流水线
# ═══════════════════════════════════════════════════

if st.session_state.ai_all and api_key:
    st.sidebar.info("🧠 AI 正在处理…")

    # 论文总结
    all_papers = database.get_all_papers()
    to_summ = [p for p in all_papers if not p.get("summary")]
    if to_summ:
        prog = st.sidebar.progress(0, "生成论文总结…")
        sts = st.sidebar.empty()
        for i, p in enumerate(to_summ):
            sts.text(f"🧠 [{i+1}/{len(to_summ)}] {p['filename'][:40]}")
            try:
                pf = database.get_paper_by_id(p["id"])
                result = ai_engine.summarize_paper(pf.get("full_text", ""), api_provider,
                                                   api_key, model=api_model or None)
                pf["summary"] = json.dumps(result, ensure_ascii=False)
                database.upsert_paper(pf)
            except Exception as e:
                st.sidebar.warning(f"跳过: {e}")
            prog.progress((i + 1) / len(to_summ))
            time.sleep(0.3)

    st.session_state.ai_all = False
    st.sidebar.success(f"✅ AI 摘要处理完成！共分析 {len(to_summ)} 篇论文")
    time.sleep(0.5)
    st.rerun()


# ═══════════════════════════════════════════════════
# 题录智能批量补全流水线（Crossref / OpenAlex）
# ═══════════════════════════════════════════════════

if st.session_state.enrich_all:
    conn = database.get_conn()
    conn.row_factory = sqlite3.Row
    to_enrich = conn.execute(
        "SELECT id, title, filename, doi FROM papers "
        "WHERE authors IS NULL OR authors = '' OR authors = '[]' "
        "ORDER BY CASE WHEN doi != '' THEN 0 ELSE 1 END, year DESC LIMIT 25"
    ).fetchall()
    conn.close()

    if to_enrich:
        eprog = st.sidebar.progress(0, "正在智能补全题录…")
        suc = 0
        for idx, r in enumerate(to_enrich):
            t_short = (r["title"] or r["filename"])[:25]
            eprog.progress((idx + 1) / len(to_enrich), f"正在补全: {t_short}…")
            try:
                ok, _ = metadata_enricher.enrich_and_update_db(
                    os.path.join(APP_DIR, "data", "papers.db"), r["id"])
                if ok:
                    suc += 1
            except Exception:
                pass
            time.sleep(0.2)
        st.sidebar.success(f"✅ 成功补全 {suc} / {len(to_enrich)} 篇文献的作者与期刊！")
    else:
        st.sidebar.info("当前文献库已全部完成作者与期刊补全！")

    st.session_state.enrich_all = False
    time.sleep(0.6)
    st.rerun()


# ═══════════════════════════════════════════════════
# 新增能力：深度分析 / 高清图表升级 / Obsidian 导出
# ═══════════════════════════════════════════════════

def _figs_for_analysis(pid, limit=24):
    """把库里的图表整理成深度分析需要的清单（只带图注，不带图片字节）。"""
    out = []
    for f in database.get_paper_figures(pid)[:limit]:
        out.append({"fig_index": f.get("fig_index"),
                    "caption": f.get("caption") or "",
                    "figure_type": f.get("figure_type") or ""})
    return out


def render_deep_analysis_panel(p, api_provider, api_key, api_model):
    """详情页底部：结构化深度分析（可复跑）、arXiv 高清图表升级、导出 Obsidian"""
    pid = p["id"]
    short = pid[:10]
    deep = database.get_paper_deep(pid)
    figs = database.get_paper_figures(pid)

    with st.container(border=True):
        st.markdown('<div class="workbench-panel-header"><span>🧠 深度分析报告</span>'
                    '<span style="font-size:11.5px; opacity:0.8;">作者主张 / 证据判断分离 · 五维评分 · 可归档</span></div>',
                    unsafe_allow_html=True)

        c1, c2, c3 = st.columns([1.35, 1.35, 1.1])
        with c1:
            btn_run = st.button("🧠 生成深度分析" if not deep["markdown"] else "🔁 重跑深度分析",
                                key=f"deep_run_{short}", type="primary",
                                use_container_width=True, disabled=not (api_key or "").strip())
        with c2:
            btn_upg = st.button("🔎 arXiv 高清图表升级", key=f"deep_upg_{short}",
                                use_container_width=True,
                                help="从 arXiv 源码里取原始矢量图，替换 PDF 里截出来的位图")
        with c3:
            btn_exp = st.button("📤 导出 Obsidian", key=f"deep_exp_{short}",
                                use_container_width=True)

        if not (api_key or "").strip():
            st.caption("填好 API Key 之后才能生成深度分析（沿用上方配置的提供商与模型）。")
        if deep.get("at"):
            st.caption(f"上次生成：{deep['at']}"
                       + (f" · arXiv {deep['arxiv_id']}" if deep.get("arxiv_id") else ""))

        if btn_run:
            with st.spinner("正在精读全文并结构化…（一篇通常 30~90 秒）"):
                res = deep_analysis.analyze_paper(
                    p, figures=_figs_for_analysis(pid),
                    provider=api_provider, api_key=api_key, model=api_model or None)
            if res.get("ok"):
                database.update_paper_deep(pid, res["markdown"], res.get("data") and
                                           deep_analysis.extract_scores(res["data"]) or {},
                                           abstract=(p.get("abstract") or "")[:4000] or None)
                if res.get("error"):
                    st.warning(res["error"])
                else:
                    st.success("深度分析已生成并存入数据库。")
                time.sleep(0.4)
                st.rerun()
            else:
                st.error(res.get("error") or "生成失败")

        if btn_upg:
            with st.spinner("正在拉取 arXiv 源码并渲染原始矢量图…"):
                up = figures_arxiv.try_upgrade(
                    p.get("abs_path") or "", full_text=p.get("full_text") or "",
                    filename=p.get("filename") or "", doi=p.get("doi") or "")
            if up.get("ok"):
                st.session_state[f"upg_{short}"] = up
                database.update_paper_deep(pid, deep["markdown"] or "",
                                           arxiv_id=up.get("arxivId") or "")
                st.success(f"已从 arXiv {up['arxivId']} 取到 {len(up['figures'])} 张原图")
            else:
                st.warning(up.get("reason") or "没能从 arXiv 取到源码图")
                if up.get("arxivId"):
                    st.caption(f"arXiv ID：{up['arxivId']}")

        # 升级结果预览 + 逐张采用（不自动覆盖，避免把好图换成配错的图）
        up = st.session_state.get(f"upg_{short}")
        if up and up.get("figures"):
            st.markdown(f"**🎯 源码图预览（{len(up['figures'])} 张，点击「采用」才写回库）**")
            preview = up["figures"][:6]
            cols = st.columns(min(3, len(preview)) or 1)
            for i, f in enumerate(preview):
                with cols[i % len(cols)]:
                    if f.get("image_path") and os.path.isfile(f["image_path"]):
                        st.image(f["image_path"], use_container_width=True)
                    st.caption(f"Figure {f.get('fig_index')} · {f.get('width')}×{f.get('height')}")
                    if st.button("采用这张", key=f"upg_use_{short}_{i}", use_container_width=True):
                        ok, msg = _adopt_source_figure(pid, f)
                        if ok:
                            st.success(msg)
                            time.sleep(0.4)
                            st.rerun()
                        else:
                            st.warning(msg)
            if st.button("🗑 关闭预览", key=f"upg_close_{short}"):
                st.session_state.pop(f"upg_{short}", None)
                st.rerun()

        if deep["markdown"]:
            if deep["scores"]:
                avg = deep["scores"].get("avg")
                chips = []
                for k, label in (("novelty", "创新"), ("technical", "技术"),
                                 ("evidence", "证据"), ("writing", "写作"),
                                 ("usefulness", "实用")):
                    if deep["scores"].get(k) is not None:
                        chips.append(f"<span class='meta-pill meta-pill-year'>{label} {deep['scores'][k]}/10</span>")
                if avg is not None:
                    chips.insert(0, f"<span class='meta-pill meta-pill-journal'>均分 {avg}</span>")
                if chips:
                    st.markdown(f"<div class='meta-pills-row' style='margin:6px 0 10px;'>{' '.join(chips)}</div>",
                                unsafe_allow_html=True)
            with st.expander("展开完整深度分析报告", expanded=not figs):
                st.markdown(deep["markdown"])
                st.download_button("⬇ 下载 Markdown 报告", deep["markdown"],
                                   file_name=f"{(p.get('title') or pid)[:60]}_深度分析.md",
                                   mime="text/markdown", key=f"deep_dl_{short}")

        if btn_exp:
            render_obsidian_export(p, deep, figs, short)


def _adopt_source_figure(pid, src_fig):
    """把 arXiv 源码原图写回 figures 表（按图序号匹配，匹配不到就追加）。"""
    try:
        target = None
        for f in database.get_paper_figures(pid):
            if int(f.get("fig_index") or 0) == int(src_fig.get("fig_index") or 0):
                target = f
                break
        conn = database.get_conn()
        if target:
            conn.execute("UPDATE figures SET image_path=?, figure_type=?, caption=COALESCE(NULLIF(?,''), caption) WHERE id=?",
                         (src_fig["image_path"], src_fig.get("figure_type") or "source_raster",
                          src_fig.get("caption") or "", target["id"]))
        else:
            conn.execute("INSERT INTO figures (paper_id, fig_index, image_path, caption, figure_type) VALUES (?,?,?,?,?)",
                         (pid, int(src_fig.get("fig_index") or 0), src_fig["image_path"],
                          src_fig.get("caption") or "", src_fig.get("figure_type") or "source_raster"))
        conn.commit()
        conn.close()
        try:
            import thumbs
            thumbs.invalidate(pid) if hasattr(thumbs, "invalidate") else None
        except Exception:
            pass
        return True, f"已采用 Figure {src_fig.get('fig_index')} 的源码原图"
    except Exception as e:
        return False, f"写回失败：{e}"


def render_obsidian_export(p, deep, figs, short):
    """导出面板：选 vault、看图注预览、确认后写盘。"""
    vault = obsidian_export.get_vault()
    st.markdown("**📤 导出为 Obsidian 笔记**")
    if not vault:
        st.caption("还没有选 Vault 目录。选一个文件夹（笔记会写到它的 20_Research/Papers/<领域>/ 下）。")
        picked = folder_picker.pick_folder_native("")
        if picked:
            st.session_state["_vault_candidate"] = picked
    else:
        st.caption(f"当前 Vault：`{vault}`")
        if st.button("更换 Vault 目录", key=f"vault_change_{short}"):
            picked = folder_picker.pick_folder_native(vault)
            if picked:
                obsidian_export.set_vault(picked)
                st.rerun()

    cand = st.session_state.get("_vault_candidate")
    if cand:
        st.info(f"已选择：`{cand}`")
        if st.button("✅ 就用这个目录", key=f"vault_use_{short}", type="primary"):
            obsidian_export.set_vault(cand)
            st.session_state.pop("_vault_candidate", None)
            st.rerun()

    _pv = obsidian_export.preview(
        p, analysis_md=deep.get("markdown") or "",
        figures=[{"fig_index": f.get("fig_index"), "image_path": f.get("image_path"),
                  "caption": f.get("caption") or "", "figure_type": f.get("figure_type") or ""}
                 for f in figs if f.get("image_path")],
        related=[], scores=deep.get("scores") or {})

    with st.expander("预览笔记内容（frontmatter + 正文 + wikilink 图）", expanded=False):
        st.code(_pv["text"][:4000], language="markdown")
        st.caption(f"文件将写入：20_Research/Papers/{_pv['domain']}/{_pv['note_name']}.md")

    overwrite = st.checkbox("如果同名笔记已存在，覆盖它", value=False, key=f"exp_force_{short}")
    if st.button("现在导出", key=f"exp_go_{short}", type="primary"):
        res = obsidian_export.export_note(
            p, analysis_md=deep.get("markdown") or "",
            figures=[{"fig_index": f.get("fig_index"), "image_path": f.get("image_path"),
                      "caption": f.get("caption") or "", "figure_type": f.get("figure_type") or ""}
                     for f in figs if f.get("image_path")],
            scores=deep.get("scores") or {}, force=overwrite)
        if res["status"] == "created":
            st.success(res["message"])
        elif res["status"] == "exists":
            st.warning(res["message"] + "（勾选上方「覆盖」可替换）")
        else:
            st.error(res["message"])


# ═══════════════════════════════════════════════════
# 页签 1：文献库概览与详情
# ═══════════════════════════════════════════════════

tab1, tab2, tab3 = st.tabs(["📖 文献库概览", "🔬 图片精确检索", "🔍 学术搜索"])

with tab1:
    selected_pid = st.session_state.get("selected_paper_id")
    if selected_pid:
        render_paper_detail_page(selected_pid, api_provider, api_key, api_model)
        st.stop()

    st.markdown("""
    <div class="sc-top-header">
      <div class="sc-brand">
        <span class="sc-logo-icon">📚</span>
        <div>
          <span class="sc-app-title">PaperLibrary <span class="sc-badge-pro">PRO</span></span>
          <span class="sc-app-subtitle">智能学术文献库 · 空间图表透视 · 毫秒级知识检索</span>
          <span class="sc-designed-by">designed by AronyounG</span>
        </div>
      </div>
      <div class="sc-status-chips">
        <span class="sc-chip"><span class="sc-dot dot-green"></span> 本地服务 8502 活跃</span>
        <span class="sc-chip"><span class="sc-dot dot-blue"></span> SQLite FTS5 加速</span>
        <span class="sc-chip"><span class="sc-dot dot-purple"></span> HTML5 PDF.js 就绪</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    try:
        papers = database.get_all_papers(selected_folder if selected_folder != "全部" else None)
    except Exception:
        papers = []

    if not papers:
        st.markdown("""
        <div style="background: linear-gradient(135deg, rgba(47,111,94,0.06), rgba(99,102,241,0.05)); border: 1.5px dashed rgba(47,111,94,0.35); border-radius: 12px; padding: 24px 20px; text-align: center; margin: 16px 0 24px 0;">
            <div style="font-size: 38px; margin-bottom: 6px;">📚</div>
            <h3 style="margin: 0 0 8px 0; color: #2f6f5e; font-weight: 700; font-size: 20px;">欢迎使用个人文献库！</h3>
            <p style="color: #475569; font-size: 14.5px; max-width: 680px; margin: 0 auto 12px auto; line-height: 1.6;">
                当前文献库尚未添加文献。您可以选择以下任意一种方式快速开始：
            </p>
            <div style="display: flex; justify-content: center; gap: 20px; flex-wrap: wrap; text-align: left; max-width: 760px; margin: 0 auto; font-size: 13.5px; color: #334155;">
                <div style="flex: 1; min-width: 220px; background: rgba(255,255,255,0.85); padding: 12px 14px; border-radius: 8px; border: 1px solid rgba(0,0,0,0.06);">
                    <strong>📥 方式一：直接在下方拖入</strong><br>
                    <span style="color:#64748b;">将下载好的 PDF 拖入下方虚线框，即可秒级自动解析入库并提取核心图表。</span>
                </div>
                <div style="flex: 1; min-width: 220px; background: rgba(255,255,255,0.85); padding: 12px 14px; border-radius: 8px; border: 1px solid rgba(0,0,0,0.06);">
                    <strong>📂 方式二：放入文件夹并扫描</strong><br>
                    <span style="color:#64748b;">将 PDF 复制到软件根目录下的 <code>【文献存放处】</code>，点击左侧边栏「🔄 扫描刷新文献库」。</span>
                </div>
                <div style="flex: 1; min-width: 220px; background: rgba(255,255,255,0.85); padding: 12px 14px; border-radius: 8px; border: 1px solid rgba(0,0,0,0.06);">
                    <strong>⚙️ 方式三：绑定已有文件夹</strong><br>
                    <span style="color:#64748b;">点击左侧边栏「⚙️ 设定文献库总大文件夹」，直接指定电脑上已有的论文大目录。</span>
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.container(border=True):
            st.markdown("#### 📥 快速添加第一篇文献（拖入 PDF 并指定分类）")
            st.caption("直接将新下载的 PDF 拖入此处，系统将自动保存至文献库大文件夹并即刻解析入库：")
            tab_files_empty = st.file_uploader("拖拽或选择 PDF", type=["pdf"], accept_multiple_files=True, key="empty_upload_pdfs", label_visibility="collapsed")
            e_col1, e_col2 = st.columns([2, 1.2])
            with e_col1:
                tab_cats_e = importer.get_existing_categories(get_scan_root())
                default_target_e = selected_folder if selected_folder in tab_cats_e else "根目录"
                e_chosen_cat = st.selectbox("导入到哪个文件夹/分类：", tab_cats_e + ["➕ 新建分类..."], index=tab_cats_e.index(default_target_e) if default_target_e in tab_cats_e else 0, key="empty_import_cat")
                e_new_txt = ""
                if e_chosen_cat == "➕ 新建分类...":
                    e_new_txt = st.text_input("输入新分类名称", placeholder="例如：最新文献 / 钙钛矿", key="empty_import_new_cat")
                final_tab_cat_e = e_new_txt.strip() if e_chosen_cat == "➕ 新建分类..." and e_new_txt.strip() else e_chosen_cat
            with e_col2:
                e_sync_disk = st.checkbox("电脑同步归档（存入对应子目录）", value=True, key="empty_import_sync_disk", help="一般默认一样：电脑与网站在同一分类子目录下；若取消勾选，则文件存入根目录但在网站打上分类标签")
                if st.button("🚀 立即导入入库", key="empty_btn_do_import", type="primary", use_container_width=True, disabled=not tab_files_empty):
                    suc_c = 0
                    with st.spinner(f"正在导入 {len(tab_files_empty)} 篇文献并解析图表…"):
                        for uf in tab_files_empty:
                            ok, _ = importer.ingest_uploaded_pdf(
                                uf.getvalue(),
                                uf.name,
                                get_scan_root(),
                                target_folder=final_tab_cat_e,
                                sync_to_disk_folder=e_sync_disk
                            )
                            if ok:
                                suc_c += 1
                    if suc_c > 0:
                        st.success(f"✅ 成功导入 {suc_c} 篇文献至「{final_tab_cat_e}」！")
                        time.sleep(0.6)
                        st.rerun()
    else:
        # 知识库全景数据概览仪表盘
        try:
            stats = database.get_stats()
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.metric("📚 文献总数", f"{stats.get('total_papers', len(papers))} 篇")
            sc2.metric("🖼️ 提取图表", f"{stats.get('total_figures', 0):,} 张")
            sc3.metric("🤖 AI 深度解读", f"{stats.get('summarized_papers', 0)} 篇")
            enriched_cnt = sum(1 for p in papers if citation.parse_authors(p.get("authors")))
            sc4.metric("🏛️ 权威题录补全", f"{enriched_cnt} 篇")
        except Exception:
            pass

        st.caption(f"共 {len(papers)} 篇文献 · 当前文件夹：{selected_folder}")

        with st.expander("📥 快速导入文献（拖入 PDF 并指定分类）", expanded=False):
            st.caption("直接将新下载的 PDF 拖入此处，系统将自动保存至文献库大文件夹并即刻解析入库：")
            tab_files = st.file_uploader("拖拽或选择 PDF", type=["pdf"], accept_multiple_files=True, key="tab1_upload_pdfs", label_visibility="collapsed")
            t_col1, t_col2 = st.columns([2, 1.2])
            with t_col1:
                tab_cats = importer.get_existing_categories(get_scan_root())
                default_target = selected_folder if selected_folder in tab_cats else "根目录"
                t_chosen_cat = st.selectbox("导入到哪个文件夹/分类：", tab_cats + ["➕ 新建分类..."], index=tab_cats.index(default_target), key="tab1_import_cat")
                t_new_txt = ""
                if t_chosen_cat == "➕ 新建分类...":
                    t_new_txt = st.text_input("输入新分类名称", placeholder="例如：最新文献 / 钙钛矿", key="tab1_import_new_cat")
                final_tab_cat = t_new_txt.strip() if t_chosen_cat == "➕ 新建分类..." and t_new_txt.strip() else t_chosen_cat
            with t_col2:
                t_sync_disk = st.checkbox("电脑同步归档（存入对应子目录）", value=True, key="tab1_import_sync_disk", help="一般默认一样：电脑与网站在同一分类子目录下；若取消勾选，则文件存入根目录但在网站打上分类标签")
                if st.button("🚀 立即导入入库", key="tab1_btn_do_import", type="primary", use_container_width=True, disabled=not tab_files):
                    suc_c = 0
                    with st.spinner(f"正在导入 {len(tab_files)} 篇文献并解析图表…"):
                        for uf in tab_files:
                            ok, _ = importer.ingest_uploaded_pdf(
                                uf.getvalue(),
                                uf.name,
                                get_scan_root(),
                                target_folder=final_tab_cat,
                                sync_to_disk_folder=t_sync_disk
                            )
                            if ok:
                                suc_c += 1
                    if suc_c > 0:
                        st.success(f"✅ 成功导入 {suc_c} 篇文献至「{final_tab_cat}」！")
                        time.sleep(0.6)
                        st.rerun()

        # ── Google 风格超显眼学术搜索英雄区 (PaperLibrary Hero Search) ──
        st.markdown("""
        <div class="google-hero-header">
            <div class="google-academic-logo">
                <span class="g-blue">P</span><span class="g-red">a</span><span class="g-yellow">p</span><span class="g-blue">e</span><span class="g-green">r</span><span class="g-red">L</span><span class="g-blue">i</span><span class="g-yellow">b</span><span class="g-green">r</span><span class="g-blue">a</span><span class="g-red">r</span><span class="g-yellow">y</span>
            </div>
            <div class="google-hero-tagline">
                极速语义检索 · 智能穿透标题、摘要、作者与正文相关性排序
            </div>
        </div>
        """, unsafe_allow_html=True)

        # 消费缓冲中的搜索词（在 widget 实例化前设置，防止 StreamlitAPIException）
        if "pending_ps" in st.session_state:
            st.session_state["ps"] = st.session_state.pop("pending_ps")

        def _clear_paper_search():
            st.session_state["ps"] = ""

        # 谷歌药丸搜索栏：左侧 Google 彩色放大镜 SVG + 中间大号纯净输入框 + 右侧清空/Enter徽章
        st.markdown('<div class="google-search-pill-card">', unsafe_allow_html=True)
        g_c1, g_c2, g_c3 = st.columns([0.45, 8.3, 1.25])
        with g_c1:
            st.markdown("""
            <div style="padding-top:10px; text-align:center;">
                <svg focusable="false" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22">
                    <path fill="#4285F4" d="M15.5 14h-.79l-.28-.27A6.471 6.471 0 0 0 16 9.5 6.5 6.5 0 1 0 9.5 16c1.61 0 3.09-.59 4.23-1.57l.27.28v.79l5 4.99L20.49 19l-4.99-5zm-6 0C7.01 14 5 11.99 5 9.5S7.01 5 9.5 5 14 7.01 14 9.5 11.99 14 9.5 14z"/>
                </svg>
            </div>
            """, unsafe_allow_html=True)
        with g_c2:
            sq = st.text_input("🔍 搜索论文",
                               placeholder="在文献库中搜索…（输入中英文关键词、作者名、年份、DOI 或专有名词）",
                               key="ps", label_visibility="collapsed")
        with g_c3:
            if sq:
                st.button("✕ 清空", key="btn_clear_sq", on_click=_clear_paper_search, use_container_width=True)
            else:
                st.markdown("""
                <div style="padding-top:11px; text-align:right;">
                    <span class="google-kbd-badge">Enter ↵</span>
                </div>
                """, unsafe_allow_html=True)
        st.markdown('</div>', unsafe_allow_html=True)

        _searching = bool(sq and sq.strip())
        if _searching:
            try:
                papers = database.search_papers(
                    sq, selected_folder if selected_folder != "全部" else None)
                # 把「查询是怎么被理解的」摊开给用户看，排序依据不黑箱
                _ex = local_rank.explain(sq)
                _terms_txt = "、".join(_ex["terms"][:8]) or "（无有效词）"
                _hit_title = sum(1 for x in papers
                                 if "title" in (x.get("matched_fields") or []))
                st.caption(
                    f"搜索「{sq}」：命中 **{len(papers)}** 篇 · "
                    f"拆成 {_ex['term_count']} 个检索词（{_terms_txt}）"
                    + (f" · 其中 {_hit_title} 篇标题命中" if papers else ""))
                st.caption("结果按**相关性**排序：标题命中 > 摘要命中 > 正文命中；"
                           "标题里没有搜索词、但正文相关的文章同样会列出，只是排在后面。")
            except Exception as _e:
                st.warning(f"检索出错：{_e}")

        # 预取这批文献的全部图片：原先每篇各查一次库（140 篇 ≈ 1 秒），现在只查一次
        _fmap = _figures_map(tuple(x["id"] for x in papers))

        # ── 分页 ──
        # 一次渲染 140 张卡片（含 280 个 expander、420 个按钮、280 个页码输入框）
        # 是页面慢的主因：Streamlit 的每个控件都有固定开销。
        # 分页把每次渲染的卡片数降一个数量级，翻页只重跑这一页。
        _total = len(papers)
        _pc1, _pc2, _pc3 = st.columns([1.2, 1.2, 3])
        with _pc1:
            _per = st.selectbox("每页", [20, 30, 50, 100, _total if _total > 100 else 200],
                                index=1, key="pg_size",
                                format_func=lambda n: f"每页 {n} 篇")
        _pages = max(1, (_total + _per - 1) // _per)
        with _pc2:
            _pg = st.number_input("页码", 1, _pages, value=1, key="pg_no")
        with _pc3:
            st.caption(f"共 {_total} 篇 · 第 {_pg} / {_pages} 页"
                       + (f"（本页 {(_pg-1)*_per+1}–{min(_pg*_per, _total)} 篇）" if _pages > 1 else ""))
        _page_items = papers[(_pg - 1) * _per: _pg * _per]

        # ── 批量引文导出工具箱 ──
        with st.expander(f"📑 批量导出本页文献引文 ({len(_page_items)} 篇)", expanded=False):
            _b_fmt = st.radio("引文格式", ["BibTeX", "GB/T 7714", "APA", "Markdown"], horizontal=True, key="bulk_fmt")
            if _b_fmt == "BibTeX":
                _bulk_str = "\n\n".join(citation.format_bibtex(p) for p in _page_items)
            elif _b_fmt == "GB/T 7714":
                _bulk_str = "\n".join(f"[{i+1}] {citation.format_gbt7714(p)}" for i, p in enumerate(_page_items))
            elif _b_fmt == "APA":
                _bulk_str = "\n\n".join(citation.format_apa(p) for p in _page_items)
            else:
                _bulk_str = "\n".join(f"- {citation.format_markdown_ref(p)}" for p in _page_items)
            st.text_area("批量引文内容（可一键全选复制）", _bulk_str, height=150, key="bulk_txt")
            st.download_button(
                label=f"📥 下载 {_b_fmt} 文本文件",
                data=_bulk_str.encode('utf-8'),
                file_name=f"citations.{'bib' if _b_fmt == 'BibTeX' else 'txt'}",
                mime="text/plain",
                key="dl_bulk_cite"
            )

        for p in _page_items:
            title = str(p.get("title") or p["filename"])
            pid = p["id"]
            au_list = citation.parse_authors(p.get("authors"))
            au_display = ", ".join(au_list[:5]) + (" 等" if len(au_list) > 5 else "") if au_list else "未知作者"
            journal = (p.get("journal") or "").strip()
            year = p.get("year") or ""
            folder = p.get("folder") or "根目录"
            p_figs = _fmap.get(pid, [])

            with st.container(border=True):
                c_main, c_side = st.columns([4.2, 0.8])
                with c_main:
                    st.markdown(f"<div style='font-size:15px; font-weight:600; line-height:1.3; margin-bottom:3px;'>{html_mod.escape(title)}</div>", unsafe_allow_html=True)
                    pills = [f"<span class='meta-pill meta-pill-author' title='作者列表'>👤 {html_mod.escape(au_display)}</span>"]
                    if journal:
                        pills.append(f"<span class='meta-pill meta-pill-journal' title='出版期刊'>🏛️ {html_mod.escape(journal)}</span>")
                    if year:
                        pills.append(f"<span class='meta-pill meta-pill-year' title='发表年份'>📅 {year}</span>")
                    if folder:
                        pills.append(f"<span class='meta-pill meta-pill-folder' title='归属文件夹'>📁 {html_mod.escape(folder)}</span>")
                    if p.get("doi"):
                        pills.append(f"<span class='meta-pill meta-pill-doi' title='数字对象唯一标识符 DOI'>🔗 {html_mod.escape(p['doi'])}</span>")
                    st.markdown(f"<div class='meta-pills-row'>{' '.join(pills)}</div>", unsafe_allow_html=True)

                    if _searching:
                        render_relevance(p)

                    # 紧凑摘要截断预览 (2 行显示，带左侧品牌边条)
                    raw_sum = database._readable_summary(p.get("summary")) or p.get("full_text") or ""
                    if raw_sum:
                        clean_sum = html_mod.escape(raw_sum[:260].strip())
                        st.markdown(f"<div class='compact-summary'>📝 {clean_sum}</div>", unsafe_allow_html=True)

                with c_side:
                    if p_figs:
                        f0 = p_figs[0]
                        img_path = resolve_media_path(f0.get("image_path", ""))
                        if img_path and os.path.isfile(img_path):
                            thumb_img = thumbs.make_thumb(img_path, APP_DIR) or img_path
                            st.image(thumb_img, width=85)
                            st.caption(f"🖼️ {len(p_figs)} 图")

                # 目录操作栏（极简利落：主按钮进入工作台 + 辅助移出）
                act1, act2 = st.columns([4.2, 0.8])
                with act1:
                    if st.button("📖 进入研读工作台（阅读PDF · AI问答 · 全部图表） ➡",
                                 key=f"enter_wb_{pid[:12]}", type="primary", use_container_width=True):
                        st.session_state["selected_paper_id"] = pid
                        st.rerun()
                with act2:
                    render_delete_button(p, key_suffix="catalog")


# ═══════════════════════════════════════════════════
# 页签 2：图片/图注精确检索（核心亮点）
# ═══════════════════════════════════════════════════

with tab2:
    st.markdown("""
    <div class="google-hero-header">
        <div class="google-academic-logo">
            <span class="g-blue">F</span><span class="g-red">i</span><span class="g-yellow">g</span><span class="g-blue">u</span><span class="g-green">r</span><span class="g-red">e</span><span class="g-blue">L</span><span class="g-yellow">e</span><span class="g-green">n</span><span class="g-blue">s</span>
            <span class="google-logo-sub" style="background:#059669;">Images & Graphs</span>
        </div>
        <div class="google-hero-tagline">
            自然语言跨语言检索每一张论文图表 · 精确到 Figure 级空间图注对齐
        </div>
    </div>
    """, unsafe_allow_html=True)

    # 谷歌药丸搜索栏（图表专用）
    st.markdown('<div class="google-search-pill-card">', unsafe_allow_html=True)
    # 消费缓冲中的图表搜索词（在 widget 实例化前设置，防止 StreamlitAPIException）
    if "pending_fs" in st.session_state:
        st.session_state["fs"] = st.session_state.pop("pending_fs")

    def _clear_figure_search():
        st.session_state["fs"] = ""

    def _set_figure_search_tag(tag_str):
        st.session_state["fs"] = tag_str

    f_c1, f_c2, f_c3 = st.columns([0.45, 8.3, 1.25])
    with f_c1:
        st.markdown("""
        <div style="padding-top:10px; text-align:center;">
            <svg focusable="false" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="22" height="22">
                <path fill="#059669" d="M21 19V5c0-1.1-.9-2-2-2H5c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h14c1.1 0 2-.9 2-2zM8.5 13.5l2.5 3.01L14.5 12l4.5 6H5l3.5-4.5z"/>
            </svg>
        </div>
        """, unsafe_allow_html=True)
    with f_c2:
        fq = st.text_input(
            "🔍 搜索图片",
            placeholder="例如: 循环伏安 CV · XRD 图谱 · SEM 图像 · 反应机理 Scheme · EPR 信号 · 晶体结构 …",
            key="fs", label_visibility="collapsed")
    with f_c3:
        if fq:
            st.button("✕ 清空", key="btn_clear_fq", on_click=_clear_figure_search, use_container_width=True)
        else:
            st.markdown("""
            <div style="padding-top:11px; text-align:right;">
                <span class="google-kbd-badge">Enter ↵</span>
            </div>
            """, unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    # 快捷学术检索标签 Chips
    quick_tags = ["循环伏安 CV", "扫描电镜 SEM", "透射电镜 TEM", "X射线衍射 XRD", "反应机理 Scheme", "红外光谱 FTIR"]
    q_cols = st.columns(len(quick_tags))
    for t_idx, tag in enumerate(quick_tags):
        with q_cols[t_idx]:
            tag_val = tag.split()[0]
            st.button(tag, key=f"qtag_{t_idx}", on_click=_set_figure_search_tag, args=(tag_val,), use_container_width=True)

    # 🔬 多图横向对比幕布（若用户加入了图表对比，优先在此处并排展示）
    render_figure_comparator_section()

    if fq:
        try:
            results = database.search_figures(
                fq, selected_folder if selected_folder != "全部" else None)
            _f_terms = local_rank.all_forms(fq)
            st.caption(f"搜索「{fq}」：找到 {len(results)} 张匹配图片"
                       f"（拆成 {len(_f_terms)} 个检索词）")
            if results:
                st.caption("结果按相关性排序：图注精准命中 > 图表类型命中 > 上下文命中")

            if not results:
                st.warning("未找到匹配结果，请尝试其他关键词")
            else:
                for r in results:
                    with st.container(border=True):
                        render_relevance(r, snippet_key="snippet")
                        c1, c2 = st.columns([1.5, 2.5])
                        with c1:
                            ip = resolve_media_path(r.get("image_path", ""))
                            if ip and os.path.isfile(ip):
                                st.image(ip, width="stretch")
                            else:
                                st.caption("⚠️ 图片文件缺失")
                        with c2:
                            # 1. 规整图表元数据头
                            ft = r.get("figure_type", "")
                            p_title = r.get("paper_title") or "未知文献"
                            folder_txt = f" · {r['paper_folder']}" if r.get("paper_folder") else ""
                            page_txt = f"第 {r['page_number']} 页" if r.get("page_number") else ""

                            meta_pills = []
                            if ft:
                                meta_pills.append(f"<span class='meta-pill meta-pill-journal'>🏷️ {html_mod.escape(ft)}</span>")
                            if page_txt:
                                meta_pills.append(f"<span class='meta-pill meta-pill-year'>📑 {page_txt}</span>")
                            meta_pills.append(f"<span class='meta-pill meta-pill-author' title='{html_mod.escape(p_title)}'>📄 {html_mod.escape(p_title[:45])}{folder_txt}</span>")
                            st.markdown(f"<div class='meta-pills-row'>{' '.join(meta_pills)}</div>", unsafe_allow_html=True)

                            # 2. 规整图注框 (Caption Card Box)
                            cap_txt = (r.get("caption") or "").strip()
                            if cap_txt:
                                st.markdown(f"""
                                <div class="caption-card-box">
                                  <div class="cap-label">📝 提取论文图注 (Caption)</div>
                                  <div class="cap-body">{html_mod.escape(cap_txt)}</div>
                                </div>
                                """, unsafe_allow_html=True)
                            else:
                                st.markdown("""
                                <div class="caption-card-box" style="border-left-color:#64748b;">
                                  <div class="cap-label" style="color:#94a3b8;">📝 图注 (Caption)</div>
                                  <div class="cap-body" style="color:#94a3b8; font-style:italic;">（原文未就近提取到独立图注）</div>
                                </div>
                                """, unsafe_allow_html=True)

                            ap = r.get("paper_abs_path", "")
                            p_id = r.get("paper_id")

                            # 🎯 图文双向快速锚定：正文讨论抽取与跳转
                            with st.expander("💬 查看原文关键讨论与页码", expanded=False):
                                mentions = figure_anchor.find_figure_mentions_in_paper(ap, cap_txt, ft)
                                if mentions:
                                    st.caption(f"🎯 本地算法毫秒级命中正文 **{len(mentions)} 处** 讨论：")
                                    for m_idx, m_item in enumerate(mentions[:4]):
                                        m_c1, m_c2 = st.columns([3, 1])
                                        with m_c1:
                                            st.markdown(f"""
                                            <div style="font-size:12px; line-height:1.5; color:#1e293b; background:#f8fafc; border-left:3px solid #2563eb; padding:6px 8px; border-radius:4px; margin-bottom:4px;">
                                              <div style="font-weight:700; color:#1d4ed8; font-size:11px; margin-bottom:2px;">📑 第 {m_item['page']} 页 · 引用 <code>{html_mod.escape(m_item['match'])}</code></div>
                                              <div>{html_mod.escape(m_item['snippet'])}</div>
                                            </div>
                                            """, unsafe_allow_html=True)
                                        with m_c2:
                                            if p_id and st.button("📍 直达该页", key=f"t2_jmp_{r['id']}_{m_idx}_{m_item['page']}", use_container_width=True, help=f"跳转至该篇研读工作台第 {m_item['page']} 页"):
                                                st.session_state["selected_paper_id"] = p_id
                                                st.session_state[f"pending_pdf_page_{p_id[:12]}"] = m_item['page']
                                                st.session_state[f"pending_pdf_mode_{p_id[:12]}"] = "🖼️ 逐页图像预览"
                                                st.toast(f"📍 已直达第 {m_item['page']} 页")
                                                st.rerun()
                                else:
                                    st.caption("正文中暂未检测到明显针对该图号的独立句式，可参考原图注。")

                            # 3. 底部整齐操作按钮行
                            b_act1, b_act_cmp, b_act2, b_act3 = st.columns([2.3, 1.8, 1.4, 1.1])
                            with b_act1:
                                if p_id and st.button("📖 进入本篇研读工作台 ➡", key=f"fig_to_wb_{r['id']}", type="primary", use_container_width=True):
                                    st.session_state["selected_paper_id"] = p_id
                                    st.rerun()
                            with b_act_cmp:
                                in_cmp = comparator.is_in_basket(st.session_state, r["id"])
                                if in_cmp:
                                    if st.button("✅ 已在对比 (移出)", key=f"btn_cmp_{r['id']}", use_container_width=True, help="点击从横向对比幕布中移出"):
                                        comparator.remove_from_basket(st.session_state, r["id"])
                                        st.rerun()
                                else:
                                    if st.button("🔬 加入横向对比", key=f"btn_cmp_{r['id']}", use_container_width=True, help="加入多图横向对比幕布（最多4张）"):
                                        ok, msg = comparator.toggle_basket(st.session_state, {
                                            "id": r["id"],
                                            "image_path": r.get("image_path", ""),
                                            "caption": r.get("caption", ""),
                                            "figure_type": r.get("figure_type") or "Figure",
                                            "paper_id": r.get("paper_id") or "",
                                            "paper_title": r.get("paper_title") or "未知文献",
                                            "journal": r.get("journal") or "",
                                            "year": r.get("year") or "",
                                            "page_number": r.get("page_number") or "",
                                            "paper_abs_path": ap,
                                        })
                                        if not ok:
                                            st.warning(msg)
                                        else:
                                            comparator.set_compare_open(st.session_state, True)
                                            st.rerun()
                            with b_act2:
                                if ap and os.path.exists(ap):
                                    pdf_url = f"http://localhost:8502/?path={urllib.parse.quote(ap, safe='')}"
                                    st.link_button("📖 新标签定位", pdf_url, help="在新标签页全屏打开 PDF 原文", use_container_width=True)
                            with b_act3:
                                if ap and os.path.exists(ap):
                                    if st.button("🖥️ 外部", key=f"open_sys_fig_{r['id']}", help="使用系统阅读器打开", use_container_width=True):
                                        webbrowser.open(ap)
        except Exception as e:
            st.error(f"搜索出错: {e}")
    else:
        try:
            stats = database.get_stats()
            m1, m2, m3, m4 = st.columns(4)
            m1.metric("📄 论文总数", stats["paper_count"])
            m2.metric("🖼️ 图片总数", stats["figure_count"])
            m3.metric("📝 已提取图注", stats.get("with_caption", 0))
            m4.metric("📁 文件夹", len(folders) if folders else 1)
        except Exception:
            pass

        st.info("""
💡 **支持的搜索类型**：
- 🔬 **光谱类**: EPR 谱图 · XPS · NMR 氢谱 · UV-Vis · 荧光光谱 · 拉曼光谱
- ⚡ **电化学**: CV 曲线 · LSV 极化曲线 · EIS 阻抗谱 · 法拉第效率 · Tafel 斜率
- 🔍 **材料表征**: XRD 图谱 · SEM 图像 · TEM · HRTEM · SAED · AFM
- ⚗️ **反应相关**: 反应机理图 · 催化剂结构 · 底物适用范围 · 反应路径
- 🎯 **特定参数**: g 因子 · 氧化峰 · 还原电位 · TON · TOF · 电流密度
- 🧲 **概念效应**: CISS 效应 · 自旋极化 · 手性诱导 · 不对称催化
""")


st.markdown("---")
st.caption("学术文献管理与图片级检索系统 | Powered by Streamlit + PyMuPDF + AI")


# ═══════════════════════════════════════════════════
# 页签 3：学术搜索（本地库 + 多源网络聚合）
#   设计参考：一个搜索框两个去处（本地库 / 网络检索），切 Tab 保留关键词；
#   网络结果跨源折叠、标注「已在库中」、可一键导入。
# ═══════════════════════════════════════════════════

def library_doi_title_sets():
    """准备本地碰撞比对用的 DOI / 标题集合（指南 3.3）"""
    dois, titles = set(), set()
    try:
        for p in database.get_all_papers():
            d = agg.normalize_doi(p.get("doi"))
            if d:
                dois.add(d)
            t = agg.title_key(p.get("title"))
            if t:
                titles.add(t)
    except Exception:
        pass
    return dois, titles


def make_paper_id(doi: str, title: str, year) -> str:
    """给「导入但没有原文文件」的记录生成一个稳定 id（不用 SHA256，因为没文件）"""
    key = f"ext:{agg.normalize_doi(doi) or agg.title_key(title)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def import_external(item, root: str, folder_rel: str, email: str = ""):
    """把一条网络结果**连原文一起**存进本地库。

    关键：必须真的把 PDF 下载到分类文件夹里，否则界面上会出现
    「能搜到但打开说 PDF 未找到、也不能移动/看图」的半成品状态。

    返回 (paper_id, fetch_result)。fetch_result 里带 ok/path/reason/tried，
    供界面如实告知成功或失败原因（付费墙/403 是常态，不假装能下）。
    """
    fetch = pdf_fetch.import_with_pdf(item, root, folder_rel, email=email)
    pid = make_paper_id(item.get("doi"), item.get("title"), item.get("year"))
    paper = {
        "id": pid,
        "filename": os.path.basename(fetch["path"]) if fetch.get("ok") else "",
        "title": item.get("title") or "",
        "authors": json.dumps(item.get("authors") or [], ensure_ascii=False),
        "doi": item.get("doi") or "",
        "year": item.get("year"),
        "folder": library_fs.folder_of(root, fetch["path"]) if fetch.get("ok") else folder_rel,
        "abs_path": fetch.get("path", "") if fetch.get("ok") else "",
        "full_text": "",
        "summary": json.dumps({
            "title": item.get("title") or "",
            "journal": item.get("venue") or "",
            "authors": ", ".join((item.get("authors") or [])[:3]),
            "summary": (item.get("abstract") or "")[:1500],
            "keywords": [], "reaction_types": [],
            "source": item.get("source") or "",
            "fetched_from": fetch.get("url", ""),
        }, ensure_ascii=False),
        "parsed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    database.upsert_paper(paper)

    # 抓到原文后顺手解析全文与图片，让它和本地导入的文献完全一致
    if fetch.get("ok") and paper["abs_path"]:
        try:
            pi = {"full_path": paper["abs_path"], "filename": paper["filename"],
                  "folder": paper["folder"]}
            parsed, figs = paper_parser.parse_one(pi)
            parsed["id"] = pid
            parsed["title"] = paper["title"]
            parsed["summary"] = paper["summary"]
            parsed["parsed_at"] = paper["parsed_at"]
            database.upsert_paper(parsed)
            database.clear_figures(pid)
            for f in figs:
                database.insert_figure(pid, f)
        except Exception:
            pass          # 解析失败不影响「原文已经在库里」这件事
    return pid, fetch


with tab3:
    st.markdown("### 🔍 学术搜索")
    st.caption("先在本地库里搜；不够就去网上找 —— 一个搜索框，两个去处。")

    sq = st.text_input(
        "学术搜索", key="acq", label_visibility="collapsed",
        placeholder="输入关键词、DOI 或标题…    例如：chirality induced spin selectivity / 10.1021/jacs.5c10757")
    search_btn = st.button("🔍 搜索", type="primary", key="acq_go")

    st.caption("当前条件：关键词：" + (sq or "（空）"))
    st.divider()

    if not sq:
        st.info("👆 输入关键词后按「搜索」。左边是**你自己的文献库**，右边会去 "
                "OpenAlex / Crossref / Europe PMC / arXiv 这些公开学术库找。")
    else:
        st_tab_local, st_tab_web = st.tabs(["📚 本地库", "🌐 网络检索"])

        # ─────────── 本地库 ───────────
        with st_tab_local:
            try:
                local = database.search_papers(sq)
            except Exception as e:
                local = []
                st.error(f"本地检索失败：{e}")

            st.markdown(
                f'<div style="display:flex;align-items:center;gap:12px;margin:.4rem 0 .8rem">'
                f'<span style="background:#e4efea;color:#235648;border-radius:6px;'
                f'padding:2px 10px;font-size:.78rem;font-weight:700">📚 本地</span>'
                f'<span style="color:#75726a;font-size:.85rem">共 <b>{len(local)}</b> 条结果</span>'
                f'</div>', unsafe_allow_html=True)

            if not local:
                st.caption("本地库里没有匹配。切到「🌐 网络检索」去公开学术库找找。")
            for p in local[:50]:
                with st.container(border=True):
                    st.markdown(f"**{p.get('title') or p.get('filename') or '(无标题)'}**")
                    au = []
                    try:
                        au = json.loads(p.get("authors") or "[]")
                    except Exception:
                        au = []
                    meta = " · ".join(x for x in [
                        ", ".join(au[:4]) + (" 等" if len(au) > 4 else "") if au else "",
                        p.get("folder") or "", str(p.get("year") or ""),
                        p.get("doi") or ""] if x)
                    if meta:
                        st.caption(meta)
                    ap = p.get("abs_path", "")
                    c1, c2 = st.columns([1, 4])
                    with c1:
                        if ap and os.path.exists(ap):
                            st.caption("📄 PDF")
                        else:
                            st.caption("⚠️ 无 PDF")
                    with c2:
                        if ap and os.path.exists(ap):
                            url = f"http://localhost:8502/?path={urllib.parse.quote(ap, safe='')}"
                            st.link_button("📖 打开原文", url)

        # ─────────── 网络检索 ───────────
        with st_tab_web:
            # 来源选择 + 排序
            c_src, c_sort = st.columns([3, 1])
            with c_src:
                picked = st.multiselect(
                    "检索来源", options=search_sources.SOURCE_IDS,
                    default=[s for s in search_sources.SOURCE_IDS
                             if s != "semanticscholar"],
                    format_func=lambda x: next(s["label"] for s in search_sources.SOURCES
                                               if s["id"] == x),
                    help="Semantic Scholar 是 AI2（Asta）的同一数据源，但匿名额度极紧、常年限流")
            with c_sort:
                web_sort = st.selectbox("排序", ["relevance", "citations", "timeDesc"],
                                        format_func=lambda x: {"relevance": "相关度",
                                                               "citations": "被引最高",
                                                               "timeDesc": "最新"}[x])

            # 高级筛选（列在结果上方，符合截图布局）
            with st.expander("⚙️ 高级筛选", expanded=False):
                f1, f2, f3, f4, f5 = st.columns(5)
                with f1:
                    y_from = st.text_input("起始年", key="acq_yf", placeholder="2015")
                with f2:
                    y_to = st.text_input("截止年", key="acq_yt", placeholder="2026")
                with f3:
                    f_venue = st.text_input("期刊/会议", key="acq_fv", placeholder="JACS")
                with f4:
                    f_author = st.text_input("作者", key="acq_fa", placeholder="Naaman")
                with f5:
                    min_cit = st.text_input("最低被引", key="acq_fc", placeholder="10")
                only_oa = st.checkbox("只看开放获取（OA）", key="acq_oa", value=False)

            # 导入设置：存到哪个分类 + 抓取用的邮箱（Unpaywall 要求）
            with st.expander("📥 导入设置", expanded=False):
                _fold_names = ["网络导入"] + [f["folder"] for f in folders
                                              if f["folder"] != "网络导入"]
                imp_folder = st.selectbox("导入到哪个分类", _fold_names, index=0,
                                          key="acq_impfolder",
                                          help="原文会下载到这个文件夹里，和本地导入的文献完全一样")
                imp_email = st.text_input(
                    "抓取用邮箱", key="acq_email",
                    value=st.session_state.get("acq_email_saved", ""),
                    placeholder="用于 Unpaywall 解析开放获取全文，随便填一个能用就行",
                    help="Unpaywall 要求带邮箱才能查询；只用于请求头，不会外传别的信息")
                st.caption("只有**开放获取**的能下到；付费墙后的（ACS / Science / Wiley）"
                           "会如实告诉你下不了，不会假装成功。")

            filters = {
                "yearFrom": y_from.strip(), "yearTo": y_to.strip(),
                "venue": f_venue.strip(), "author": f_author.strip(),
                "minCitations": min_cit.strip(), "onlyOA": bool(only_oa),
            }

            # 会话缓存：条件完全一致就不重查（外部检索吃额度）
            cache_key = json.dumps({"q": sq, "src": sorted(picked), "f": filters},
                                   ensure_ascii=False, sort_keys=True)
            if st.session_state.get("acq_cache_key") != cache_key or search_btn:
                with st.spinner("正在并发检索多个来源…"):
                    res = search_sources.search(
                        sq, {"sources": picked, "limit": 10, "page": 1, "filters": filters})
                st.session_state.acq_cache_key = cache_key
                st.session_state.acq_result = res
            res = st.session_state.get("acq_result") or {"items": [], "sources": {}}

            # 各源状态：✓/⚠ 角标，不堆红字
            st.markdown('<div style="font-size:.82rem;color:#75726a;margin:.5rem 0">来源：</div>',
                        unsafe_allow_html=True)
            for s in search_sources.SOURCES:
                stt = (res.get("sources") or {}).get(s["id"])
                if not stt:
                    continue
                if stt.get("ok"):
                    st.markdown(
                        f'<span style="display:inline-block;margin:2px 6px 2px 0;'
                        f'background:#e4efea;color:#235648;border-radius:999px;'
                        f'padding:2px 10px;font-size:.78rem">✓ {s["label"]} '
                        f'{stt.get("count",0)} 条</span>', unsafe_allow_html=True)
                else:
                    st.markdown(
                        f'<span style="display:inline-block;margin:2px 6px 2px 0;'
                        f'background:#fbeeec;color:#b4453a;border-radius:999px;'
                        f'padding:2px 10px;font-size:.78rem">⚠ {s["label"]}：'
                        f'{stt.get("error","失败")}</span>', unsafe_allow_html=True)

            # 折叠 + 本地碰撞 + 本地兜底过滤 + 排序
            lists = []
            for sid in picked:
                if (res.get("sources") or {}).get(sid, {}).get("ok"):
                    lists.append([i for i in res["items"] if i.get("source") == sid])
            merged = agg.merge_results(lists)
            merged = agg.apply_filters(merged, filters)
            merged = agg.rank_by_similarity(merged, sq)
            if web_sort != "relevance":
                merged = agg.sort_items(merged, web_sort)
            _dois, _titles = library_doi_title_sets()
            agg.mark_in_library(merged, _dois, _titles)

            # facet（只对已取回这批聚合，必须如实标注口径）
            facets = agg.compute_facets(merged)
            st.caption(f"已取回 **{len(merged)}** 条（计数只统计这批，不等于全网分布）。"
                       f"展开某条可看摘要。")

            if not merged:
                st.warning("没有取回结果。可以换关键词、放宽筛选，或勾选更多来源。")

            for i, it in enumerate(merged[:60]):
                with st.container(border=True):
                    title = it.get("title") or "(无标题)"
                    st.markdown(f"**{title}**")
                    au = it.get("authors") or []
                    au_txt = ", ".join(au[:4]) + (f" 等 {len(au)} 人" if len(au) > 4 else "")
                    meta = "  ".join(x for x in [au_txt, it.get("venue") or "",
                                                 str(it.get("year") or "")] if x)
                    if meta:
                        st.caption(meta)
                    badges = []
                    for sid in it.get("sources") or []:
                        lbl = next((s["label"] for s in search_sources.SOURCES if s["id"] == sid), sid)
                        badges.append(lbl)
                    stat = f"被引 {it.get('citations', 0)}"
                    if it.get("isOpenAccess"):
                        stat += " · OA"
                    if it.get("relevance") is not None:
                        stat += f" · 相关度 {it['relevance']}"
                    if badges:
                        stat += " · 来源 " + "/".join(badges)
                    st.caption(stat)

                    bc1, bc2, bc3 = st.columns([1.2, 1, 3.8])
                    with bc1:
                        if it.get("inLibrary"):
                            st.markdown(
                                '<span style="background:#e4efea;color:#235648;'
                                'border-radius:6px;padding:2px 8px;font-size:.78rem;'
                                'font-weight:600">已在库中</span>', unsafe_allow_html=True)
                        else:
                            if st.button("⬇ 下载并入库", key=f"imp_{i}",
                                         width="stretch", type="primary"):
                                _mail = (imp_email or "").strip()
                                if _mail:
                                    st.session_state.acq_email_saved = _mail
                                _r = None
                                with st.spinner("正在抓取原文并解析…"):
                                    try:
                                        _pid, _r = import_external(
                                            it, get_scan_root(), imp_folder, email=_mail)
                                    except Exception as e:
                                        st.error(f"导入失败：{e}")
                                if _r:
                                    st.session_state[f"impres_{i}"] = _r
                                    st.session_state.acq_cache_key = None   # 让下次重查刷新「已在库中」
                                    st.rerun()
                    with bc2:
                        link = it.get("oaUrl") or it.get("url") or (
                            f"https://doi.org/{it['doi']}" if it.get("doi") else "")
                        if link:
                            st.link_button("🔗 查看原文", link, width="stretch")
                    with bc3:
                        if it.get("doi"):
                            st.caption(f"DOI: {it['doi']}")

                    with st.expander("摘要 / 更多字段", expanded=False):
                        if it.get("abstract"):
                            st.write(it["abstract"][:1200])
                        else:
                            st.caption("这个来源没有返回摘要。")
                        st.caption(f"类型：{it.get('type') or '—'} ｜ "
                                   f"源 id：{it.get('sourceId') or '—'}")

                    # 上一次导入的结果：成功就报文件与页数，失败就报原因（不静默）
                    _ir = st.session_state.get(f"impres_{i}")
                    if _ir:
                        if _ir.get("ok"):
                            st.success(f"✅ 已下载并入库：{os.path.basename(_ir['path'])}"
                                       f"（{_ir.get('bytes', 0)//1024} KB）")
                            st.caption(f"抓取来源：{_ir.get('url', '')[:110]}")
                        else:
                            st.warning(f"⚠️ 原文没抓到：{_ir.get('reason', '未知原因')}")
                            with st.expander("查看尝试过的链接", expanded=False):
                                for _u, _why in _ir.get("tried", []):
                                    st.caption(f"· {_u[:100]} → {_why}")
                                if not _ir.get("tried"):
                                    st.caption("这个来源没有给出任何可用的开放获取链接。")
                            st.caption("付费墙后的论文（ACS / Science / Wiley 等）无法自动下载；"
                                       "可以点右边「查看原文」自己去下，再放进文献库文件夹。")

            if facets["years"]["items"]:
                with st.expander(f"筛选（基于已取回的 {facets['total']} 条）", expanded=False):
                    st.markdown("**年份**")
                    for g in facets["years"]["items"][:12]:
                        st.caption(f"· {g['value']} —— {g['count']} 条")
                    st.markdown("**期刊 / 会议**")
                    for g in facets["venues"]["items"][:12]:
                        st.caption(f"· {g['value']} —— {g['count']} 条")
                    st.markdown("**作者**")
                    for g in facets["authors"]["items"][:12]:
                        st.caption(f"· {g['value']} —— {g['count']} 条")
                    st.caption(f"开放获取：{facets['oa']['items'][0]['count']} 条")
