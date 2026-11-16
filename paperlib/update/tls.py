"""更新请求使用的 HTTPS 上下文（P1-02/P2-02）。

Windows 证书库可能把 USERTrust ECC 以“由 AAA 交叉签名”的形式作为信任锚；
GitHub 的 Sectigo E36 链在 OpenSSL 3.0（Python 3.11 内置运行时）下需要
VERIFY_X509_PARTIAL_CHAIN 才能在该信任锚处终止。Python 3.14 默认已启用该 flag，
所以系统 Python 能检查成功、包内 Python 报 unable to get issuer certificate。

这里只增加 partial-chain 行为，CERT_REQUIRED/主机名校验保持开启，绝不关闭证书校验。
"""
from __future__ import annotations

import ssl
import urllib.request


def create_https_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.verify_flags |= getattr(ssl, "VERIFY_X509_PARTIAL_CHAIN", 0)
    return context


def https_handler() -> urllib.request.HTTPSHandler:
    return urllib.request.HTTPSHandler(context=create_https_context())


def build_opener(*handlers) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(https_handler(), *handlers)
