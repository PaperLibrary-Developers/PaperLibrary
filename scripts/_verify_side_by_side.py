"""校验：文献详情页默认「PDF 原文视窗」与「论文专属 AI 问答顾问」左右并排。"""
from streamlit.testing.v1 import AppTest
import database

papers = database.get_all_papers(None)
print("PAPER_COUNT =", len(papers))
target = None
for p in papers:
    if p.get("abs_path"):
        target = p
        break
target = target or papers[0]
pid = target["id"]
print("TARGET_PID =", pid, "|", (target.get("title") or "")[:50])

at = AppTest.from_file(r"Y:\私人\PAPER\paper-library-app\app.py", default_timeout=900)
at.session_state["selected_paper_id"] = pid
at.run()

if at.exception:
    print("APP_EXCEPTION =", [str(e.value) for e in at.exception])
    raise SystemExit(2)

# 1) 版式单选控件：默认值必须落在「并排」选项上
lay = [r for r in at.radio if str(r.key or "").startswith("wb_layout2_")]
print("LAYOUT_RADIO_KEYS =", [r.key for r in lay])
if lay:
    print("LAYOUT_OPTIONS =", lay[0].options)
    print("LAYOUT_DEFAULT_VALUE =", lay[0].value)

# 2) 渲染树里的折叠框
print("EXPANDERS =", [e.label for e in at.expander])

# 3) 聊天的输入与快捷芯片是否真的渲染出来了
chat_in = [t for t in at.text_input if str(t.key or "").startswith("chat_in_")]
chips = [b for b in at.button if str(b.key or "").startswith("chip_")]
print("CHAT_INPUT_KEYS =", [t.key for t in chat_in])
print("CHIP_BUTTON_COUNT =", len(chips))
print("SIDEBAR_COUNT_UNCHANGED =", len([e for e in at.sidebar.expander]))
print("VERIFIED =", "PASS" if (lay and "并排" in str(lay[0].value) and chat_in) else "CHECK")
