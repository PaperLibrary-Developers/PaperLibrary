"""更新相关端点（阶段 2B：下载/状态/应用；阶段 1 的只读检查保留）。"""
from __future__ import annotations

from paperlib.update import check as check_mod
from paperlib.update import fetch as fetch_mod
from paperlib.update import service as service_mod
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


def status(ctx):
    """GET /api/update/status：只读当前下载/校验/staging 状态（不会启动任务）。

    契约：200 {ok, phase, running, version, progress:{downloadedBytes,totalBytes,percent},
    ready, stagingVersion, stagingPath, error, code, updatedAt, lastCheckAt}。
    phase: idle/checking/downloading/verifying/extracting/ready/failed/cancelled。
    """
    ctx.json(200, ctx.services.update.snapshot())


def download(ctx):
    """POST /api/update/download：启动下载/续传（后台执行下载→校验→staging）。

    契约：200 {ok, started, phase, running, version, progress, ready, stagingVersion,
    stagingPath, error, code, updatedAt, lastCheckAt}；
    400 {ok:false, code:"no-update"/..., error}（已是最新/无需下载）；
    502 {ok:false, code, error}（清单网络/签名/清单无效）；500（本地 version.json 损坏）。
    """
    try:
        snapshot = ctx.services.update.start_download()
    except service_mod.UpdateServiceError as exc:
        return ctx.fail(exc.status, exc.message, {"code": exc.code})
    except fetch_mod.FetchError as exc:
        return ctx.fail(502, exc.message, {"code": exc.code})
    except check_mod.CheckError as exc:
        return ctx.fail(502, exc.message, {"code": exc.code})
    except VersionError as exc:
        return ctx.fail(500, "读取当前版本失败：" + str(exc))
    ctx.json(200, snapshot)
