# P0-03 Ed25519 签名工具链与内置公钥

日期：2026-11-15　阶段 0 / P0-03　状态：已完成并核验

## 1. 交付物

| 交付物 | 位置 |
|---|---|
| Ed25519（RFC 8032）纯标准库实现（sign/verify/public_key） | `paperlib/update/ed25519.py` |
| 签名/验签、私钥加载（环境变量或包外文件） | `paperlib/update/signing.py` |
| 客户端内置验签公钥（32 字节 base64 + keyId） | `paperlib/update/keys.py` |
| 签名命令行：keygen / show-key / sign / verify | `tools/update_manifest.py` |
| 真实签名样例（可脱离私钥复验） | `shared/fixtures/update-manifest.signed.json` |
| 测试（15 条：RFC 向量、确定性、篡改、错误公钥、私钥管理、CLI） | `test/test_update_signing.py` |

## 2. 密钥管理结论

- 私钥：32 字节 seed，**不在仓库、不在发布包**。默认保存到
  `%USERPROFILE%\.paperlibrary\update-signing-key.json`（包外），已用 icacls 收紧到仅当前用户；
  CI 可用环境变量 `PAPERLIB_UPDATE_SIGNING_KEY`（base64 seed）。
- 公钥：内置 `paperlib/update/keys.py`，keyId=`e777a2f6`（sha256(公钥) 前 8 位），
  生成时间 2026-11-15T12:29:39Z。
- 私钥生成脚本只打印 keyId/公钥/路径，**从不打印 seed**；CLI 的 keygen 拒绝把私钥写进仓库目录。
- 轮换：`keygen --force` + 更新 keys.py 公钥；旧客户端会验签失败并拒绝更新（预期行为，不静默降级），
  需在发布说明写明。

## 3. 验签语义（原文 §3.3 / §9）

1. 结构校验（P0-02 的 schema 校验，256 KB 上限、字段/类型/URL/sha256 等）；
2. 用内置公钥验证 Ed25519 签名，签名覆盖“去掉 signature 的规范化 JSON”；
3. 只有验签通过，才信任清单中的 URL、版本与哈希（下载/安装阶段再校验 size/sha256）。

实现说明：便携运行时（Python 3.11）没有 cryptography，因此用纯标准库实现 Ed25519，
只做验签所需的椭圆曲线运算；RFC 8032 §7.1 三条测试向量逐字节比对通过，
并与本机 cryptography 50.0.2 交叉验证（双向签名可互验、确定性签名一致）。

## 4. 核验记录

| 项目 | 命令 | 结果 |
|---|---|---|
| 单元测试（全量） | `python -m unittest discover -s test` | **491 tests OK**（P0-02 后 476 + 新增 15） |
| 签名测试 | `python -m unittest test.test_update_signing -v` | 15 tests OK |
| RFC 8032 向量 | 测试内固定向量 | 3/3 公钥与签名逐字节一致、验签通过 |
| 交叉实现 | cryptography 50.0.2（本机） | 双方签名互验通过；Ed25519 确定性签名一致 |
| 篡改拒绝 | 改 publishedAt/minWindows/releaseNotesUrl/size/sha256/url、换公钥、改签名 | 全部拒绝 |
| 真实样例 | `python tools/update_manifest.py verify --in shared/fixtures/update-manifest.signed.json` | 通过（keyId=e777a2f6） |
| 发布构建 | `python tools/release.py` | 成功；包内便携 Python 可导入 signing/ed25519 并验签真实样例（无 cryptography） |
| 私钥不泄漏 | 仓库与 dist 包全文搜索 seed（不打印 seed）、`git status` | 未出现；仓库新增文件不含私钥 |
| 版本一致性 | `python tools/version_check.py --package <新包>` | 通过 |

## 5. 待确认项

1. 私钥最终存放：本机文件（当前）还是 CI secret（发布上 CI 时二选一，原文 §10 末段）。
2. 公钥轮换策略与旧版本兼容窗口（客户端内置公钥，轮换即强制升级）。
3. 是否启用 Authenticode 代码签名（P3-01，当前未启用）。
