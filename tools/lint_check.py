"""轻量零依赖 Python 静态检查（离线便携环境与本地开发均直接可用）。

与 pyproject.toml 中 [tool.ruff.lint] 保持同口径：
- 语法错误（SyntaxError）
- 裸 except（E722）
- 重复字典字面量键（F601）
- 与 None / bool 的 == / != 比较（E711 / E712）
- 顶层未用导入（F401，尊重 # noqa 与 __all__）

用法：
  python -B tools/lint_check.py
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGETS = ("paperlib", "shared", "tools")
NOQA_RE = re.compile(r"#\s*noqa\b(.*)", re.IGNORECASE)


def _has_noqa(lines: list[str], lineno: int, code: str) -> bool:
    if 1 <= lineno <= len(lines):
        match = NOQA_RE.search(lines[lineno - 1])
        if match:
            rest = match.group(1).strip()
            if not rest or rest.startswith("#") or code in rest:
                return True
    return False


def check_file(path: Path) -> list[tuple[int, str, str]]:
    src = path.read_text(encoding="utf-8-sig")
    lines = src.splitlines()
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:
        return [(exc.lineno or 1, "E999", f"SyntaxError: {exc.msg}")]

    issues: list[tuple[int, str, str]] = []
    exported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                        for elt in node.value.elts:
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                exported.add(elt.value)

    loaded: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            loaded.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.type is None:
            if not _has_noqa(lines, node.lineno, "E722"):
                issues.append((node.lineno, "E722", "bare except"))
        elif isinstance(node, ast.Dict):
            seen: set[tuple[type, object]] = set()
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, (str, int, float, bool, bytes)):
                    token = (type(key.value), key.value)
                    if token in seen and not _has_noqa(lines, key.lineno, "F601"):
                        issues.append((key.lineno, "F601", f"duplicate dict key {key.value!r}"))
                    seen.add(token)
        elif isinstance(node, ast.Compare):
            for op, comp in zip(node.ops, node.comparators):
                if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(comp, ast.Constant):
                    if comp.value is None and not _has_noqa(lines, node.lineno, "E711"):
                        issues.append((node.lineno, "E711", "comparison to None (use 'is' / 'is not')"))
                    elif isinstance(comp.value, bool) and not _has_noqa(lines, node.lineno, "E712"):
                        issues.append((node.lineno, "E712", f"comparison to {comp.value!r} (use 'is')"))

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                if bound in exported or bound in loaded or _has_noqa(lines, node.lineno, "F401"):
                    continue
                issues.append((node.lineno, "F401", f"unused import '{bound}'"))
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                if bound in exported or bound in loaded or _has_noqa(lines, node.lineno, "F401"):
                    continue
                issues.append((node.lineno, "F401", f"unused import '{bound}' from '{node.module}'"))

    return sorted(issues)


def main(argv: list[str] | None = None) -> int:
    roots = argv if argv else list(TARGETS)
    checked = 0
    total = 0
    for item in roots:
        target = Path(item)
        if not target.is_absolute():
            target = ROOT / target
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        for path in files:
            checked += 1
            for lineno, code, message in check_file(path):
                rel = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)
                print(f"{rel}:{lineno}: {code} {message}")
                total += 1
    if total:
        print(f"[lint_check] 发现 {total} 处问题（共扫描 {checked} 个 Python 文件）", file=sys.stderr)
        return 1
    print(f"[lint_check] 通过：共扫描 {checked} 个 Python 文件，0 处告警")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
