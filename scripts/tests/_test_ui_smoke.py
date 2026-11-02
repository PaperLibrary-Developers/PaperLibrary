# -*- coding: utf-8 -*-
"""用 Streamlit 官方 AppTest 真正跑一遍界面（不写入数据库）。

重点验证：
  1. 页面能无异常加载
  2. 中文查询「循环伏安」在图片检索页能出结果（跨语言命中）
  3. 相关性徽章 / match_reason 真的渲染出来了
"""
import os
import sys

APP = r"Y:\私人\PAPER\paper-library-app"
sys.path.insert(0, APP)
os.chdir(APP)

from streamlit.testing.v1 import AppTest  # noqa: E402

passed = failed = 0


def check(name, ok, extra=""):
    global passed, failed
    if ok:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name}  {extra}")


print("=== 1) 页面加载 ===")
at = AppTest.from_file(os.path.join(APP, "app.py"), default_timeout=180)
at.run()
check("无未捕获异常", not at.exception, [e.value for e in at.exception])
check("有两个页签", len(at.tabs) >= 2, len(at.tabs))
check("侧边栏有控件", len(at.sidebar) > 0, len(at.sidebar))

print("\n=== 2) 图片检索：中文查英文图注 ===")
at.text_input(key="fs").set_value("循环伏安").run()
check("仍无异常", not at.exception, [e.value for e in at.exception])
caps = [c.value for c in at.caption]
hit_line = next((c for c in caps if "找到" in c and "匹配图片" in c), "")
check("提示里报出命中张数", bool(hit_line), caps[:3])
print(f"        {hit_line}")
markdowns = " ".join(m.value for m in at.markdown)
check("渲染出相关性徽章", "相关性" in markdowns)
check("渲染出命中字段说明",
      any(k in markdowns for k in ("图注", "AI 描述", "图表类型", "上下文")))

print("\n=== 3) 文献检索：标题没词、正文有词也要展示 ===")
at.text_input(key="ps").set_value("循环伏安").run()
check("仍无异常", not at.exception, [e.value for e in at.exception])
check("页面上有相关性徽章", "相关性" in " ".join(m.value for m in at.markdown))
check("提示里报出拆词数量",
      any("检索词" in c.value for c in at.caption), [c.value for c in at.caption][:3])

print("\n=== 4) 无结果时不炸 ===")
at.text_input(key="fs").set_value("zzzz_不存在的词_qqqq").run()
check("空结果不抛异常", not at.exception, [e.value for e in at.exception])

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
