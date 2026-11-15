# P2-09 安装确认提示（强制用户确认）

日期：2026-11-16　阶段 2C / P2-09　状态：已完成并核验

## 1. 实现

- @public/js/api.mjs@：新增 @applyUpdate()@ → @POST /api/update/apply@；
- @public/js/views/settings.mjs@：ready 状态显示「重启并安装」按钮；点击后弹出确认对话框，
  文案明确：
  - 关闭窗口时安装新版本，安装完成后自动重新打开；
  - 文献库数据（@data/@）会保留；
  - 正在进行的 AI 请求会被中断；
  - **不会静默安装**：点「稍后」保持当前版本，不做任何修改；
- 只有用户点「准备安装」才调用 apply；取消/关闭对话框 → 不写 apply.json、状态提示“已取消安装准备”；
- 确认成功后状态显示「安装包已准备好：vX（关闭窗口后自动安装，数据保留）」；
- 真正的替换/重启仍由后续阶段（2D/2E 启动器与 helper）执行，本步只写安装标记。

## 2. 真实验收（临时 1.6.0 副本 + 公网 v1.7.1，headless 浏览器）

| 步骤 | 结果 |
|---|---|
| 下载到 ready | 出现「重启并安装」按钮 |
| 打开确认框 | 文案包含“关闭窗口”“数据”“中断”“不会静默安装”；截图见下 |
| 取消确认 | 对话框关闭；状态「已取消安装准备，保持当前版本」；**apply.json 不存在** |
| 再次确认安装 | 状态「安装包已准备好：v1.7.1（关闭窗口后自动安装，数据保留）」；apply.json 存在 |
| apply.json 字段 | version=1.7.1、fromVersion=1.6.0、staging/backup 路径、package/manifest 哈希、714 文件/99,436,397 字节齐全 |
| JS 错误 | 0 |

截图：

- @docs/update-p2-20261116/screenshots/settings-update-install-confirm.png@（确认框）
- @docs/update-p2-20261116/screenshots/settings-update-install-prepared.png@（已准备安装）

## 3. 验收对照

| P2-09 验收项 | 结果 |
|---|---|
| 明确告知关闭窗口、自动重开、数据保留 | PASS |
| 提示 AI 请求会被中断 | PASS |
| 用户确认后才安装 | PASS（取消不写 apply.json） |
| 无静默替换路径 | PASS（唯一 apply 入口在确认框之后；后续执行由启动器 helper 负责） |
| 拒绝确认保持现状 | PASS |

## 4. 测试与契约

- 本步只改前端（api/settings）；@python -m unittest discover -s test@ 仍 **631 tests OK（1 skip）**；
- 契约不变：**PASS 61 / FAIL 0 / SKIP 13（共 74）**；
- 真实 UI 证据由 CDP 脚本 @docs/update-p2-20261116/scripts/update-install-confirm.mjs@ 生成。
