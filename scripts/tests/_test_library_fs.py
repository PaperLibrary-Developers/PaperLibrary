# -*- coding: utf-8 -*-
# library_fs 的测试：在临时目录里模拟文献库，覆盖正常路径与各类错误分支。
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import library_fs as L

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


def expect_error(name, fn, *a, **kw):
    """期望抛出 FolderError"""
    global passed, failed
    try:
        fn(*a, **kw)
        failed += 1
        print(f"  [FAIL] {name} —— 本该报错却成功了")
    except L.FolderError as e:
        passed += 1
        print(f"  [OK]   {name}  -> {e}")
    except Exception as e:
        failed += 1
        print(f"  [FAIL] {name} —— 抛了非预期异常 {type(e).__name__}: {e}")


root = tempfile.mkdtemp(prefix="lvtest_")
try:
    print("测试根目录:", root)

    # 预置：一个已存在的分类
    os.makedirs(os.path.join(root, "羰基还原"))
    with open(os.path.join(root, "羰基还原", "a.pdf"), "wb") as f:
        f.write(b"%PDF-1.4 test")

    print("\n=== 1) 新建文件夹 ===")
    p = L.create_folder(root, "电催化")
    check("建目录成功", os.path.isdir(p))
    check("列表包含新目录", "电催化" in [n for n, _ in L.list_folders(root)])
    expect_error("空名字被拒", L.create_folder, root, "   ")
    expect_error("重复创建被拒", L.create_folder, root, "电催化")
    expect_error("非法字符被拒", L.create_folder, root, "a/b")
    expect_error("非法字符2被拒", L.create_folder, root, "a:b")
    expect_error("保留名被拒", L.create_folder, root, "CON")
    expect_error("点结尾被拒", L.create_folder, root, "abc.")
    expect_error("超长被拒", L.create_folder, root, "x" * 61)
    check("列表中不出现程序目录", "paper-library-app" not in [n for n, _ in L.list_folders(root)])

    print("\n=== 2) 删除文件夹 ===")
    L.create_folder(root, "空的")
    check("空目录可删", os.path.isdir(L.delete_folder(root, "空的")) is False)
    expect_error("非空目录被拒", L.delete_folder, root, "羰基还原")
    expect_error("不存在的被拒", L.delete_folder, root, "查无此夹")
    expect_error("根目录被拒", L.delete_folder, root, "")
    expect_error("穿越被拒", L.delete_folder, root, "../")

    print("\n=== 3) 移动 PDF ===")
    src = os.path.join(root, "羰基还原", "a.pdf")
    dst = L.move_pdf(root, src, "电催化")
    check("文件已移到新分类", os.path.isfile(dst) and not os.path.exists(src))
    check("落点文件名不变", os.path.basename(dst) == "a.pdf")
    check("folder_of 反推正确", L.folder_of(root, dst) == "电催化")

    dst2 = L.move_pdf(root, dst, "")          # 移回根目录
    check("可移回根目录", os.path.dirname(dst2) == os.path.abspath(root))
    check("根目录 folder_of = 根目录", L.folder_of(root, dst2) == "根目录")

    expect_error("重复移到当前分类被拒", L.move_pdf, root, dst2, "")
    expect_error("目标不存在被拒", L.move_pdf, root, dst2, "查无此夹")
    expect_error("源文件不存在被拒", L.move_pdf, root, os.path.join(root, "nope.pdf"), "电催化")

    # 同名冲突
    os.makedirs(os.path.join(root, "冲突"), exist_ok=True)
    with open(os.path.join(root, "冲突", os.path.basename(dst2)), "wb") as f:
        f.write(b"other")
    expect_error("同名文件冲突被拒", L.move_pdf, root, dst2, "冲突")

    # 库外文件不许动
    outside = os.path.join(tempfile.gettempdir(), "_lvtest_outside.pdf")
    with open(outside, "wb") as f:
        f.write(b"%PDF")
    expect_error("库外文件被拒", L.move_pdf, root, outside, "电催化")
    os.remove(outside)

    print("\n=== 4) 路径安全 ===")
    check("safe_join 正常", L.safe_join(root, "a/b").startswith(os.path.abspath(root)))
    expect_error("safe_join 拦穿越", L.safe_join, root, "../../etc")
    expect_error("safe_join 拦绝对路径", L.safe_join, root, "C:/Windows")

    print("\n=== 5) 计数 ===")
    check("空目录计数 0", L.count_files(os.path.join(root, "电催化")) == 0)
    with open(os.path.join(root, "电催化", "b.pdf"), "wb") as f:
        f.write(b"%PDF")
    check("有文件计数 1", L.count_files(os.path.join(root, "电催化")) == 1)

finally:
    shutil.rmtree(root, ignore_errors=True)
    print("\n临时目录已清理")

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
