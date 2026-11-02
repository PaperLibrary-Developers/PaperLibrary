"""
scripts/_test_paper_chat.py — 端到端论文对话引擎与上下文装配验证
"""
import os
import sys

# 保证项目根目录在 sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database
import ai_engine


def test_paper_chat_context_assembly():
    print("=== 测试 1: 从数据库取真实文献并装配对话上下文 ===")
    papers = database.get_all_papers()
    assert len(papers) > 0, "数据库中无论文"
    
    # 找一篇有图表的论文
    target_p = None
    target_figs = []
    for p in papers:
        figs = database.get_paper_figures(p["id"])
        if figs and len(figs) >= 2:
            target_p = database.get_paper_by_id(p["id"])
            target_figs = figs
            break
            
    assert target_p is not None, "未找到含有图表的文献"
    print(f"选中测试论文: {target_p.get('title', '')[:50]} (ID: {target_p['id'][:8]})")
    print(f"论文提取图表数: {len(target_figs)}")
    
    # 测试上下文装配
    context = ai_engine.build_paper_context(target_p, target_figs)
    assert "Figure" in context, "上下文未挂载图表清单"
    assert "论文信息" in context, "上下文未挂载论文元数据"
    print(f"[OK] Context assembled, length: {len(context)} chars")
    
    # 测试 System Prompt 格式化
    sys_prompt = ai_engine.CHAT_SYSTEM_TEMPLATE.format(paper_context=context)
    assert "国际顶尖学术期刊资深审稿人" in sys_prompt
    assert target_p.get('title', '') in sys_prompt
    print("[OK] Chat System Prompt built successfully")
    print("All tests passed!")


if __name__ == "__main__":
    test_paper_chat_context_assembly()
