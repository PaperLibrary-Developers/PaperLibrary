"""
scripts/_test_delete_paper.py — 验证文献删除与级联清理功能
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import database


def test_delete_paper_flow():
    print("=== 测试: 文献删除功能与级联清理 ===")
    conn = database.get_conn()
    test_pid = "test_del_paper_123"
    test_pdf = os.path.abspath("test_dummy.pdf")
    test_fig = os.path.abspath("test_dummy_fig.png")
    
    # 创建临时测试文件
    with open(test_pdf, "w", encoding="utf-8") as f:
        f.write("dummy pdf")
    with open(test_fig, "w", encoding="utf-8") as f:
        f.write("dummy fig")
        
    try:
        # 1. 插入测试数据
        database.upsert_paper({
            "id": test_pid,
            "filename": "test_dummy.pdf",
            "title": "A Test Paper for Deletion",
            "abs_path": test_pdf,
            "full_text": "This is a full text for deletion test."
        })
        database.insert_figure(test_pid, {
            "fig_index": 0,
            "image_path": test_fig,
            "caption": "Figure 1. Test caption",
            "context_text": "context",
            "figure_type": "Figure"
        })
        
        # 验证写入成功
        p = database.get_paper_by_id(test_pid)
        assert p is not None, "测试论文未写入"
        figs = database.get_paper_figures(test_pid)
        assert len(figs) == 1, "测试图表未写入"
        print("[OK] Test paper and figure inserted")
        
        # 2. 测试仅移出库（保留本地 PDF 原文件）
        ok, msg = database.delete_paper(test_pid, delete_source_file=False)
        assert ok, f"删除失败: {msg}"
        print(f"[OK] delete_paper result: {msg}")
        
        # 3. 验证数据库中已彻底删除
        assert database.get_paper_by_id(test_pid) is None, "papers 表中仍残留数据"
        assert len(database.get_paper_figures(test_pid)) == 0, "figures 表中仍残留数据"
        
        # 4. 验证检索结果中不再包含已删除的 test_pid
        res = database.search_papers("A Test Paper for Deletion")
        assert not any(r["id"] == test_pid for r in res), "检索结果中仍然包含已删除的测试论文"
        print("[OK] Search result does not contain deleted paper")
        
        # 5. 验证图表图片已从磁盘删除，而 PDF 原文件保留
        assert not os.path.exists(test_fig), "图表缓存文件未被清理"
        assert os.path.exists(test_pdf), "原 PDF 文件被错误删除了"
        print("[OK] Figure cache cleaned, source PDF safely preserved")
        
        # 6. 测试物理删除 PDF 原文件
        database.upsert_paper({
            "id": test_pid,
            "filename": "test_dummy.pdf",
            "title": "A Test Paper for Deletion 2",
            "abs_path": test_pdf,
        })
        ok, msg = database.delete_paper(test_pid, delete_source_file=True)
        assert ok
        assert not os.path.exists(test_pdf), "delete_source_file=True 时未删除原文件"
        print("[OK] delete_source_file=True completely removed source PDF")
        
        print("All deletion tests passed!")
    finally:
        # 清理残留
        if os.path.exists(test_pdf):
            try:
                os.remove(test_pdf)
            except Exception:
                pass
        if os.path.exists(test_fig):
            try:
                os.remove(test_fig)
            except Exception:
                pass


if __name__ == "__main__":
    test_delete_paper_flow()
