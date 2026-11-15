"""更新清单的签名/验签与私钥管理（阶段 0 / P0-03）。

职责边界：
- 签名私钥：32 字节 seed，**不得进入仓库与发布包**。默认放在
  %USERPROFILE%\\.paperlibrary\\update-signing-key.json（包外，本机文件权限收紧），
  或由环境变量 PAPERLIB_UPDATE_SIGNING_KEY 提供 base64(seed)（CI secret 用）。
- 公钥：内置在客户端 paperlib/update/keys.py（公钥不是秘密）。
- 本模块不打印、不记录 seed；日志只允许出现 keyId / 公钥。

客户端验签顺序（原文 §3.3 / §9）：先验签 manifest，再信任其中的 URL/版本/哈希。
"""
from __future__ import annotations

import base64
import json
import os
import stat
import subprocess
import time
from pathlib import Path

from paperlib.update import ed25519, keys, manifest

KEY_SCHEMA = 1
KEY_ENV = "PAPERLIB_UPDATE_SIGNING_KEY"
KEY_FILE = "update-signing-key.json"


class SigningError(ValueError):
    """私钥缺失/损坏、签名参数错误。"""


class ManifestSignatureError(ValueError):
    """清单验签失败（签名不匹配、公钥不匹配、清单被篡改）。"""


def key_id(public: bytes) -> str:
    """公钥标识：sha256(公钥) 前 8 位十六进制，用于日志/轮换排查。"""
    import hashlib
    return hashlib.sha256(public).hexdigest()[:8]


def generate_keypair() -> tuple[bytes, bytes]:
    """生成 (seed, public)；seed 只返回给调用方，不落日志。"""
    seed = os.urandom(32)
    return seed, ed25519.public_key(seed)


def default_key_path() -> Path:
    return Path.home() / ".paperlibrary" / KEY_FILE


def _check_seed(seed: bytes) -> bytes:
    if not isinstance(seed, (bytes, bytearray)) or len(seed) != 32:
        raise SigningError("签名私钥必须是 32 字节 seed")
    return bytes(seed)


def _restrict_acl(path: Path) -> str:
    """Windows 上把私钥文件权限收紧到当前用户（尽力而为，返回说明）。"""
    if os.name != "nt":
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return "chmod 600"
    user = os.environ.get("USERNAME") or ""
    if not user:
        return "跳过（取不到 USERNAME）"
    try:
        subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", user + ":F"],
                       capture_output=True, timeout=20)
        return "icacls 仅当前用户"
    except (OSError, subprocess.SubprocessError):
        return "icacls 未执行（请手动确认文件权限）"


def save_private_key(seed: bytes, path=None) -> dict:
    """把 seed 写到包外文件（默认路径），返回 {path,keyId,publicKey}（不含 seed）。"""
    seed = _check_seed(seed)
    path = Path(path) if path else default_key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    public = ed25519.public_key(seed)
    record = {
        "schema": KEY_SCHEMA,
        "keyId": key_id(public),
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "publicKey": base64.b64encode(public).decode("ascii"),
        "seed": base64.b64encode(seed).decode("ascii"),
        "note": "Ed25519 更新签名私钥；只存本机/CI secret，绝不提交到仓库或打进发布包",
    }
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(path), "keyId": record["keyId"], "publicKey": record["publicKey"],
            "acl": _restrict_acl(path)}


def load_private_key(path=None, env=None) -> bytes:
    """按“环境变量 → 默认/指定文件”顺序取 seed；找不到时给出可操作的错误。"""
    env = os.environ if env is None else env
    raw = env.get(KEY_ENV)
    if raw:
        try:
            seed = base64.b64decode(raw, validate=True)
        except Exception:  # noqa: BLE001
            raise SigningError("环境变量 %s 不是合法 base64" % KEY_ENV)
        return _check_seed(seed)
    path = Path(path) if path else default_key_path()
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise SigningError("找不到更新签名私钥：%s（先运行 python tools/update_manifest.py keygen，"
                           "或设置环境变量 %s）" % (path, KEY_ENV))
    except OSError as exc:
        raise SigningError("无法读取签名私钥 %s：%s" % (path, exc))
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise SigningError("签名私钥文件不是合法 JSON：%s" % exc)
    if not isinstance(data, dict) or not data.get("seed"):
        raise SigningError("签名私钥文件缺少 seed 字段")
    try:
        seed = base64.b64decode(str(data["seed"]), validate=True)
    except Exception:  # noqa: BLE001
        raise SigningError("签名私钥文件里的 seed 不是合法 base64")
    seed = _check_seed(seed)
    expected = data.get("publicKey")
    if expected:
        actual = base64.b64encode(ed25519.public_key(seed)).decode("ascii")
        if actual != expected:
            raise SigningError("签名私钥与文件内 publicKey 不匹配（文件可能损坏）")
    return seed


def sign_manifest(data, seed: bytes) -> dict:
    """给清单签名；返回含 signature 的归一化 dict（不打印 seed）。"""
    seed = _check_seed(seed)
    payload = manifest.canonical_payload(data)
    signature = base64.b64encode(ed25519.sign(payload, seed)).decode("ascii")
    unsigned = {key: value for key, value in data.items() if key != "signature"}
    unsigned["signature"] = signature
    return manifest.validate(unsigned)


def verify_manifest(data, public_key=None) -> dict:
    """验签清单；成功返回归一化 dict，失败抛 ManifestSignatureError。"""
    public = keys.public_key() if public_key is None else public_key
    if len(public) != 32:
        raise ManifestSignatureError("验签公钥必须是 32 字节")
    normalized = manifest.validate(data)  # 先做结构校验（缺字段/类型错误等）
    signature = base64.b64decode(normalized["signature"], validate=True)
    payload = manifest.canonical_payload(normalized)
    if not ed25519.verify(signature, payload, public):
        raise ManifestSignatureError("更新清单验签失败（清单被篡改、签名不匹配或公钥不匹配）")
    return normalized


def embedded_public_key_info() -> dict:
    return {"keyId": keys.PUBLIC_KEY_ID, "publicKey": keys.PUBLIC_KEY_B64,
            "createdAt": keys.PUBLIC_KEY_CREATED_AT}
