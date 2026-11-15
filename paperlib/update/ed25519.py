"""Ed25519（RFC 8032）纯标准库实现：只做清单签名/验签。

为什么不用第三方库：发布包内置的便携运行时只有 PyMuPDF/Pillow，没有 cryptography；
方案一要求“复用现有 stdlib、不引入新依赖”，而验签必须在客户端可用。
因此这里按 RFC 8032 实现（扩展坐标，避免每次点运算求逆），只覆盖
public_key / sign / verify 三个操作，不处理私钥文件与密钥管理（见 signing.py）。

安全说明：
- 私钥是 32 字节 seed；本模块不做私钥存储、日志或编码，调用方负责保管。
- verify 对畸形签名/公钥一律返回 False（不抛异常），并拒绝非规范 S（S >= L）。
"""
from __future__ import annotations

import hashlib

# 曲线参数：p = 2^255 - 19；L 为基点阶
P = 2 ** 255 - 19
L = 2 ** 252 + 27742317777372353535851937790883648493
_D = (-121665 * pow(121666, P - 2, P)) % P
_I = pow(2, (P - 1) // 4, P)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _inv(x: int) -> int:
    return pow(x, P - 2, P)


def _recover_x(y: int, sign: int) -> int:
    if y >= P:
        raise ValueError("y 超出域")
    xx = (y * y - 1) * _inv(_D * y * y + 1) % P
    x = pow(xx, (P + 3) // 8, P)
    if (x * x - xx) % P != 0:
        x = x * _I % P
    if (x * x - xx) % P != 0:
        raise ValueError("点不在曲线上")
    if (x & 1) != sign:
        x = P - x
    return x


_BY = 4 * _inv(5) % P
_BX = _recover_x(_BY, 0)
# 扩展坐标 (X, Y, Z, T)，x = X/Z, y = Y/Z, T = XY/Z
_B = (_BX, _BY, 1, _BX * _BY % P)


def _point_add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % P
    b = (p[1] + p[0]) * (q[1] + q[0]) % P
    c = 2 * p[3] * q[3] * _D % P
    d = 2 * p[2] * q[2] % P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % P, g * h % P, f * g % P, e * h % P)


def _point_mul(point, scalar: int):
    result = (0, 1, 1, 0)  # 单位元
    addend = point
    while scalar > 0:
        if scalar & 1:
            result = _point_add(result, addend)
        addend = _point_add(addend, addend)
        scalar >>= 1
    return result


def _encode_point(point) -> bytes:
    x, y, z, _t = point
    zi = _inv(z)
    x = x * zi % P
    y = y * zi % P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _decode_point(raw: bytes):
    if len(raw) != 32:
        raise ValueError("点编码必须是 32 字节")
    value = int.from_bytes(raw, "little")
    sign = value >> 255
    y = value & ((1 << 255) - 1)
    x = _recover_x(y, sign)
    return (x, y, 1, x * y % P)


def _expand_seed(seed: bytes):
    if len(seed) != 32:
        raise ValueError("Ed25519 seed 必须是 32 字节")
    digest = _sha512(seed)
    a_bytes = bytearray(digest[:32])
    a_bytes[0] &= 248
    a_bytes[31] &= 127
    a_bytes[31] |= 64
    return int.from_bytes(a_bytes, "little"), digest[32:]


def public_key(seed: bytes) -> bytes:
    """由 32 字节 seed 推导 32 字节公钥。"""
    a, _prefix = _expand_seed(seed)
    return _encode_point(_point_mul(_B, a))


def sign(message: bytes, seed: bytes) -> bytes:
    """确定性签名（RFC 8032 Ed25519）：返回 64 字节签名。"""
    a, prefix = _expand_seed(seed)
    public = _encode_point(_point_mul(_B, a))
    r = int.from_bytes(_sha512(prefix + message), "little") % L
    r_point = _point_mul(_B, r)
    encoded_r = _encode_point(r_point)
    k = int.from_bytes(_sha512(encoded_r + public + message), "little") % L
    s = (r + k * a) % L
    return encoded_r + s.to_bytes(32, "little")


def verify(signature: bytes, message: bytes, public: bytes) -> bool:
    """验签：任何畸形输入返回 False，不抛异常。"""
    try:
        if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
            return False
        if not isinstance(public, (bytes, bytearray)) or len(public) != 32:
            return False
        if not isinstance(message, (bytes, bytearray)):
            return False
        encoded_r = bytes(signature[:32])
        s = int.from_bytes(signature[32:], "little")
        if s >= L:
            return False
        r_point = _decode_point(encoded_r)
        a_point = _decode_point(bytes(public))
        k = int.from_bytes(_sha512(encoded_r + bytes(public) + bytes(message)), "little") % L
        return _encode_point(_point_mul(_B, s)) == _encode_point(_point_add(r_point, _point_mul(a_point, k)))
    except (ValueError, OverflowError):
        return False
