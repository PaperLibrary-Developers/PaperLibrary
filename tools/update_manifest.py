r"""更新清单的签名/验签命令行（阶段 0 / P0-03）。

用法：
    python tools/update_manifest.py keygen [--key <路径>] [--force]
    python tools/update_manifest.py show-key
    python tools/update_manifest.py sign --in <manifest.json> [--out <路径>] [--key <路径>]
    python tools/update_manifest.py verify --in <manifest.json> [--pubkey <base64>]

密钥约定：
- 私钥默认在 %USERPROFILE%\.paperlibrary\update-signing-key.json（包外，权限收紧到当前用户），
  或由环境变量 PAPERLIB_UPDATE_SIGNING_KEY（base64 seed）提供（CI secret）。
- 公钥内置在 paperlib/update/keys.py；本工具**从不打印 seed**。
- keygen 拒绝把私钥写进仓库目录（防止误提交）。

sign 会先规范化再签名，并把结果自检（用对应公钥验签）后才落盘；
默认原地重写为规范化单行 JSON（发布产物就是这个字节串）。
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperlib.update import ed25519, keys, manifest, signing  # noqa: E402


def _inside_repo(path: Path) -> bool:
    try:
        path.resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def cmd_keygen(args):
    path = Path(args.key) if args.key else signing.default_key_path()
    if _inside_repo(path):
        print("[update] 拒绝把签名私钥写进仓库目录：" + str(path))
        return 1
    if path.exists() and not args.force:
        print("[update] 私钥文件已存在，未覆盖：" + str(path))
        print("[update] 如需轮换，先备份并用 --force，然后更新 paperlib/update/keys.py 的公钥")
        return 1
    seed, public = signing.generate_keypair()
    info = signing.save_private_key(seed, path)
    print("[update] 已生成 Ed25519 更新签名密钥（seed 不打印、不落仓库）：")
    print("[update]   keyId     " + info["keyId"])
    print("[update]   私钥      " + info["path"] + "（" + info["acl"] + "）")
    print("[update]   公钥      " + info["publicKey"])
    print("[update] 下一步：把 publicKey/keyId 写入 paperlib/update/keys.py，并妥善备份私钥。")
    return 0


def cmd_show_key(args):
    print("[update] 内置验签公钥 keyId=" + keys.PUBLIC_KEY_ID
          + " createdAt=" + keys.PUBLIC_KEY_CREATED_AT)
    print("[update]   " + keys.PUBLIC_KEY_B64)
    try:
        seed = signing.load_private_key()
    except signing.SigningError as exc:
        print("[update] 本机签名私钥：不可用（" + str(exc) + "）")
        return 0
    print("[update] 本机签名私钥可用，keyId=" + signing.key_id(ed25519.public_key(seed)))
    return 0


def _read_json(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise SystemExit("[update] 找不到清单：" + str(path))
    except ValueError as exc:
        raise SystemExit("[update] 清单不是合法 JSON：" + str(exc))
    return data


def cmd_sign(args):
    path = Path(args.infile)
    data = _read_json(path)
    try:
        seed = signing.load_private_key(args.key)
    except signing.SigningError as exc:
        print("[update] " + str(exc))
        return 1
    try:
        signed = signing.sign_manifest(data, seed)
        # 自检：用对应公钥验签，避免写出坏产物
        signing.verify_manifest(signed, ed25519.public_key(seed))
    except (manifest.ManifestError, signing.ManifestSignatureError, ValueError) as exc:
        print("[update] 签名失败：" + str(exc))
        return 1
    raw = manifest.canonical_bytes(signed)
    out = Path(args.out) if args.out else path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    print("[update] 已签名：" + str(out))
    print("[update]   %s %s size=%d sha256=%s… keyId=%s"
          % (signed["tag"], signed["version"], signed["assets"]["full"]["size"],
             signed["assets"]["full"]["sha256"][:12], signing.key_id(ed25519.public_key(seed))))
    return 0


def cmd_verify(args):
    path = Path(args.infile)
    try:
        data = manifest.load_file(path)
        public = base64.b64decode(args.pubkey, validate=True) if args.pubkey else keys.public_key()
        if len(public) != 32:
            print("[update] 公钥必须是 32 字节（base64 解码后）")
            return 1
        signing.verify_manifest(data, public)
    except (manifest.ManifestError, signing.ManifestSignatureError, ValueError) as exc:
        print("[update] 验签失败：" + str(exc))
        return 1
    print("[update] 验签通过：%s %s（keyId=%s）" % (data["tag"], data["version"],
                                                   signing.key_id(public)))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="更新清单签名/验签工具")
    sub = parser.add_subparsers(dest="command", required=True)

    keygen = sub.add_parser("keygen", help="生成 Ed25519 签名密钥（私钥放包外）")
    keygen.add_argument("--key", default="", help="私钥文件路径（默认 %%USERPROFILE%%\\.paperlibrary\\...）")
    keygen.add_argument("--force", action="store_true", help="覆盖已有私钥（轮换时用）")
    keygen.set_defaults(func=cmd_keygen)

    show = sub.add_parser("show-key", help="显示内置公钥与本机私钥可用性（不显示 seed）")
    show.set_defaults(func=cmd_show_key)

    sign = sub.add_parser("sign", help="给清单签名并规范化落盘")
    sign.add_argument("--in", dest="infile", required=True)
    sign.add_argument("--out", default="", help="输出路径（默认原地重写为规范化 JSON）")
    sign.add_argument("--key", default="", help="私钥文件路径（默认本机路径或环境变量）")
    sign.set_defaults(func=cmd_sign)

    verify = sub.add_parser("verify", help="校验清单结构与签名")
    verify.add_argument("--in", dest="infile", required=True)
    verify.add_argument("--pubkey", default="", help="覆盖内置公钥（base64，32 字节）")
    verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
