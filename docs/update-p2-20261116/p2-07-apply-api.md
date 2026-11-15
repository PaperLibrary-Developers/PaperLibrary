# P2-07 POST /api/update/apply（确认 staging 后写 apply.json）

日期：2026-11-16　阶段 2B / P2-07　状态：已完成并核验

## 1. 端点契约

@POST /api/update/apply@（无请求体）：

- 200 @{ok, applied, idempotent, apply:{schema,version,tag,fromVersion,fromTag,stagingPath,
  stagingRel,backupPath,backupRel,packageSha256,manifestSha256,files,bytes,createdAt}}@
- 409 @{ok:false, code, error}@：
  - @staging-missing@：还没有已准备好的更新；
  - @staging-incomplete@：staging 目录/关键文件/清单缺失或校验失败；
  - @version-mismatch@：staging 内 version.json 与目标/清单版本不一致；
  - @version-same@：目标版本 == 当前版本；
  - @downgrade@：目标版本低于当前版本；
  - @busy@：下载/校验/解压任务仍在进行；
- 500：本地 @version.json@ 损坏。

实现：@UpdateService.apply()@；只写标记，**不执行替换/回滚/重启**（那属于 P2-10~P2-17）。
apply.json 契约见 [shared/contracts/update-apply.md](shared/contracts/update-apply.md)。

## 2. apply.json（写入 @<data>/updates/apply.json@，schema=1）

字段：@schema, version, tag, fromVersion, fromTag, stagingPath, stagingRel, backupPath,
backupRel, packageSha256, manifestSha256, files, bytes, createdAt@

- @stagingPath@：已通过 P2-04 全部校验的 staging 绝对路径；@stagingRel@ 为相对数据目录路径；
- @backupPath@：@updates/backups/app-<旧版本>@（后续 helper 在替换前创建/使用）；
- @packageSha256@/@manifestSha256@：本次完整包与签名清单哈希，便于启动器复核；
- 写入前重新确认：staging 存在、@PaperLibrary.exe@ 与 @version.json@ 存在、版本一致、
  清单重新验签、目标版本 > 当前版本；写操作走原子替换。

幂等与清理：

- 同版本/同 staging/同备份路径重复 apply → @idempotent=true@，不重复改写；
- 开始一次新的下载时后端会清除旧 apply.json（避免启动器读取过期标记）。

## 3. 真实验证（临时 1.6.0 副本 + 公网 v1.7.1）

| 步骤 | 结果 |
|---|---|
| 下载到 staging | @phase=ready, stagingVersion=1.7.1, percent=100@ |
| 第一次 apply | 200 @applied=true, idempotent=false@ |
| 第二次 apply | 200 @applied=true, idempotent=true@（文件未改写） |
| apply.json 字段 | 契约要求的 14 个字段齐全；version=1.7.1、fromVersion=1.6.0、backupPath=…/app-1.6.0、staging 目录存在 |
| apply 后状态 | @phase=ready, running=false, ready=true@（apply 不启动下载） |
| 当前版本改成 1.7.1 后 apply | **409 @version-same@**「staging 版本与当前版本相同，无需安装」 |
| 删除 staging 的 PaperLibrary.exe 后 apply | **409 @staging-incomplete@**「staging 缺少 …」 |

## 4. 单测（test/test_update_apply.py，13 条）

写入字段/路径/哈希；重复 apply 幂等且不改写文件；无 staging→409 staging-missing；
缺 exe→409 staging-incomplete；staging 版本不符→409 version-mismatch；同版本→version-same；
降级→downgrade；下载中→busy；新下载清理旧 apply；store apply 读写/清理；
API handler 200/409/500 映射；apply 不启动下载、不改 staging。

## 5. 测试与契约

- @python -m unittest test.test_update_apply -v@ → **13 tests OK**；
- 全量：**627 tests OK（1 skip）**（P2-06 后 614 + 13）；
- 契约：**PASS 61 / FAIL 0 / SKIP 13（共 74）**；@/api/update/apply@ 为有副作用端点，快照 skip。

## 6. 未执行/后续

- 未执行真正的程序文件替换、备份创建、data 保留、回滚或启动器 helper（P2-10~P2-17）；
  本步只产出“启动器可识别的安装标记”，apply.json 识别/执行约定见契约文档。
- 下载取消后 .part 保留策略仍是待确认项（同 P2-02/P2-05）。
