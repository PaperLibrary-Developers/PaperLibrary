# P2-17 健康检查判定与超时

日期：2026-11-16　阶段 2E-3 / P2-17　状态：已完成并核验

## 1. 实现

健康客户端严格按口径 B（**不修改后端 `/api/health` 字段**）：

1. `GET /api/health`：HTTP 200、`ok=true`、`app=="literature-vault"`；
2. 再 `GET /api/version`：`product=="PaperLibrary"` 且 `version==目标版本`；
3. 单次请求 2 秒、间隔 1 秒、总超时 60 秒、连续成功 2 次；
4. 失败即回滚，并把具体失败码写入日志、`journal.json`、`state.json` 的 `code`。

失败码：

| code | 判定 |
|---|---|
| `health-timeout` | 单次请求超时（无更具体失败时也归此码） |
| `health-http` | 连接失败 / HTTP 非 200 |
| `health-json` | 返回不是预期 JSON/缺字段 |
| `health-not-ok` | `ok=false` |
| `health-app` | `app != literature-vault` |
| `health-version` | `product != PaperLibrary` 或版本不符 |

连续计数：任何一次失败都会把 streak 清零；只有连续 2 次完整通过才写 success。
失败 state 同时记录 `healthChecks`（尝试次数）与 `healthSeconds`，便于定位。

## 2. 单测（test/test_update_helper_health.py）

真实 helper + 可配置假 `PaperLibrary.exe`，覆盖：

- 失败码与回滚：`health-not-ok`、`health-app`、`health-version`（版本/产品两种）、
  `health-json`、`health-http`(500)、`health-timeout`（请求 400ms、服务延迟 3s、总 2s）；
  每例断言安装返回 3、state.code 一致、旧版 1.6.0 恢复、数据保留、旧版重新启动；
- 连续 2 次成功：正常安装成功，state.healthChecks >= 2；
- 只有 1 次成功不算：假服务首次 healthy、之后 `ok=false`，最终失败并回滚；
- 超时参数：总超时 2 秒时实际耗时 >= 1.5 秒，code=health-timeout。

命令与结果：

```text
python -m unittest test.test_update_helper_health -v
Ran 3 tests in 85.765s
OK
```

（3 个测试方法内含 7 个失败码子用例 + 2 个连续成功用例。）

## 3. 真实运行证据（版本不符 → 回滚）

假服务报告 `version=1.7.0`，目标为 1.7.1：

```text
INSTALL_EXIT 3
INSTALL_STDOUT {"ok":false,"command":"install","version":"1.7.1","fromVersion":"1.6.0",
 "code":"health-version","restored":true,"oldLaunchedPid":105968,...}
STATE {"phase":"failed","version":"1.7.1","fromVersion":"1.6.0","restored":true,
 "healthSeconds":3,"healthChecks":12,"code":"health-version",...}
PROGRAM_VERSION {"product": "PaperLibrary", "version": "1.6.0"}
DATA_DB user database
APPLY_STILL_THERE True
LOG_HEALTH_LINES
health attempt 1: health-version
...
health attempt 12: health-version
state: failed
```

失败 state 同时记录 `healthSeconds=3`、`healthChecks=12`；真实失败路径的 exit=3、code、
回滚、数据保留、apply 保留均已确认。

## 4. 验收对照

| P2-17 验收项 | 结果 | 证据 |
|---|---|---|
| 超时/版本不符/ok=false/app 不符均失败并回滚 | PASS | 6 类失败码子用例 + 真实版本不符 |
| 2 次连续成功才算成功 | PASS | 正常成功 healthChecks>=2；首次成功后续失败 → 失败回滚 |
| 成功写 state.json | PASS | 成功 state.phase=success，healthChecks>=2 |
| 失败有明确 code | PASS | state.code 为上述 6 个具体码之一，日志逐次记录 |
| 不修改后端 health 字段 | PASS | 仅客户端判定；后端代码未改 |
| 单次 2s/间隔 1s/总 60s | PASS | 生产默认值未变；测试用覆盖值验证总超时 |

## 5. 测试与契约

- P2-17：`Ran 3 tests ... OK`；
- 回归 P2-13/P2-16：各自 **11 / 6 tests OK**；
- 全量：**699 tests OK（1 skip）**（696 + 3）；
- 契约：**PASS 61 / FAIL 0 / SKIP 13（共 74）**，无回归；版本检查通过；
- `shared/contracts/update-install.md` 新增第 7 节“健康判定（P2-17）”，更新失败码与边界。

## 6. 遗留

- 启动器/UI 如何展示失败码与日志路径在 P2-10R/P2-09R；
- 健康检查的 2s/1s/60s 已是生产默认，未新增可配置产品项。
