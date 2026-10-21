# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for 文献管理与图片检索系统
Build: pyinstaller paper_library.spec
Output: dist/PaperLibrary/PaperLibrary.exe
"""

import os
import sys
from pathlib import Path

# ── 路径 ──
ROOT = Path(os.path.dirname(os.path.abspath(__file__)))
STREAMLIT_DIR = None
FITZ_DIR = None

# 查找 streamlit 和 fitz 的安装位置
for p in sys.path:
    sp = os.path.join(p, 'streamlit')
    if os.path.isdir(sp):
        STREAMLIT_DIR = sp
        break

for p in sys.path:
    fp = os.path.join(p, 'fitz')
    if os.path.isdir(fp):
        FITZ_DIR = fp
        break

# ── 收集 Streamlit 静态文件 ──
streamlit_datas = []
if STREAMLIT_DIR:
    static_dir = os.path.join(STREAMLIT_DIR, 'static')
    if os.path.isdir(static_dir):
        # 将 streamlit/static -> streamlit/static
        streamlit_datas.append((static_dir, 'streamlit/static'))

    # streamlit 的前端 HTML
    for f in ['index.html', 'asset-manifest.json']:
        fp = os.path.join(STREAMLIT_DIR, 'static', f)
        if os.path.isfile(fp):
            streamlit_datas.append((fp, f'streamlit/static/{f}'))

# ── 收集 PyMuPDF 数据 ──
fitz_datas = []
if FITZ_DIR:
    fitz_datas.append((FITZ_DIR, 'fitz'))

# ── 项目 Python 文件 ──
project_files = [
    ('app.py', '.'),
    ('database.py', '.'),
    ('parser.py', '.'),
    ('ai_engine.py', '.'),
    ('requirements.txt', '.'),
]

# ── hidden imports ──
hidden_imports = [
    # Streamlit
    'streamlit', 'streamlit.runtime', 'streamlit.web', 'streamlit.commands',
    'streamlit.components.v1', 'streamlit.elements', 'streamlit.proto',
    'streamlit.web.server', 'streamlit.runtime.scriptrunner',
    'streamlit.runtime.state', 'streamlit.runtime.caching',
    'streamlit.watcher', 'streamlit.watcher.local_sources_watcher',
    'streamlit.web.bootstrap',
    # PDF
    'fitz', 'pdfplumber',
    # AI SDK
    'openai', 'anthropic',
    # Utils
    'tornado', 'watchdog', 'protobuf', 'pyarrow',
    'numpy', 'pandas', 'pillow', 'PIL',
    'altair', 'blinker', 'cachetools', 'click',
    'git', 'rich', 'tenacity', 'toml', 'tzlocal',
    'validators', 'importlib_metadata',
    # network
    'urllib3', 'certifi', 'idna', 'charset_normalizer', 'requests',
    # other
    'json', 'hashlib', 'subprocess', 'threading', 'socket',
]

# ── 排除的模块（减小体积） ──
excluded_modules = [
    'matplotlib', 'scipy', 'jedi', 'jupyter', 'ipython',
    'notebook', 'nbconvert', 'nbformat',
    'qtconsole', 'spyder', 'sphinx',
    'pytest', 'setuptools', 'wheel', 'pip',
    'tkinter.test', 'unittest', 'test',
]

a = Analysis(
    [str(ROOT / 'launcher_gui.py')],
    pathex=[str(ROOT)],
    binaries=[],
    datas=(streamlit_datas + fitz_datas + project_files),
    hiddenimports=hidden_imports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excluded_modules,
    noarchive=False,
    optimize=0,
)

# 过滤掉有问题的二进制文件
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='PaperLibrary',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,          # 用 UPX 压缩（如果可用）
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,     # 不显示控制台窗口
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ROOT / 'icon.ico') if os.path.exists(str(ROOT / 'icon.ico')) else None,
)
