# 📚 PaperLibrary Nexus (v2.5) — 学术文献管理与多维横向对比科研中枢

基于 **Streamlit + PyMuPDF + SQLite + AI**，专为实验科学打造。支持 PDF 全文提取、精确到 Figure 级别的图表检索、以及独特的**跨论文多维结构化横向对比工作台**。

---

## 🚀 启动方式

| 方式 | 文件 | 说明 |
|------|------|------|
| **本地使用** | 双击 `启动文献检索系统.bat` | 自动打开浏览器 → localhost:8501 |
| **GUI 桌面版** | `python launcher_gui.py` | tkinter 状态窗口 + 自动启动 |
| **局域网共享** | 双击 `启动文献检索系统_局域网.bat` | 同 WiFi 下手机/平板也能访问 |
| **公网访问** | 双击 `启动公网隧道.bat` | 用 ngrok 或 Cloudflare 生成公网 URL |

---

## 📦 打包成安装包发给别人

### 方案 A：便携版（推荐 — 最可靠，首次分发）

```bash
python scripts/build_portable_release.py
```

生成桌面 `个人文献库_便携版.zip`（内嵌免配置独立 Python 运行时），发给师兄师姐解压即用，**内置空数据库，0 个人隐私泄露**。

### 🔄 方案 B：微型热升级补丁（日常发更新给师兄师姐 — 保留全部文献）

当系统推出新功能或修复问题时，**无需让对方重新下载 130MB 大包**，直接运行补丁构建脚本：

```bash
python scripts/build_update_patch.py
```

- **超轻体积**：一秒在桌面生成 `个人文献库_升级补丁.zip`（仅 **0.3 MB**，可在微信/群聊秒发）。
- **文献 100% 安全**：补丁包仅包含 `app` 纯代码文件，完全不碰 `data/`、`papers.db` 与用户的文献文件夹。
- **升级方法**：对方解压后，把里面的 `app` 文件夹直接拖入自己的文献库主目录**覆盖同名文件**（或双击补丁包内的 `【双击我一键升级·保留文献】.bat`）。重新双击启动，所有已有文献和笔记毫发无损，新功能瞬间生效！

### 方案 B：Setup.exe 安装包

