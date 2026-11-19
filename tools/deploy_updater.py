"""把发布包内的 Updater Helper 部署到包外（P2-12 的部署脚本）。

用法：
    python tools/deploy_updater.py --package-root <包目录>
    python tools/deploy_updater.py --package-root <包目录> --home <包外目录> --json

默认写到 %LOCALAPPDATA%/PaperLibrary/updater；--home / PAPERLIB_UPDATER_HOME 仅用于
测试与排查（生产路径不依赖调用方传入）。成功时返回 0 并打印部署记录；
helper 缺失/元数据不符/哈希不符时返回 1，不留下半个可执行文件。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paperlib.update import helper as helper_mod  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--package-root", default=str(ROOT),
                        help="发布包目录（含 tools/updater/PaperLibraryUpdater.exe）")
    parser.add_argument("--home", default="",
                        help="包外 helper 根目录（默认 %%LOCALAPPDATA%%\\PaperLibrary\\updater）")
    parser.add_argument("--keep", type=int, default=helper_mod.DEFAULT_KEEP,
                        help="保留的 helper 版本目录数（至少保留当前与上一版本）")
    parser.add_argument("--preserve-newer", action="store_true",
                        help="保留已校验通过的更新版本 helper")
    parser.add_argument("--json", action="store_true", help="只输出 JSON 记录")
    args = parser.parse_args(argv)

    try:
        result = helper_mod.deploy(args.package_root,
                                   home=(args.home or None),
                                   keep=args.keep, preserve_newer=args.preserve_newer)
    except helper_mod.HelperError as exc:
        print("[deploy-updater] %s: %s" % (exc.code, exc.message), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0
    print("[deploy-updater] 版本 %s（%s）" % (result["version"], result["tag"]))
    print("[deploy-updater] 包外路径 " + result["helperPath"])
    print("[deploy-updater] SHA-256 " + result["sha256"])
    print("[deploy-updater] %s" % ("已部署" if result["deployed"] else "已是最新，无需复制"))
    if result.get("previousVersion"):
        print("[deploy-updater] 上一版本保留在 " + result["previousVersion"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
