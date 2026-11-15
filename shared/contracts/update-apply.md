# apply.json 契约（安装标记，schema=1）

> 阶段 2B / P2-07 定义。后端只负责在 staging 完整时写这个标记；
> **真正的替换/回滚/重启由后续阶段的启动器与包外 helper 执行**（P2-10~P2-17），本文件不涉及执行。

## 1. 位置与写入时机

- 路径：`<data>/updates/apply.json`（数据目录在包内或外部时结构不变）；
- 写入：`POST /api/update/apply` 在以下条件全部满足后**原子写入**：
  - 该版本 staging 存在，且包含 `PaperLibrary.exe` 与 `version.json`；
  - staging 内 `version.json` 的 version == 目标版本 == 清单版本（清单重新验签）；
  - 目标版本 > 当前 `version.json` 版本（拒绝相同/降级）；
  - 没有正在进行的下载任务；
- 幂等：同版本/同 staging/同备份路径重复 apply 返回 `idempotent=true`，不重复改写；
- 新开始一次下载时后端会清除旧 `apply.json`，避免启动器读到过期标记。

## 2. 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `schema` | int | 固定 1 |
| `version` | string | 新版本（x.y.z） |
| `tag` | string | 新版本 tag（v1.7.1） |
| `fromVersion` | string | 当前（旧）版本 |
| `fromTag` | string | 当前（旧）tag |
| `stagingPath` | string | 已校验 staging 的绝对路径 |
| `stagingRel` | string | staging 相对数据目录的路径（可移植记录） |
| `backupPath` | string | 旧程序文件备份目录的绝对路径（`updates/backups/app-<oldVersion>/`，由后续阶段创建/校验） |
| `backupRel` | string | 备份目录相对数据目录的路径 |
| `packageSha256` | string | 完整包 SHA-256（来自已验证的 staging 记录） |
| `manifestSha256` | string | 本次签名清单 SHA-256 |
| `files` / `bytes` | int | staging 文件数 / 解压字节数 |
| `createdAt` | string | UTC ISO8601 写入时间 |

示例（值会随版本变化）：

```json
{
  "schema": 1,
  "version": "1.7.1",
  "tag": "v1.7.1",
  "fromVersion": "1.7.0",
  "fromTag": "v1.7",
  "stagingPath": "<data>/updates/1.7.1/staging",
  "stagingRel": "updates/1.7.1/staging",
  "backupPath": "<data>/updates/backups/app-1.7.0",
  "backupRel": "updates/backups/app-1.7.0",
  "packageSha256": "e844ba33…",
  "manifestSha256": "b9c82a46…",
  "files": 714,
  "bytes": 99436397,
  "createdAt": "2026-11-16T00:00:00Z"
}
```

## 3. 启动器（后续阶段）读取约定

- 只在应用退出路径读取 `apply.json`；启动时若发现它存在，应提示/等待用户在界面确认重启安装，
  不得静默替换（原文 §7.3）；
- 读取后必须重新校验：`stagingPath` 仍在数据目录内、`PaperLibrary.exe` 与 `version.json` 存在、
  `version.json` 版本 == `version`；
- `data/` 原地保留，不随包替换；备份目录写入计划路径由 helper 在替换前创建；
- apply.json 只是“准备安装”的标记，不代表已安装；安装成功后应清理（后续阶段定义）。

## 4. 安全边界

- 后端不执行任何替换/命令；只写标记；
- 不信任客户端传入路径/版本：apply 全部从 staging/清单/当前 version.json 推导；
- apply.json 不含 API Key、用户数据或本机无关路径；路径字段仅指向数据目录内。
