# P2-01 更新目录与状态文件结构

日期：2026-11-16　阶段 2A / P2-01　状态：已完成并核验

## 1. 目录结构（数据目录移到外部目录时不变）

@paperlib/update/store.py@ 提供 @UpdateStore(data_dir)@：

    <data>/updates/
      <version>/                     # 1.7.1；每版本独立目录，互不串包
        package.zip.part             # 下载中的临时文件（支持 Range 续传）
        package.zip                  # 下载完成并通过 P2-03 校验的完整包
        manifest.json                # 本次使用的已签名清单原始字节
        staging/                     # P2-04 解压暂存区（等价原文 staging/<version>/）
        download-state.json          # 下载/校验/解压状态，原子写
        ready.json                   # P2-04 全部安全校验通过后才写入

## 2. 状态文件字段（download-state.json，schema=1）

| 字段 | 含义 |
|---|---|
| @schema@ | 固定 1 |
| @version@ | 目标版本（x.y.z） |
| @phase@ | @idle@ / @downloading@ / @verifying@ / @extracting@ / @ready@ / @failed@ / @cancelled@ |
| @downloadedBytes@ / @totalBytes@ | 已下载/总字节（进度用） |
| @resumedFrom@ | 本次从多少字节处续传（0 = 从头） |
| @sourceUrl@ | 本次下载的资产 URL |
| @sha256@ | 校验通过的包哈希（未完成时为空） |
| @startedAt@ / @updatedAt@ | UTC 时间 |
| @error@ / @code@ | 失败时的可读原因/机器码 |

## 3. 生命周期与安全约束

- @version_dir()@ 先校验 @x.y.z@ 再拼路径，并确认解析结果位于 @updates/@ 内（拒绝
  @../evil@、@1.7.1/../2.0.0@、@v1.7.1@、空串等，防路径注入）。
- @prepare(version)@：清理该版本旧产物（part/zip/manifest/staging/state/ready）→ 建目录 →
  写 @phase=downloading@ 初始状态；**同版本重下不会复用旧 .part**。
- 版本切换策略（阶段 2A）：@prepare()@ 会删除其它版本目录（只保留当前目标版本）。
  阶段 2B/2F 若需要保留旧版本产物/备份，需在该阶段显式调整；本步没有 apply/回滚语义。
- 状态读写走 @util.write_json_atomic@（临时文件 + 原子替换），不残留 @.tmp-*@；
  读取到损坏状态时回退为默认状态，不让磁盘上的坏 JSON 阻塞下载。
- 不写用户数据、不创建 @updates/@ 直到真正开始下载。

## 4. 验收对照

| P2-01 验收项 | 证据 | 结果 |
|---|---|---|
| 包根/版本目录结构正确 | @test_update_store@：paths 逐一断言（part/zip/manifest/staging/state/ready） | PASS |
| 同一版本重复下载不串包 | @test_old_part_is_not_reused_after_prepare@：旧 .part 被清掉 | PASS |
| 版本切换旧目录按策略清理 | @test_prepare_prunes_other_versions@：只保留当前版本并记录策略 | PASS |
| 路径均在 @data/updates/@ 下 | 非法版本/越界路径全部 @UpdateStoreError@ | PASS |
| @data/@ 在外部目录结构不变 | 测试用仓库外临时 data 目录；真实运行输出同上 | PASS |
| 状态文件可原子读写 | 状态 roundtrip、@.tmp-*@ 无残留、坏 JSON 回退默认 | PASS |

## 5. 测试与真实运行

- 单测：@python -m unittest test.test_update_store -v@ → **11 tests OK**；
- 全量：@python -m unittest discover -s test@ → **556 tests OK（1 skip）**（P1-05 后 545 + 11）；
- 真实运行（仓库外临时 data 目录）：创建 @updates/1.7.1/@{download-state.json,manifest.json}，
  状态为 @phase=downloading, totalBytes=43238599@，与设计一致。
