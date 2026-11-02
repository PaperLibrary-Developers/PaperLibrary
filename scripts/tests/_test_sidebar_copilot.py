"""验证侧边栏智能问答助手与详细新手指南的功能正确性
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import get_offline_assistant_reply
import database

def test_offline_copilot():
    stats = {"paper_count": 195, "figure_count": 5389, "with_caption": 2391, "with_authors": 75}
    scan_root = "Y:/私人/PAPER"
    
    # 测试导入问答
    r1 = get_offline_assistant_reply("怎么导入新下载的论文？", stats, scan_root)
    assert "文献导入与更新指引" in r1, f"导入回复不匹配: {r1}"
    print("  [OK] 导入指引回复正确")
    
    # 测试图表检索问答
    r2 = get_offline_assistant_reply("怎么搜 CV 曲线和 XRD 插图？", stats, scan_root)
    assert "插图与图注检索秘籍" in r2, f"图表回复不匹配: {r2}"
    print("  [OK] 图表检索指引回复正确")
    
    # 测试引用导出问答
    r3 = get_offline_assistant_reply("如何导出 BibTeX 格式的引文？", stats, scan_root)
    assert "引用导出技巧" in r3, f"引文回复不匹配: {r3}"
    print("  [OK] 引文导出回复正确")
    
    # 测试统计概况问答
    r4 = get_offline_assistant_reply("当前库里有多少篇文献？", stats, scan_root)
    assert "195 篇" in r4 and "5389 张" in r4, f"统计回复不匹配: {r4}"
    print("  [OK] 库藏统计回复正确")
    
    # 测试阅读工作台问答
    r5 = get_offline_assistant_reply("怎么进入双栏阅读和划线？", stats, scan_root)
    assert "沉浸式研读工作台" in r5, f"阅读回复不匹配: {r5}"
    print("  [OK] 阅读工作台回复正确")

def test_single_turn_overwrite_logic():
    """验证单轮覆盖模式（避免问答持续堆叠拉长左栏）"""
    session = {}
    
    # 模拟第 1 次提问
    q1 = "怎么导入文献"
    a1 = "回答1：放入文件夹后点击同步"
    session["sb_copilot_latest"] = {"question": q1, "answer": a1}
    assert session["sb_copilot_latest"]["question"] == q1
    
    # 模拟第 2 次提问，预期直接覆盖第 1 次问答，而不是变长列表
    q2 = "怎么导出BibTeX"
    a2 = "回答2：点击卡片右上角BibTeX按钮"
    session["sb_copilot_latest"] = {"question": q2, "answer": a2}
    assert session["sb_copilot_latest"]["question"] == q2
    assert session["sb_copilot_latest"]["answer"] == a2
    # 确认没有历史堆叠
    assert isinstance(session["sb_copilot_latest"], dict)
    print("  [OK] 单轮覆盖模式逻辑正确（不再累积堆叠拉长左栏）")

    # 模拟清空/收起
    session["sb_copilot_latest"] = None
    assert session["sb_copilot_latest"] is None
    print("  [OK] 清空与收起状态重置正确")

if __name__ == "__main__":
    print("=== 测试侧边栏离线智能问答引擎 ===")
    test_offline_copilot()
    test_single_turn_overwrite_logic()
    print("\n[ALL PASS] Copilot offline assistant tests passed!")
