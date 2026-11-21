"""服务装配（对应 Node server/services.js）。

把存储访问器、业务对象、外部适配器组装成一份 services，由 app.py 创建一次、
路由层通过 ctx.services 注入给每个 handler：handler 用到的每个东西都在这里有名字。
"""
from __future__ import annotations

import shutil
import time
from functools import partial
from pathlib import Path
from types import SimpleNamespace

from paperlib import __version__
from paperlib.adapters import external, metadata
from paperlib.adapters.attachment_text import extract_attachment_text
from paperlib.adapters.llm import LLM
from paperlib.adapters.pdf import FullText
from paperlib.adapters.sources import Sources
from paperlib.adapters.websearch import WebSearch
from paperlib.domain.folders import Folders
from paperlib.domain.library import Library
from paperlib.update.service import UpdateService
from paperlib.util import ensure_dir, now_iso, safe_path_join

try:  # 纯函数移植由 T1 交付；先允许缺失，端点实现时会显式依赖
    from shared.pure import boolean_query as bool_query
except Exception:  # noqa: BLE001
    bool_query = None
try:
    from shared.pure.passages import pick_passages
except Exception:  # noqa: BLE001
    pick_passages = None


class Prefs:
    """data/prefs.json：用户可调项（与 Node prefs.js 同语义，带范围保护）。"""

    LIMITS = {
        "importMaxFiles": {"def": 20, "min": 1, "max": 200},
        "chatBodyChars": {"def": 14000, "min": 2000, "max": 60000},
        "chatAttachChars": {"def": 12000, "min": 2000, "max": 80000},
        "chatAttachTotalChars": {"def": 24000, "min": 2000, "max": 200000},
    }

    def __init__(self, store):
        self.store = store

    @property
    def file(self):
        return self.store.data_dir / "prefs.json"

    def read(self):
        return self.store.read_prefs()

    def merge(self, patch):
        return self.store.write_prefs(patch)

    def limit(self, key):
        spec = self.LIMITS.get(key)
        if not spec:
            return 0
        try:
            value = int(float((self.read() or {}).get(key)))
        except (TypeError, ValueError):
            return spec["def"]
        return min(spec["max"], max(spec["min"], int(round(value))))

    def import_max_files(self):
        return self.limit("importMaxFiles")


def create_services(config, store):
    started_at = getattr(config, "started_at", 0.0) or time.time()
    library = Library(store)
    folders = Folders(store)

    fulltext = FullText(store)
    try:
        fulltext.load_all(store.list_papers())
    except NotImplementedError:
        pass
    llm = LLM(config.data_dir)
    websearch = WebSearch(config.data_dir)
    sources = Sources(config.data_dir)
    prefs = Prefs(store)
    external.configure(sources)
    metadata.configure(sources)

    # 布尔检索在 scope=fulltext 时要能看到正文；调用前设置预算，查询后自然归零
    budget = {"value": 0}

    def fulltext_text_for(paper_id):
        if budget["value"] <= 0:
            return ""
        budget["value"] -= 1
        try:
            return fulltext.read(paper_id, 60000) or ""
        except Exception:  # noqa: BLE001
            return ""

    library.fulltext_text_for = fulltext_text_for

    def extract_paper_fulltext(paper):
        if not paper or not paper.get("pdfFile"):
            return {"ok": False, "error": "这条文献没有关联 PDF"}
        abs_path = store.path_for_paper_file(paper.get("pdfFile"))
        if not abs_path or not Path(abs_path).is_file():
            return {"ok": False, "error": "PDF 文件不存在：" + str(paper.get("pdfFile"))}
        try:
            result = fulltext.extract_from_file(abs_path)
            text = str(result.get("text") or "")
            if len(text.strip()) < 20:
                return {"ok": False, "error": "这个 PDF 里没有可提取的文字（可能是扫描件 / 图片版），需要先做 OCR",
                        "code": "NO_TEXT"}
            saved = fulltext.save(paper.get("id"), text)
            return {"ok": True, "pages": result.get("pages"), "chars": saved["chars"],
                    "preview": text[:400]}
        except NotImplementedError:
            raise
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": "提取失败：" + str(exc)}

    def trigger_auto_summary_zh(paper_id):
        # 自动中文总结改为同步调用领域函数（避免 HTTP 自请求），由 T4 实现
        try:
            from paperlib.domain import ai as ai_domain

            paper = library.get(paper_id)
            if not paper or str(paper.get("summaryZh") or "").strip():
                return None
            return ai_domain.summary_zh(services, paper, {"auto": True})
        except NotImplementedError:
            return None
        except Exception as exc:  # noqa: BLE001
            print("[summary-zh] 自动生成异常：" + str(exc))
            return None

    runtime = SimpleNamespace(
        root=config.root,
        data_dir=config.data_dir,
        port=config.port,
        host=config.host,
        app=config.app,
        version=__version__,
        started_at=started_at,
        timing={"bootMs": None},
        on_shutdown=lambda: None,
        config=config,
    )

    services = SimpleNamespace(
        db=store,
        library=library,
        folders=folders,
        prefs=prefs,
        fulltext=fulltext,
        llm=llm,
        websearch=websearch,
        sources=sources,
        external=external,
        metadata=metadata,
        bool_query=bool_query,
        pick_passages=pick_passages,
        runtime=runtime,
        extract_paper_fulltext=extract_paper_fulltext,
        extract_attachment_text=partial(extract_attachment_text, extract_pdf=fulltext.extract_from_file),
        trigger_auto_summary_zh=trigger_auto_summary_zh,
        fulltext_budget=budget,
        update=UpdateService(config.root, config.data_dir),
    )
    return services
