# -*- coding: utf-8 -*-
"""
scripts/_test_figure_anchor_integration.py
测试图文双向锚定引擎与 AI 提炼结论完整集成：
1. figure_anchor 核心算法校验
2. database 提炼结论更新与持久化校验
3. Copilot 离线助手关于有无配置 AI 区别的精准应答
"""

import sys
import os
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import figure_anchor
import app


def test_db_persistence():
    print("=== 1. 测试数据库结论持久化与读取 ===")
    conn = database.get_conn()
    row = conn.execute("SELECT id, caption, ai_conclusion FROM figures LIMIT 1").fetchone()
    conn.close()
    assert row, "No figures found in database!"
    fig_id = row["id"]
    orig_conc = row["ai_conclusion"]

    test_conc = "💡 【测试结论】：催化剂表现出优异的过电位，电流密度提升 2.5 倍。"
    database.update_figure_conclusion(fig_id, test_conc)
    
    updated = database.get_figure_by_id(fig_id)
    assert updated, "Failed to get figure by id!"
    assert updated["ai_conclusion"] == test_conc, f"Expected {test_conc}, got {updated['ai_conclusion']}"
    print(f"  [OK] 成功写入并读取图表 #{fig_id} 的持久化结论")

    # 还原
    database.update_figure_conclusion(fig_id, orig_conc)
    print("  [OK] 数据完美回滚还原\n")


def test_copilot_offline_reply():
    print("=== 2. 测试 Copilot 对有无配置 AI 区别的离线回答 ===")
    stats = database.get_stats()
    scan_root = app.get_scan_root()
    queries = [
        "有无配置AI有什么区别？",
        "没有配置AI能不能提炼结论？",
        "图文锚定是怎么工作的？",
    ]
    for q in queries:
        ans = app.get_offline_assistant_reply(q, stats, scan_root)
        assert "有无配置 AI 的区别" in ans or "离线模式" in ans or "秒级" in ans, f"Bad answer for query '{q}':\n{ans}"
        print(f"  [OK] 提问: {q}")
        print(f"       回答前 60 字: {ans[:60]}...\n")


def test_offline_conclusion_extractor():
    print("=== 3. 测试离线结论提炼规则引擎 ===")
    mock_mentions = [
        {"page": 4, "snippet": "As shown in Fig. 1b, the synthesized catalyst exhibits a significantly enhanced reaction rate compared with the bare sample, with an overpotential of 180 mV."},
        {"page": 5, "snippet": "The XRD spectrum in Figure 1c reveals the presence of crystalline planes."}
    ]
    res = figure_anchor.extract_offline_conclusion(mock_mentions, "Figure 1b. Polarization curves.")
    assert "正文核心论断" in res
    assert "significantly enhanced" in res
    print(f"  [OK] 离线结论成功提取高分机理句: {res[:80]}...\n")


if __name__ == "__main__":
    test_db_persistence()
    test_copilot_offline_reply()
    test_offline_conclusion_extractor()
    print("ALL INTEGRATION TESTS PASSED!")
