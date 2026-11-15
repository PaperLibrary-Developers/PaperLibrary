# PaperLibrary Nexus（Nexus 文献库）

PaperLibrary（Python 分析引擎）与 Sci-lib（Web 壳）合并后的单产品：
一个入口、一份 SQLite 数据、一套功能，单 Python runtime、离线可用、≤120MB。

## 运行（开发）

依赖只有 PyMuPDF 与 Pillow，其余全是标准库（发布包已内置便携运行时，无需安装）：

    python -m pip install -r requirements.txt

Windows 发布包的唯一入口是双击 `PaperLibrary.exe`：后台启动 Python 服务后，打开一个自带的
桌面窗口（WinForms + Microsoft Edge WebView2）载入现有 `public/` 界面，不再打开外部浏览器；
关闭窗口时保存数据并停止服务。需要系统装有 WebView2 运行时（Windows 11 自带，Win10 装了新版 Edge 一般也有）。

发布包根目录只有一个可见入口 `PaperLibrary.exe`；同目录的 `PaperLibrary.exe.config` 是 .NET 运行配置
（已设为隐藏文件），不是启动器。.NET CLR 只读取这个固定文件名（`<exe 文件名>.config`），请勿改名或删除。

    python -m paperlib --port 4321            # 源码运行：启动并用默认浏览器打开
    python -m paperlib --port 4321 --no-open  # 源码运行：只起服务，不自动开浏览器
    python -m paperlib --stop                 # 命令行停止（开发 / 排错用）

数据目录默认 ./data（可用 --data-dir 指定）。首次运行会播种 1 条示例文献（data/.seeded 标记）。

服务会盯着有没有网页连着（前端每个页面一条 `/api/presence` SSE；发布包的内置窗口也算一个页面）：**关掉所有页面 30 秒后自动保存并退出、释放端口**，
不用再手动 stop。时间可用 `--idle-exit 秒数` 调整（0 = 不自动停），启动宽限用 `--idle-grace 秒数`（默认 8）。
自动化脚本（contract/e2e/bench）启动服务时都显式传了 `--idle-exit 0`，不会被自动停机打断。

## 目录

    public/      前端壳（ESM，PaperLibrary 品牌；视图在 public/js/views/）
    shared/      契约文档 + 前后端共用纯函数（Python 移植在 shared/pure/）
    paperlib/    Python 后端（http / api / domain / adapters / data）
    vendor/      pdfjs（内置阅读器）+ katex（可选公式）
    tools/       契约测试、e2e、bench、便携打包、启动器
    test/        unittest 用例
    docs/        工程计划 v2、前置核查、内部接口、工程报告（不入库，见下节）

## 测试

    python -m unittest discover -s test -v
    python tools/contract_check.py           # 64 端点契约快照
    python tools/e2e.py                      # 端到端（含 Node 参照 diff）
    python tools/bench.py                    # 体积/启动/内存基线

## 数据与隐私

- data/ 不入 git；settings.json / websearch.json / sources.json 里有 API Key（明文、只在本机）。
- 发布包不包含任何用户数据。

## 关于 docs/（只在本机）

`docs/` 按用户要求 **不纳入版本库**（`.gitignore` 里有 `/docs/`），因此克隆本仓库后看不到
工程计划、前置核查报告、工程报告 v1、功能回归表、离线验收清单等文档——这不是缺文件。
这些文档只保留在作者本机的 `docs/` 目录里；需要对外发布时应单独拷贝，不要指望从 git 里取。

对外可交付的契约以 `shared/contracts/api.md`（端点契约）、`shared/contracts/data-model.md`
（数据模型）、`shared/contracts/api-snapshot.json`（快照）为准，这三个是入库的。

## 合并来源

- Sci-lib（Node 壳，重构后作为参考实现保留在原仓库，不删除）
- PaperLibrary（Python 引擎，复用的 23 个模块移植到 paperlib/legacy/）

详见 docs/工程计划-文献库合并-20261107.md（v2）与 docs/前置核查报告-20261107.md。
