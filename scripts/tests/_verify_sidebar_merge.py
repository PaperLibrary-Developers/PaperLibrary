"""临时校验：合并面板在真实 Streamlit 渲染树中确实作为单个侧边栏展开框出现。"""
import sys
import traceback

from streamlit.testing.v1 import AppTest

MERGED = "📥 导入新文献 (上传并选择分类)  ·  ⚙️ 设定文献库总大文件夹"

at = AppTest.from_file(r"Y:\私人\PAPER\paper-library-app\app.py", default_timeout=300)
at.run()

if at.exception:
    print("APP_EXCEPTION:", [str(e.value) for e in at.exception])
    sys.exit(2)


def labels_of(block):
    out = []
    try:
        for el in block.expander:
            out.append(getattr(el, "label", None) or getattr(el, "value", None))
    except Exception as exc:  # noqa: BLE001
        print("  (读取 expander 失败:", exc, ")")
    return out


side = labels_of(at.sidebar)
print("SIDEBAR_EXPANDERS =", side)
print("MERGED_PRESENT_IN_SIDEBAR =", MERGED in side)

# 侧边栏里不应再有独立的第二个导入框
dupes = [l for l in side if l and "导入新文献 (上传并选择分类)" in l]
print("IMPORT_EXPANDER_COUNT_IN_SIDEBAR =", len(dupes))

try:
    up = at.sidebar.file_uploader
    print("SIDEBAR_UPLOADER_KEYS =", [getattr(u, "key", None) for u in up])
    print("SIDEBAR_BUTTON_KEYS_IMPORT =", [getattr(b, "key", None) for b in at.sidebar.button if getattr(b, "key", None)])
except Exception as exc:  # noqa: BLE001
    print("  (读取控件失败:", exc, ")")

ok = MERGED in side and len(dupes) == 1
print("VERIFY_MERGED_PANEL =", "PASS" if ok else "CHECK")
