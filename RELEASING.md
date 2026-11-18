# PaperLibrary 发布流程（方案一：GitHub Releases 整包替换）

> 适用：阶段 0 及其后的每次公开发布。更新机制（检查/下载/立即安装/回滚/自动下载）已落地。
> 本文与 `shared/contracts/update-manifest.md`（清单契约）、`version.json`（唯一版本来源）配套。

## 1. 仓库与稳定地址

- 规范仓库：`https://github.com/PaperLibrary-Developers/PaperLibrary`（公开）
- 旧地址 `yijiey53-svg/paper-library-app` 会 301 到上者；客户端与新发布一律使用规范地址。
- 客户端只访问稳定地址（**不调用 api.github.com**，避开匿名 60 次/小时限制）：

      https://github.com/PaperLibrary-Developers/PaperLibrary/releases/latest/download/update-manifest.json

- 资产下载实测重定向：`github.com`(301→规范名) → `github.com`(302) → `release-assets.githubusercontent.com`(200)。
  下载实现的允许主机列表至少包含 `github.com`、`release-assets.githubusercontent.com`（最终列表待复核）。

## 2. 资产命名规范

每个公开 Release **必须**包含：

| 资产 | 命名 | 说明 |
|---|---|---|
| 完整包 | `PaperLibrary.<tag>.Nexus.zip`（如 `PaperLibrary.v1.6.Nexus.zip`） | 由 `tools/release.py` 构建 + `tools/package_zip.py` 打包；**不得包含 `data/`** |
| 更新清单 | `update-manifest.json` | schema=1，Ed25519 签名，见清单契约 |

可选：`SHA256SUMS`（一行一个 `<sha256>  <文件名>`）、`release-notes-<tag>.md`（更新说明）。

硬约束：

- 版本号只在 `version.json` 维护；`tag` 规则：patch=0 用 `v<major>.<minor>`（如 `v1.6`），
  patch>0 用完整 `v<major>.<minor>.<patch>`（如 `v1.7.1`），与 zip 名一致。
- zip 内不得有用户 `data/`、设置、API Key、本机路径；根目录必须有 `PaperLibrary.exe`（新包还要有 `version.json`）。
- 私钥（`%USERPROFILE%\.paperlibrary\update-signing-key.json` 或 CI secret）**绝不**上传或打进包。

## 3. 发布步骤（P0-06 起脚本化）

发布脚本：`tools/update_publish.py`。**默认只产出资产不发布**；真正发版必须显式 `--publish --yes`。

### 3.1 一条命令产出全部资产（不推送）

     python tools/update_publish.py --build --min-windows <x.y.z>

依次执行：全量测试 → `tools/release.py` 构建 → `tools/package_zip.py` 打包 →
计算 zip 大小/SHA-256 → 生成并 Ed25519 签名 `update-manifest.json` → 写 `SHA256SUMS` 与
`release-<tag>.json` 记录 → 用 `tools/verify_release.py` 离线校验（清单结构+验签、资产大小/
SHA-256、zip 无 `data/`、含 `PaperLibrary.exe` 与匹配的 `version.json`）。

- 输出目录默认 `dist`（`--out` 可改）；`--zip <path>` 复用已有包；`--skip-tests` 仅紧急重跑用。
- `--min-windows` **必须显式给出**（或环境变量 `PAPERLIB_MIN_WINDOWS`）：它是最低 Windows
  版本的产品决定项（待确认），脚本不猜默认值。
- 任意一步失败即返回非 0，不会留下"看似成功"的发布。

### 3.2 改版本号

     python tools/update_publish.py --set-version 1.7.0

只改 `version.json`（version=1.7.0、tag=v1.7），**需要自己提交**；发布时脚本要求工作区干净、
`version.json` 已提交到 HEAD。

### 3.3 真正发版（推送 tag + 创建 Release + 发布后校验）

     python tools/update_publish.py --zip dist/PaperLibrary.v1.6.Nexus.zip \
         --min-windows <x.y.z> --publish --yes [--notes-file dist/release-notes-v1.6.md]

脚本会：校验工作区干净 → 拒绝本地/远端已存在的 tag → 创建并推送 `v<major>.<minor>` tag →
`gh release create` 上传 zip + `update-manifest.json` + `SHA256SUMS` + `release-<tag>.json` →
用稳定地址 + 真实下载做发布后校验。发布后校验失败会返回非 0（Release 已建，不回滚，需人工处理记录）。

### 3.4 手动等价步骤（脚本不可用时）

