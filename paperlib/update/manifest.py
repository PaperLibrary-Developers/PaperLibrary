"""更新清单 update-manifest.json 的 schema（schema=1）、规范化 JSON 与校验。

本模块是方案一阶段 0 / P0-02 的交付物：冻结更新清单的字段、类型、取值约束，
以及 Ed25519 签名覆盖的“规范化 JSON”规则。签名/验签本身在 paperlib.update.signing。

规范化规则（签名的唯一输入，必须可逐字节复现）：
- 字段顺序固定：FIELDS 顶层顺序 + assets.full 的 ASSET_FIELDS 顺序；
- UTF-8、无 BOM、无多余空白（分隔符 "," 与 ":"），不转义非 ASCII；
- 签名字段 signature 不参与签名，放在序列化结果的最后一个键；
- 发布产物写规范化字节本身（单行 JSON，无结尾换行），便于用户直接复核。

安全约束（原文 §3.3 / §9，不得放宽）：
- 拒绝未知 schema / product / channel / arch 与未知字段（schema 冻结，扩展必须提升 schema）；
- 资产 URL 只接受 https + GitHub 主机 + /releases/ 路径；重定向的允许主机在下载阶段另行校验
  （实测 GitHub 资产 302 落到 release-assets.githubusercontent.com，见 P0-04 记录）；
- sha256 必须是小写 64 位十六进制；size 必须是正整数；
- 清单本身限制 256 KB（原文 §4.2 的响应体上限），解析前先按字节长度拦截。
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from paperlib.version import VersionError, semver, ui_label

# 清单 schema 版本。字段/取值规则一旦发布即冻结；扩展只能新增 schema=2。
MANIFEST_SCHEMA = 1
PRODUCT = "PaperLibrary"
CHANNEL = "stable"
ARCH = "x64"
MAX_BYTES = 256 * 1024

# 顶层字段顺序（也是规范化 JSON 的顺序；signature 固定最后）
FIELDS = ("schema", "product", "channel", "version", "tag", "publishedAt",
          "releaseNotesUrl", "minWindows", "arch", "assets", "signature")
# 不含 signature 的签名覆盖字段
PAYLOAD_FIELDS = tuple(name for name in FIELDS if name != "signature")
ASSET_FIELDS = ("url", "size", "sha256")

# 清单里的链接只允许 GitHub 主机；下载重定向的允许主机在下载阶段单独校验。
URL_HOSTS = {"github.com"}
MIN_WINDOWS_RE = re.compile(r"^\d+\.\d+\.\d+$")
ISO_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RELEASE_PATH_RE = re.compile(r"^/[^/]+/[^/]+/releases/")


class ManifestError(ValueError):
    """清单缺失、超大、JSON 损坏或字段/取值不符合冻结的 schema。"""


def _require(data, key, where="清单"):
    if key not in data:
        raise ManifestError(where + "缺少字段：" + key)
    return data[key]


def _check_str(value, key, where="清单"):
    if not isinstance(value, str):
        raise ManifestError(where + "字段 %s 必须是字符串" % key)
    return value


def _check_https_github(url, key):
    _check_str(url, key)
    if not url:
        raise ManifestError(key + " 不能为空")
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ManifestError(key + " 必须是 https 地址（拒绝明文 HTTP）：" + url)
    if parts.username or parts.password:
        raise ManifestError(key + " 不能包含用户名/密码：" + url)
    host = (parts.hostname or "").lower()
    if host not in URL_HOSTS:
        raise ManifestError(key + " 主机不在允许列表 %s 内：%s" % (sorted(URL_HOSTS), host))
    return url


def _check_assets(value):
    if not isinstance(value, dict):
        raise ManifestError("assets 必须是对象")
    unknown = sorted(set(value) - {"full"})
    if unknown:
        raise ManifestError("assets 含未知字段（schema=1 只定义了 full）：" + "、".join(unknown))
    full = _require(value, "full", "assets.")
    if not isinstance(full, dict):
        raise ManifestError("assets.full 必须是对象")
    unknown = sorted(set(full) - set(ASSET_FIELDS))
    if unknown:
        raise ManifestError("assets.full 含未知字段：" + "、".join(unknown))
    for key in ASSET_FIELDS:
        _require(full, key, "assets.full.")
    url = _check_https_github(full["url"], "assets.full.url")
    path = urlsplit(url).path
    if not RELEASE_PATH_RE.match(path):
        raise ManifestError("assets.full.url 必须是 GitHub Release 资产地址（/releases/…）：" + url)
    size = full["size"]
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ManifestError("assets.full.size 必须是正整数（字节数）：%r" % (size,))
    sha = _check_str(full["sha256"], "assets.full.sha256")
    if not SHA256_RE.match(sha):
        raise ManifestError("assets.full.sha256 必须是 64 位小写十六进制：" + sha)
    return {"url": url, "size": size, "sha256": sha}


def _check_signature(value):
    _check_str(value, "signature")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ManifestError("signature 必须是合法的 base64 字符串")
    if len(raw) != 64:
        raise ManifestError("signature 必须是 64 字节的 Ed25519 签名（base64 解码后 %d 字节）" % len(raw))
    return value


def validate(data, require_signature: bool = True) -> dict:
    """校验清单并返回固定顺序的归一化 dict（含 signature）。

    require_signature=False 只用于“签名工具接收待签清单”：此时允许没有 signature
    （返回的归一化 dict 里 signature 为空串），其余字段约束完全相同。
    任何不符合 schema=1 的情况都抛 ManifestError（消息可直接展示，不含堆栈）。
    """
    if not isinstance(data, dict):
        raise ManifestError("清单顶层必须是 JSON 对象")
    unknown = sorted(set(data) - set(FIELDS))
    if unknown:
        raise ManifestError("清单含未知字段（schema=1 已冻结，扩展需提升 schema）：" + "、".join(unknown))
    for key in PAYLOAD_FIELDS:
        _require(data, key)
    if require_signature:
        _require(data, "signature")

    schema = data["schema"]
    if isinstance(schema, bool) or not isinstance(schema, int) or schema != MANIFEST_SCHEMA:
        raise ManifestError("schema 只支持 %d（当前：%r）" % (MANIFEST_SCHEMA, schema))
    product = _check_str(data["product"], "product").strip()
    if product != PRODUCT:
        raise ManifestError("product 必须是 %s（当前：%r）" % (PRODUCT, product))
    channel = _check_str(data["channel"], "channel").strip()
    if channel != CHANNEL:
        raise ManifestError("channel 只支持 %s（当前：%r）" % (CHANNEL, channel))
    arch = _check_str(data["arch"], "arch").strip()
    if arch != ARCH:
        raise ManifestError("arch 只支持 %s（当前：%r）" % (ARCH, arch))

    version = _check_str(data["version"], "version").strip()
    try:
        semver(version)
    except VersionError as exc:
        raise ManifestError("version 无效：" + str(exc))
    tag = _check_str(data["tag"], "tag").strip()
    if tag != ui_label(version):
        raise ManifestError("tag 必须等于 %s（当前：%r）" % (ui_label(version), tag))

    published_at = _check_str(data["publishedAt"], "publishedAt").strip()
    if not ISO_UTC_RE.match(published_at):
        raise ManifestError("publishedAt 必须是 UTC ISO8601（如 2026-12-01T00:00:00Z）：" + published_at)
    release_notes = _check_https_github(data["releaseNotesUrl"], "releaseNotesUrl")
    if "/releases/" not in urlsplit(release_notes).path:
        raise ManifestError("releaseNotesUrl 必须是 GitHub Release 页面：" + release_notes)

    min_windows = _check_str(data["minWindows"], "minWindows").strip()
    if not MIN_WINDOWS_RE.match(min_windows):
        raise ManifestError("minWindows 必须是 x.y.z 形式（如 10.0.17763）：" + min_windows)

    assets = _check_assets(data["assets"])
    signature = _check_signature(data["signature"]) if "signature" in data else ""

    return {
        "schema": MANIFEST_SCHEMA,
        "product": product,
        "channel": channel,
        "version": version,
        "tag": tag,
        "publishedAt": published_at,
        "releaseNotesUrl": release_notes,
        "minWindows": min_windows,
        "arch": arch,
        "assets": {"full": assets},
        "signature": signature,
    }


def canonical_payload(data) -> bytes:
    """签名覆盖的规范化 JSON 字节（不含 signature，固定字段顺序，无多余空白）。"""
    normalized = validate(data, require_signature=False)
    payload = {key: normalized[key] for key in PAYLOAD_FIELDS}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def canonical_bytes(data) -> bytes:
    """完整清单的规范化字节（含 signature，signature 最后；发布产物就是这个字节串）。"""
    normalized = validate(data)
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_bytes(raw, max_bytes: int = MAX_BYTES) -> dict:
    """按字节加载并校验清单；超过 max_bytes 直接拒绝（原文 §4.2 的 256 KB 上限）。"""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    if not isinstance(raw, (bytes, bytearray)):
        raise ManifestError("清单必须是 bytes 或 str")
    if len(raw) > max_bytes:
        raise ManifestError("清单超过 %d KB（%d 字节）上限，实际 %d 字节"
                            % (max_bytes // 1024, max_bytes, len(raw)))
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ManifestError("清单不是合法 UTF-8：" + str(exc))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ManifestError("清单不是合法 JSON：" + str(exc))
    return validate(data)


def load_file(path) -> dict:
    path = Path(path)
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        raise ManifestError("找不到更新清单：" + str(path))
    except OSError as exc:
        raise ManifestError("无法读取更新清单 %s：%s" % (path, exc))
    return load_bytes(raw)
