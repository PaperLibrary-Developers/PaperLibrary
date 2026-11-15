# P2-03 下载后校验（大小 / SHA-256 / 清单签名 + 失败清理）

日期：2026-11-16　阶段 2A / P2-03　状态：已完成并核验

## 1. 实现

@paperlib/update/verify.py@（@verify_download@）：

1. 重新读取本次下载保存的 @manifest.json@ 并做**结构校验 + Ed25519 验签**（内置公钥；
   与 P1-02 的签名链一致，不信任磁盘上的清单字节）；
2. @package.zip.part@ 必须存在，字节数 == 清单 @assets.full.size@；
   若调用方给出 Content-Length、或状态文件里记录过响应总大小，也一并比对；
3. 1 MB/块流式计算 SHA-256（不整读内存）== 清单 @assets.full.sha256@；
4. 全部通过：@os.replace@ 把 .part 转正为 @package.zip@，状态置 **verified**（在 P2-01 的阶段集合中新增）；
5. 任一失败：删除本版本临时包（.part 与 package.zip），状态置 @failed@ + code，**不进入解压/安装**，
   不触碰 staging/ready 与其它版本目录。

错误码：@manifest-missing@、@invalid-manifest@、@signature@、@version-mismatch@、
@missing-package@、@size-mismatch@、@sha256-mismatch@、@disk-write@。

## 2. 验收对照

| P2-03 验收项 | 证据 | 结果 |
|---|---|---|
| size == 清单 size | 单测 size-mismatch（短 1 KB）；真实截断包 → 拒绝并清理 | PASS |
| Content-Length / 清单 size 一致 | 单测 content_length 不一致、状态 totalBytes 不一致 | PASS |
| SHA-256 == 清单 sha256 | 单测改 1 字节；真实包改 1 字节 → sha256-mismatch + 清理 | PASS |
| manifest 签名已通过 | 单测错密钥/篡改清单字段；真实清单被改 size → signature + 清理 | PASS |
| 失败删除临时包、保留旧版本、不进入安装 | 失败后 part/package 均不存在，状态 failed；staging 未创建/未改动 | PASS |
| 成功转正 | 真实包校验后 @package.zip@ 存在、.part 删除、phase=verified | PASS |

## 3. 真实验证（公网 v1.7.1 43,238,599 B）

- 下载 12.88 s → @verify_download@：返回 size=43,238,599、sha256=@e844ba33ba07e44f…@；
  状态 @verified@；@package.zip@ 存在、@.part@ 不存在。
- 用真实包构造三类负向样本（不改动源文件）：
  - 改 1 字节 → @sha256-mismatch@，part/package 均被删除，状态 @failed@；
  - 截断 1 KB → @size-mismatch@，同样清理；
  - 把清单里的 size 改大（不重新签名）→ @signature@，同样清理。

## 4. 单测（test/test_update_verify.py，12 条）

成功转正；size 短/长、Content-Length、状态总大小、SHA-256 不符；错签名、篡改清单字段、
坏 JSON、缺清单、版本不符、缺 .part；@sha256_file@ 与 hashlib 一致性；每类失败均断言
清理与 failed/code。

## 5. 测试

- @python -m unittest test.test_update_verify -v@ → **12 tests OK**；
- 全量：**581 tests OK（1 skip）**（P2-02 后 569 + 12）。

## 6. 说明

- 阶段集合新增 @verified@（明确“下载已校验、等待解压”），P2-04 成功后写 @ready@。
- 新包替换/旧版本保留仍不在本步：失败只清理本版本临时包，不触碰任何用户数据/旧程序文件。
