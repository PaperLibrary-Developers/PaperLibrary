"""Unit tests for category creation, renaming, merging, and deletion both in DB and on disk."""
import os
import sys
import shutil
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import database
import library_fs


def test_category_management():
    # 1. Test database custom categories
    test_cat = "测试分类_Alpha"
    ok, msg = database.add_custom_category(test_cat)
    assert ok or "已存在" in msg, f"Add category failed: {msg}"

    # Verify custom category shows in get_folders
    folders = database.get_folders()
    folder_names = [f["folder"] for f in folders]
    assert test_cat in folder_names, f"{test_cat} not found in folders: {folder_names}"

    # 2. Test renaming category in DB
    new_cat = "测试分类_Beta"
    ok, msg = database.rename_category_in_db(test_cat, new_cat)
    assert ok, f"Rename category in DB failed: {msg}"
    folders = database.get_folders()
    folder_names = [f["folder"] for f in folders]
    assert new_cat in folder_names, f"{new_cat} not found after rename"
    assert test_cat not in folder_names, f"{test_cat} still present after rename"

    # 3. Test merging category in DB
    target_merge_cat = "测试分类_Gamma"
    database.add_custom_category(target_merge_cat)
    ok, msg = database.merge_category_in_db(new_cat, target_merge_cat)
    assert ok, f"Merge category failed: {msg}"
    folders = database.get_folders()
    folder_names = [f["folder"] for f in folders]
    assert new_cat not in folder_names, f"{new_cat} should be removed after merge"
    assert target_merge_cat in folder_names, f"{target_merge_cat} should exist"

    # 4. Test deleting category in DB
    ok, msg = database.delete_custom_category(target_merge_cat)
    assert ok, f"Delete category failed: {msg}"
    folders = database.get_folders()
    folder_names = [f["folder"] for f in folders]
    assert target_merge_cat not in folder_names, f"{target_merge_cat} should be deleted"

    # 5. Test library_fs physical folder ops
    with tempfile.TemporaryDirectory() as tmpdir:
        sub1 = library_fs.create_folder(tmpdir, "MySubDir")
        assert os.path.isdir(sub1), "create_folder failed to create directory"

        renamed = library_fs.rename_folder(tmpdir, "MySubDir", "MyRenamedSubDir")
        assert os.path.isdir(renamed), "rename_folder failed"
        assert not os.path.exists(sub1), "old directory should not exist"

        del_ok = library_fs.delete_folder(tmpdir, "MyRenamedSubDir")
        assert del_ok and not os.path.exists(renamed), "delete_folder failed"

    print("ALL CATEGORY MANAGEMENT TESTS PASSED!")


if __name__ == "__main__":
    test_category_management()