1. 确认 `version.json` 已更新并提交。
2. `python -m unittest discover -s test`；`python tools/release.py`；`python tools/package_zip.py --src <包>`。
3. `python tools/update_manifest.py sign --in <manifest.json> --out dist/update-manifest.json`。
4. `git tag v<tag>`；`git push origin v<tag>`；
   `gh release create v<tag> <zip> dist/update-manifest.json --title "PaperLibrary v<tag> Nexus" --notes-file <说明>`。
5. `python tools/verify_release.py --manifest dist/update-manifest.json --latest --download`。

### 3.5 发布后校验（必须全绿才视为发布成功）

     python tools/verify_release.py --manifest dist/update-manifest.json --latest --download

## 4. 发布后校验项（tools/verify_release.py）

- 稳定地址 `latest/download/update-manifest.json` 可匿名访问；
- 清单结构符合 schema=1，Ed25519 验签通过（内置公钥）；
- 清单字节数与 `Content-Length`、实际下载字节数一致；
- 下载资产 SHA-256 与清单一致；
- zip 无顶层 `data/`、无绝对路径/上跳条目、含 `PaperLibrary.exe`、`version.json` 版本匹配；
- `--latest` 时线上清单与本地清单语义一致。

任一 FAIL 即判定发布失败；WARN 需人工确认（例如历史资产没有 version.json）。

## 5. 当前状态（2026-11-19）

- **v1.8.2 已发布**（tag v1.8.2 / Release 指向 `9367ed6`，大库装载与详情页生命周期优化 + 写入一致性/升级回滚修复）：
  - zip `PaperLibrary.v1.8.2.Nexus.zip` 43,498,137 字节，sha256 `d15f9f45f699fe85465d19bfa5ed9be784b0684ec61dca6e11886c7df02ade81`；
  - 资产：zip、`update-manifest.json`（keyId=e777a2f6）、`SHA256SUMS`、`release-v1.8.2.json`；
  - 发布后校验：9 PASS / 0 FAIL / 0 WARN（latest 清单一致、真实下载、SHA-256、zip 无 data/）。
  - 注意：发布前若已升级 `version.json` 但尚未打 tag，不要直接跑全量测试——旧版
    `test_update_publish` 会在该窗口真的执行发布；现已改为 tag 不存在时跳过并说明原因。
- **v1.8.1 已发布**（tag v1.8.1 / Release 指向 `9cd64b6`，修复更新检查 SSL 证书链校验失败）：
  - zip `PaperLibrary.v1.8.1.Nexus.zip` 43,371,547 字节，sha256 `331ef50245f9a4ba7b26f6469ca654bc5e2c364a4c9e68afc3c376e5986618cf`；
  - 资产：zip、`update-manifest.json`（keyId=e777a2f6）、`SHA256SUMS`、`release-v1.8.1.json`；
  - 发布后校验：9 PASS / 0 FAIL / 0 WARN（latest 清单一致、真实下载、SHA-256、zip 无 data/）。
- **v1.8.0 已发布**（tag v1.8 / Release 指向 `ca4c1f4`，完整更新机制 + 立即安装 + 自动下载开关）：
  - zip `PaperLibrary.v1.8.Nexus.zip` 43,370,712 字节，sha256 `6b147db1215134db28cc27f587cff921951d2ee3d86816d126065c892688de41`；
  - 资产：zip、`update-manifest.json`（keyId=e777a2f6）、`SHA256SUMS`、`release-v1.8.json`；
  - 发布后校验：9 PASS / 0 FAIL / 0 WARN（stable 清单一致、真实下载、SHA-256、zip 无 data/）。
- **v1.7.1 已发布**（tag/Release 指向 `c9aa3268`，含 P0-01~P0-06 与批量导入/AI 总结修复）：
  - zip `PaperLibrary.v1.7.1.Nexus.zip` 43,238,599 字节，sha256 `e844ba33ba07e44f…0f30032`；
  - 资产：zip、`update-manifest.json`（签名 keyId=e777a2f6）、`SHA256SUMS`、`release-v1.7.1.json`；
  - `latest/download/update-manifest.json` 匿名访问 200，清单 minWindows=`10.0.17763`；
  - 发布后校验：9 项通过 / 0 失败 / 0 警告（stable 清单一致、真实下载、SHA-256、zip 无 data/）。
- v1.6 的 Release 只保留 zip（无清单，历史资产）；新客户端从 v1.7.1 起走清单更新。
- 旧分支 `PaperLibrary`、`sci-lib-refactor/p0-safety-net` 与旧整理 tag 已按审计结论删除（R1）；
  GitHub 侧不可达对象缓存仍需 Support 清理，详见 `docs/update-p0-20261115/p0-05-audit.md`。
- 仓库已公开可见；私有数据（data/、settings、私钥）不在任何资产中。
