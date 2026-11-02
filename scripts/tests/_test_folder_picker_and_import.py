# -*- coding: utf-8 -*-
"""scripts/_test_folder_picker_and_import.py — 验证 Windows 文件夹选择器与分类导入系统"""

import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import folder_picker
import importer
import database
import library_fs


def test_folder_picker():
    print("=== 1. 测试 folder_picker ===")
    drives = folder_picker.get_available_drives()
    assert len(drives) > 0, "应检测到至少一个盘符"
    print(f"  [OK] 检测到盘符: {drives}")

    test_path = os.path.abspath(".")
    crumbs = folder_picker.get_breadcrumbs(test_path)
    assert len(crumbs) >= 1, "面包屑应至少有 1 级"
    print(f"  [OK] 面包屑导航: {[c['name'] for c in crumbs]}")

    subdirs = folder_picker.list_subfolders(test_path)
    assert isinstance(subdirs, list), "子目录列表应为 list"
    print(f"  [OK] 当前目录检测到 {len(subdirs)} 个子文件夹")


def test_importer_and_categories():
    print("=== 2. 测试 importer 分类提取与导入逻辑 ===")
    cats = importer.get_existing_categories(os.path.abspath("."))
    assert "根目录" in cats, "分类中必须包含 '根目录'"
    print(f"  [OK] 成功汇总现有分类 ({len(cats)} 项): {cats[:5]}...")

    # 测试文件名排重
    with tempfile.TemporaryDirectory() as tmp_dir:
        f1 = os.path.join(tmp_dir, "sample.pdf")
        with open(f1, "w") as f:
            f.write("test")
        uniq = importer.ensure_unique_filename(tmp_dir, "sample.pdf")
        assert uniq == "sample_1.pdf", f"重名文件应命名为 sample_1.pdf，实际为: {uniq}"
        print(f"  [OK] 文件名防冲突排重正常: {uniq}")


def test_database_folder_update():
    print("=== 3. 测试 database.update_paper_folder 虚拟分类 ===")
    papers = database.get_all_papers()
    if papers:
        target_p = papers[0]
        orig_folder = target_p.get("folder") or "根目录"
        pid = target_p["id"]
        
        # 尝试更新分类
        ok, msg = database.update_paper_folder(pid, "测试实验分类")
        assert ok, f"更新分类应返回 True: {msg}"
        
        # 验证读取
        p_updated = database.get_paper_by_id(pid)
        assert p_updated["folder"] == "测试实验分类", "分类应已被更新为 '测试实验分类'"
        print("  [OK] 数据库分类更新成功（实现网站独立分类标签）")
        
        # 还原回原分类
        database.update_paper_folder(pid, orig_folder)
        p_reverted = database.get_paper_by_id(pid)
        assert p_reverted["folder"] == orig_folder, "还原分类成功"
        print("  [OK] 分类还原成功")
    else:
        print("  [SKIP] 暂无论文，跳过")


def test_copilot_folder_answers():
    print("=== 4. 测试 Copilot 对新功能的离线解答 ===")
    import app
    stats = database.get_stats()
    ans1 = app.get_offline_assistant_reply("像windows文件管理器一样选择文件夹", stats, "Y:\\私人\\PAPER")
    assert "弹出 Windows 文件管理器选择" in ans1, f"应包含 Windows 文件夹选择说明: {ans1}"
    print("  [OK] 文件夹选择问题回答准确")

    ans2 = app.get_offline_assistant_reply("导入文献的时候选择存入哪个文件夹", stats, "Y:\\私人\\PAPER")
    assert "导入时指定分类" in ans2 or "目标分类" in ans2, f"应包含导入时选择分类说明: {ans2}"
    print("  [OK] 导入文献选择分类问题回答准确")


if __name__ == "__main__":
    test_folder_picker()
    test_importer_and_categories()
    test_database_folder_update()
    test_copilot_folder_answers()
    print("\n[ALL PASS] 文件夹选择与分类导入系统全部测试通过！")
