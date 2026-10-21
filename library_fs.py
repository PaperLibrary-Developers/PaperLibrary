# -*- coding: utf-8 -*-
"""library_fs.py — 文献库的文件夹与文件操作

给 app.py 提供「新建 / 删除分类文件夹」「把某篇文献移动到别的分类」的能力。
磁盘是唯一事实来源：分类 == 文献库根目录下的（可嵌套）子文件夹，
folder 字段用相对根目录的路径表示（如 "羰基还原"、"MOF&COF CISS 纳米材料/手性螺旋聚合物"）。

设计要点：
  · 所有路径都先过 safe_join()，确保跑不出文献库根目录（防 .. 穿越）
  · 建文件夹时拒绝 Windows 非法字符与保留设备名，避免建出删不掉的目录
  · 删文件夹只删**空的**；非空一律拒绝（防止误删文献）
  · 移动文件：跨盘也能用（走 shutil.move）；目标已存在时拒绝，不覆盖
  · 移动后同步 papers.db 的 abs_path 与 folder
"""

import os
import re
import shutil

SKIP_DIRS = {"paper-library-app", "_scilib_ref", "data", "__pycache__", ".si-rename-backup"}

# Windows 文件名里不允许的字符
_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Windows 保留设备名（建出来会是删不掉的坑）
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class FolderError(Exception):
    """文件夹/移动操作的可预期错误（直接显示给用户，不需要堆栈）"""


def safe_join(root: str, rel: str) -> str:
    """把相对路径拼到 root 下，并保证结果仍在 root 内。

    rel 用 "/" 分隔（数据库里的 folder 就是这种写法，也兼容 Windows 的 "\\"）。
    """
    root_abs = os.path.abspath(root)
    rel = str(rel or "").replace("\\", "/").strip("/")
    target = os.path.abspath(os.path.join(root_abs, rel.replace("/", os.sep)))
    if target != root_abs and not target.startswith(root_abs + os.sep):
        raise FolderError("路径超出文献库范围")
    return target


def validate_name(name: str) -> str:
    """校验并规范化一个文件夹名（只允许单层，不允许带路径分隔符）"""
    n = str(name or "").strip()
    if not n:
        raise FolderError("名字不能为空")
    if len(n) > 60:
        raise FolderError("名字太长（最多 60 个字符）")
    if _ILLEGAL.search(n):
        raise FolderError('名字里不能包含 < > : " / \\ | ? * 这些字符')
    if n.endswith(".") or n.endswith(" "):
        raise FolderError("名字不能以点或空格结尾（Windows 不允许）")
    if n.upper() in _RESERVED:
        raise FolderError(f"「{n}」是 Windows 保留名，换一个")
    return n


def list_folders(root: str):
    """列出文献库根目录下现有的分类文件夹（单层），返回 [(名称, 绝对路径)]。

    跳过程序自身目录等 SKIP_DIRS。
    """
    out = []
    if not os.path.isdir(root):
        return out
    try:
        for entry in sorted(os.listdir(root), key=lambda s: s.lower()):
            p = os.path.join(root, entry)
            if os.path.isdir(p) and entry not in SKIP_DIRS and not entry.startswith("."):
                out.append((entry, p))
    except Exception:
        pass
    return out


def create_folder(root: str, name: str) -> str:
    """在文献库根目录下新建一个分类文件夹，返回绝对路径。"""
    n = validate_name(name)
    path = safe_join(root, n)
    if os.path.isdir(path):
        return path  # 已存在则直接返回
    if os.path.isfile(path):
        raise FolderError(f"「{n}」已被一个同名文件占用")
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        raise FolderError(f"新建失败：{e}")
    return path


def rename_folder(root: str, old_rel: str, new_name: str) -> str:
    """重命名一个分类文件夹。返回新的绝对路径。"""
    old_rel = str(old_rel or "").replace("\\", "/").strip("/")
    if not old_rel or old_rel == "根目录":
        raise FolderError("不能重命名文献库根目录")
    old_path = safe_join(root, old_rel)
    if not os.path.isdir(old_path):
        raise FolderError(f"原文件夹「{old_rel}」在电脑磁盘上不存在")

    new_n = validate_name(new_name)
    parent = os.path.dirname(old_path)
    new_path = os.path.join(parent, new_n)
    if os.path.exists(new_path) and os.path.abspath(old_path) != os.path.abspath(new_path):
        raise FolderError(f"已存在同名文件夹「{new_n}」")
    try:
        os.rename(old_path, new_path)
    except Exception as e:
        raise FolderError(f"电脑文件夹重命名失败：{e}")
    return new_path


def count_files(folder: str, exts=(".pdf",)) -> int:
    """统计文件夹（含子文件夹）里的文献文件数"""
    n = 0
    if not os.path.isdir(folder):
        return 0
    for dp, dn, fn in os.walk(folder):
        dn[:] = [d for d in dn if d not in SKIP_DIRS]
        n += sum(1 for f in fn if f.lower().endswith(exts))
    return n


def delete_folder(root: str, rel: str) -> str:
    """删除一个分类文件夹。只允许删空的；非空则拒绝并说明还剩几篇。"""
    rel = str(rel or "").replace("\\", "/").strip("/")
    if not rel:
        raise FolderError("不能删除文献库根目录")
    path = safe_join(root, rel)
    if not os.path.isdir(path):
        raise FolderError("这个文件夹不存在")
    if path == os.path.abspath(root):
        raise FolderError("不能删除文献库根目录")
    n = count_files(path)
    if n > 0:
        raise FolderError(f"「{rel}」里还有 {n} 篇文献，先把它们移到别处再删")
    # 空目录才删（含空子目录）
    try:
        shutil.rmtree(path)
    except Exception as e:
        raise FolderError(f"删除失败：{e}")
    return path


def move_pdf(root: str, abs_path: str, dest_rel: str):
    """把一篇文献的 PDF 移动到目标分类文件夹，返回新的绝对路径。

    dest_rel 为空字符串表示「根目录」。跨盘也能用。
    目标同名文件已存在时拒绝（不覆盖）。不碰数据库，由调用方同步。
    """
    src = os.path.abspath(abs_path)
    if not os.path.isfile(src):
        raise FolderError("原文文件不存在，可能已被移动或删除")

    src_abs = os.path.abspath(src)
    root_abs = os.path.abspath(root)
    if not src_abs.startswith(root_abs + os.sep):
        raise FolderError("这篇文献的原文不在文献库目录内，不敢移动")

    dest_dir = safe_join(root, dest_rel) if str(dest_rel or "").strip("/") else root_abs
    if not os.path.isdir(dest_dir):
        raise FolderError("目标文件夹不存在，请先新建")

    if os.path.dirname(src_abs) == os.path.abspath(dest_dir):
        raise FolderError("它已经在这个分类里了")

    dst = os.path.join(dest_dir, os.path.basename(src_abs))
    if os.path.exists(dst):
        raise FolderError(f"目标文件夹里已经有同名文件：{os.path.basename(src_abs)}")

    try:
        shutil.move(src_abs, dst)
    except Exception as e:
        raise FolderError(f"移动失败：{e}")
    return dst


def folder_of(root: str, abs_path: str) -> str:
    """由绝对路径反推它在库里的分类名（相对根目录，用 "/" 分隔；根目录返回 "根目录"）"""
    root_abs = os.path.abspath(root)
    d = os.path.dirname(os.path.abspath(abs_path))
    if os.path.abspath(d) == root_abs:
        return "根目录"
    try:
        rel = os.path.relpath(d, root_abs).replace(os.sep, "/")
    except Exception:
        return "根目录"
    return rel
