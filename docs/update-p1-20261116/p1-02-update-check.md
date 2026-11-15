# P1-02 后端 GET /api/update/check（只检查，§4.3 全部分支）

日期：2026-11-16　阶段 1 / P1-02　状态：已完成并核验

## 1. 实现

| 文件 | 内容 |
|---|---|
| @paperlib/update/fetch.py@ | 安全拉取清单：仅 HTTPS + 允许主机、手动跟随重定向并逐跳校验、超时 9s、重试 2 次（共 3 次）、响应 ≤256 KB、no-cache 请求头、404 立即判“发布流程异常”不重试 |
| @paperlib/update/check.py@ | UpdateChecker：结构校验 → Ed25519 验签 → arch/Windows 版本检查 → semver 比较；只读，不下载 |
| @paperlib/api/update.py@ | @GET /api/update/check@ handler 与错误码映射 |
| @paperlib/routes.py@、@paperlib/services.py@ | 注册端点、装配 UpdateChecker |
| @shared/contracts/api.md@、@api-snapshot.json@ | 契约登记（71 条；update-check 为网络端点，快照标记 skip，不参与重放） |
| @test/test_update_check.py@ | 27 条测试：结果分支、§4.3 错误分支、拉取/重定向/重试/上限、API 映射 |

响应契约：

- @200 {ok, available, reason, current:{version,tag}, latest:{version,tag,publishedAt,size,notesUrl}, checkedAt, message?}@
  - reason：@newer@（有更新）/ @current@（已最新）/ @older@（更新源更低，拒绝降级）/
    @unsupported-win@ / @unsupported-arch@
- @502 {ok:false, code, error}@：@timeout@、@network@、@no-manifest@、@invalid-manifest@、
  @signature@、@manifest-too-large@、@redirect-host@、@bad-url@
- @500 {ok:false, error}@：本地 @version.json@ 损坏

## 2. §4.3 分支覆盖

| §4.3 场景 | 实现/测试 | 结果 |
|---|---|---|
| 离线 / 连接失败 | FetchError network → 502 | 测试覆盖（URLError） |
| 超时 | FetchError timeout → 502；按 retries 重试 | 测试覆盖（socket.timeout，3 次尝试） |
| 404（latest 缺清单） | FetchError no-manifest → 502，不重试、不猜版本 | 测试 + 真实 404 验证 |
| 无效 JSON / schema 不符 | CheckError invalid-manifest → 502 | 测试覆盖（坏 JSON、product 错误） |
| 签名失败 / 被篡改 | CheckError signature → 502，不进入下载 | 测试覆盖（错密钥、改 size） |
| 版本更低（降级） | 200 available=false reason=older，不提示更新 | 测试覆盖 |
| 版本相同 | 200 available=false reason=current | 测试 + 真实网络 |
| 版本更高 | 200 available=true reason=newer | 测试 + 真实网络（1.6.0 → v1.7.1） |
| Windows 过低 / 架构不符 | 200 available=false reason=unsupported-* | 测试覆盖 |
| 清单 >256 KB | invalid-manifest（含 256 KB 提示） | 测试 + 拉取层上限 |

## 3. 核验

| 项目 | 命令/证据 | 结果 |
|---|---|---|
| 单元测试 | @python -m unittest test.test_update_check -v@ | **27 tests OK** |
| 全量测试 | @python -m unittest discover -s test@ | **544 tests OK（1 skip）**（P1-01 后 517 + 27） |
| 契约快照 | @python tools/contract_check.py@ | PASS 60 / FAIL 0 / SKIP 11（共 71） |
| 真实网络（当前版本） | 临时 data 启动服务 → @GET /api/update/check@ | 2.7s 返回 @reason=current@，latest=v1.7.1 / 43,238,599 B；关闭后端口释放 |
| 真实网络（旧版本） | 临时 root version=1.6.0 → UpdateChecker.check() | @available=true, reason=newer, message="发现新版本 v1.7.1"@ |
| 真实 404 分支 | 拉取不存在的 latest 资产 | @code=no-manifest@，消息为“发布流程异常，不猜测版本” |

## 4. 说明

- 允许重定向主机：@github.com@、@release-assets.githubusercontent.com@（实测链条；@objects.githubusercontent.com@ 未观测到，未加入）。该列表为**待观察项**：GitHub 若更换资产主机，需要随新版本更新。
- 未实现：下载、哈希校验、解压、安装（阶段 2）；本端点不写任何文件、不缓存结果。
- 自动检查的默认开关属于 P1-04 的产品待确认项，本步不涉及。
