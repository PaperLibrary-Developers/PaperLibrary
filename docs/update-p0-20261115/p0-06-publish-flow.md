# P0-06 发布流程脚本化（构建 → 签名 → 上传 → 发布后校验）

日期：2026-11-15　阶段 0 / P0-06　状态：脚本与本地全流程已核验；**真实发布（推送 tag/创建 Release）未执行**，见第 4 节。

## 1. 交付物

| 交付物 | 位置 |
|---|---|
| 发布编排脚本（一条命令产出全部资产；默认不发布） | `tools/update_publish.py` |
| 发布流程文档（脚本化步骤 + 手动等价 + 校验项 + 当前状态） | `RELEASING.md` |
| 测试（8 条：清单/资产/签名、缺 min-windows、data/ 拒绝、版本不符、错公钥、发布前拦截） | `test/test_update_publish.py` |

脚本按方案一原文 §10 固化八步：

1. （可选）`--set-version x.y.z` 改 `version.json`；
2. 全量测试 `python -m unittest discover -s test`；
3. `tools/release.py` 构建完整包；
4. `tools/package_zip.py` 生成 `PaperLibrary.<tag>.Nexus.zip` 并计算 SHA-256；
5. 生成并 Ed25519 签名 `update-manifest.json`（schema=1、规范化 JSON、内置公钥自检）；
6. 写 `SHA256SUMS` 与 `release-<tag>.json` 记录；
7. `--publish --yes` 时：校验工作区干净 + tag 不存在 → 建 tag 推送 → `gh release create` 上传
   zip / manifest / SHA256SUMS / 记录 → （可选 `--notes-file`）；
8. 发布后校验：`tools/verify_release.py --manifest … --latest --download`
   （稳定地址匿名访问、清单验签、latest 一致、真实下载、大小/SHA-256、zip 无 data/）。

安全约束：私钥只从包外文件/环境变量读取且不打印；内置公钥与私钥必须匹配（测试/轮换可 `--pubkey` 覆盖）；
默认不发版；`--publish` 缺少 `--yes`、工作区脏、tag 已存在、缺资产、zip 含 data/、版本不符、哈希不符均直接失败。

## 2. 本次核验（本地全流程，dry-run）

命令：

```
python tools/update_publish.py --build --min-windows 10.0.17763 --out dist/p0-06-dryrun
```

结果（HEAD=`5abf57cd`）：

- 测试：**OK (skipped=1)**（同全量 514 tests）；
- 构建：`dist/PaperLibrary-20261115-220812-85568`；
- 资产：`PaperLibrary.v1.6.Nexus.zip` **43,238,276 字节**，SHA-256 `cdf07042e7709a2d…1822354`；
- 清单：签名通过，keyId=`e777a2f6`；`SHA256SUMS` 两行（zip + manifest）；
- 记录：`dist/p0-06-dryrun/release-v1.6.json`（publish=false，commit=5abf57cd）；
- 离线校验：**5 PASS / 0 FAIL / 0 WARN**（清单结构+验签、资产大小、SHA-256、zip 无 data/ 且含 exe/version.json）。

负向用例（测试覆盖）：zip 含 `data/` → 拒绝；zip 内 version 与清单不符 → 拒绝；
签名私钥与期望公钥不匹配 → 拒绝；`--publish` 无 `--yes` → 拒绝；tag 已存在 → 拒绝入库。

## 3. 验收标准对照

| P0-06 验收项 | 结果 |
|---|---|
| 任一版本可一键产出全部资产 | **PASS**（dry-run 一条命令产出 zip + 签名清单 + SHA256SUMS + 记录，并自校验） |
| 脚本对缺资产/哈希不一致报错 | **PASS**（缺资产/版本不符/错公钥/zip 违规均失败；哈希不一致由 `verify_release` 覆盖并测试） |
| 发布后校验项全绿 | **未执行真实发布**：v1.6 tag 已存在且下一版版本号未确定（待确认）；脚本已实现并接入，
  本地等价校验全绿；真实发布需用户确认版本号与推送授权 |

## 4. 为什么没有真正发布（待确认）

1. **下一版版本号未确定**（原文与清单均列为待确认；当前 `version.json` 仍为 1.6.0/v1.6）。
2. 当前 `v1.6` tag 与 Release 已存在，且 v1.6 的 zip 不含 P0-01 之后的改动；
   重新构建出的同名资产不能覆盖已发布包。
3. 推送 tag / 创建公开 Release 属于对公开仓库的发布行为，需用户明确授权（按交接约定不擅自推送/发版）。

待确认：下一版版本号（如 1.7.0/v1.7）→ 是否授权推送并发布 → `minWindows` 最终取值 →
是否同时发布含 P0-01~P0-06 的修复包（含 AI 总结/批量导入修复）。
