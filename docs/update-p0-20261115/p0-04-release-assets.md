# P0-04 Release 资产、稳定地址与发布后校验

日期：2026-11-15　阶段 0 / P0-04　状态：脚本与规范已完成；"新 Release 带清单"因版本号/发布授权待确认而阻塞

## 0. 更新（2026-11-15）

v1.7.1 已按本步规范发布，P0-04 的稳定地址验收在发布后通过；见 `v1.7.1-release.md`。

## 1. 交付物

| 交付物 | 位置 |
|---|---|
| 资产命名/稳定地址/zip 校验规则 | `paperlib/update/release.py` |
| 发布后校验脚本（稳定地址 + 清单验签 + 资产大小/SHA-256 + zip 内容） | `tools/verify_release.py` |
| 发布流程与上传步骤（人工步骤，P0-06 起脚本化） | `RELEASING.md` |
| 测试（14 条：命名/URL/允许主机/zip 约束/离线 CLI） | `test/test_update_release.py` |

## 2. 关键事实（实测，非推断）

- **仓库迁移**：`yijiey53-svg/paper-library-app` 301 到
  **`PaperLibrary-Developers/PaperLibrary`**（公开仓库，`gh api` 返回 `full_name`）。
  规范地址与文档已统一使用新名。
- **资产重定向主机**：`github.com`(301) → `github.com`(302) →
  `release-assets.githubusercontent.com`(200)。原文示例 `objects.githubusercontent.com` 未观测到。
  `tools/verify_release.py` 对手动跟随的每一跳校验允许主机（https + 白名单）。
- **稳定地址机制可用**：`releases/latest/download/<asset>` 对已有 v1.6 zip 返回 200/302 链；
  `latest/download/update-manifest.json` 当前 **404**（v1.6 Release 没有该资产）。
- **v1.6 资产真实校验通过**：匿名下载 45,625,893 字节，大小与 SHA-256
  `0952a533…28ee70` 与已知值一致；zip 无顶层 `data/`、无绝对路径/上跳、含 `PaperLibrary.exe`。
  v1.6 zip 是 P0-01 之前构建的，缺 `version.json`（告警，不判失败；新包必须包含）。

## 3. 本步实际验证（命令与结果）

### 3.1 真实资产匿名下载 + 校验（PASS）

```
python tools/verify_release.py --manifest shared/fixtures/update-manifest.signed.json --download --legacy
```

- 清单结构/验签通过（keyId=e777a2f6）
- 资产匿名下载成功（45,625,893 字节，重定向主机在允许列表内）
- 大小与清单一致、SHA-256 与清单一致
- zip：无顶层 data/、无绝对路径/上跳、含 PaperLibrary.exe；缺 version.json → WARN（历史资产）
- 汇总：6 项通过 / 0 项失败 / 1 项警告，耗时 13.5s

### 3.2 稳定地址清单当前状态（FAIL，预期）

```
python tools/verify_release.py
```

- `GET .../releases/download/v1.6/update-manifest.json → HTTP 404`（latest 指向 v1.6，该 Release 无清单）
- 退出码 1，说明脚本能识别"发布流程未完成"，不会把缺清单当成正常。

## 4. 验收标准对照

| P0-04 验收项 | 状态 | 说明 |
|---|---|---|
| 匿名环境 GET `latest/download/update-manifest.json` | **已通过（v1.7.1）** | 2026-11-15 发布 v1.7.1 后实测 200；本步当时因版本号/发布授权未执行 |
| manifest 内 URL/size/hash 与实际资产一致 | 已验证 | 用真实 v1.6 资产按同规则验证（URL/200/大小/SHA-256） |
| zip 内无 `data/` | 已验证 | v1.6 真实资产 + 本地新构建包均通过 |
| 资产命名规范/上传步骤/校验脚本 | 已完成 | `RELEASING.md` + `paperlib/update/release.py` + `tools/verify_release.py` |

**后续（2026-11-15 已完成）**：版本号确定为 v1.7.1，重新构建并发布；`latest/download/update-manifest.json`
匿名 200，清单/资产/大小/SHA-256 与真实下载一致（发布记录见 `docs/update-p0-20261115/v1.7.1-release.md`）。

## 5. 测试与构建

| 项目 | 命令 | 结果 |
|---|---|---|
| 资产/CLI 测试 | `python -m unittest test.test_update_release -v` | 14 tests OK |
| 全量测试 | `python -m unittest discover -s test` | **505 tests OK**（P0-03 后 491 + 新增 14） |
| 发布构建 | `python tools/release.py` | 成功；zip 校验（无 data/、含 exe/version.json）通过 |
| 版本一致性 | `python tools/version_check.py --package <新包>` | 通过 |
