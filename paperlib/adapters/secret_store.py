"""本机密钥落盘加密（Windows DPAPI CurrentUser 作用域，零外部依赖）。

设计要点：
- 仅在落盘序列化时将敏感字段（如 API Key）加密为 ``dpapi:v1:<base64>``；内存中仍保持明文，
  上层调用方与前端打码逻辑（keyHint）完全无感。
- 使用 Windows 标准库 ``crypt32.dll``（``CryptProtectData`` / ``CryptUnprotectData``），
  仅当前 Windows 登录用户可解密，无需额外第三方依赖。
- 兼容旧版本明文配置：读取到旧明文密钥时，若当前环境支持 DPAPI，自动平滑迁移落盘为密文。
- 非 Windows 环境透明降级为原样读写；若遇到无法解密的密文（例如把便携包拷到另一台机器/账户），
  返回空串而不阻断程序启动。
"""
from __future__ import annotations

import base64
import ctypes
import sys
from ctypes import wintypes

DPAPI_PREFIX = "dpapi:v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1
_APP_ENTROPY = b"PaperLibrary.SecretStore.v1"


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


_WIN32_FUNCS = None


def _load_win32():
    global _WIN32_FUNCS
    if _WIN32_FUNCS is not None:
        return _WIN32_FUNCS
    if sys.platform != "win32":
        _WIN32_FUNCS = False
        return False
    try:
        crypt32 = ctypes.windll.crypt32
        kernel32 = ctypes.windll.kernel32
        blob_ptr = ctypes.POINTER(_DataBlob)

        protect = crypt32.CryptProtectData
        protect.argtypes = [
            blob_ptr,
            wintypes.LPCWSTR,
            blob_ptr,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_ptr,
        ]
        protect.restype = wintypes.BOOL

        unprotect = crypt32.CryptUnprotectData
        unprotect.argtypes = [
            blob_ptr,
            ctypes.POINTER(wintypes.LPWSTR),
            blob_ptr,
            ctypes.c_void_p,
            ctypes.c_void_p,
            wintypes.DWORD,
            blob_ptr,
        ]
        unprotect.restype = wintypes.BOOL

        local_free = kernel32.LocalFree
        local_free.argtypes = [ctypes.c_void_p]
        local_free.restype = ctypes.c_void_p

        _WIN32_FUNCS = (protect, unprotect, local_free)
    except (AttributeError, OSError):
        _WIN32_FUNCS = False
    return _WIN32_FUNCS


def is_dpapi_available() -> bool:
    """当前平台是否支持 Windows DPAPI 密钥加密。"""
    return bool(_load_win32())


def is_protected(value) -> bool:
    return isinstance(value, str) and value.startswith(DPAPI_PREFIX)


def _make_blob(data: bytes):
    buf = ctypes.create_string_buffer(data, len(data))
    blob = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    return blob, buf


def _dpapi_protect(raw: bytes) -> bytes:
    funcs = _load_win32()
    if not funcs:
        raise RuntimeError("DPAPI unavailable")
    protect, _, local_free = funcs
    in_blob, _in_buf = _make_blob(raw)
    ent_blob, _ent_buf = _make_blob(_APP_ENTROPY)
    out_blob = _DataBlob()
    ok = protect(
        ctypes.byref(in_blob),
        "PaperLibrary API Key",
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("CryptProtectData failed: %d" % ctypes.GetLastError())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            local_free(out_blob.pbData)


def _dpapi_unprotect(cipher: bytes) -> bytes:
    funcs = _load_win32()
    if not funcs:
        raise RuntimeError("DPAPI unavailable")
    _, unprotect, local_free = funcs
    in_blob, _in_buf = _make_blob(cipher)
    ent_blob, _ent_buf = _make_blob(_APP_ENTROPY)
    out_blob = _DataBlob()
    ok = unprotect(
        ctypes.byref(in_blob),
        None,
        ctypes.byref(ent_blob),
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError("CryptUnprotectData failed: %d" % ctypes.GetLastError())
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        if out_blob.pbData:
            local_free(out_blob.pbData)


def protect_secret(plaintext: str) -> str:
    """将明文密钥转为可落盘形式；空串保持空串，非 Windows 原样返回。"""
    text = str(plaintext or "")
    if not text or is_protected(text):
        return text
    if not is_dpapi_available():
        return text
    try:
        cipher = _dpapi_protect(text.encode("utf-8"))
        return DPAPI_PREFIX + base64.b64encode(cipher).decode("ascii")
    except Exception:  # noqa: BLE001
        return text


def unprotect_secret(stored: str) -> tuple[str, bool]:
    """还原落盘密钥为 ``(plaintext, needs_migration)``。

    - 若存储值已是 DPAPI 密文：尝试解密，失败时安全降级返回 ``("", False)``；
    - 若存储值是旧明文且当前环境支持 DPAPI：返回 ``(stored, True)`` 提示上层回写迁移。
    """
    raw = str(stored or "")
    if not raw:
        return "", False
    if is_protected(raw):
        if not is_dpapi_available():
            return "", False
        payload = raw[len(DPAPI_PREFIX):]
        try:
            cipher = base64.b64decode(payload.encode("ascii"), validate=True)
            plain = _dpapi_unprotect(cipher).decode("utf-8")
            return plain, False
        except Exception as exc:  # noqa: BLE001
            print("[secret_store] 无法解密已保存的密钥（可能来自其他用户或设备），已清空：" + str(exc))
            return "", False
    return raw, is_dpapi_available()


def load_secret_field(settings: dict, field: str) -> bool:
    """就地解密 settings[field]；返回是否需要将旧明文平滑迁移落盘。"""
    raw = settings.get(field)
    if not raw:
        settings[field] = ""
        return False
    plain, needs_migration = unprotect_secret(str(raw))
    settings[field] = plain
    return needs_migration


def dump_with_protected_secret(settings: dict, field: str) -> dict:
    """返回浅拷贝字典，仅把指定密钥字段替换为落盘密文（不修改内存中 settings）。"""
    out = dict(settings)
    if field in out:
        out[field] = protect_secret(str(out.get(field) or ""))
    return out
