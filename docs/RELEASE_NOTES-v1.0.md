# PaperLibrary v1.0（合并版）发布说明

Sci-lib（Web 壳）与 PaperLibrary（Python 分析引擎）合并为单产品：一个入口、一份 SQLite 数据、单 Python runtime、离线可用。

## 验收数字

| 项 | 结果 |
|---|---|
| 契约快照（原 64 端点口径） | PASS 59 / FAIL 0 / SKIP 10（69 行） |
| Node 参照跨语言 e2e | 37 步 / 0 字段差异 |
| 全量单测 | 360 tests OK |
| 真实库迁移 | 75 篇 / 1,441 图 / 75 全文（3.87M 字符），二次运行零写入 |
| 发布包 | 92.4MB（manifest 702 文件），≤120MB |
| 冷启动 / 内存 | health ≈1.3s / RSS 62MB |
| 界面 | 6 旧视图 + 3 新视图（图表画廊/横向对比/深度分析），0 环 ESM |

## 包含内容

- 前端 public/（ESM 单入口，PaperLibrary 品牌，内置 pdfjs/KaTeX）
- 后端 paperlib/（stdlib HTTP + 路由表 + 领域层 + 适配器 + SQLite 写穿 + PaperLibrary 能力移植）
- 便捷启动：启动.cmd / 停止.cmd（自带 Python 3.11.9 便携运行时 + PyMuPDF + Pillow）
- 文档：工程计划 v2、前置核查、功能回归表、离线清单、工程报告 v1、架构图
- 测试与工具：test/ 360 用例、tools/contract_check.py、tools/e2e.py、tools/bench.py、tools/release.py

## 用法

解压后双击 启动.cmd（默认 http://127.0.0.1:4321）。数据在 data/（首次启动自动创建；可把旧库 data/library.json 或 data/papers.db 放进去自动合并）。

## 已知偏差

- e2e 覆盖 37 步（约 30 条端点由单测/冒烟覆盖）；
- 前端 5 个既有视图 >600 行（继承批次 1 backlog）；
- 旧库图表含非图表图；Obsidian 全量导出约 275MB（含 media）。
详见 docs/功能回归表-20261107.md 与 docs/工程报告v1.md。
