# P0-02 冻结 update-manifest.json schema（schema=1）

日期：2026-11-15　阶段 0 / P0-02　状态：已完成并核验

## 1. 交付物

| 交付物 | 位置 |
|---|---|
| schema 契约文档（字段/取值/规范化规则/拒绝条件） | `shared/contracts/update-manifest.md` |
| 服务端解析与校验函数 | `paperlib/update/manifest.py`（`validate` / `load_bytes` / `load_file` / `canonical_payload` / `canonical_bytes`） |
| 样例 manifest（规范化单行 JSON，签名占位） | `shared/fixtures/update-manifest.example.json` |
| 测试（18 条：规范化可复现 + 逐项拒绝） | `test/test_update_manifest.py` |

## 2. 冻结内容（与方案一原文 §3.3 对齐）

- 顶层字段顺序：`schema, product, channel, version, tag, publishedAt, releaseNotesUrl,
  minWindows, arch, assets, signature`；`assets.full`：`url, size, sha256`。
- 取值：`schema=1`、`product=PaperLibrary`、`channel=stable`、`arch=x64`；
  `version` 为 `x.y.z`，`tag` 必须等于 `v<major>.<minor>`；`publishedAt` 为 UTC ISO8601；
  `size` 正整数；`sha256` 64 位小写十六进制；`signature` base64 解码后 64 字节。
- 规范化 JSON（签名输入）：固定字段顺序、UTF-8 无 BOM、分隔符 `,`/`:`、无空白无换行、
  非 ASCII 不转义、`signature` 不参与签名并放在序列化结果最后。发布产物即该字节串。
- 拒绝：未知 schema/product/channel/arch、未知字段、缺字段、类型错误、超过 256 KB、
  非法 UTF-8/JSON、URL 非 https 或主机不在允许列表、非 GitHub Release 路径、sha256/size 非法、
  signature 缺失或不是 64 字节。
- 安全边界：清单只含数据，不含脚本/命令/PAT；客户端不执行清单内容；URL 只信任 `github.com`。

## 3. 证据与判断

- 样例使用当前真实的 v1.6 Release 元数据（仓库规范地址、资产大小 45,625,893、真实 SHA-256），
  签名是 64 个零字节的占位值，明确不是可发布清单（P0-03 才生成真实签名）。
- 仓库在 2026-11-15 实测已被迁移：`yijiey53-svg/paper-library-app` 301 到
  **`PaperLibrary-Developers/PaperLibrary`**（公开仓库）。样例与文档使用规范地址。
- 实测下载重定向链条：`github.com`(301) → `github.com`(302) → `release-assets.githubusercontent.com`(200)。
  原文示例中的 `objects.githubusercontent.com` 本次未观测到；下载阶段的最终允许主机列表仍列为待确认项。
- `minWindows` 仅校验格式，样例值 `10.0.17763` 来自原文示例，**不是**已确认的产品最低版本（待确认项）。

## 4. 核验记录

| 项目 | 命令 | 结果 |
|---|---|---|
| 单元测试（全量） | `python -m unittest discover -s test` | **476 tests OK**（P0-01 后 458 + 新增 18） |
| 清单测试 | `python -m unittest test.test_update_manifest -v` | 18 tests OK |
| 规范化可复现 | 两次 `canonical_bytes`；打乱输入键顺序 | 字节完全一致；无空格/换行；字段顺序固定 |
| 篡改覆盖 | 改 sha256/version 后比对 payload | 字节变化（签名覆盖有效） |
| 发布构建 | `python tools/release.py` | 成功（94.8 MB），包内含 `paperlib/update/manifest.py` 与样例 |
| 包内导入 | 包内便携 Python 载入样例 | `pkg-ok v1.6`，payload 484 字节 |
| 版本一致性 | `python tools/version_check.py --package dist/PaperLibrary-20261115-212553-89628` | 通过 |

## 5. 待确认项（不猜值）

1. `minWindows` 的最终取值（10.0.17763 只是原文示例）。
2. 下载重定向允许主机最终列表（至少 github.com + release-assets.githubusercontent.com，需在 P2 下载实现前复核）。
3. 是否随 Release 附带 `SHA256SUMS` 与更新说明（原文标为可选）。
