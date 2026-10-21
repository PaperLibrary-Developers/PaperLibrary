# -*- coding: utf-8 -*-
"""pdf_index.py — PDF 哈希缓存，避免每次刷新都重算全库 SHA256

为什么需要：侧边栏「🆕 待导入」要判断哪些 PDF 还没入库，判断依据是文件 SHA256。
但每次刷新都把全库 139 个 PDF（**771 MB**）重读一遍算哈希，实测 **525 ms**；
而查数据库只占 2.5 ms —— 时间全花在读文件上。

做法：把「路径 → (大小, 修改时间, 哈希)」缓存在 data/.pdf_index.json。
刷新时只 stat() 每个文件（139 个约 **5.5 ms**，快 95 倍）：
  · 大小与修改时间都没变 → 直接复用缓存里的哈希，不读文件内容
  · 变了（新增/替换/改动）→ 只对这一个文件重算
文件被删除时，它对应的缓存条目会被清掉，索引不会无限膨胀。
"""

import hashlib
import json
import os
import threading

_LOCK = threading.Lock()
CHUNK = 65536


def _index_path(app_dir: str) -> str:
    return os.path.join(app_dir, "data", ".pdf_index.json")


def load_index(app_dir: str) -> dict:
    try:
        with open(_index_path(app_dir), "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_index(app_dir: str, index: dict) -> None:
    """原子写入，避免中途失败把索引写坏"""
    path = _index_path(app_dir)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_pdfs(pdf_infos, app_dir: str, previous: dict = None):
    """给一批 PDF 计算 SHA256，尽量复用缓存。

    返回 (结果列表, 新索引, 统计)
      结果列表: [{'full_path','filename','folder','id','size','mtime','cached'}]
      统计: {'total','cached','hashed','failed'}
    """
    prev = previous if previous is not None else load_index(app_dir)
    new_index = {}
    out = []
    stats = {"total": 0, "cached": 0, "hashed": 0, "failed": 0}

    for pi in pdf_infos:
        p = pi["full_path"]
        stats["total"] += 1
        try:
            st = os.stat(p)
        except Exception:
            stats["failed"] += 1
            continue
        key = os.path.abspath(p)
        size, mtime = int(st.st_size), round(float(st.st_mtime), 3)
        ent = prev.get(key)
        if ent and ent.get("size") == size and ent.get("mtime") == mtime and ent.get("id"):
            digest, cached = ent["id"], True
            stats["cached"] += 1
        else:
            try:
                digest = sha256_file(p)
            except Exception:
                stats["failed"] += 1
                continue
            cached = False
            stats["hashed"] += 1
        new_index[key] = {"size": size, "mtime": mtime, "id": digest}
        out.append({"full_path": p, "filename": pi["filename"],
                    "folder": pi.get("folder", ""), "id": digest,
                    "size": size, "mtime": mtime, "cached": cached})

    return out, new_index, stats


def count_new(pdf_infos, app_dir: str, exists_fn, save: bool = True):
    """统计有多少 PDF 尚未入库。

    exists_fn(paper_id) -> bool：由调用方提供（查 papers 表），
    这样本模块不依赖 database，保持纯粹、便于测试。
    返回 (待导入数, 总数, 统计)
    """
    with _LOCK:
        entries, new_index, stats = hash_pdfs(pdf_infos, app_dir)
        new_count = 0
        for e in entries:
            try:
                if not exists_fn(e["id"]):
                    new_count += 1
            except Exception:
                new_count += 1
        if save:
            save_index(app_dir, new_index)
        return new_count, stats["total"], stats
