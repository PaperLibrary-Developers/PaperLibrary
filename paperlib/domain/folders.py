"""用原文目录组织分类。papers.folder 是供文献查询使用的路径。"""
from __future__ import annotations

import os
import shutil
from collections import Counter, defaultdict
from contextlib import ExitStack

from paperlib.assets import asset_path, parent_path, unique_path, walk_assets

MAX_DEPTH = 8          # 再深就不好在侧栏里读了
MAX_NAME = 60

BAD_SEGMENTS = {"", ".", ".."}


def clean_name(name) -> str:
    """把用户输入的一个文件夹名洗干净；返回空串表示非法。"""
    text = str(name or "").replace("\u3000", " ").replace("/", " ").replace("\\", " ")
    text = " ".join(text.split())
    if text in BAD_SEGMENTS:
        return ""
    return text[:MAX_NAME]


def normalize_path(path) -> str:
    """把任意深度的路径归一化成 'A/B/C'（去掉空段、截断层级）。"""
    parts = [clean_name(part) for part in str(path or "").split("/")]
    parts = [part for part in parts if part]
    return "/".join(parts[:MAX_DEPTH])


def parent_of(path) -> str:
    path = str(path or "")
    return path.rsplit("/", 1)[0] if "/" in path else ""


def name_of(path) -> str:
    path = str(path or "")
    return path.rsplit("/", 1)[-1] if path else ""


