"""
database.py — SQLite 数据库操作
使用 FTS5 实现全文搜索，支持中英文混合检索
检索结果由 local_rank 做字段加权相关性排序（标题 ≫ 摘要 > 正文）
"""
import sqlite3
import os

import local_rank

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'papers.db')


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS papers (
            id TEXT PRIMARY KEY,
            filename TEXT NOT NULL,
            title TEXT DEFAULT '',
            authors TEXT DEFAULT '[]',
            doi TEXT DEFAULT '',
            year INTEGER,
            journal TEXT DEFAULT '',
            folder TEXT DEFAULT '',
            abs_path TEXT NOT NULL,
            full_text TEXT DEFAULT '',
            summary TEXT DEFAULT '',
            parsed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS figures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            paper_id TEXT NOT NULL,
            fig_index INTEGER NOT NULL,
            image_path TEXT DEFAULT '',
            caption TEXT DEFAULT '',
            context_text TEXT DEFAULT '',
            ai_description TEXT DEFAULT '',
            figure_type TEXT DEFAULT '',
            FOREIGN KEY (paper_id) REFERENCES papers(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS custom_categories (
            name TEXT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS papers_fts USING fts5(
            title, authors, full_text, summary,
            content='papers', content_rowid='rowid'
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS figures_fts USING fts5(
            caption, ai_description, figure_type, context_text,
            content='figures', content_rowid='rowid'
        );
        -- triggers
        CREATE TRIGGER IF NOT EXISTS p_ai AFTER INSERT ON papers BEGIN
            INSERT INTO papers_fts(rowid, title, authors, full_text, summary)
            VALUES (new.rowid, new.title, new.authors, new.full_text, new.summary);
        END;
        CREATE TRIGGER IF NOT EXISTS p_ad AFTER DELETE ON papers BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, title, authors, full_text, summary)
            VALUES ('delete', old.rowid, old.title, old.authors, old.full_text, old.summary);
        END;
        CREATE TRIGGER IF NOT EXISTS p_au AFTER UPDATE ON papers BEGIN
            INSERT INTO papers_fts(papers_fts, rowid, title, authors, full_text, summary)
            VALUES ('delete', old.rowid, old.title, old.authors, old.full_text, old.summary);
            INSERT INTO papers_fts(rowid, title, authors, full_text, summary)
            VALUES (new.rowid, new.title, new.authors, new.full_text, new.summary);
        END;
        CREATE TRIGGER IF NOT EXISTS f_ai AFTER INSERT ON figures BEGIN
            INSERT INTO figures_fts(rowid, caption, ai_description, figure_type, context_text)
            VALUES (new.rowid, new.caption, new.ai_description, new.figure_type, new.context_text);
        END;
        CREATE TRIGGER IF NOT EXISTS f_ad AFTER DELETE ON figures BEGIN
            INSERT INTO figures_fts(figures_fts, rowid, caption, ai_description, figure_type, context_text)
            VALUES ('delete', old.rowid, old.caption, old.ai_description, old.figure_type, old.context_text);
        END;
        CREATE TRIGGER IF NOT EXISTS f_au AFTER UPDATE ON figures BEGIN
            INSERT INTO figures_fts(figures_fts, rowid, caption, ai_description, figure_type, context_text)
            VALUES ('delete', old.rowid, old.caption, old.ai_description, old.figure_type, old.context_text);
            INSERT INTO figures_fts(rowid, caption, ai_description, figure_type, context_text)
            VALUES (new.rowid, new.caption, new.ai_description, new.figure_type, new.context_text);
        END;
    """)

    # 兼容性迁移：检查并自动为 papers 增加 journal 字段
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()]
        if 'journal' not in cols:
            conn.execute("ALTER TABLE papers ADD COLUMN journal TEXT DEFAULT ''")
    except Exception:
        pass

    # 兼容性迁移：确保 custom_categories 表存在
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_categories (
                name TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    except Exception:
        pass

    # 兼容性迁移：检查并自动为 figures 增加 ai_conclusion 字段
    try:
        f_cols = [r[1] for r in conn.execute("PRAGMA table_info(figures)").fetchall()]
        if 'ai_conclusion' not in f_cols:
            conn.execute("ALTER TABLE figures ADD COLUMN ai_conclusion TEXT DEFAULT ''")
    except Exception:
        pass

    # 兼容性迁移：深度分析 / arXiv 关联 / 摘要（新增列一律用 ALTER，老库直接升）
    try:
        p_cols = [r[1] for r in conn.execute("PRAGMA table_info(papers)").fetchall()]
        for col, ddl in (
            ('arxiv_id', "TEXT DEFAULT ''"),
            ('abstract', "TEXT DEFAULT ''"),
            ('deep_analysis', "TEXT DEFAULT ''"),
            ('deep_scores', "TEXT DEFAULT ''"),
            ('deep_at', "TEXT DEFAULT ''"),
        ):
            if col not in p_cols:
                conn.execute(f"ALTER TABLE papers ADD COLUMN {col} {ddl}")
    except Exception:
        pass

    conn.commit()
    conn.close()


def _ensure_custom_categories_table(conn):
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_categories (
                name TEXT PRIMARY KEY,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    except Exception:
        pass


# ── Papers CRUD ──

def upsert_paper(data: dict):
    conn = get_conn()
    d = dict(data)
    for k in ('authors', 'doi', 'journal', 'folder', 'full_text', 'summary', 'parsed_at'):
        d.setdefault(k, '')
    d.setdefault('year', None)
    conn.execute("""
        INSERT INTO papers (id, filename, title, authors, doi, year, journal, folder, abs_path, full_text, summary, parsed_at)
        VALUES (:id, :filename, :title, :authors, :doi, :year, :journal, :folder, :abs_path, :full_text, :summary, :parsed_at)
        ON CONFLICT(id) DO UPDATE SET
            filename=excluded.filename, title=excluded.title, authors=excluded.authors,
            doi=excluded.doi, year=excluded.year, journal=excluded.journal, folder=excluded.folder,
            abs_path=excluded.abs_path, full_text=excluded.full_text,
            summary=excluded.summary, parsed_at=excluded.parsed_at
    """, d)
    conn.commit()
    conn.close()


def get_all_papers(folder_filter=None):
    conn = get_conn()
    if folder_filter and folder_filter != '全部':
        rows = conn.execute(
            "SELECT id, filename, title, authors, doi, year, journal, folder, abs_path, summary, parsed_at FROM papers WHERE folder=? ORDER BY year DESC, title",
            (folder_filter,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, filename, title, authors, doi, year, journal, folder, abs_path, summary, parsed_at FROM papers ORDER BY year DESC, title"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_paper_by_id(pid: str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_paper_figures(pid: str):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM figures WHERE paper_id=? ORDER BY fig_index", (pid,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def delete_paper(pid: str, delete_source_file: bool = False):
    """从文献库中删除某篇文献。

    1. 从数据库中删除 figures 与 papers（自动触发 FTS5 虚拟索引与级联清理）
    2. 物理清理 data/figures 目录下已提取的图片缓存
    3. 若 delete_source_file=True，则物理删除本地原始 PDF 文件（带异常防护）
    """
    conn = get_conn()
    try:
        p = conn.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
        if not p:
            return False, "文献不存在或已被删除"

        pdf_path = p["abs_path"]

        # 获取关联提取图片路径列表
        figs = conn.execute("SELECT image_path FROM figures WHERE paper_id=?", (pid,)).fetchall()
        fig_paths = [f["image_path"] for f in figs if f["image_path"]]

        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM figures WHERE paper_id=?", (pid,))
        conn.execute("DELETE FROM papers WHERE id=?", (pid,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        return False, f"数据库删除失败: {e}"
    finally:
        conn.close()

    # 清理图片缓存
    for fp in fig_paths:
        try:
            if fp and os.path.exists(fp):
                os.remove(fp)
        except Exception:
            pass

    # 若指定同时删除源 PDF 文件
    if delete_source_file and pdf_path and os.path.exists(pdf_path):
        try:
            os.remove(pdf_path)
            return True, "已成功从文献库移除并删除了本地 PDF 文件"
        except Exception as e:
            return True, f"已从文献库移除，但删除本地源文件失败: {e}"

    return True, "已成功从文献库中移除该项"


def update_paper_folder(pid: str, new_folder: str):
    """更新某篇文献在网站中的分类名称（支持与电脑物理文件夹独立，实现虚拟分类）。"""
    conn = get_conn()
    try:
        conn.execute("UPDATE papers SET folder=? WHERE id=?", (new_folder or "根目录", pid))
        conn.commit()
        return True, "分类已成功更新"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def get_figures_map(paper_ids=None):
    """一次取回多篇文献的图片，返回 {paper_id: [figure, ...]}。

    性能：列表页原先对每篇文献各开一次数据库连接查 figures（140 篇实测 1011 ms），
    改成本函数一次查完（预计降到几十毫秒）。paper_ids 为空则取全部。
    """
    conn = get_conn()
    try:
        if paper_ids:
            ids = list(paper_ids)
            out = {pid: [] for pid in ids}
            # 分批 IN，避免 SQLite 变量数上限
            for i in range(0, len(ids), 500):
                chunk = ids[i:i + 500]
                ph = ",".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT * FROM figures WHERE paper_id IN ({ph}) ORDER BY paper_id, fig_index",
                    chunk).fetchall()
                for r in rows:
                    out.setdefault(r["paper_id"], []).append(dict(r))
            return out
        rows = conn.execute(
            "SELECT * FROM figures ORDER BY paper_id, fig_index").fetchall()
        out = {}
        for r in rows:
            out.setdefault(r["paper_id"], []).append(dict(r))
        return out
    finally:
        conn.close()


def add_custom_category(name: str):
    """在网站中登记一个新建分类（即使暂无文献也会在分类筛选中展示）。"""
    n = (name or "").strip()
    if not n or n == "根目录":
        return False, "分类名称无效"
    conn = get_conn()
    try:
        _ensure_custom_categories_table(conn)
        conn.execute("INSERT OR IGNORE INTO custom_categories (name) VALUES (?)", (n,))
        conn.commit()
        return True, f"已在网站中添加分类「{n}」"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def delete_custom_category(name: str, reassign_to: str = "根目录"):
    """删除网站中的分类。若该分类下有文献，则将其重新归类到 reassign_to。"""
    n = (name or "").strip()
    if not n or n == "根目录":
        return False, "不能删除根目录"
    conn = get_conn()
    try:
        _ensure_custom_categories_table(conn)
        conn.execute("DELETE FROM custom_categories WHERE name=?", (n,))
        conn.execute("UPDATE papers SET folder=? WHERE folder=?", (reassign_to or "根目录", n))
        conn.commit()
        return True, f"已删除分类「{n}」"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def rename_category_in_db(old_name: str, new_name: str,
                          old_abs_prefix: str = None, new_abs_prefix: str = None):
    """重命名网站分类。
    若传入 old_abs_prefix 和 new_abs_prefix（表示电脑磁盘也同步重命名了），则批量同步更新 papers.abs_path。
    """
    old_n = (old_name or "").strip()
    new_n = (new_name or "").strip()
    if not old_n or not new_n:
        return False, "名称不能为空"
    if old_n == new_n:
        return True, "名称未变更"
    if old_n == "根目录":
        return False, "不能重命名根目录"

    conn = get_conn()
    try:
        _ensure_custom_categories_table(conn)
        # 1. 更新 custom_categories
        conn.execute("UPDATE custom_categories SET name=? WHERE name=?", (new_n, old_n))
        conn.execute("INSERT OR IGNORE INTO custom_categories (name) VALUES (?)", (new_n,))
        
        # 2. 更新 papers.folder
        conn.execute("UPDATE papers SET folder=? WHERE folder=?", (new_n, old_n))

        # 3. 若电脑物理文件夹也同步重命名了，更新对应文献的 abs_path
        if old_abs_prefix and new_abs_prefix:
            rows = conn.execute("SELECT id, abs_path FROM papers WHERE folder=?", (new_n,)).fetchall()
            for r in rows:
                p_id = r["id"]
                orig_abs = r["abs_path"] or ""
                if orig_abs.startswith(old_abs_prefix):
                    updated_abs = new_abs_prefix + orig_abs[len(old_abs_prefix):]
                    conn.execute("UPDATE papers SET abs_path=? WHERE id=?", (updated_abs, p_id))

        conn.commit()
        return True, f"分类已从「{old_n}」更名为「{new_n}」"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def merge_category_in_db(from_name: str, to_name: str):
    """把 from_name 分类下的所有文献合并转移到 to_name 分类中。"""
    fn = (from_name or "").strip()
    tn = (to_name or "").strip()
    if not fn or not tn or fn == tn:
        return False, "源分类与目标分类无效或相同"
    conn = get_conn()
    try:
        _ensure_custom_categories_table(conn)
        conn.execute("UPDATE papers SET folder=? WHERE folder=?", (tn, fn))
        conn.execute("DELETE FROM custom_categories WHERE name=?", (fn,))
        conn.commit()
        return True, f"已将「{fn}」的全部文献合并至「{tn}」"
    except Exception as e:
        conn.rollback()
        return False, str(e)
    finally:
        conn.close()


def get_folders():
    """获取所有分类及其文献数量统计（融合 papers 实际分布与 custom_categories 注册的分类）。"""
    conn = get_conn()
    try:
        _ensure_custom_categories_table(conn)
        # 统计现有文献的 folder
        rows = conn.execute("SELECT folder, COUNT(*) as count FROM papers GROUP BY folder ORDER BY folder").fetchall()
        folder_dict = {}
        for r in rows:
            f_name = r["folder"] or "根目录"
            folder_dict[f_name] = folder_dict.get(f_name, 0) + r["count"]

        # 确保根目录始终在列
        if "根目录" not in folder_dict:
            folder_dict["根目录"] = 0

        # 合并自定义分类（可能 count=0）
        try:
            custom_rows = conn.execute("SELECT name FROM custom_categories").fetchall()
            for cr in custom_rows:
                c_name = cr["name"]
                if c_name and c_name not in folder_dict:
                    folder_dict[c_name] = 0
        except Exception:
            pass

        # 排序：根目录靠前，其余按字母排序
        sorted_names = sorted(folder_dict.keys(), key=lambda x: (0 if x == "根目录" else 1, x.lower()))
        return [{"folder": k, "count": folder_dict[k]} for k in sorted_names]
    finally:
        conn.close()


# ── Figures CRUD ──

def insert_figure(paper_id: str, fig: dict):
    conn = get_conn()
    conn.execute("""
        INSERT INTO figures (paper_id, fig_index, image_path, caption, context_text, ai_description, figure_type)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (paper_id, fig.get('fig_index', 0), fig.get('image_path', ''),
          fig.get('caption', ''), fig.get('context_text', ''),
          fig.get('ai_description', ''), fig.get('figure_type', '')))
    conn.commit()
    conn.close()


def clear_figures(paper_id: str):
    conn = get_conn()
    conn.execute("DELETE FROM figures WHERE paper_id=?", (paper_id,))
    conn.commit()
    conn.close()


# ── FTS5 Search ──

def _safe_fts(conn, table, query, where_clause='', where_params=(), limit=50):
    """安全的 FTS5 查询，失败时降级为 LIKE"""
    fts_q = query.replace('"', '""')
    try:
        sql = f"SELECT {table}_fts.rowid FROM {table}_fts WHERE {table}_fts MATCH ?"
        params = [f'"{fts_q}"']
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []  # 降级到 LIKE


def _readable_summary(raw) -> str:
    """summary 字段存的是 JSON（{"title":…,"summary":…}），取出人可读的那段。

    直接拿 JSON 原文当片段，会露出 `itle": "非手性CoNi-MOFs…` 这样的键名，很难看。
    """
    if not raw:
        return ""
    s = raw if isinstance(raw, str) else str(raw)
    st = s.lstrip()
    if st.startswith("{"):
        try:
            import json as _json
            obj = _json.loads(s)
            if isinstance(obj, dict):
                # 正文优先，其次中文标题，最后把值拼起来
                for k in ("summary", "摘要", "abstract", "content"):
                    v = obj.get(k)
                    if isinstance(v, str) and v.strip():
                        return v
                parts = [v for v in obj.values() if isinstance(v, str) and v.strip()]
                if parts:
                    return " ".join(parts)
        except Exception:
            pass
    return s


def _like_escape(s: str) -> str:
    """转义 LIKE 的通配符，避免查询里的 % _ 被当成模式"""
    return (str(s or "").replace("\\", "\\\\")
            .replace("%", "\\%").replace("_", "\\_"))


def search_papers(query: str, folder_filter=None, limit=50):
    """按相关性排序检索本地文献（Google 式）。

    为什么重写：原来只把整串查询当**短语**丢给 FTS5，命中极少且没有相关性排序。
    FTS5 的 unicode61 分词器不切分中日韩文字，所以「循环伏安」在索引里是一个整词，
    正文写成「循环伏安法」就匹配不上（实测整库只命中 1 条）。

    现在的流程：
      1. 分词：拉丁词 + 中文 bigram（循环伏安 → 循环/环伏/伏安）
      2. SQL 预筛：对词做 OR LIKE，缩小候选（实测扫 13 MB 全文仅 25–37 ms）
      3. Python 打分：标题 ≫ 摘要 > 正文，按相关性降序

    关键效果：**标题里没有搜索词、但正文相关的文献同样会展示**，只是排在更后面；
    每条结果带 relevance（0–100）与 match_reason（命中了哪些字段），排序依据透明可见。
    实测召回：「循环伏安」1 条 → 40 条；「spin selectivity」24 条 → 155 条。
    """
    terms = local_rank.tokenize(query)[0]
    if not terms:
        return []
    # 预筛必须用**展开后**的全部写法（含中英对照），
    # 否则英文的 "cyclic voltammetry" 根本进不了候选集，中文查询就永远搜不到英文正文
    forms = local_rank.all_forms(query)[:60]

    conn = get_conn()
    try:
        # ── 1) SQL 预筛：候选必须至少含一个写法（否则不可能得分）──
        conds, params = [], []
        for t in forms:
            esc = _like_escape(t)
            for f in ("title", "summary", "full_text"):
                conds.append(f"{f} LIKE ? ESCAPE '\\'")
                params.append(f"%{esc}%")
        sql = ("SELECT id, filename, title, authors, doi, year, journal, folder, abs_path, "
               "summary, full_text FROM papers WHERE (" + " OR ".join(conds) + ")")
        if folder_filter and folder_filter != "全部":
            sql += " AND folder=?"
            params.append(folder_filter)

        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    if not rows:
        return []

    # ── 2) 相关性打分与排序（标题 ≫ 摘要 > 正文）──
    ranked = local_rank.rank_items(rows, query)
    if not ranked:
        return []

    out = []
    for r in ranked[:limit]:
        # full_text 只在打分时需要；带出去会让每条结果占几 MB 内存，这里丢掉
        full = r.pop("full_text", "") or ""
        r.pop("_score", None)
        # 片段要取自**真正命中的那个字段**，否则会从正文开头截一段无关内容。
        # 摘要优先于正文：摘要短、信息密度高，更适合展示「为什么相关」。
        detail = r.get("match_detail") or {}
        if "summary" in detail and r.get("summary"):
            src = _readable_summary(r["summary"])
        elif "full_text" in detail and full:
            src = full
        elif "title" in detail and r.get("title"):
            src = r["title"]
        else:
            src = full or _readable_summary(r.get("summary")) or ""
        r["snippet"] = local_rank.make_snippet(src, terms)
        out.append(r)
    return out


def search_figures(query: str, folder_filter=None, limit=50):
    """按相关性排序检索图片（与 search_papers 同一套机制）。

    字段权重：图注 8 > AI 描述 6 > 图表类型 3 > 上下文 1。
    空查询时退回「按 fig_index 列出全部」——AI 总结流水线依赖这个行为
    （`search_figures("", limit=99999)` 取全部图片），不能因为空词就打分返回空。
    """
    terms, _phrase = local_rank.tokenize(query)
    if not terms:
        # 空查询（或纯标点）时退回「按 fig_index 列出全部」——AI 总结流水线依赖这个行为
        conn = get_conn()
        try:
            base_cols = """f.*, p.title AS paper_title, p.filename, p.folder AS paper_folder,
                           p.abs_path AS paper_abs_path"""
            sql = f"SELECT {base_cols} FROM figures f JOIN papers p ON p.id = f.paper_id"
            params = []
            if folder_filter and folder_filter != "全部":
                sql += " WHERE p.folder=?"
                params.append(folder_filter)
            sql += " ORDER BY f.fig_index"
            if limit:
                sql += f" LIMIT {int(limit)}"
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
        finally:
            conn.close()

    # 预筛用展开后的全部写法（含中英对照）：图片字段是英文的，
    # 中文查询必须靠对照表展开成 cyclic voltammetry 之类才能进候选集
    forms = local_rank.all_forms(query)[:60]

    conn = get_conn()
    try:
        base_cols = """f.*, p.title AS paper_title, p.filename, p.folder AS paper_folder,
                       p.abs_path AS paper_abs_path"""

        # ── 1) SQL 预筛：至少含一个写法 ──
        conds, params = [], []
        for t in forms:
            esc = _like_escape(t)
            for f in ("f.caption", "f.ai_description", "f.figure_type", "f.context_text"):
                conds.append(f"{f} LIKE ? ESCAPE '\\'")
                params.append(f"%{esc}%")
        sql = (f"SELECT {base_cols} FROM figures f JOIN papers p ON p.id = f.paper_id "
               f"WHERE (" + " OR ".join(conds) + ")")
        if folder_filter and folder_filter != "全部":
            sql += " AND p.folder=?"
            params.append(folder_filter)

        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()

    if not rows:
        return []

    # ── 2) 相关性打分（图注 > AI 描述 > 类型 > 上下文）──
    ranked = local_rank.rank_items(rows, query, field_map=local_rank.FIGURE_FIELDS,
                                   weights=local_rank.FIGURE_WEIGHTS,
                                   labels=local_rank.FIGURE_LABELS)
    if not ranked:
        return []

    out = []
    for r in ranked[:limit]:
        r.pop("_score", None)
        # 片段取自命中权重最高的那个字段（优先图注，其次 AI 描述）
        detail = r.get("match_detail") or {}
        src, src_field = "", ""
        for fname in ("caption", "ai_description", "context_text", "figure_type"):
            if fname in detail and r.get(fname):
                src, src_field = r[fname], fname
                break
        if src_field == "ai_description":
            # AI 描述存的是 JSON，取里面可读的那段
            src = _readable_summary(src)
        r["snippet"] = local_rank.make_snippet(src, terms)
        out.append(r)
    return out


def get_stats():
    conn = get_conn()
    pc = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    fc = conn.execute("SELECT COUNT(*) FROM figures").fetchone()[0]
    ws = conn.execute("SELECT COUNT(*) FROM papers WHERE summary != ''").fetchone()[0]
    wc = conn.execute("SELECT COUNT(*) FROM figures WHERE caption IS NOT NULL AND caption != ''").fetchone()[0]
    wa = conn.execute("SELECT COUNT(*) FROM figures WHERE ai_description != ''").fetchone()[0]
    wau = conn.execute("SELECT COUNT(*) FROM papers WHERE authors IS NOT NULL AND authors != '' AND authors != '[]'").fetchone()[0]
    conn.close()
    return {'paper_count': pc, 'figure_count': fc, 'with_summary': ws, 'with_caption': wc, 'with_ai_desc': wa, 'with_authors': wau}


def update_figure_conclusion(fig_id: int, conclusion: str):
    """更新并持久化图表的正文提炼结论"""
    conn = get_conn()
    conn.execute("UPDATE figures SET ai_conclusion=? WHERE id=?", (conclusion, fig_id))
    conn.commit()
    conn.close()


def get_figure_by_id(fig_id: int):
    """根据图表 ID 获取图表详情"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM figures WHERE id=?", (fig_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


# ── 深度分析 / arXiv 关联 ──

def update_paper_deep(pid: str, markdown: str, scores: dict = None,
                      arxiv_id: str = None, abstract: str = None):
    """写入深度分析报告。scores 存成 JSON 字符串，便于排序/筛选时不用再解析正文。

    刻意只更新传入的列：arxiv_id/abstract 为 None 时不覆盖已有值，
    因为自动识别出来的 arXiv 号不该被一次失败的重跑清空。
    """
    import json as _json
    import time as _time
    sets, params = ["deep_analysis=?", "deep_at=?"], [markdown or "",
                                                      _time.strftime("%Y-%m-%d %H:%M:%S")]
    if scores is not None:
        sets.append("deep_scores=?")
        params.append(_json.dumps(scores, ensure_ascii=False) if scores else "")
    if arxiv_id is not None:
        sets.append("arxiv_id=?")
        params.append(arxiv_id or "")
    if abstract is not None:
        sets.append("abstract=?")
        params.append(abstract or "")
    params.append(pid)
    conn = get_conn()
    conn.execute(f"UPDATE papers SET {', '.join(sets)} WHERE id=?", params)
    conn.commit()
    conn.close()


def get_paper_deep(pid: str):
    """取单篇的深度分析：{'markdown','scores'(dict),'at','arxiv_id','abstract'}"""
    import json as _json
    conn = get_conn()
    row = conn.execute(
        "SELECT deep_analysis, deep_scores, deep_at, arxiv_id, abstract "
        "FROM papers WHERE id=?", (pid,)).fetchone()
    conn.close()
    if not row:
        return {"markdown": "", "scores": {}, "at": "", "arxiv_id": "", "abstract": ""}
    scores = {}
    if row["deep_scores"]:
        try:
            scores = _json.loads(row["deep_scores"])
        except Exception:
            scores = {}
    return {"markdown": row["deep_analysis"] or "", "scores": scores,
            "at": row["deep_at"] or "", "arxiv_id": row["arxiv_id"] or "",
            "abstract": row["abstract"] or ""}


def get_deep_map():
    """{pid: {'markdown','scores','at','arxiv_id'}}，列表页一次取全，避免 N+1"""
    import json as _json
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, deep_analysis, deep_scores, deep_at, arxiv_id FROM papers "
        "WHERE deep_analysis != '' OR arxiv_id != ''").fetchall()
    conn.close()
    out = {}
    for r in rows:
        scores = {}
        if r["deep_scores"]:
            try:
                scores = _json.loads(r["deep_scores"])
            except Exception:
                scores = {}
        out[r["id"]] = {"markdown": r["deep_analysis"] or "", "scores": scores,
                        "at": r["deep_at"] or "", "arxiv_id": r["arxiv_id"] or ""}
    return out


def get_deep_stats():
    """深度分析覆盖率（给首页/设置页显示进度用）"""
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0]
    deep = conn.execute("SELECT COUNT(*) FROM papers WHERE deep_analysis != ''").fetchone()[0]
    arx = conn.execute("SELECT COUNT(*) FROM papers WHERE arxiv_id != ''").fetchone()[0]
    conn.close()
    return {"total": total, "deep": deep, "arxiv": arx}


if __name__ == '__main__':
    init_db()
    print('[OK] DB initialized:', DB_PATH)
