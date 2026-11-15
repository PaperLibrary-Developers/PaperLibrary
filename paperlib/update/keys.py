"""内置的更新清单验签公钥（Ed25519，32 字节原始公钥的 base64）。

公钥不是秘密，随客户端分发；私钥不在仓库、不在发布包里：
默认 %USERPROFILE%\\.paperlibrary\\update-signing-key.json，或 CI 环境变量
PAPERLIB_UPDATE_SIGNING_KEY（base64 seed）。生成/轮换走
`python tools/update_manifest.py keygen`，并把新公钥写回本文件。

轮换公钥 = 提升兼容性要求的发布事件：旧客户端会验签失败并拒绝更新（这是预期行为，
不是静默降级），需在发布说明里写明。
"""
from __future__ import annotations

import base64

# 2026-11-15 生成；对应私钥只在本机 %USERPROFILE%\\.paperlibrary\\update-signing-key.json
PUBLIC_KEY_B64 = "ZS4xJCIQ6iDfz7cDEP9t7ZCBJG1yIBTr6OJbIjdKor4="
PUBLIC_KEY_ID = "e777a2f6"
PUBLIC_KEY_CREATED_AT = "2026-11-15T12:29:39Z"


def public_key() -> bytes:
    return base64.b64decode(PUBLIC_KEY_B64, validate=True)
