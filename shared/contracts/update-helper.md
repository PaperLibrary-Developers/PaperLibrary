# Updater Helper 契约（包外更新助手）

版本：1　状态：P2-12 已实现（骨架）；安装时序/回滚/健康检查由 P2-13~P2-17 增补，不改本契约。

## 1. 目的与边界

Windows 无法可靠覆盖正在运行的 `PaperLibrary.exe`、WebView2 DLL 与 Python runtime，
启动器也不能替换自身。因此安装替换必须由一个**包外**的小体积 helper 执行：

- helper 是单文件 C#（.NET Framework 4.x，`csc` 编译）可执行程序；
- 随发布包分发，但**不在包根**，位于 `tools/updater/`；
- 应用把它复制到 `%LOCALAPPDATA%\PaperLibrary\updater\<version>\` 后从包外运行；
- 程序目录被整体替换时，helper 自身不在替换范围内。

本契约只描述 helper 的**分发、部署、版本标识与命令行接口**；安装替换、备份、journal、
回滚、健康检查的时序与字段由后续步骤补充（P2-14~P2-17），但不得改变这里的路径与
部署记录格式。程序文件替换范围与旧程序备份见 [update-install.md](update-install.md)。

## 2. 包内位置

```text
<package>/
  PaperLibrary.exe                 ← 启动器（不在本契约范围）
  version.json                     ← 产品版本唯一来源
  tools/
    make_updater.ps1               ← 构建脚本（随包，便于排查/重建）
    deploy_updater.py              ← 启动器调用的部署脚本
    updater/
      PaperLibraryUpdater.exe      ← helper（绝不放在包根）
      helper.json                  ← 版本 + 大小 + SHA-256 元数据
```

`release.py` 在打包时调用 `make_updater.ps1` 生成 helper，并把版本/SHA-256 写入
`release-manifest.json` 的 `entry.updater`。`package_zip.py` 校验 zip 内部署脚本、
`tools/updater/PaperLibraryUpdater.exe` 与 `helper.json` 都存在。

## 3. helper.json（构建产物）

固定字段（UTF-8 JSON）：

| 字段 | 类型 | 说明 |
|---|---|---|
| schema | int | 固定 `1` |
| product | string | 固定 `PaperLibrary` |
| version | string | `x.y.z`，与包根 `version.json` 一致 |
| tag | string | 与 `version.json` 的 tag 规则一致（`v1.7` / `v1.7.1`） |
| exe | string | 固定 `PaperLibraryUpdater.exe` |
| bytes | int | 可执行文件字节数 |
| sha256 | string | 小写 64 位十六进制 |
| builtAt | string | 构建时间（UTC ISO8601） |
| source | string | 固定 `tools/updater/PaperLibraryUpdater.cs` |
| runtime | string | 编译运行时说明 |

## 4. 包外部署布局

```text
%LOCALAPPDATA%\PaperLibrary\updater\
  current.json                    ← 当前应启动的 helper（原子写）
  1.7.1\PaperLibraryUpdater.exe
  1.7.1\helper.json
  1.7.2\PaperLibraryUpdater.exe   ← 升级后新增；旧版本目录保留
  1.7.2\helper.json
  logs\updater.log
```

`current.json` 字段：

| 字段 | 类型 | 说明 |
|---|---|---|
| schema | int | 固定 `1` |
| product | string | 固定 `PaperLibrary` |
| version | string | 当前 helper 版本 |
| tag | string | 对应发布标签 |
| exe | string | 相对 `updater/` 的路径，如 `1.7.1/PaperLibraryUpdater.exe` |
| meta | string | 相对路径，如 `1.7.1/helper.json` |
| sha256 | string | 当前 helper SHA-256（读取时重新校验） |
| bytes | int | 字节数 |
| deployedAt | string | 部署时间（UTC ISO8601） |
| previousVersion | string | 被替换的版本（首次部署为空） |
| source | string | 固定 `tools/updater` |

部署实现：`paperlib/update/helper.py`（`read_package_helper` / `deploy` /
`deployed_helper`）；命令行入口 `tools/deploy_updater.py`。

桌面启动器使用便携 Python 调用该部署脚本。普通启动在打开主界面前部署，更新验证启动在
新版本提交成功后部署。启动器保留已通过哈希校验的较新版本 helper。安装预检发现包外
helper 缺失或无效时重新尝试部署，再重新预检。
恢复时优先使用当前有效的包外 helper，缺失或无效时尝试从当前程序目录部署。启动时部署
失败会记入 `data/logs/launcher.log`，已通过启动检查的文献库仍可打开。

部署规则：

1. 包内 `helper.json` 的 schema/product/version/tag/size/SHA-256 全部校验通过才复制；
2. 复制到 `<home>/<version>/`，临时文件 + 原子替换，失败不留半个 exe；
3. 同一版本且哈希一致 → 幂等返回 `deployed=false`，不重写；
4. 升级 → 新版本目录 + `current.json` 指向新版本，`previousVersion` 记录旧版本；
5. 保留当前与上一版本，其余版本目录按 `keep` 清理（默认 2）。

## 5. 命令行接口

所有命令输出单行 JSON 到 stdout；诊断信息写日志（默认
`<updater-home>\logs\updater.log`）。日志/部署目录可用 `--updater-home` 覆盖
（测试用；生产不传）。

| 命令 | 作用 | 退出码 |
|---|---|---|
| `version` / `--version` | 输出 product/schema/helperVersion/helperTag/exe/exeSha256/updaterHome/pid/CLR/OS/arch | 0 |
| `self-check` / `--self-check` [`--program-root DIR`] | 诊断：helper 不在包根、位于程序目录之外、位于 updater home 内、home 可写、日志可写、程序目录存在 | 0 全部通过；3 有检查失败 |
| `hold <seconds>` | 存活指定秒数（自举/替换期间运行验证） | 0 |
| `wait-pid <pid>` [`--timeout SEC`] | 等待进程退出，默认 60 s | 0 已退出；4 超时 |
| `help` / `--help` | 用法 | 0 |

参数错误/未知命令 → 2；内部异常 → 5。

## 6. 安全与版本约束

- helper 不在包根；包根出现 `PaperLibraryUpdater.exe` 视为打包错误（`release.py` 拒绝）；
- 部署前校验包内 SHA-256；包外运行前由启动器/后端重新校验 `current.json` 记录的哈希；
- 版本号必须 `x.y.z`，部署路径必须位于 updater home 内（防路径注入）；
- helper 不读取清单 URL、不执行 manifest 中的任意脚本；安装输入（staging/备份路径）
  由 `apply.json` 提供并在后续步骤校验；
- helper 自身不写程序目录（P2-12 仅诊断/等待；替换在 P2-13 后由 helper 执行）。

## 7. 已确认决策

- **helper 自身升级策略**（todolist 待确认项 17，2026-11-16 确认）：保持当前实现——
  随应用版本升级，按版本目录部署（`updater/<version>/`），`current.json` 指向新版本；
  应用在启动/准备安装时用新包内 helper 部署，旧版本目录按保留策略清理；
  不单独从 Release 下载 helper，也不固定 helper 版本。

## 8. 待确认（不猜值）

1. **旧版本保留时长/数量**：当前保留当前与上一个版本（`DEFAULT_KEEP=2`）；是否改为
   “保留 1–7 天”或固定一个版本，待产品确认。
2. helper 等待启动器/Python 退出、端口/文件锁释放的超时与重试参数（P2-13 落地时确认）。
