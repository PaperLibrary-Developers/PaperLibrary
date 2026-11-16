# P2-09R 立即安装确认与 UI 请求

日期：2026-11-17　阶段 2C / P2-09R　状态：已完成并核验（启动器执行由 P2-11R 完成）

## 1. 实现

- 新增 `public/js/update-install.mjs`：
  - 冻结协议 `paperlibrary:install.request/result`；
  - requestId 生成、来源/结果类型校验、20 秒超时、JSON 字符串/对象两种消息；
  - 非 WebView2 返回 `not-webview`，供设置页降级。
- `public/js/views/settings.mjs`：
  - 按钮改为“立即安装”；确认框明确：立即关闭、安装后自动重开、data/ 保留、
    进行中的 AI 请求会中断、不会静默安装；
  - 确认后先 `POST /api/update/apply`，成功再 `requestInstall({version})`；
  - 启动器 `ok:true` → “正在关闭并安装…”并禁用按钮；失败/超时 → 窗口保持并显示 code/message；
  - 浏览器预览 → apply 成功后显示“请关闭窗口后安装”降级提示；
  - `installing` 防重复，取消确认不改任何状态。
- 新增 `tools/update_install_cdp.mjs`：连接 WebView2 CDP，点击立即安装、
  截取确认框/关闭前状态；供 P2-11R/P2-19 GUI 验收使用。
- 契约：`shared/contracts/update-apply.md` 第 7 节 UI 立即安装流程。

## 2. 测试

命令与结果：

    node --test test/js/update-install.test.mjs test/js/settings-install-contract.test.mjs
    tests 11
    pass 11
    fail 0

覆盖：

- 协议请求/成功/失败/超时/JSON 字符串/非 WebView 拒绝；
- requestId 唯一、监听器清理、默认超时 20s；
- 设置页使用握手模块、apply 先于请求、确认文案覆盖立即关闭/自动重开/data/AI 中断/
  不静默安装、无旧“关闭窗口后自动更新”文案。

CDP 脚本语法：

    node --check tools/update_install_cdp.mjs

## 3. 验收对照

| P2-09R 验收项 | 结果 | 证据 |
|---|---|---|
| 确认后立即发出安装请求 | PASS | 确认→apply→requestInstall；Node 测试验证请求/结果 |
| 握手失败/启动器拒绝/超时窗口不关闭 | PASS | requestInstall reject；设置页 catch 后保留按钮并显示错误 |
| 非 WebView2 有降级提示 | PASS | isWebViewHost/fallbackInstallMessage 测试 |
| 拒绝确认保持现状 | PASS | 取消分支仅提示“已取消安装” |
| 无静默安装 | PASS | 只有确认框通过后才 apply/request |
| 文案覆盖关闭、自动重开、数据保留、AI 中断 | PASS | 静态契约测试断言 |

## 4. 测试与契约

- Node：11 tests OK（本步新增 11；原有 update-auto 另 12 条仍通过）；
- Python 全量：706 tests OK（1 skip）基线；本步不改 Python，新增前端测试单独运行；
- 契约：`update-apply.md` 第 7 节。

## 5. 遗留

- 真实点击→启动器关闭→helper 安装→自动重开，需要 P2-11R 的启动器执行路径；
  GUI 截图/CDP 在 P2-11R/P2-19 运行。
- 浏览器预览仅降级提示，不提供自动安装。
