"""文献库内的文件夹分类（类似资源管理器，**纯库内**，不与磁盘目录同步）。

为什么不复用磁盘目录：库里的分类是给「检索与整理」用的逻辑分组，文献的 PDF 仍然按
data/article/papers/ 平铺存放；两者解耦后，挪文件不会打乱分类，也不会有路径权限问题。

路径规则（对外统一用「字符串路径」）：
  · '/' 分隔层级，根目录是空串 ''（不入表）；
  · 每段去掉首尾空白、中间连续空白压成一个；'/'、'\\'、'.'、'..' 视为非法段；
  · 同名同级视为同一个文件夹：create 是幂等的，不会因为重复点击报错。

数据落点：
  · folders 表：path / name / parent / sort，用来表达「空的文件夹」也要存在；
  · papers.folder：文献当前所在路径（唯一真源，data JSON 里的旧值会被覆盖）。
"""
from __future__ import annotations

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
        rows = self.db.list_folders()
        direct = self.db.folder_counts()
        total_papers = sum(direct.values())

        children = {}
        for row in rows:
            children.setdefault(row["parent"], []).append(row["path"])

        def deep_count(path):
            total = direct.get(path, 0)
            for child in children.get(path, []):
                total += deep_count(child)
            return total

        out = []
        for row in rows:
            path = row["path"]
            out.append({
                "path": path,
                "name": row["name"],
                "parent": row["parent"],
                "depth": path.count("/"),
                "count": direct.get(path, 0),
                "countDeep": deep_count(path),
                "createdAt": row.get("createdAt") or "",
            })
        out.sort(key=lambda item: (item["parent"], item["name"].casefold()))
        return {"folders": out, "total": total_papers, "rootCount": direct.get("", 0)}

    def exists(self, path) -> bool:
        return bool(path) and self.db.get_folder(path) is not None

    # ---------------- 写 ----------------
    def ensure(self, path) -> str:
        """保证路径存在（逐级补建），返回归一化后的路径。导入时用它兜底。"""
        path = normalize_path(path)
        if not path:
            return ""
        parent = ""
        for part in path.split("/"):
            current = (parent + "/" + part) if parent else part
            if self.db.get_folder(current) is None:
                self.db.save_folder(current, part, parent)
            parent = current
        return path

    def create(self, path=None, parent=None, name=None):
        """新建文件夹：支持 {path} 或 {parent, name} 两种入参。已存在则原样返回（幂等）。"""
        if path is None:
            # 只给了 name：先校验它本身合法，否则「电催化/  」会被归一化成父目录，
            # 静默返回 200 让人以为建成功了
            if not clean_name(name):
                raise FolderError("文件夹名不能为空（不能只用斜杠或空格）")
            path = ((parent or "") + "/" + clean_name(name)).strip("/")
        normalized = normalize_path(path)
        if not normalized:
            raise FolderError("文件夹名不能为空（不能只用斜杠或空格）")
        existed = self.exists(normalized)
        self.ensure(normalized)
        return {
            "path": normalized,
            "name": name_of(normalized),
            "parent": parent_of(normalized),
            "created": not existed,
        }

    def rename(self, path, name):
        """重命名（同父级下改名）：整棵子树一起改。"""
        path = normalize_path(path)
        if not path:
            raise FolderError("根目录不能重命名")
        if not self.exists(path):
            raise FolderError("文件夹不存在或已被删除", 404)
        new_name = clean_name(name)
        if not new_name:
            raise FolderError("文件夹名不能为空")
        parent = parent_of(path)
        target = (parent + "/" + new_name) if parent else new_name
        target = normalize_path(target)
        if target == path:
            return {"path": path, "renamed": False, "moved": 0}
        if self.exists(target):
            raise FolderError("同级下已经有同名文件夹「%s」" % new_name, 409)
        moved = self.db.rename_folder_prefix(path, target)
        return {"path": target, "name": new_name, "parent": parent, "renamed": True, "moved": moved}

    def delete(self, path):
        """删除文件夹及其子文件夹；里面的文献**不删除**，统一上移到被删文件夹的父级。"""
        path = normalize_path(path)
        if not path:
            raise FolderError("根目录不能删除")
        if not self.exists(path):
            raise FolderError("文件夹不存在或已被删除", 404)
        dest = parent_of(path)
        moved = self.db.move_folder_papers(path, dest)
        removed = len([r for r in self.db.list_folders()
                       if r["path"] == path or r["path"].startswith(path + "/")])
        self.db.delete_folder_rows(path)
        return {"ok": True, "removed": removed, "moved": moved, "to": dest}

    def move_papers(self, ids, path):
        """把一批文献移到指定文件夹（path 为空串 = 根目录），目标不存在会自动补建。"""
        target = normalize_path(path)
        if target:
            self.ensure(target)
        count = self.db.set_paper_folders(ids, target)
        return {"ok": True, "folder": target, "moved": count}
