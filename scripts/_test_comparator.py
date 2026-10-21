# -*- coding: utf-8 -*-
"""
多图横向对比幕布模块单元测试
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import comparator

def test_comparator_lifecycle():
    fake_state = {}
    
    # 1. 初始应为空
    assert comparator.get_basket(fake_state) == []
    assert not comparator.is_in_basket(fake_state, 101)
    
    # 2. 加入第 1 张图
    item1 = {"id": 101, "caption": "Fig 1. CV curve of MoS2", "image_path": "a.png", "paper_title": "Paper 1"}
    ok, msg = comparator.toggle_basket(fake_state, item1)
    assert ok is True
    assert comparator.is_in_basket(fake_state, 101)
    assert len(comparator.get_basket(fake_state)) == 1
    
    # 3. 反选同一张图（自动移除）
    ok, msg = comparator.toggle_basket(fake_state, item1)
    assert ok is True
    assert not comparator.is_in_basket(fake_state, 101)
    assert len(comparator.get_basket(fake_state)) == 0
    
    # 4. 连续加入 4 张图
    for i in range(1, 5):
        item = {"id": i, "caption": f"Fig {i}", "image_path": f"{i}.png", "paper_title": f"Paper {i}"}
        ok, msg = comparator.toggle_basket(fake_state, item)
        assert ok is True
    
    assert len(comparator.get_basket(fake_state)) == 4
    
    # 5. 加入第 5 张图（超出上限，应友好拒绝）
    item5 = {"id": 5, "caption": "Fig 5", "image_path": "5.png"}
    ok, msg = comparator.toggle_basket(fake_state, item5)
    assert ok is False
    assert "最多并排比对 4 张" in msg
    assert len(comparator.get_basket(fake_state)) == 4
    
    # 6. 单张移除
    assert comparator.remove_from_basket(fake_state, 2) is True
    assert len(comparator.get_basket(fake_state)) == 3
    assert not comparator.is_in_basket(fake_state, 2)
    
    # 7. 一键清空
    comparator.clear_basket(fake_state)
    assert len(comparator.get_basket(fake_state)) == 0
    
    print("ALL COMPARATOR UNIT TESTS PASSED!")

if __name__ == "__main__":
    test_comparator_lifecycle()
