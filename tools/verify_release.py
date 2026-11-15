r"""发布后校验：稳定地址 + 清单验签 + 资产大小/SHA-256 + zip 不含 data/。

用法：
    # 1) 在线校验当前 latest Release（稳定地址，匿名，不调用 api.github.com）
    python tools/verify_release.py

    # 2) 校验本地已签名清单（发布前自检；--asset-path 校验本地 zip）
    python tools/verify_release.py --manifest dist/update-manifest.json --asset-path dist/x.zip

    # 3) 本地清单 + 在线比对 latest 清单是否一致，并真实下载资产复核
    python tools/verify_release.py --manifest dist/update-manifest.json --latest --download

    # 4) 历史资产（P0-01 之前构建的包没有 version.json）：降级为警告
    python tools/verify_release.py --manifest shared/fixtures/update-manifest.signed.json --download --legacy

校验项：稳定地址可匿名访问、清单结构 + Ed25519 验签、latest 清单与本地一致（--latest）、
资产 HTTP 200、字节数与清单一致、SHA-256 与清单一致、zip 无顶层 data/、无绝对路径/上跳、
根目录有 PaperLibrary.exe、version.json 存在且版本匹配（--legacy 时缺失降为警告）。
任一 FAIL → 退出码 1；WARN 不影响退出码。
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperlib.update import manifest, release, signing  # noqa: E402

RESULTS: list[tuple[str, str]] = []


def record(ok: bool, message: str):
    RESULTS.append(("PASS" if ok else "FAIL", message))
    print(("  [PASS] " if ok else "  [FAIL] ") + message)


def warn(message: str):
    RESULTS.append(("WARN", message))
    print("  [WARN] " + message)


class VerifyError(Exception):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)
_UA = "PaperLibrary-update-verifier/1.0 (+https://github.com/%s)" % release.REPO


def _request_once(url: str, timeout: float):
    request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        return _OPENER.open(request, timeout=timeout)
    except urllib.error.HTTPError as exc:
        return exc


def open_chain(url: str, timeout: float = 25.0, max_hops: int = 6):
    """匿名请求并手动跟随重定向；每一步都必须在允许主机列表内。返回 (final_url, response)。"""
    current = url
    for _ in range(max_hops):
        if not release.allowed_download_url(current):
            raise VerifyError("请求主机不在允许列表（https + %s）：%s"
                              % ("、".join(sorted(release.ALLOWED_DOWNLOAD_HOSTS)), current))
        response = _request_once(current, timeout)
        status = getattr(response, "status", None) or getattr(response, "code", 0)
        if status in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            try:
                response.close()
            except Exception:  # noqa: BLE001
                pass
            if not location:
                raise VerifyError("收到 %d 但没有 Location：%s" % (status, current))
            current = urllib.parse.urljoin(current, location)
            continue
        return current, response, status
    raise VerifyError("重定向次数超过 %d 次：%s" % (max_hops - 1, url))


def fetch_manifest(url: str, timeout: float) -> tuple[bytes, str]:
    final_url, response, status = open_chain(url, timeout=timeout)
    try:
        if status != 200:
            raise VerifyError("GET %s → HTTP %d（稳定地址没有清单；发布流程未完成？）"
                              % (final_url, status))
        raw = response.read(manifest.MAX_BYTES + 1)
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass
    if len(raw) > manifest.MAX_BYTES:
        raise VerifyError("清单超过 %d KB 上限" % (manifest.MAX_BYTES // 1024))
    return raw, final_url


def verify_manifest_bytes(raw: bytes, public=None, source: str = "清单") -> dict:
    try:
        data = manifest.load_bytes(raw)
        signing.verify_manifest(data, public)
    except (manifest.ManifestError, signing.ManifestSignatureError) as exc:
        record(False, "%s 结构/验签失败：%s" % (source, exc))
        raise SystemExit(1)
    record(True, "%s 结构校验通过（schema=%d，%s %s）"
           % (source, data["schema"], data["tag"], data["version"]))
    record(True, "%s Ed25519 验签通过（keyId=%s）"
           % (source, signing.key_id(public if public is not None else signing.keys.public_key())))
    return data


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_asset_file(path: Path, asset: dict, require_version_file: bool = True,
                      expected_version: str = "") -> list[str]:
    """校验本地 zip：大小、SHA-256、zip 内容约束。返回问题列表（空 = 通过）。"""
    problems: list[str] = []
    actual_size = path.stat().st_size
    if actual_size != asset["size"]:
        problems.append("资产大小 %d 与清单 %d 不一致" % (actual_size, asset["size"]))
    else:
        record(True, "资产字节数与清单一致（%d 字节）" % actual_size)
    actual_sha = _sha256_file(path)
    if actual_sha != asset["sha256"]:
        problems.append("资产 SHA-256 %s 与清单 %s 不一致" % (actual_sha, asset["sha256"]))
    else:
        record(True, "资产 SHA-256 与清单一致")
    zip_problems, zip_warnings = release.check_zip(
        path, expected_version=expected_version, require_version_file=require_version_file)
    for item in zip_warnings:
        warn(item)
    for item in zip_problems:
        problems.append(item)
    if not zip_problems:
        record(True, "zip 内容检查通过：无顶层 data/、无绝对路径/上跳、含 %s" % release.LAUNCHER)
    return problems


def download_asset(url: str, dest: Path, asset: dict, timeout: float) -> None:
    final_url, response, status = open_chain(url, timeout=timeout)
    try:
        if status != 200:
            raise VerifyError("GET %s → HTTP %d" % (final_url, status))
        length = response.headers.get("Content-Length")
        if length is not None:
            try:
                declared = int(length)
            except ValueError:
                declared = -1
            if declared >= 0 and declared != asset["size"]:
                raise VerifyError("Content-Length %d 与清单 size %d 不一致" % (declared, asset["size"]))
        digest = hashlib.sha256()
        total = 0
        with open(dest, "wb") as fh:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > asset["size"]:
                    raise VerifyError("下载字节数超过清单 size（可能被替换/截断）")
                fh.write(chunk)
                digest.update(chunk)
    finally:
        try:
            response.close()
        except Exception:  # noqa: BLE001
            pass
    if total != asset["size"]:
        raise VerifyError("下载完成但字节数 %d 与清单 size %d 不一致" % (total, asset["size"]))
    record(True, "资产匿名下载成功（%d 字节，重定向主机已在允许列表内）" % total)


def main(argv=None):
    parser = argparse.ArgumentParser(description="发布后校验（稳定地址 / 清单验签 / 资产哈希 / zip 内容）")
    parser.add_argument("--manifest", default="", help="本地已签名清单路径（默认取 latest 稳定地址）")
    parser.add_argument("--asset-path", default="", help="本地完整包 zip（离线校验，不下载）")
    parser.add_argument("--download", action="store_true",
                        help="按清单 URL 真实下载资产再校验（本地清单模式默认不下载）")
    parser.add_argument("--latest", action="store_true",
                        help="另外拉取 latest 稳定地址清单，要求与本地清单一致")
    parser.add_argument("--pubkey", default="", help="覆盖内置验签公钥（base64，32 字节）")
    parser.add_argument("--legacy", action="store_true",
                        help="历史资产：zip 内缺 version.json 降级为警告")
    parser.add_argument("--keep", action="store_true", help="保留下载的临时资产")
    parser.add_argument("--timeout", type=float, default=25.0)
    args = parser.parse_args(argv)

    public = None
    if args.pubkey:
        import base64
        try:
            public = base64.b64decode(args.pubkey, validate=True)
        except Exception:  # noqa: BLE001
            print("[verify] --pubkey 不是合法 base64")
            return 1
        if len(public) != 32:
            print("[verify] --pubkey 必须是 32 字节")
            return 1

    started = time.time()
    print("[verify] 仓库：" + release.REPO)
    try:
        if args.manifest:
            path = Path(args.manifest)
            try:
                raw = path.read_bytes()
            except OSError as exc:
                print("[verify] 无法读取本地清单 %s：%s" % (path, exc))
                return 1
            data = verify_manifest_bytes(raw, public, "本地清单")
            if args.latest:
                remote_raw, remote_url = fetch_manifest(release.stable_manifest_url(), args.timeout)
                remote = verify_manifest_bytes(remote_raw, public, "latest 清单")
                same = (manifest.canonical_bytes(remote) == manifest.canonical_bytes(data))
                record(same, "latest 稳定地址清单与本地清单一致（%s）" % remote_url)
                if not same:
                    raise SystemExit(1)
        else:
            raw, manifest_url = fetch_manifest(release.stable_manifest_url(), args.timeout)
            print("[verify] 稳定地址：" + manifest_url)
            data = verify_manifest_bytes(raw, public, "latest 清单")

        asset = data["assets"]["full"]
        print("[verify] 资产：" + asset["url"])
        if not release.allowed_download_url(asset["url"]):
            record(False, "资产 URL 主机不在允许列表")
            return 1

        if args.asset_path:
            local = Path(args.asset_path)
            if not local.is_file():
                print("[verify] 本地资产不存在：" + str(local))
                return 1
            problems = verify_asset_file(local, asset, require_version_file=not args.legacy,
                                         expected_version=data["version"])
        elif args.manifest and not args.download:
            warn("本地清单模式未下载资产（加 --download 或 --asset-path 可校验资产）")
            problems = []
        else:
            temp_dir = Path(tempfile.mkdtemp(prefix="pl-verify-"))
            target = temp_dir / release.asset_name(data["tag"])
            try:
                download_asset(asset["url"], target, asset, args.timeout)
                problems = verify_asset_file(target, asset, require_version_file=not args.legacy,
                                             expected_version=data["version"])
            finally:
                if not args.keep:
                    try:
                        target.unlink(missing_ok=True)
                        temp_dir.rmdir()
                    except OSError:
                        pass
    except VerifyError as exc:
        record(False, str(exc))
        problems = []
    except SystemExit:
        raise
    except urllib.error.URLError as exc:
        record(False, "网络错误：" + str(exc.reason if hasattr(exc, "reason") else exc))
        problems = []

    for item in problems:
        if not any(item == message for _state, message in RESULTS):
            record(False, item)

    failures = [message for state, message in RESULTS if state == "FAIL"]
    print("\n[verify] 结果：%d 项通过 / %d 项失败 / %d 项警告，耗时 %.1fs"
          % (sum(1 for state, _ in RESULTS if state == "PASS"), len(failures),
             sum(1 for state, _ in RESULTS if state == "WARN"), time.time() - started))
    if failures:
        print("[verify] 发布校验未通过")
        return 1
    print("[verify] 发布校验通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
