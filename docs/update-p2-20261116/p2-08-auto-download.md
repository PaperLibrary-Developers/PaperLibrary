# P2-08 自动下载开关（P2-19 前置）

日期：2026-11-17　决策：第一阶段加入自动下载开关，**默认关**，仍不静默安装。

## 实现

- 新增 prefs `updateAutoDownload`：仅显式 `true` 开启，默认/缺失为关。
- 设置页新增“自动下载更新（默认关；下载后仍需点击立即安装）”开关与说明。
- `update-auto.mjs` 发现新版本时：开启自动下载 → 调 `download()` 自动下载，
  不弹普通“发现新版本”通知；未开启 → 保持只通知。
- 自动下载失败静默；下载完成仍由用户点击“立即安装”，无自动安装路径。
- 自动检查/24h 节流/跳过版本逻辑不变；跳过版本不自动下载。

## 测试

- `node test/js/update-auto.test.mjs` → 17 tests passed（新增自动下载 5 条）；
- `node --test test/js/update-install.test.mjs test/js/settings-install-contract.test.mjs`
  → 12 tests passed（含自动下载开关静态契约）；
- `python -m unittest test.test_update_auto` → 1 test OK（Node 17 条包装）。
