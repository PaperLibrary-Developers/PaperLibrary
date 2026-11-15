"""更新相关端点（阶段 1 只读检查；下载/安装留给阶段 2）。"""
from __future__ import annotations

from paperlib.update import check as check_mod
from paperlib.update import fetch as fetch_mod
from paperlib.version import VersionError


def check(ctx):
    """GET /api/update/check：只检查，不下载。

    契约：200 {ok, available, reason, current, latest, checkedAt, message?}；
    502 {ok:false, code: timeout|network|no-manifest|invalid-manifest|signature|
    manifest-too-large|redirect-host|bad-url, error}；
    500 {ok:false, error}（本地 version.json 损坏）。
    """
    try:
        result = ctx.services.update.check()
    except fetch_mod.FetchError as exc:
        return ctx.fail(502, exc.message, {"code": exc.code})
    except check_mod.CheckError as exc:
        return ctx.fail(502, exc.message, {"code": exc.code})
    except VersionError as exc:
        return ctx.fail(500, "读取当前版本失败：" + str(exc))
    ctx.json(200, result)
