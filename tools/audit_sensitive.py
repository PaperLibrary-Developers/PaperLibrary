r"""公开前敏感信息审计：工作区、完整 git 历史、发布 zip。

用法：
    python tools/audit_sensitive.py                       # 工作区 + 全部 git 历史
    python tools/audit_sensitive.py --zip <a.zip> [--zip ...]
    python tools/audit_sensitive.py --json out.json       # 机器可读结果（同样脱敏）

规则（命中只报告类型/位置/脱敏片段，**不打印完整密钥**）：
- HIGH：常见服务密钥格式（OpenAI/Anthropic sk-、GitHub token、AWS、Google、Slack、
  私钥块、JWT）、签名私钥 seed；
- MEDIUM：疑似凭据赋值（api_key/secret/password/token = "..."）、本机用户目录绝对路径。

退出码：0 = 无 HIGH（MEDIUM 仅提示）；1 = 存在 HIGH。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SKIP_DIRS = {".git", "runtime", "vendor", "node_modules", "__pycache__", "data", "dist",
             ".archify", ".workbuddy", ".pytest_cache"}
TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".mjs", ".js", ".css", ".html", ".ps1", ".psm1",
                 ".yml", ".yaml", ".toml", ".ini", ".cfg", ".config", ".xml", ".sh", ".bat",
                 ".cmd", ".csv", ".log", ".gitignore", ""}
MAX_BYTES = 5 * 1024 * 1024

HIGH_PATTERNS = [
    ("openai_or_anthropic_key", re.compile(rb"\bsk-[A-Za-z0-9_-]{16,}")),
    ("github_token", re.compile(rb"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})")),
    ("aws_access_key", re.compile(rb"\bAKIA[0-9A-Z]{16}\b")),
    ("google_api_key", re.compile(rb"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("slack_token", re.compile(rb"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("private_key_block", re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("jwt", re.compile(rb"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
MEDIUM_PATTERNS = [
    ("credential_assignment", re.compile(
        rb"(?i)(?:api[_-]?key|apikey|secret|password|passwd|access[_-]?token)\s*[:=]\s*[\"'][^\"'\r\n]{8,}[\"']")),
    ("local_user_path", re.compile(rb"[A-Za-z]:[\\/]Users[\\/][^\\/\r\n\"'<>|]{2,}")),
]
ALL_PATTERNS = [(kind, pattern, "HIGH") for kind, pattern in HIGH_PATTERNS] + \
               [(kind, pattern, "MEDIUM") for kind, pattern in MEDIUM_PATTERNS]

# 第三方构建产物（早期提交把便携 runtime/site-packages 提交进过历史）：
# 里面大量示例字符串是上游测试向量，扫描会产生假阳性；只记录数量，不做内容判定。
THIRD_PARTY_PREFIXES = ("runtime/", "vendor/")

# 绝不能出现在公开仓库里的用户数据/配置路径（工作区受控文件 + 全部历史）
USER_DATA_RULES = [
    ("user_library", re.compile(r"(?:^|/)data/library\.json$")),
    ("user_prefs", re.compile(r"(?:^|/)data/prefs\.json$")),
    ("user_settings", re.compile(r"(?:^|/)(?:data/)?settings\.json$")),
    ("user_database", re.compile(r"(?:^|/)(?:library|papers)\.db(?:-wal|-shm)?$")),
    ("user_backups", re.compile(r"(?:^|/)data/backups/")),
    ("user_fulltext", re.compile(r"(?:^|/)data/fulltext/")),
    ("user_logs", re.compile(r"(?:^|/)data/server\.log$")),
]


def user_data_hits(rel_path: str):
    return [kind for kind, pattern in USER_DATA_RULES if pattern.search(rel_path)]


def redact(raw: bytes) -> str:
    text = raw.decode("utf-8", "replace")
    if len(text) <= 10:
        return text[:2] + "…"
    return text[:6] + "…（已脱敏，长度 %d）" % len(text)


def scan_bytes(raw: bytes, where: str, findings: list):
    for kind, pattern, severity in ALL_PATTERNS:
        for match in pattern.finditer(raw):
            findings.append({"severity": severity, "kind": kind, "where": where,
                             "snippet": redact(match.group(0))})


def is_texty(path: Path) -> bool:
    suffix = path.suffix.lower()
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".dll", ".exe", ".pyd",
                  ".zip", ".pdf", ".woff", ".woff2", ".ttf", ".db", ".db-wal", ".db-shm"}:
        return False
    return suffix in TEXT_SUFFIXES or suffix == ""


def scan_worktree(findings: list):
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(name for name in dirnames if name not in SKIP_DIRS)
        for name in filenames:
            path = Path(dirpath) / name
            if not is_texty(path):
                continue
            try:
                if path.stat().st_size > MAX_BYTES:
                    continue
                raw = path.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:4096]:
                continue
            scan_bytes(raw, "worktree:" + path.relative_to(ROOT).as_posix(), findings)
            scanned += 1
    return scanned


def _git(*args, **kwargs):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, **kwargs)


def scan_tracked_paths(findings: list):
    """受控文件里不得有用户数据/配置路径（即使当前工作区看不到）。"""
    listed = _git("ls-files", text=True, encoding="utf-8", errors="replace")
    if listed.returncode != 0:
        return 0
    hits = 0
    for path in listed.stdout.splitlines():
        for kind in user_data_hits(path):
            findings.append({"severity": "HIGH", "kind": "user_data_tracked:" + kind,
                             "where": "tracked:" + path, "snippet": "受控文件"})
            hits += 1
    return hits


def scan_history(findings: list):
    """扫描全部可达历史里的 blob（不 checkout，不改工作区）。"""
    listed = _git("rev-list", "--objects", "--all", text=True, encoding="utf-8", errors="replace")
    if listed.returncode != 0:
        raise SystemExit("git rev-list 失败：" + (listed.stderr or ""))
    blobs = {}
    for line in listed.stdout.splitlines():
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        sha, path = parts
        if path.endswith("/"):  # tree
            continue
        # 用户数据路径命中：内容扫描之前先按路径判定（这是"数据进过历史"的直接证据）
        for kind in user_data_hits(path):
            findings.append({"severity": "HIGH", "kind": "user_data_in_history:" + kind,
                             "where": "history:" + path + "@" + sha[:10],
                             "snippet": path})
        blobs.setdefault(sha, path)
    if not blobs:
        return 0
    proc = subprocess.Popen(["git", "cat-file", "--batch"], cwd=str(ROOT),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    scanned = 0
    third_party = 0
    try:
        for sha, path in blobs.items():
            proc.stdin.write((sha + "\n").encode())
            proc.stdin.flush()
            header = proc.stdout.readline().decode("utf-8", "replace").split()
            if len(header) < 3:
                continue
            object_type = header[1]
            size = int(header[2])
            # batch 协议对任何对象都会跟 <size> 字节内容 + 换行，必须始终消费，否则协议错位
            data = proc.stdout.read(size)
            proc.stdout.read(1)
            if object_type != "blob" or size > MAX_BYTES or b"\x00" in data[:4096]:
                continue
            if path.startswith(THIRD_PARTY_PREFIXES):
                third_party += 1
                continue
            scan_bytes(data, "history:%s@%s" % (path, sha[:10]), findings)
            scanned += 1
    finally:
        try:
            proc.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        proc.wait(timeout=60)
    print("[audit] 第三方 runtime/vendor blob（仅计数，不做内容判定）：%d 个" % third_party)
    return scanned


def scan_zip(path: Path, findings: list):
    """扫描发布资产：用户数据目录/配置/密钥/本机路径。"""
    path = Path(path)
    problems = []
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            data_entries = [name for name in names
                            if name == "data" or name.startswith("data/")]
            if data_entries:
                problems.append("%s 含用户数据目录：%s" % (path.name, "、".join(data_entries[:5])))
            for name in names:
                base = name.rsplit("/", 1)[-1]
                if base in {"settings.json", "prefs.json", "sources.json", "library.db",
                            "server.runtime.json"}:
                    problems.append("%s 含用户配置文件/数据库：%s" % (path.name, name))
            for info in zf.infolist():
                if info.is_dir() or info.file_size > MAX_BYTES:
                    continue
                suffix = Path(info.filename).suffix.lower()
                if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".dll", ".exe",
                              ".pyd", ".pdf", ".woff", ".woff2", ".ttf"}:
                    continue
                try:
                    raw = zf.read(info)
                except (OSError, RuntimeError, zipfile.BadZipFile):
                    continue
                if b"\x00" in raw[:4096]:
                    continue
                scan_bytes(raw, "zip:%s!/%s" % (path.name, info.filename), findings)
    except (OSError, zipfile.BadZipFile) as exc:
        problems.append("%s 不是可读 zip：%s" % (path.name, exc))
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description="公开前敏感信息审计（工作区 / git 历史 / 发布 zip）")
    parser.add_argument("--zip", action="append", default=[], help="额外扫描的发布 zip（可重复）")
    parser.add_argument("--no-history", action="store_true", help="跳过 git 历史扫描")
    parser.add_argument("--json", default="", help="把结果写入 JSON（同样脱敏）")
    args = parser.parse_args(argv)

    findings: list = []
    zip_problems: list = []
    scanned = scan_worktree(findings)
    print("[audit] 工作区文本文件：%d 个" % scanned)
    tracked_hits = scan_tracked_paths(findings)
    print("[audit] 受控文件里的用户数据路径：%d 处" % tracked_hits)
    history_count = 0
    if not args.no_history:
        history_count = scan_history(findings)
        print("[audit] git 历史 blob：%d 个" % history_count)
    for item in args.zip:
        zip_problems.extend(scan_zip(Path(item), findings))
        print("[audit] 已扫描 zip：%s" % item)

    high = [item for item in findings if item["severity"] == "HIGH"]
    medium = [item for item in findings if item["severity"] == "MEDIUM"]
    for label, items in (("HIGH", high), ("MEDIUM", medium)):
        for item in items:
            print("[audit][%s] %s @ %s :: %s" % (label, item["kind"], item["where"], item["snippet"]))
    for item in zip_problems:
        print("[audit][HIGH] zip 用户数据/配置：" + item)

    if args.json:
        Path(args.json).write_text(json.dumps({
            "scannedWorktreeFiles": scanned, "scannedHistoryBlobs": history_count,
            "zips": args.zip, "zipProblems": zip_problems,
            "findings": findings,
        }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n[audit] 汇总：HIGH %d / MEDIUM %d / zip 问题 %d"
          % (len(high), len(medium), len(zip_problems)))
    if high or zip_problems:
        print("[audit] 存在必须处理的问题（公开前修复或下架）")
        return 1
    print("[audit] 未发现硬编码密钥/用户数据；MEDIUM 项需人工确认是否为示例或本机路径")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