1. 先运行 `build_portable.bat` 生成便携版
2. 安装 [Inno Setup](https://jrsoftware.org/isinfo.php)
3. 运行 `iscc setup.iss`
4. 输出 `dist\PaperLibrary_Setup_v1.0.exe`

安装包支持：开始菜单快捷方式、桌面图标、标准卸载程序。

### 方案 C：PyInstaller 单文件

```bash
build_exe.bat
```

生成 `dist\PaperLibrary\PaperLibrary.exe`，单文件夹分发。

---

## 🌍 长期公网访问方案

### 方案 A：Cloudflare Tunnel（推荐 — 完全免费、无限制）

```
特点: ✅ 完全免费  ✅ 无流量限制  ✅ 无时长限制  ✅ 适合 7×24 长期运行
缺点: ⚠️ 快速隧道模式每次重启 URL 会变（配置域名后可固定）
```

**安装步骤**：

1. 下载 cloudflared：https://github.com/cloudflare/cloudflared/releases
   - 选择 `cloudflared-windows-amd64.exe`，重命名为 `cloudflared.exe`
   - 放到 `C:\Windows\System32\` 或本项目目录下

2. 运行 `python tunnel.py cloudflare` 即可获得公网 URL

**固定域名（可选）**：如果你有域名托管在 Cloudflare，可以绑定永久 URL。

### 方案 B：ngrok（简单易用 — 免费固定域名）

```
特点: ✅ 1 个免费固定域名（URL 永久不变）  ✅ 无限隧道时长
限制: ⚠️ 1GB/月流量
```

**安装步骤**：

1. 注册 ngrok 账号：https://ngrok.com/signup
2. 下载 ngrok：https://ngrok.com/download
3. 运行 `ngrok config add-authtoken <你的token>`
4. 获取免费固定域名：https://dashboard.ngrok.com/cloud-edge/domains
5. 运行 `python tunnel.py setup` 填入你的固定域名
6. 之后每次运行 `python tunnel.py ngrok` 即可，URL 永久不变

**ngrok 免费版已更新（2026）**：不再有 2 小时断连限制，可以长期在线。

---

## 📥 如何更新文献库

新增 PDF 文献的流程极其简单：

```
1. 把新的 PDF 论文拖入 y:\私人\PAPER\ 下的对应子文件夹
2. 在网页侧边栏点击「🔄 更新文献库」
3. 系统自动检测新文件，仅导入未入库的（已入库的自动跳过）
4. 更新完成！新论文即刻可搜索
```

**关键特性**：
- **增量更新**：不会重复处理已入库的论文（SHA256 哈希去重）
- **实时检测**：侧边栏会显示「🆕 待导入: N 篇」
- **文件夹组织**：支持按子文件夹分类（羰基还原、交叉偶联、Cu 等）
- **不改动原文件**：PDF 保持原位，系统只提取文本和图片到 `data/` 目录

---

## 🤖 AI 提炼

新建文献导入后，下一步是 AI 提炼：

1. 侧边栏配置 API Key（DeepSeek 或 Claude）
2. 点击「🧠 AI 总结全部」
3. AI 会为每篇论文生成：
   - 结构化中文总结（背景、方法、发现、创新点、关键词）
   - 每张图片的深度文字描述和检索标签

之后在「🔬 图片精确检索」页签中，就能用自然语言搜索到每一张图表。

---

## 🧠 深度分析

论文详情页（点击任一论文进入）顶部多了一排按钮：

| 按钮 | 作用 |
|---|---|
| 🧠 生成深度分析 | 按学术评审的骨架产出一篇读稿：核心信息、中文概述、研究背景、方法拆解（含关键公式）、实验证据（体系/基线/条件/指标/消融）、**作者主张 vs 证据判断分开列**、优势、局限（明确区分「作者自己承认的」和「我推断的」）、适用边界、五维评分表（创新性/技术质量/证据充分性/写作/实用性，各带理由） |
| 🔎 arXiv 高清图表升级 | 从 arXiv 源码包（`arxiv.org/e-print/<id>`）里扒原始插图：矢量图直接 300 DPI 渲染成 PNG，比从 PDF 里抠的位图清晰得多。识别不到 arXiv ID 时会说明原因 |
| 📤 导出 Obsidian | 把深度分析 + 元数据 + 图片导出成带 YAML frontmatter 的 Markdown 笔记，图片用 `![[...]]` 双链，评分写成 `quality_score`，可直接丢进 Obsidian vault |

深度分析结果会存进数据库（`papers.deep_analysis` / `deep_scores`），重开页面不用重新花钱。

---

## 🧭 文献横向对比工作台（新）

支持跨篇多选同类课题文献，通过 **Map-Reduce** 架构实现精准结构化矩阵提取与深度综合对比：

1. **多选加篮**：在文献卡片点击「＋ 对比」按钮，多篇论文加入对比篮（支持 P1..Pn 标识）；
2. **专业化学与材料预设**：
   - **化学有机合成 / 催化（11 维）**：反应体系、催化剂与配体、反应条件、产率、立体/区域选择性、底物普适性、机理、关键表征、局限性、核心创新点等；
   - **电化学 / 电催化（9 维）**：电解质体系、电催化剂材料、过电位/电位窗口、Tafel斜率、法拉第效率与稳定性等；
   - **功能材料 / 晶态材料 (MOF/COF)（9 维）**：前驱体单体、合成工艺、BET比表面积/孔径、性能指标与稳定性；
   - **通用对比（5 维）** + 支持自定义任意补充维度。
3. **两步流 Map-Reduce（保真防截断）**：
   - **Map（单篇抽取）**：每篇论文在独立上下文抽取并提取原文短句佐证（Evidence），自动持久化存入 SQLite `compare_cache` 表；换文献组合完全复用缓存，绝不重复消耗 Token；
   - **Reduce（跨篇综述）**：AI 基于结构化事实产出 7 节深度综述，包含体系共识、定量关键分歧、演进脉络与科学空白（Research Gap）。
4. **表格交付与导出**：
   - 交互式横向矩阵（隐藏空值、按维度横排并列）；
   - 一键下载完整分析 Markdown 研报；
   - 一键下载 UTF-8 BOM 编码的 CSV 矩阵表（Excel 双击直接打开不乱码）。

---

## 🔬 图片级检索的两种图源

| 来源 | 画质 | 说明 |
|---|---|---|
| PDF 内嵌图（默认，导入时自动做） | 一般 | PyMuPDF 抠 `page.get_images()`，受原 PDF 位图分辨率限制 |
| **arXiv 源码包（手动升级）** | 高 | 矢量 PDF 图 300 DPI 渲染，实测能拿到 3600×1409、6900×2850 这种尺寸 |

升级后的图落在 `data/images_arxiv/<arxiv_id>/`，和原图并存不覆盖。

---

## 📁 项目结构

```
PAPER/
├── 启动文献检索系统.bat          ← 双击启动（本地）
├── 启动文献检索系统_局域网.bat    ← 双击启动（局域网共享）
├── 启动公网隧道.bat              ← 双击启动（公网隧道）
├── app.py                        ← Streamlit 主界面
├── compare_engine.py             ← 文献横向对比引擎（化学模板/Map-Reduce抽取/综述/导出）
├── database.py                   ← SQLite + FTS5 全文搜索 + 对比缓存
├── parser.py                     ← PyMuPDF 多模态解析
├── ai_engine.py                  ← DeepSeek / Claude API
├── deep_analysis.py              ← 深度分析报告引擎（作者主张 vs 证据判断分离）
├── figures_arxiv.py              ← 从 arXiv 源码包取高清矢量图
├── obsidian_export.py            ← 导出 Obsidian 笔记（YAML + wikilink）
├── tunnel.py                     ← 公网隧道管理器
├── launcher.py                   ← Python 启动器
├── requirements.txt              ← Python 依赖
├── data/
│   ├── papers.db                 ← SQLite 数据库（93 篇 / 3435 张图）
│   ├── images/                   ← 从 PDF 里抠出来的图片
│   └── images_arxiv/             ← arXiv 源码包渲染的高清图（按 arxiv_id 分目录）
└── 你的 PDF 论文...               ← 原始 PDF 不动
```

---

## 🔧 首次安装

```bash
cd y:\私人\PAPER
pip install -r requirements.txt
```

环境要求：Python >= 3.10

---

## 📝 常见问题

**Q: 每次更新文献库会很慢吗？**
A: 不会。只有新增的 PDF 会被解析，已入库的自动跳过。93 篇全部扫描约 2-5 分钟，增量更新通常几十秒。

**Q: 公网 URL 关机后会失效吗？**
A: ngrok 固定域名模式下，重新开机后运行 `python tunnel.py ngrok`，同一个 URL 即刻恢复。Cloudflare Tunnel 也是一样。

**Q: 删除本地 PDF 后文献库会同步吗？**
A: 目前需要手动操作。数据库中的记录和提取的图片会保留（作为历史存档）。

**Q: 我能用手机访问吗？**
A: 可以！使用局域网模式（同一 WiFi）或公网隧道模式（任意网络）。

---

## 📦 打包分发给别人（便携版）

生成「解压即用、无需装 Python」的压缩包：

```bat
python scripts\build_portable_release.py
```

产物默认落在桌面：

| 路径 | 说明 |
|---|---|
| `桌面\个人文献库_便携版\` | 目录形态（约 400MB），可直接运行 |
| `桌面\个人文献库_便携版.zip` | 分发用压缩包（约 130MB） |

打包内容 = **内置 Python 3.11 运行时**（本机 `C:\Program Files\Python311` + 用户 site-packages 合并）
\+ 程序源码 + 空白数据库 + `【文献存放处】\` 空目录 + 启动器 + 使用说明，**不含任何个人文献/图片**。

对方使用流程：解压 → 双击 `启动个人文献库.bat` → 等 20~40 秒 → 浏览器自动打开，
同时桌面自动生成「个人文献库」快捷方式（由 `start_all.py: ensure_desktop_shortcut()` 生成）。

说明文档源文件：`scripts/README_portable.md`（改成 HTML/TXT 并重新压包：`python scripts/add_readme_to_release.py`）。
