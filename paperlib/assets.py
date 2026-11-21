"""原文目录的相对路径与文件枚举。"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path


def is_link(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def asset_path(root, relative):
    """只接受原文根目录内的普通路径，跳过链接和 Windows 目录联接。"""
    target = Path(root)
    try:
        if is_link(target.lstat()):
            return None
    except FileNotFoundError:
        pass
    except OSError:
        return None
    relative = str(relative or "").replace("\\", "/")
    if not relative:
        return target
    for name in relative.split("/"):
        if name in ("", ".", "..") or ":" in name or "\0" in name:
            return None
        target /= name
        try:
            if is_link(target.lstat()):
                return None
        except FileNotFoundError:
            continue
        except OSError:
            return None
    return target


def walk_assets(root):
    """以相对路径返回普通子目录和 PDF，空目录也保留。"""
    root = Path(root)
    if not _ordinary(root, stat.S_ISDIR):
        return
    for current, dirs, files in os.walk(root):
        base = Path(current)
        dirs[:] = [name for name in sorted(dirs) if _ordinary(base / name, stat.S_ISDIR)]
        relative = base.relative_to(root)
        yield ("" if base == root else relative.as_posix(), [
            (relative / name).as_posix() for name in sorted(files)
            if name.lower().endswith(".pdf") and _ordinary(base / name, stat.S_ISREG)
        ])


def _ordinary(path, check):
    try:
        info = path.lstat()
    except OSError:
        return False
    return check(info.st_mode) and not is_link(info)


def parent_path(relative):
    return str(relative or "").replace("\\", "/").rpartition("/")[0]


def unique_path(path):
    candidate = path
    number = 2
    while os.path.lexists(candidate):
        candidate = path.with_name(f"{path.stem} ({number}){path.suffix}")
        number += 1
    return candidate


def sha256_file(path):
    try:
        with open(path, "rb") as source:
            return hashlib.file_digest(source, "sha256").hexdigest()
    except OSError:
        return ""
