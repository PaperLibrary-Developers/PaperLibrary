# P1-05 latest/download 与签名链验收

日期：2026-11-16　阶段 1 / P1-05　状态：**通过**（v1.7.1 真实公网 Release）

## 1. 验收对象

- 稳定地址：@https://github.com/PaperLibrary-Developers/PaperLibrary/releases/latest/download/update-manifest.json@
- 版本：@v1.7.1@（tag 指向 @c9aa3268@）
- 资产：@PaperLibrary.v1.7.1.Nexus.zip@，**43,238,599 字节**，
  SHA-256 @e844ba33ba07e44f8e7abd68e98b7f83a57f9f3d5d62212bb0789beaf0f30032@
- 清单签名：Ed25519，keyId @e777a2f6@（内置公钥）；minWindows=@10.0.17763@

## 2. 验收项与结果（真实网络、匿名）

用 @tools/verify_release.py@（urllib，无 token/cookie，逐跳校验允许主机）：

| 验收项 | 结果 |
|---|---|
| 稳定地址匿名访问 | PASS（HTTP 200；无认证头） |
| 清单结构校验（schema=1 / product / channel / arch / version / tag） | PASS |
| Ed25519 验签（内置公钥） | PASS（keyId=e777a2f6） |
| 线上 latest 清单与本地已签名清单一致 | PASS（规范化字节一致） |
| 资产 URL 可访问（跟随 GitHub 重定向） | PASS |
| 资产真实下载 | PASS（43,238,599 字节） |
| 下载字节数与清单 size 一致 | PASS |
| 资产 SHA-256 与清单一致 | PASS |
| zip 内容（无顶层 data/、无绝对路径/上跳、含 PaperLibrary.exe/version.json） | PASS |

汇总：**9 项通过 / 0 失败 / 0 警告**（@--manifest … --latest --download@，耗时 8.9s，退出码 0）。

## 3. 客户端侧签名链（用发布包本身的代码与公钥验证）

把 @PaperLibrary.v1.7.1.Nexus.zip@ 解压到临时目录，用包内 **便携 Python**（无第三方依赖）运行包内
@tools/verify_release.py@（即用户拿到的客户端代码 + 内置公钥）：

- 结果：**6 项通过 / 0 失败 / 0 警告**（稳定地址、清单验签、资产下载、大小、SHA-256、zip 内容）；
- 说明：签名链不依赖开发机上的 cryptography/私钥，发布包自带公钥即可完成验证。

## 4. 负向验证（任一不符即判失败）

复制本地清单并把 @assets.full.size@ 改大 1 字节（破坏签名）：

- @python tools/verify_release.py --manifest <被篡改清单> --latest@ → **退出码 1**，
  报“清单验签失败（清单被篡改、签名不匹配或公钥不匹配）”。
- 其余不符场景（size/SHA-256/zip 违规）在主验收脚本中逐项 FAIL 并返回 1，已有单测覆盖
  （@test/test_update_release.py@：篡改资产、错公钥、data/、版本不符）。

## 5. 结论

- P1-05 验收通过：稳定地址匿名可用、签名有效、大小与 SHA-256 与资产一致；任一不符会判失败。
- 该验收依赖 v1.7.1 Release（已发布）；后续每次发版都应重跑同一命令，失败即视为发布失败。
- 日志中不记录带临时签名的重定向 URL（@verify_release.py@ 只打印稳定地址），便于安全留档。

## 6. 复现命令

```
python tools/verify_release.py
python tools/verify_release.py --manifest dist/p0-07-assets/update-manifest.json --latest --download
# 发布包内：
runtime\portable\python.exe tools\verify_release.py
```
