# P2-19 测试矩阵（§11 + 立即安装）

日期：2026-11-17　阶段 2G / P2-19　状态：已完成；真实发布闭环见 P2-20

## 1. 执行环境与结果

- HEAD：`e26667ede8ee7409b646d5d8724c61ac127ed5a8`（P2-19 前）
- 全量：`python -m unittest discover -s test` → **716 tests OK（1 skip）**
- 契约：`python tools/contract_check.py` → **PASS 61 / FAIL 0 / SKIP 13（共 74）**
- 前端：`node --test test/js/update-install.test.mjs test/js/settings-install-contract.test.mjs`
  → 13 tests OK；`node test/js/update-auto.test.mjs` → 17 tests passed
- 新增契约测试：`test/test_update_contracts.py`（2 条，覆盖 update-apply/install）
- GUI：`python tools/gui_acceptance.py --package <dist>`，截图在
  `docs/update-p2-20261116/p2-19-shots/`

## 2. §11 测试矩阵

| 区域 | 覆盖用例（自动化/半自动） | 结果 |
|---|---|---|
| manifest | 最新/相同/更旧、无效 JSON、签名失败、字段缺失、降级拒绝：`test_update_check.py`、`test_update_manifest.py`、`test_update_signing.py` | PASS |
| 网络 | 离线/超时/404/重定向/部分下载/续传/慢速/代理：`test_update_download.py`、`test_update_verify.py` | PASS（自动化子集） |
| 校验 | size/SHA-256/zip 损坏/路径穿越/zip bomb：`test_update_verify.py`、`test_update_staging.py` | PASS |
| 安装 | 运行中安装、单实例、文件占用、中文/空格路径、无管理员、磁盘不足：P2-13/16/18 测试 + GUI disk-low/program-files | PASS |
| 数据 | data/prefs/settings/sources 不丢失、数据库可恢复：`test_update_helper_data_backup.py` | PASS |
| 回滚 | 新版本启动失败/健康失败/helper 中途失败/断电强杀：P2-13/16/17 测试（含五阶段强制退出） | PASS |
| UI | 进度/取消/稍后/跳过/手动自动检查/错误/自动下载：Node 测试 + GUI 截图 | PASS |
| 发布 | latest/download 匿名可用、资产一致、旧版本可下载：`p2-19-whitelist.txt` + P2-20 | PASS |
| 安全 | HTTPS、签名、允许域名、无 PAT、无用户数据外传：`test_update_signing.py`、`test_update_download.py`、白名单实测 | PASS |
| 契约 | update-apply.md / update-install.md：`test_update_contracts.py` | PASS |

## 3. 立即安装专项

| 用例 | 证据 | 结果 |
|---|---|---|
| 确认后立即请求安装 | CDP 确认框截图 `01-click-helper-fail/02-confirm-dialog.png`；Node 握手测试 | PASS |
| 拒绝确认不关闭 | 设置页取消分支 + Node/静态测试 | PASS |
| apply 失败不关闭 | 设置页 catch；P2-09R 测试 | PASS |
| 握手失败/超时窗口保持 | `update-install.test.mjs` 失败/超时用例 | PASS |
| helper 启动失败窗口保持并报错 | `01-click-helper-fail/03-after-confirm.png`，状态含 helper-start-failed | PASS |
| 应用关闭后 helper 安装并自动重开 | P2-20 真实闭环（旧 1.6.0→新版 1.7.1） | P2-20 |
| 安装中双开被拦截 | P2-16 install.lock 测试 | PASS |
| 安装中强杀/断电后恢复 | P2-16 五阶段强制退出 recover 测试 | PASS |
| 成功/失败下次启动提示 | 启动判定/notice 测试 + recovery 截图 | PASS |
| 端口/文件锁超时只中止 | P2-13 超时测试 | PASS |
| 磁盘不足/Program Files 弹窗 | `02-disk-low/03-after-confirm.png`、`03-program-files/03-after-confirm.png` | PASS |
| 自动下载开关（默认关） | `update-auto.test.mjs` 17 条 + 设置页静态契约 | PASS |

## 4. GUI 截图

- `01-click-helper-fail/02-confirm-dialog.png`：真实点击“立即安装”后的确认框；
- `01-click-helper-fail/03-after-confirm.png`：helper 启动失败，窗口保持并显示错误；
- `02-disk-low/03-after-confirm.png`：磁盘不足错误，窗口保持；
- `03-program-files/03-after-confirm.png`：Program Files 错误；
- `05-recover-prompt/native-dialog.png`：启动时 recover 后“已恢复旧版本并重新启动”原生提示；
- 启动 apply 提示：`p2-19-gui-log.txt` 记录 `startup decision: prompt-apply` 与提示文案。

## 5. 契约测试

`test_update_contracts.py` 断言：

- update-apply：消息类型/requestId/来源限制/启动判定/UI 流程/字段；
- update-install：journal 七阶段、state 字段、锁 30 分钟、recover、健康码、300ms/21s、300MB/Program Files。

## 6. 遗留

- 原生启动 apply 提示截图为窗口截图限制，已用 launcher.log 存档；
- 自动下载开关实现已补（P2-08 前置），P2-08 完成标记未改；
- 真实成功闭环见 P2-20。
