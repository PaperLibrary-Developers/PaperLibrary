# P2-12 Helper 部署位置与包外自举

日期：2026-11-16　阶段 2E-1 / P2-12　状态：已完成并核验

## 1. 实现

新增包外 Updater Helper 的**骨架、构建脚本、版本标识与部署模块**（安装替换/回滚/健康检查
留给 P2-13~P2-17，本步不做）：

- `tools/updater/PaperLibraryUpdater.cs`：单文件 C#（.NET Framework 4.x，csc 单文件 exe），
  提供 `version` / `self-check` / `hold` / `wait-pid` 四个诊断命令；
- `tools/make_updater.ps1`：从包根 `version.json` 读取版本，生成 `HelperInfo` 常量并编译到
  `<package>/tools/updater/PaperLibraryUpdater.exe`，同时写 `helper.json`（版本/大小/SHA-256）；
  显式拒绝 helper 出现在包根；
- `paperlib/update/helper.py`：包内 helper 校验 + 部署到
  `%LOCALAPPDATA%\PaperLibrary\updater\<version>\` + `current.json` 原子写 + 版本保留策略；
- `tools/deploy_updater.py`：部署命令行入口（排查/测试用）；
- `tools/release.py`：打包时自动构建 helper，校验“不在包根”、版本与 version.json 一致，
  写入 `release-manifest.json.entry.updater`；
- `tools/package_zip.py`：zip 必须包含 `tools/updater/PaperLibraryUpdater.exe` 与 `helper.json`；
- `.gitignore`：忽略 helper 构建产物（仓库只保留源码）；
- 契约：`shared/contracts/update-helper.md`（包内位置、包外布局、current.json/helper.json 字段、
  命令行与退出码、升级/保留策略、待确认项）。

设计要点：

1. helper **不在包根**，随包位于 `tools/updater/`；应用部署到包外后才运行。
2. 部署按版本目录：`updater/<version>/PaperLibraryUpdater.exe`。安装期间程序目录被替换时，
   正在运行的 helper 在包外，不受影响；升级 helper 时新版本写新目录，旧版本（可能仍在运行）
   不被覆盖。
3. 部署前校验 `helper.json` 的 schema/product/version/tag/size/SHA-256；包外读取
   `current.json` 时重新校验哈希；失败不留半个可执行文件（临时文件 + `os.replace`）。
4. `current.json` 记录 `previousVersion`；默认保留当前与上一版本，其余版本目录清理。

## 2. 真实验证（仓库外临时目录，真实发布包）

环境：`python tools/release.py --out <TEMP>/pl-p2-12-evidence-…` 生成真实发布包
（**95.0 MB**，runtime 82.4 / vendor 8.5 / public 1.0 / code 2.0 MB），再用
`tools/deploy_updater.py` 部署到临时 `updater-home`。

### 2.1 helper 在包内、不在包根

```text
root helper exists: False
tools/updater helper exists: True
helper bytes: 15872
helper sha256: b26c1361aadb8c4b3fb9b8e62b9f3b3bfd86f187903413bcf98abda91208d947
helper.json: {"schema":1,"product":"PaperLibrary","version":"1.7.1","tag":"v1.7.1",
              "exe":"PaperLibraryUpdater.exe","bytes":15872,
              "sha256":"b26c1361…08d947", …}
release-manifest entry.updater:
{"exe":"tools/updater/PaperLibraryUpdater.exe","meta":"tools/updater/helper.json",
 "version":"1.7.1","tag":"v1.7.1","sha256":"b26c1361…08d947",
 "note":"包外自举：复制到 %LOCALAPPDATA%/PaperLibrary/updater/<version>/ 后运行，不随程序目录替换"}
```

### 2.2 部署到包外

```text
$ python tools/deploy_updater.py --package-root <package> --home <TEMP>/updater-home --json
{"deployed": true, "reason": "deployed", "version": "1.7.1", "tag": "v1.7.1",
 "sha256": "b26c1361…08d947", "bytes": 15872,
 "helperPath": "<TEMP>/updater-home/1.7.1/PaperLibraryUpdater.exe",
 "updaterHome": "<TEMP>/updater-home", "previousVersion": ""}
```

`current.json`：

```json
{
  "schema": 1, "product": "PaperLibrary", "version": "1.7.1", "tag": "v1.7.1",
  "exe": "1.7.1/PaperLibraryUpdater.exe", "meta": "1.7.1/helper.json",
  "sha256": "b26c1361aadb8c4b3fb9b8e62b9f3b3bfd86f187903413bcf98abda91208d947",
  "bytes": 15872, "deployedAt": "2026-11-15T17:36:04Z",
  "previousVersion": "", "source": "tools/updater"
}
```

### 2.3 部署后 helper 身份与自检

```text
$ <TEMP>/updater-home/1.7.1/PaperLibraryUpdater.exe --version --updater-home <TEMP>/updater-home
{"ok":true,"command":"version","product":"PaperLibrary","schema":1,
 "helperVersion":"1.7.1","helperTag":"v1.7.1",
 "exe":"<TEMP>/updater-home/1.7.1/PaperLibraryUpdater.exe",
 "exeSha256":"b26c1361…08d947","updaterHome":"<TEMP>/updater-home",
 "pid":40380,"clr":"4.0.30319.42000","arch":"x64", …}
