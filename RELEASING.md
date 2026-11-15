# PaperLibrary 发布流程（方案一：GitHub Releases 整包替换）

> 适用：阶段 0 及其后的每次公开发布。更新机制其余部分（下载/安装/回滚）仍在后续阶段实现。
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

- 版本号只在 `version.json` 维护；`tag` 必须是 `v<major>.<minor>`，与 zip 名一致。
- zip 内不得有用户 `data/`、设置、API Key、本机路径；根目录必须有 `PaperLibrary.exe`（新包还要有 `version.json`）。
- 私钥（`%USERPROFILE%\.paperlibrary\update-signing-key.json` 或 CI secret）**绝不**上传或打进包。

## 3. 上传步骤（当前为手动步骤；P0-06 起用脚本固化）

1. 确认 `version.json` 已更新（version/tag），或由发布脚本写入。
2. 运行测试与构建：

       python -m unittest discover -s test
       python tools/release.py
       python tools/package_zip.py --src dist/PaperLibrary-<stamp>

3. 生成并签名清单（指向该 tag 的固定资产地址）：

       python tools/update_manifest.py sign --in <manifest.json> [--out dist/update-manifest.json]

4. 创建并推送 tag（`v<major>.<minor>`），然后创建 Release 并上传资产：

       git tag v1.6
       git push origin v1.6
       gh release create v1.6 dist/PaperLibrary.v1.6.Nexus.zip dist/update-manifest.json \
         --title "PaperLibrary v1.6 Nexus" --notes-file dist/release-notes-v1.6.md

5. 发布后校验（必须全绿才视为发布成功）：

       python tools/verify_release.py --manifest dist/update-manifest.json --latest --download

## 4. 发布后校验项（tools/verify_release.py）

- 稳定地址 `latest/download/update-manifest.json` 可匿名访问；
- 清单结构符合 schema=1，Ed25519 验签通过（内置公钥）；
- 清单字节数与 `Content-Length`、实际下载字节数一致；
- 下载资产 SHA-256 与清单一致；
- zip 无顶层 `data/`、无绝对路径/上跳条目、含 `PaperLibrary.exe`、`version.json` 版本匹配；
- `--latest` 时线上清单与本地清单语义一致。

任一 FAIL 即判定发布失败；WARN 需人工确认（例如历史资产没有 version.json）。

## 5. 当前状态（2026-11-15）

- 已发布版本 v1.6 的 Release **只有 zip，没有 update-manifest.json**（发布流程尚未跑过 P0-04 之后的新流程）。
  实测 `latest/download/update-manifest.json` 返回 404；zip 可匿名下载且与已知 SHA-256 一致。
- v1.6 zip 是 P0-01 之前构建的，**不含 `version.json`**；新构建包已包含。
- **不要**为了补齐 v1.6 而给已发布 tag 追加清单：清单应与构建该 zip 的源码一致；
  下一次正式发布（版本号待确认）按第 3 节一次产出 zip + 签名清单。
- 仓库已公开可见；私有数据（data/、settings、私钥）不在任何资产中。
