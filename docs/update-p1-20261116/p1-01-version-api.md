# P1-01 后端 GET /api/version（契约与错误分支）

日期：2026-11-16　阶段 1 / P1-01　状态：已完成并核验

## 1. 实现与契约

- 路由：@GET /api/version@ → @paperlib/api/system.py: version@（P0-01 已实现，本步补齐契约测试）。
- 返回：@200 {ok, product, version, tag, ui, builtAt, commit}@；数据源为包根 @version.json@；
  @version@ 用于更新比较（semver），@ui@/@tag@ 用于显示（v1.7.1）。
- 失败：@version.json@ 缺失/损坏 → @500 {ok:false, error:"读取版本信息失败：…"}@（中文原因，含文件缺失/JSON 错误）。
- 契约：@shared/contracts/api-snapshot.json@ 的 @version@ 行（70 条快照之一）与 @shared/contracts/api.md@。

## 2. 本步改动

| 文件 | 改动 |
|---|---|
| @test/test_version.py@ | 新增 @VersionEndpointErrorTest@（3 条）：成功契约字段、version.json 缺失 500、损坏 500；用最小 FakeCtx 直接驱动 handler |

## 3. 核验

| 项目 | 命令/证据 | 结果 |
|---|---|---|
| 单元测试 | @python -m unittest test.test_version -v@ | 13 tests OK（新增 3） |
| 全量测试 | @python -m unittest discover -s test@ | **517 tests OK（1 skip）**（基线 514 + 3） |
| 契约快照 | @python tools/contract_check.py --only health,version@ | PASS 2 / FAIL 0（全量另跑 60 PASS/10 SKIP/70） |
| 真实运行 | 临时 data 启动服务 → @GET /api/version@ | @{ok:true, product:PaperLibrary, version:1.7.1, tag:v1.7.1, ui:v1.7.1}@；关闭后端口释放 |

## 4. 验收对照

| P1-01 验收项 | 结果 |
|---|---|
| 返回当前 version/tag | PASS（真实运行与测试均验证） |
| version.json 缺失时明确错误而非崩溃 | PASS（500 + 含 version.json 的中文错误） |
| 契约测试覆盖 | PASS（快照 + api.md 已登记） |
