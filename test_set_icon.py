# -*- coding: utf-8 -*-
"""验证 set_icon.py 的核心逻辑（只读快捷方式 + 试转图标，不真正替换）"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image
import set_icon

out = []

# 1. 能否正确读出现有快捷方式的目标（保证换图标不会破坏启动）
target, workdir, desc = set_icon.read_shortcut()
out.append("[1] 读取现有快捷方式")
out.append(f"    target  = {target}")
out.append(f"    workdir = {workdir}")
out.append(f"    desc    = {desc}")
out.append(f"    目标文件存在: {os.path.exists(target)}")

# 2. 非正方形图片能否补成正方形（不变形）
img = Image.new("RGBA", (760, 2054), (40, 90, 200, 255))
sq = set_icon.to_square(img)
out.append("")
out.append("[2] 补正方形")
out.append(f"    原尺寸 {img.size} -> {sq.size}  (应为正方形: {sq.size[0] == sq.size[1]})")

# 3. 多尺寸 ICO 生成（写到临时文件，不动真图标）
tmp_ico = os.path.join(os.environ["TEMP"], "_test_icon.ico")
frames = [sq.resize((s, s), Image.LANCZOS) for s in set_icon.SIZES]
frames[0].save(tmp_ico, format="ICO", sizes=[(s, s) for s in set_icon.SIZES],
               append_images=frames[1:])
out.append("")
out.append("[3] 生成多尺寸 ICO")
out.append(f"    文件: {tmp_ico}  ({os.path.getsize(tmp_ico)} bytes)")
ico = Image.open(tmp_ico)
out.append(f"    主尺寸: {ico.size}")

# 4. 各尺寸是否都写进去了
sizes_in = []
for s in set_icon.SIZES:
    try:
        ico.size = (s, s)
        ico.load()
        sizes_in.append(s)
    except Exception:
        pass
out.append(f"    包含尺寸: {sizes_in}")

with open(os.path.join(os.environ["TEMP"], "set_icon_test.txt"), "w",
          encoding="utf-8") as f:
    f.write("\n".join(out))
print("done")
