r"""发布流程编排（阶段 0 / P0-06，方案一原文 §10）。

一条命令产出全部发布资产并在发布后校验：

    python tools/update_publish.py --build --min-windows 10.0.17763
        # 测试 → 构建 → zip → SHA-256 → 签名 update-manifest.json → SHA256SUMS
        # 记录 dist/release-<tag>.json → 用 verify_release 离线校验（默认不发版）

    python tools/update_publish.py --build --min-windows 10.0.17763 --publish --yes
        # 额外：检查工作区干净/tag 不存在 → 建 tag 推送 → gh release create 上传
        #       → 用稳定地址 + 真实下载做发布后校验

其他用法：
    python tools/update_publish.py --set-version 1.7.0 --build ...   # 先改 version.json（需另行提交）
    python tools/update_publish.py --zip dist/x.zip --min-windows ... --no-build
    python tools/update_publish.py ... --key <私钥文件> --pubkey <公钥base64>  # 轮换/测试用

安全与约束：
- 私钥只从 %USERPROFILE%\.paperlibrary\update-signing-key.json 或 PAPERLIB_UPDATE_SIGNING_KEY 读取，
  不打印、不落盘；内置公钥与签名私钥必须匹配（除非显式 --pubkey 测试覆盖）。
- 默认不推送、不算发布：必须同时给 --publish --yes；拒绝 tag 已存在、拒绝脏工作区。
- 发布后校验失败即返回非 0（tag/Release 已建，不自动回滚，需人工处理并记录）。
- minWindows 是产品决定（待确认项），不写默认值：必须 --min-windows 或 PAPERLIB_MIN_WINDOWS。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperlib.update import ed25519, keys, manifest, release, signing  # noqa: E402
from paperlib.version import (VersionError,  # noqa: E402
                              load as load_version, ui_label, validate as validate_version)

TAG_RE = re.compile(r"^v\d+\.\d+(?:\.\d+)?$")


class PublishError(RuntimeError):
    pass


def log(message: str):
    print("[publish] " + message)


def display_path(path) -> str:
    """日志里的路径：仓库内用相对路径，仓库外（测试/自定义输出目录）用绝对路径。"""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def run(cmd, timeout=1800, capture=True):
    # 子进程统一用 UTF-8 输出，避免在中文 Windows 下以 cp936 写管道导致父进程解码/再编码失败
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=capture, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return proc


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def set_version(value: str) -> dict:
    try:
        info = validate_version({"product": "PaperLibrary", "version": value, "tag": ui_label(value)})
    except VersionError as exc:
        raise PublishError("版本号不合法：" + str(exc))
    path = ROOT / "version.json"
    path.write_text(json.dumps({key: info[key] for key in ("product", "version", "tag")},
                               ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    log("已更新 version.json：%s（%s）；请提交后再发布" % (info["tag"], info["version"]))
    return info


def run_tests():
    log("运行测试：python -m unittest discover -s test")
    proc = run([sys.executable, "-m", "unittest", "discover", "-s", "test"])
    tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-3:]
    if proc.returncode != 0:
        log("测试失败：\n" + "\n".join(tail))
        raise PublishError("测试未通过，停止发布")
    log("测试通过：" + (tail[-1] if tail else "OK"))


def build_package() -> Path:
    log("构建发布包：python tools/release.py")
    started = time.time()
    proc = run([sys.executable, "tools/release.py"])
    if proc.returncode != 0:
        log("构建失败：\n" + ((proc.stdout or "") + (proc.stderr or ""))[-800:])
        raise PublishError("构建失败")
    dist = ROOT / "dist"
    candidates = [path for path in dist.iterdir()
                  if path.is_dir() and path.name.startswith("PaperLibrary-")
                  and path.stat().st_mtime >= started - 5]
    if not candidates:
        raise PublishError("构建完成但找不到新产物目录")
    target = max(candidates, key=lambda path: path.stat().st_mtime)
    log("构建产物：" + str(target.relative_to(ROOT)))
    return target


def package_zip(package: Path, out_zip: Path) -> Path:
    log("打包 zip：python tools/package_zip.py --src <包> --out " + display_path(out_zip))
    proc = run([sys.executable, "tools/package_zip.py", "--src", str(package),
                "--out", str(out_zip)])
    if proc.returncode != 0:
        log("打包失败：\n" + ((proc.stdout or "") + (proc.stderr or ""))[-800:])
        raise PublishError("打包失败")
    return out_zip


def check_zip(zip_path: Path, version_info: dict):
    problems, warnings = release.check_zip(zip_path, expected_version=version_info["version"],
                                           require_version_file=True)
    for warning in warnings:
        log("警告：" + warning)
    if problems:
        for problem in problems:
            log("zip 校验失败：" + problem)
        raise PublishError("完整包不符合发布约束")


def build_manifest(version_info: dict, zip_path: Path, min_windows: str,
                   published_at: str = "") -> dict:
    data = {
        "schema": manifest.MANIFEST_SCHEMA,
        "product": manifest.PRODUCT,
        "channel": manifest.CHANNEL,
        "version": version_info["version"],
        "tag": version_info["tag"],
        "publishedAt": published_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "releaseNotesUrl": "https://github.com/%s/releases/tag/%s" % (release.REPO, version_info["tag"]),
        "minWindows": str(min_windows).strip(),
        "arch": manifest.ARCH,
        "assets": {"full": {
            "url": release.release_asset_url(version_info["tag"]),
            "size": zip_path.stat().st_size,
            "sha256": sha256_file(zip_path),
        }},
    }
    # 先按 schema 校验（不含签名），再交给 signing 签名
    manifest.validate(data, require_signature=False)
    return data


def sign_manifest(data: dict, seed: bytes, expected_public: bytes = b"") -> dict:
    public = ed25519.public_key(seed)
    if expected_public and public != expected_public:
        raise PublishError("签名私钥与 expected pubkey 不匹配（keyId=%s vs %s）"
                           % (signing.key_id(public), signing.key_id(expected_public)))
    signed = signing.sign_manifest(data, seed)
    signing.verify_manifest(signed, public)
    return signed


def write_sums(out_dir: Path, paths) -> Path:
    lines = []
    for path in paths:
        lines.append("%s  %s" % (sha256_file(path), path.name))
    sums = out_dir / "SHA256SUMS"
    sums.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sums


def offline_verify(manifest_path: Path, zip_path: Path, pubkey_b64: str = ""):
    log("离线校验：清单结构/验签 + 资产大小/SHA-256 + zip 内容")
    cmd = [sys.executable, "tools/verify_release.py", "--manifest", str(manifest_path),
           "--asset-path", str(zip_path)]
    if pubkey_b64:
        cmd += ["--pubkey", pubkey_b64]
    proc = run(cmd)
    for line in ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines():
        log("  " + line.strip())
    if proc.returncode != 0:
        raise PublishError("本地发布校验未通过")


def tag_exists(tag: str) -> str:
    local = run(["git", "tag", "-l", tag])
    if (local.stdout or "").strip():
        return "本地已存在 tag " + tag
    remote = run(["git", "ls-remote", "--tags", "origin", "refs/tags/" + tag])
    if remote.returncode == 0 and (remote.stdout or "").strip():
        return "远端已存在 tag " + tag
    return ""


def require_clean_tree():
    proc = run(["git", "status", "--porcelain=v1", "--untracked-files=normal"])
    if (proc.stdout or "").strip():
        raise PublishError("工作区不干净（发布要求已提交的 version.json 与源码）：\n" + proc.stdout)
    if run(["git", "diff", "--quiet", "HEAD", "--", "version.json"]).returncode != 0:
        raise PublishError("version.json 未提交到 HEAD，先提交版本变更")


def publish(tag: str, assets, notes_file: str, pubkey_b64: str):
    log("发布：tag=%s（需要工作区干净、tag 不存在）" % tag)
    require_clean_tree()
    reason = tag_exists(tag)
    if reason:
        raise PublishError(reason)
    commit = (run(["git", "rev-parse", "HEAD"]).stdout or "").strip()
    log("tag 将指向 %s" % commit[:12])
    # 先推送当前分支，保证 main 与 tag/Release 指向同一份代码
    branch = (run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).stdout or "").strip()
    if branch and branch != "HEAD":
        proc = run(["git", "push", "origin", branch])
        if proc.returncode != 0:
            raise PublishError("推送分支 %s 失败：%s" % (branch, (proc.stderr or proc.stdout or "")[-400:]))
        log("已推送分支 " + branch)
    proc = run(["git", "tag", "-a", tag, "-m", "PaperLibrary %s" % tag])
    if proc.returncode != 0:
        raise PublishError("创建 tag 失败：" + (proc.stderr or ""))
    proc = run(["git", "push", "origin", tag])
    if proc.returncode != 0:
        raise PublishError("推送 tag 失败：" + ((proc.stderr or proc.stdout or ""))[-400:])
    cmd = ["gh", "release", "create", tag] + [str(item) for item in assets]
    cmd += ["--title", "PaperLibrary %s Nexus" % tag]
    if notes_file:
        cmd += ["--notes-file", notes_file]
    else:
        cmd += ["--notes", "PaperLibrary %s Nexus" % tag]
    proc = run(cmd, timeout=900)
    if proc.returncode != 0:
        raise PublishError("创建 GitHub Release 失败：" + ((proc.stderr or proc.stdout or ""))[-800:])
    log("Release 已创建；开始发布后校验（稳定地址 + 真实下载）")
    verify = [sys.executable, "tools/verify_release.py", "--manifest", str(assets[1]),
              "--latest", "--download"]
    if pubkey_b64:
        verify += ["--pubkey", pubkey_b64]
    proc = run(verify, timeout=1800)
    for line in ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines():
        log("  " + line.strip())
    if proc.returncode != 0:
        raise PublishError("发布后校验失败（Release 已创建，请人工确认并记录）")
    log("发布后校验通过")


def main(argv=None):
    parser = argparse.ArgumentParser(description="发布流程编排（构建/签名/上传/校验）")
    parser.add_argument("--set-version", default="", help="先写入 version.json（x.y.z）")
    parser.add_argument("--zip", default="", help="复用已有 zip（跳过构建与打包）")
    parser.add_argument("--build", action="store_true", help="构建（默认行为；显式写出便于阅读）")
    parser.add_argument("--no-build", action="store_true", help="不构建（配合 --zip 使用）")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--min-windows", default=os.environ.get("PAPERLIB_MIN_WINDOWS", ""),
                        help="最低 Windows 版本（x.y.z，必填；产品决定项）")
    parser.add_argument("--out", default="dist", help="资产输出目录（默认 dist）")
    parser.add_argument("--key", default="", help="签名私钥文件（默认本机路径或环境变量）")
    parser.add_argument("--pubkey", default="", help="验签公钥 base64（测试/轮换覆盖）")
    parser.add_argument("--notes-file", default="", help="Release 说明 Markdown")
    parser.add_argument("--publish", action="store_true", help="真正推送 tag 并创建 Release")
    parser.add_argument("--yes", action="store_true", help="与 --publish 一起使用，确认执行")
    args = parser.parse_args(argv)

    # 控制台编码兜底：日志里若出现无法编码的字符，替换而不是让发布流程失败
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass

    try:
        if args.set_version:
            set_version(args.set_version)
            # 只改版本号（未要求构建/复用 zip/发布）时到此为止，便于先提交版本再发布
            if not (args.build or args.zip or args.publish):
                return 0
        version_info = load_version(ROOT)
        tag = version_info["tag"]
        if not args.min_windows.strip():
            raise PublishError("缺少 --min-windows（或 PAPERLIB_MIN_WINDOWS）："
                               "最低 Windows 版本是产品决定项，见待确认清单")
        if not TAG_RE.match(tag):
            raise PublishError("version.json 的 tag 非法：" + tag)

        out_dir = (ROOT / args.out).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        asset_name = release.asset_name(tag)
        zip_path = out_dir / asset_name

        if args.zip:
            source = Path(args.zip).resolve()
            if not source.is_file():
                raise PublishError("指定的 zip 不存在：" + str(source))
            zip_path = out_dir / release.asset_name(tag)
            if source != zip_path:
                shutil.copyfile(source, zip_path)
                log("复用已有 zip：" + source.name + " → " + display_path(zip_path))
            else:
                log("复用已有 zip：" + source.name)
        else:
            if not args.skip_tests:
                run_tests()
            package = build_package()
            package_zip(package, zip_path)
        check_zip(zip_path, version_info)

        manifest_data = build_manifest(version_info, zip_path, args.min_windows)
        seed = signing.load_private_key(args.key)
        expected_public = base64.b64decode(args.pubkey, validate=True) if args.pubkey else keys.public_key()
        signed = sign_manifest(manifest_data, seed, expected_public)
        manifest_path = out_dir / release.MANIFEST_NAME
        manifest_path.write_bytes(manifest.canonical_bytes(signed))
        log("清单已签名并写入：" + display_path(manifest_path)
            + "（keyId=%s）" % signing.key_id(expected_public))

        sums = write_sums(out_dir, [zip_path, manifest_path])
        record = {
            "publishedAt": manifest_data["publishedAt"],
            "product": version_info["product"],
            "version": version_info["version"],
            "tag": tag,
            "asset": zip_path.name,
            "size": zip_path.stat().st_size,
            "sha256": sha256_file(zip_path),
            "manifestSha256": sha256_file(manifest_path),
            "keyId": signing.key_id(expected_public),
            "commit": (run(["git", "rev-parse", "HEAD"]).stdout or "").strip(),
            "publish": bool(args.publish),
        }
        record_path = out_dir / ("release-%s.json" % tag)
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        offline_verify(manifest_path, zip_path, args.pubkey)

        log("资产就绪：")
        log("  zip      %s（%d 字节，sha256 %s…）" % (zip_path.name, record["size"], record["sha256"][:12]))
        log("  manifest %s" % manifest_path.name)
        log("  sums     %s" % sums.name)
        log("  record   %s" % record_path.name)
        if not args.publish:
            log("未发布。确认无误后执行：")
            log("  python tools/update_publish.py --zip %s --min-windows %s --publish --yes"
                % (zip_path, args.min_windows))
            return 0
        if not args.yes:
            raise PublishError("--publish 需要同时给 --yes 确认")
        publish(tag, [zip_path, manifest_path, sums, record_path], args.notes_file, args.pubkey)
        return 0
    except PublishError as exc:
        log("失败：" + str(exc))
        return 1
    except (manifest.ManifestError, signing.SigningError, VersionError, ValueError) as exc:
        log("失败：" + str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
