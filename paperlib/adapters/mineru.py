"""MinerU 版面解析服务（云端 Standard API，v4）。

配置存 data/mineru.json；token 用 DPAPI 加密，前端只看到 keyHint（不回显明文）。

解析流程（与官方文档一致）：
    POST /file-urls/batch                    申请上传地址（返回 batch_id + file_urls）
    PUT  file_urls[0]                        上传 PDF 字节
    GET  /extract-results/batch/{batch_id}   轮询（state: pending / running / done / failed）
    GET  full_zip_url                        下载 zip（layout.json / content_list.json / full.md）

免费额度约 1000 页/天（以 mineru.net 官网为准）；PDF 会上传到官方服务，
因此默认只在用户显式配置 token 后才启用，失败时由调用方降级到本地 PyMuPDF。
"""
from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from paperlib.adapters.secret_store import dump_with_protected_secret, load_secret_field
from paperlib.util import read_json, write_json_atomic

BASE = "https://mineru.net/api/v4"
POLL_INTERVAL = 3.0
HTTP_TIMEOUT = 60

DEFAULTS = {
    "enabled": True,
    "apiKey": "",
    "modelVersion": "pipeline",
    "language": "en",
    "isOcr": False,
    "enableTable": True,
}


class MinerUError(RuntimeError):
    """调用 MinerU 失败（网络 / 配额 / 业务码）。"""


class MinerU:
    def __init__(self, data_dir):
        self.dir = Path(data_dir)
        self.file = self.dir / "mineru.json"
        self.settings = dict(DEFAULTS)
        self.load()

    # ---------------- 配置 ----------------

    def load(self):
        raw = read_json(self.file, {})
        self.settings = dict(DEFAULTS)
        if isinstance(raw, dict):
            self.settings.update(raw)
        if load_secret_field(self.settings, "apiKey") and self.file.is_file():
            try:
                self.save()
            except OSError:
                pass
        return self.settings

    def save(self):
        write_json_atomic(self.file, dump_with_protected_secret(self.settings, "apiKey"))

    def update(self, patch):
        patch = patch or {}
        for key in ("enabled", "isOcr", "enableTable"):
            if patch.get(key) is not None:
                self.settings[key] = bool(patch.get(key))
        for key in ("modelVersion", "language", "apiKey"):
            if patch.get(key) is not None:
                self.settings[key] = str(patch.get(key)).strip()
        self.save()
        return self.settings

    def key(self):
        env = str(os.environ.get("MINERU_TOKEN") or "").strip()
        return env or str(self.settings.get("apiKey") or "").strip()

    def ready(self):
        return len(self.key()) > 8

    def public_settings(self):
        key = self.key()
        return {
            "enabled": bool(self.settings.get("enabled")),
            "hasKey": self.ready(),
            "keyHint": (key[:5] + "…" + key[-4:]) if key else "",
            "fromEnv": bool(str(os.environ.get("MINERU_TOKEN") or "").strip()),
            "modelVersion": self.settings.get("modelVersion"),
            "language": self.settings.get("language"),
            "isOcr": bool(self.settings.get("isOcr")),
            "enableTable": bool(self.settings.get("enableTable")),
            "signupUrl": "https://mineru.net",
            "note": ("已配置 MinerU token：解析时 PDF 会上传到 mineru.net（免费额度约 1000 页/天）"
                     if self.ready() else
                     "未配置 token：翻译会改用本地 PyMuPDF 提取版面，图注与参考文献分不开，成品观感下降"),
        }

    # ---------------- 客户端 ----------------

    def _headers(self):
        token = self.key()
        if not token:
            raise MinerUError("未配置 MinerU API Key（设置 → API 服务 → MinerU API）")
        return {"Authorization": "Bearer " + token, "Content-Type": "application/json"}

    def _request(self, method, path, body=None):
        url = BASE + path
        data = None
        headers = self._headers()
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001 - 统一转 MinerUError
            raise MinerUError("请求 MinerU 失败：%s" % exc) from exc
        try:
            payload = json.loads(raw)
        except ValueError as exc:
            raise MinerUError("MinerU 返回的不是 JSON：%s" % raw[:200]) from exc
        code = payload.get("code")
        if code not in (0, "0", None):
            raise MinerUError("MinerU 业务错误 %s：%s" % (code, payload.get("msg") or payload.get("message") or ""))
        return payload.get("data") or {}

    def submit(self, pdf_path, data_id=""):
        """申请上传地址；返回 (batch_id, upload_url)。"""
        name = Path(pdf_path).name
        body = {
            "files": [{"name": name, "data_id": data_id or name}],
            "model_version": self.settings.get("modelVersion") or "pipeline",
            "language": self.settings.get("language") or "en",
            "is_ocr": bool(self.settings.get("isOcr")),
            "enable_table": bool(self.settings.get("enableTable")),
        }
        data = self._request("POST", "/file-urls/batch", body)
        batch_id = str(data.get("batch_id") or "")
        urls = data.get("file_urls") or []
        if not batch_id or not urls:
            raise MinerUError("MinerU 没有返回 batch_id / 上传地址")
        return batch_id, urls[0]

    def upload(self, upload_url, pdf_path):
        """PUT 上传 PDF。curl 优先（urllib 对 OSS 预签名地址容易 SSL EOF）。"""
        pdf_path = str(pdf_path)
        curl = "curl.exe" if os.name == "nt" else "curl"
        try:
            proc = subprocess.run(
                [curl, "-sS", "-X", "PUT", "-T", pdf_path, upload_url],
                capture_output=True, timeout=300,
            )
            if proc.returncode == 0:
                return True
            raise MinerUError("curl 上传失败：%s" % proc.stderr.decode("utf-8", "replace")[:200])
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        # 兜底：urllib
        try:
            body = Path(pdf_path).read_bytes()
            req = urllib.request.Request(upload_url, data=body, method="PUT")
            with urllib.request.urlopen(req, timeout=300) as resp:
                return 200 <= resp.status < 300
        except Exception as exc:  # noqa: BLE001
            raise MinerUError("上传 PDF 失败：%s" % exc) from exc

    def poll(self, batch_id):
        data = self._request("GET", "/extract-results/batch/" + batch_id)
        items = data.get("extract_result") or []
        return items[0] if items else {}

    def wait(self, batch_id, *, timeout=900, on_progress=None, should_cancel=None):
        deadline = time.time() + timeout
        while True:
            if should_cancel and should_cancel():
                raise MinerUError("任务已取消")
            item = self.poll(batch_id)
            state = str(item.get("state") or "")
            if on_progress:
                on_progress(state, item)
            if state == "done":
                return item
            if state == "failed":
                raise MinerUError("MinerU 解析失败：%s" % (item.get("err_msg") or "未知原因"))
            if time.time() > deadline:
                raise MinerUError("MinerU 解析超时（%d 秒）" % timeout)
            time.sleep(POLL_INTERVAL)

    def download(self, url, dest):
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            with urllib.request.urlopen(url, timeout=300) as resp:
                dest.write_bytes(resp.read())
        except Exception as exc:  # noqa: BLE001
            raise MinerUError("下载解析结果失败：%s" % exc) from exc
        return dest
