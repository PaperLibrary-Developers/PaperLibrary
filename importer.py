# -*- coding: utf-8 -*-
"""importer.py — 文献导入与分类管理核心模块

支持两种导入方式与灵活的分类策略：
1. 网页直接上传导入 (PDF 文件流 -> 保存到指定分类子目录 -> 即时解析入库)
2. 磁盘新文献同步导入向导 (批量扫描到的新文件 -> 按电脑子目录分类 OR 统一归入指定分类)
3. 遵循核心原则：大文件夹在网站与电脑中相同；各自分类方式可以不一样，但一般默认一样。
"""

import os
import time
import hashlib
import shutil
import database
import parser as paper_parser
import library_fs


def ensure_unique_filename(target_dir: str, filename: str) -> str:
    """确保目标文件夹中文件名唯一，若冲突则追加序号 _1, _2 等"""
    base, ext = os.path.splitext(filename)
    candidate = filename
    counter = 1
    while os.path.exists(os.path.join(target_dir, candidate)):
        candidate = f"{base}_{counter}{ext}"
        counter += 1
    return candidate


def ingest_uploaded_pdf(
    file_bytes: bytes,
    original_filename: str,
    scan_root: str,
    target_folder: str = "根目录",
    sync_to_disk_folder: bool = True
) -> tuple[bool, str]:
    """导入网页上传的 PDF 文件流。
    
    参数：
      file_bytes: PDF 字节流
      original_filename: 上传的文件名
      scan_root: 文献库总根目录 (电脑与网站的大文件夹)
      target_folder: 导入的目标分类 (如 '电催化' 或 '根目录')
      sync_to_disk_folder: 是否在电脑总文件夹中同步创建子目录并将文件存入其中 (默认 True，保持一致)
      
    返回：
      (success: bool, message: str)
    """
    if not file_bytes:
        return False, "上传文件内容为空"
        
    scan_root_abs = os.path.abspath(scan_root)
    if not os.path.isdir(scan_root_abs):
        return False, f"文献库根目录不存在: {scan_root}"

    # 规范化分类与保存目录
    target_folder = (target_folder or "根目录").strip().replace("\\", "/")
    if target_folder in ("", "根目录", "/"):
        rel_folder = ""
        effective_folder = "根目录"
    else:
        rel_folder = target_folder
        effective_folder = target_folder

    # 确定在电脑硬盘上的目标物理目录
    if sync_to_disk_folder and rel_folder:
        target_dir = library_fs.safe_join(scan_root_abs, rel_folder)
        os.makedirs(target_dir, exist_ok=True)
    else:
        # 如果不与电脑子目录同步，文件存入文献库根目录，但在网站打上该分类标签
        target_dir = scan_root_abs

    # 避免覆盖同名文件
    safe_filename = ensure_unique_filename(target_dir, original_filename)
    dest_path = os.path.join(target_dir, safe_filename)

    # 写入文件
    try:
        with open(dest_path, "wb") as f:
            f.write(file_bytes)
    except Exception as e:
        return False, f"写入本地文件失败: {e}"

    # 计算 sha256 检验排重
    h = hashlib.sha256()
    h.update(file_bytes)
    pid = h.hexdigest()

    conn = database.get_conn()
    exists = conn.execute("SELECT id FROM papers WHERE id=?", (pid,)).fetchone()
    conn.close()

    if exists:
        # 已经存在同内容文献：更新分类与路径
        database.update_paper_folder(pid, effective_folder)
        return True, f"该文献此前已入库，已自动更新分类为「{effective_folder}」"

    # 执行解析与入库
    try:
        pi = {
            "full_path": dest_path,
            "folder": effective_folder,
            "filename": safe_filename
        }
        paper, figs = paper_parser.parse_one(pi)
        paper["parsed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        database.upsert_paper(paper)
        database.clear_figures(paper["id"])
        for f in figs:
            database.insert_figure(paper["id"], f)
        return True, f"成功导入《{paper.get('title') or safe_filename}》，已归入「{effective_folder}」，提取 {len(figs)} 张图表！"
    except Exception as e:
        return False, f"文件已保存至磁盘，但解析入库遇到问题: {e}"


def get_existing_categories(scan_root: str) -> list[str]:
    """获取所有可用的分类列表（融合数据库已有分类 + 磁盘现有子目录），默认 '根目录' 在首位"""
    categories = {"根目录"}
    
    # 1. 来自数据库的分类
    try:
        for f in database.get_folders():
            folder_name = f.get("folder", "").strip()
            if folder_name and folder_name != "根目录":
                categories.add(folder_name)
    except Exception:
        pass

    # 2. 来自磁盘大文件夹下的实际子目录
    try:
        disk_dirs = library_fs.list_folders(scan_root)
        for name, _ in disk_dirs:
            if name and name != "根目录":
                categories.add(name)
    except Exception:
        pass

    sorted_cats = sorted(c for c in categories if c != "根目录")
    return ["根目录"] + sorted_cats
