# PaperLibrary v1.0 Nexus（Nexus 文献库）

PaperLibrary（Python 分析引擎）与 Sci-lib（Web 壳）合并后的单产品：
一个入口、一份 SQLite 数据、一套功能，单 Python runtime、离线可用、≤120MB。

## 运行（开发）

    python -m paperlib --port 4321            # 启动，自动打开浏览器
    python -m paperlib --port 4321 --no-open  # 不自动开浏览器
    python -m paperlib --stop                 # 停止正在运行的服务

数据目录默认 ./data（可用 --data-dir 指定）。首次运行会播种 1 条示例文献（data/.seeded 标记）。

## 目录

    public/      前端壳（ESM，PaperLibrary 品牌；9 视图含 3 个合并新视图）
    shared/      契约文档 + 前后端共用纯函数（Python 移植在 shared/pure/）
    paperlib/    Python 后端（http / api / domain / adapters / data）
    vendor/      pdfjs（内置阅读器）+ katex（可选公式）
    tools/       契约测试、e2e、bench、便携打包、启动器
    test/        unittest 用例
    docs/        工程计划 v2、前置核查、内部接口、工程报告

## 测试

    python -m unittest discover -s test -v
    python tools/contract_check.py           # 64 端点契约快照
    python tools/e2e.py                      # 端到端（含 Node 参照 diff）
    python tools/bench.py                    # 体积/启动/内存基线

## 数据与隐私

- data/ 不入 git；settings.json / websearch.json / sources.json 里有 API Key（明文、只在本机）。
- 发布包不包含任何用户数据。

## 合并来源

- Sci-lib（Node 壳，重构后作为参考实现保留在原仓库，不删除）
- PaperLibrary（Python 引擎，复用的 23 个模块移植到 paperlib/legacy/）

详见 docs/工程计划-文献库合并-20261107.md（v2）与 docs/前置核查报告-20261107.md。
