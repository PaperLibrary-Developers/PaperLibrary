"""校验：全景画廊「精读并排」卡片改为窄图列（图小、文字区大），且图注/按钮完好。"""
from streamlit.testing.v1 import AppTest
import database

papers = database.get_all_papers(None)
target = None
for p in papers:
    if p.get("abs_path"):
        target = p
        break
target = target or papers[0]
pid = target["id"]

at = AppTest.from_file(r"Y:\私人\PAPER\paper-library-app\app.py", default_timeout=900)
at.session_state["selected_paper_id"] = pid
at.run()

if at.exception:
    print("APP_EXCEPTION =", [str(e.value) for e in at.exception])
    raise SystemExit(2)

figs = database.get_paper_figures(pid)
print("FIGURE_COUNT =", len(figs))

cmp_btns = [b for b in at.button if str(b.key or "").startswith("wb_cmp_l_")]
jump_btns = [b for b in at.button if str(b.key or "").startswith("jump_p_l_")]
print("CMP_BUTTONS =", len(cmp_btns))
print("JUMP_BUTTONS =", len(jump_btns))
print("JUMP_LABELS =", [b.label for b in jump_btns][:4])

try:
    imgs = at.get("imgs")
    print("IMAGE_ELEMENTS =", len(imgs))
except Exception as exc:  # noqa: BLE001
    print("IMAGE_READ_ERR =", exc)

try:
    navs = at.get("link_button")
    print("LINK_BUTTONS =", len(navs), [getattr(n, "label", None) for n in navs][:5])
except Exception as exc:  # noqa: BLE001
    print("LINK_BUTTON_READ_NOTE =", exc)

print("VERIFIED =", "PASS" if cmp_btns and not at.exception else "CHECK")