version exit code: 0

$ … --self-check --program-root <package> --updater-home <TEMP>/updater-home
{"ok":true, …,"checks":[
 {"name":"helper-not-at-package-root","ok":true,…},
 {"name":"helper-outside-program-root","ok":true,…},
 {"name":"helper-inside-updater-home","ok":true,…},
 {"name":"program-root-exists","ok":true,…},
 {"name":"updater-home-writable","ok":true,…},
 {"name":"log-writable","ok":true,…}]}
self-check exit code: 0
```

对照：直接运行包内副本（`<package>/tools/updater/PaperLibraryUpdater.exe --self-check`）会
退出 3，且 `helper-not-at-package-root=true`、`helper-outside-program-root=false`、
`helper-inside-updater-home=false`——明确报告“这是包内副本、尚未部署”，不会被误当成可安装状态。

### 2.4 程序目录被替换期间 helper 可运行

复制真实发布包为 `program-sim`，启动包外 helper `--hold 6`，随后删除
`program-sim/tools/updater` 与 `program-sim/PaperLibrary.exe`：

```text
helper pid: 90524 running: True
after replacing program dir, helper still running: True
helper exit code: 0
2026-11-16 02:36:08 hold start pid=90524 seconds=6 home=<TEMP>/updater-home
2026-11-16 02:36:09 hold alive pid=90524 t=1
…
2026-11-16 02:36:14 hold end pid=90524
deployed helper still exists after replacement: True
package copy removed: True
```

### 2.5 helper 自身可升级（1.7.1 → 1.7.2）

复制发布包、把 `version.json` 改为 1.7.2 后重新构建 helper，再部署到同一 updater home；
升级期间旧 1.7.1 helper 仍在运行：

```text
updater helper version: 1.7.2 (v1.7.2)
updater helper sha256: 43e828a19233dc4bc0f1719df1214cf8ea0f8222b7a6cdbc05b5d6443935a753
old 1.7.1 helper running before upgrade: True
{"deployed": true, "version": "1.7.2", "previousVersion": "1.7.1", …}
old 1.7.1 helper still running after upgrade: True
current.json: {"version":"1.7.2", "exe":"1.7.2/PaperLibraryUpdater.exe",
               "previousVersion":"1.7.1", …}
version dirs: 1.7.1, 1.7.2
new helper --version: {"helperVersion":"1.7.2","helperTag":"v1.7.2", …}
old helper exit code after its hold: 0
```

## 3. 单测（test/test_update_helper.py，24 条）

- 部署路径与 `current.json` 字段；包根出现 helper 被拒；
- 幂等部署；升级切换 current 且保留旧版本；保留策略只留两个版本；
- SHA-256/大小/tag 不符拒绝；缺 exe/meta 拒绝；损坏 current.json 重新部署；
- 部署后篡改 helper → 不再被视为当前可用；
- 真实编译 helper：构建产物在 `tools/updater`、`--version`、部署后 `--self-check` 通过、
  包内副本自检报告未部署、`--hold` 期间程序目录被删除仍存活、`--wait-pid` 超时/退出；
- 部署 CLI 成功与缺 helper 失败。

## 4. 验收对照

| P2-12 验收项 | 结果 | 证据 |
|---|---|---|
| helper 不在包根 | PASS | 真实包 `root helper exists: False`；`tools/updater/PaperLibraryUpdater.exe` 存在；release.py/package_zip 双重校验 |
| 程序替换期间 helper 可运行 | PASS | 真实包副本删除 `tools/updater` 与 exe 后 helper 仍运行并正常退出（2.4） |
| helper 自身可升级 | PASS（机制） | 1.7.1 → 1.7.2 新版本目录 + current 切换 + 旧版本保留 + 旧进程不受影响（2.5）；升级策略本身仍列为待确认项 |

## 5. 测试

- `python -m unittest test.test_update_helper -v` → **24 tests OK**；
- 全量：`python -m unittest discover -s test` → **655 tests OK（1 skip）**（基线 631 + 24，无新增失败）；
- 契约：`python tools/contract_check.py` → **PASS 61 / FAIL 0 / SKIP 13（共 74）**，无回归；
- 版本：`python tools/version_check.py` 通过（v1.7.1）。

## 6. 未做与待确认

- 本步只做 helper 骨架/部署/版本标识，**不做**启动器集成（P2-10/P2-11）、替换/备份/journal/
  回滚/健康检查（P2-13~P2-17）；
- 待确认（沿用 todolist）：**helper 自身升级策略**（当前为随应用版本、版本目录 + current.json；
  可选固定版本/独立下载）、**旧版本保留数量/时长**（当前 2 个版本）、helper 等待超时参数（P2-13 定）；
- 真实安装/回滚需在后续步骤用模拟程序目录 + 真实包验证，不使用假数据替代。
