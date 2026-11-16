# P2-20 发布验收（旧客户端真实走一遍）

日期：2026-11-17　阶段 2G / P2-20　状态：真实闭环通过；线上发布动作未执行

## 1. 冲突与执行方式

用户要求不推送、不打 tag、不发 Release，而 P2-20 原验收要求“旧版本客户端真实走一遍
latest/download”。本次采用**不违反发布约束的真实闭环**：

- 旧客户端：用当前代码构建完整包，仅把 `version.json` 改为 **1.6.0/v1.6**，
  部署包外 helper 后作为旧客户端；
- 更新源：真实 GitHub 公开 Release `v1.7.1`（`latest/download`，官方 zip/manifest）；
- 因此“检查→下载→立即安装→自动重开”走的是真实公开资产与真实 helper，
  但“历史上真实发布的 v1.6 客户端”与“当前 30 个提交的新 Release”尚未发布；
  线上发布验收需在获准 push/tag/release 后补做一次。

## 2. 执行记录

    python tools/release_acceptance.py --package <旧 1.6.0 包> --home <helper home>
    OLD_VERSION 1.6.0 / v1.6
    OLD_EXIT 0
    NEW_VERSION 1.7.1 / v1.7.1
    elapsedSeconds 31.6
    dataDb true

- 设置页：检查到 v1.7.1 → 下载官方 zip → 状态 ready → 点击“立即安装” → 确认；
- 启动器退出码 0；helper 完成 1.6.0→1.7.1；健康检查 2 次连续成功，7 秒；
- 新客户端 `/api/version` = 1.7.1（tag v1.7.1，commit c9aa3268）。

## 3. 证据

截图：`docs/update-p2-20261116/p2-20-shots/`

| 文件 | 内容 |
|---|---|
| `01-old-version.png` | 旧客户端 v1.6 设置页 |
| `02-found.png` | 发现新版本 v1.7.1 |
| `03-ready.png` | 下载/校验完成，待安装 1.7.1 |
| `04-confirm.png` | 立即安装确认框（立即关闭/自动重开/data 保留/AI 中断/不静默） |
| `05-closing.png` | 点击确认后关闭前状态 |
| `06-new-version.png` | 自动重开后的 v1.7.1 主界面 |
| `results.json` | 版本、退出码、耗时、dataDb |
| `p2-20-install-log.txt` | journal/备份/替换/健康/state 日志 |

日志摘要：`backup-data` 4 files（library.db 139264 B）、`backup-program` 878 files、
`replace-program` 714 files / 99436397 B、health 7s、state success。
数据：`data/library.db` 保留；备份 `data/updates/backups/app-1.6.0` 存在；
staging topLevel 不含 data；apply 成功清理。

## 4. 验收对照

| P2-20 验收项 | 结果 | 证据 |
|---|---|---|
| 真实环境完整闭环成功 | PASS（真实 GitHub 资产） | 1.6.0→1.7.1，31.6s，old exit 0，new health 1.7.1 |
| 点击后应用主动关闭并自动重开 | PASS | launcher exit 0；新 `/api/version` 1.7.1；06 截图 |
| 失败回滚成功 | PASS（分步测试） | P2-13/16/17 回滚测试；本轮未制造线上失败 |
| 数据完整 | PASS | dataDb true；library.db 备份；app-1.6.0 备份 |
| 无静默安装 | PASS | 必须点击“立即安装并关闭”；确认框截图 |
| 成功/失败下次启动提示 | PASS（成功路径） | state success；P2-10R notice 测试；失败提示由 P2-10R/P2-19 |
| latest/download 匿名可用 | PASS | p2-19-whitelist.txt：HTTP 200，落点 release-assets |

## 5. 契约与代码

- 本步无产品代码改动；新增验收脚本：
  `tools/release_acceptance.py`、`tools/release_acceptance_cdp.mjs`、`tools/capture_current_page.mjs`；
- 契约沿用 `update-apply.md`/`update-install.md`，无变更；
- P2-19 已覆盖契约测试。

## 6. 遗留

- 线上“当前 30 个提交”的 Release 发布、真实历史客户端验收、失败回滚线上演练，
  需在获得 push/tag/release 授权后补做；
- 旧客户端为“当前代码 + 1.6.0 版本号”的合成包，非历史 v1.6 二进制；
- Authenticode 仍暂缓阶段 3。