class FolderError(ValueError):
    """业务校验失败（API 层转成 4xx，message 直接给用户看）。"""

    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class Folders:
    def __init__(self, store):
        self.db = store

    # ---------------- 读 ----------------
    def list(self):
        """返回扁平列表 + 直属/含子级计数，前端自己拼树。"""
        with self.db.paper_writes():
            paths = [path for path, _ in walk_assets(self.db.papers_dir) if path]
            papers = self.db.list_papers()
        direct = Counter(p.get("folder") or "" for p in papers)
        deep = direct.copy()
        for path in reversed(paths):
            deep[parent_of(path)] += deep[path]
        out = [{"path": path, "name": name_of(path), "parent": parent_of(path),
                "depth": path.count("/"), "count": direct[path], "countDeep": deep[path],
                "createdAt": ""} for path in paths]
        out.sort(key=lambda item: (item["parent"], item["name"].casefold()))
        return {"folders": out, "total": len(papers), "rootCount": direct[""]}

    def exists(self, path) -> bool:
        target = asset_path(self.db.papers_dir, path) if path else None
        return bool(target and target.is_dir())

    def sync_locations(self):
        """从已有 PDF 路径重建分类，保留指向真实目录的无原文卡片。"""
        with self.db.paper_writes():
            patches = {}
            actual = {"": ""}
            for paper in self.db.list_papers():
                path = (parent_path(paper["pdfFile"]) if paper.get("pdfFile") else paper.get("folder")) or ""
                if path not in actual:
                    actual[path] = path if self.exists(path) else ""
                patches[paper["id"]] = {"folder": actual[path]}
            return self.db.patch_papers(patches)

    # ---------------- 写 ----------------
    def ensure(self, path) -> str:
        """保证路径存在（逐级补建），返回归一化后的路径。导入时用它兜底。"""
        path = str(path or "").replace("\\", "/")
        if not path:
            return ""
        target = asset_path(self.db.papers_dir, path)
        if target is None or (path.split("/", 1)[0] == "未分类" and not target.is_dir()):
            raise FolderError("文件夹路径不合法")
        try:
            target.mkdir(parents=True, exist_ok=True)
            return target.resolve().relative_to(self.db.papers_dir.resolve()).as_posix()
        except (OSError, ValueError) as exc:
            raise FolderError("无法创建文件夹：" + str(exc)) from exc

    def create(self, path=None, parent=None, name=None):
        """新建文件夹：支持 {path} 或 {parent, name} 两种入参。已存在则原样返回（幂等）。"""
        if path is None:
            new_name = clean_name(name)
            if not new_name:
                raise FolderError("文件夹名不能为空（不能只用斜杠或空格）")
            normalized = (str(parent) + "/" + new_name) if parent else new_name
        else:
            normalized = str(path) if self.exists(path) else normalize_path(path)
        if not normalized:
            raise FolderError("文件夹名不能为空（不能只用斜杠或空格）")
        with self.db.paper_writes():
            existed = self.exists(normalized)
            normalized = self.ensure(normalized)
        return {
            "path": normalized,
            "name": name_of(normalized),
            "parent": parent_of(normalized),
            "created": not existed,
        }

    def rename(self, path, name):
        """重命名（同父级下改名）：整棵子树一起改。"""
        path = str(path or "")
        if not path:
            raise FolderError("根目录不能重命名")
        if not self.exists(path):
            raise FolderError("文件夹不存在或已被删除", 404)
        new_name = clean_name(name)
        if not new_name:
            raise FolderError("文件夹名不能为空")
        parent = parent_of(path)
        target = (parent + "/" + new_name) if parent else new_name
        if target == path:
            return {"path": path, "renamed": False, "moved": 0}
        old_dir = asset_path(self.db.papers_dir, path)
        new_dir = asset_path(self.db.papers_dir, target)
        if new_dir is None or target == "未分类":
            raise FolderError("文件夹路径不合法")
        with self.db.paper_writes(), ExitStack() as rollback:
            if os.path.lexists(new_dir) and os.path.normcase(path) != os.path.normcase(target):
                raise FolderError("同级下已经有同名文件夹「%s」" % new_name, 409)
            try:
                old_dir.rename(new_dir)
            except OSError as exc:
                raise FolderError("无法重命名文件夹：" + str(exc)) from exc
            rollback.callback(new_dir.rename, old_dir)
            patches = {}
            for paper in self.db.list_papers():
                file = paper.get("pdfFile") or ""
                folder = parent_path(file) if file else paper.get("folder") or ""
                if folder == path or folder.startswith(path + "/"):
                    patch = {"folder": target + folder[len(path):]}
                    if file:
                        patch["pdfFile"] = target + file[len(path):]
                    patches[paper["id"]] = patch
            moved = self.db.patch_papers(patches)
            rollback.pop_all()
        return {"path": target, "name": new_name, "parent": parent, "renamed": True, "moved": moved}

    def delete(self, path):
        """删除文件夹及其子文件夹；里面的文献**不删除**，统一上移到被删文件夹的父级。"""
        path = str(path or "")
        if not path:
            raise FolderError("根目录不能删除")
        if not self.exists(path):
            raise FolderError("文件夹不存在或已被删除", 404)
        dest = parent_of(path)
        root = self.db.papers_dir
        source = asset_path(root, path)
        destination = asset_path(root, dest)
        with self.db.paper_writes(), ExitStack() as rollback:
            tree = list(walk_assets(source))
            dirs = [source / folder for folder, _ in tree]
            files = [source / file for _, entries in tree for file in entries]
            known = set(dirs + files)
            if any(entry not in known for directory in dirs for entry in directory.iterdir()):
                raise FolderError("文件夹含有其他文件或链接，请先在资源管理器中整理")
            locations = {}
            try:
                for file in files:
                    target = unique_path(destination / file.name)
                    file.rename(target)
                    rollback.callback(target.rename, file)
                    locations[file.relative_to(root).as_posix()] = target.relative_to(root).as_posix()
                for directory in reversed(dirs):
                    directory.rmdir()
                    rollback.callback(directory.mkdir)
            except OSError as exc:
                raise FolderError("无法删除文件夹：" + str(exc)) from exc
            patches = {}
            for paper in self.db.list_papers():
                file = paper.get("pdfFile") or ""
                folder = parent_path(file) if file else paper.get("folder") or ""
                if folder == path or folder.startswith(path + "/"):
                    next_file = locations.get(file, file)
                    patches[paper["id"]] = {"folder": dest if not file or file in locations else "",
                                            "pdfFile": next_file}
            moved = self.db.patch_papers(patches)
            rollback.pop_all()
            removed = len(dirs)
        return {"ok": True, "removed": removed, "moved": moved, "to": dest}

    def move_papers(self, ids, path):
        """把一批文献移到指定文件夹（path 为空串 = 根目录），目标不存在会自动补建。"""
        with self.db.paper_writes(), ExitStack() as rollback:
            folder = self.ensure(path)
            root = self.db.papers_dir
            destination = asset_path(root, folder)
            selected = set(ids)
            references = defaultdict(list)
            patches = {}
            for paper in self.db.list_papers():
                file = paper.get("pdfFile") or ""
                if file:
                    references[file].append(paper["id"])
                elif paper["id"] in selected:
                    patches[paper["id"]] = {"folder": folder}

            for file, users in references.items():
                affected = selected.intersection(users)
                if not affected:
                    continue
                if parent_path(file) != folder:
                    source = asset_path(root, file)
                    if source is None or not source.is_file():
                        raise FolderError("原文文件不存在，请先重新扫描：" + file, 409)
                    target = unique_path(destination / source.name)
                    try:
                        if affected == set(users):
                            source.rename(target)
                            rollback.callback(target.rename, source)
                        else:
                            rollback.callback(target.unlink, missing_ok=True)
                            shutil.copyfile(source, target)
                    except OSError as exc:
                        raise FolderError("无法移动原文：" + str(exc)) from exc
                    file = target.relative_to(root).as_posix()
                for paper_id in affected:
                    patches[paper_id] = {"folder": folder, "pdfFile": file}
            self.db.patch_papers(patches)
            rollback.pop_all()
            return {"ok": True, "folder": folder, "moved": len(patches)}
