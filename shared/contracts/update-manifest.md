# 更新清单 `update-manifest.json` 契约（schema=1）

> 状态：阶段 0 / **P0-02 已冻结**。实现：`paperlib/update/manifest.py`；
> 样例：`shared/fixtures/update-manifest.example.json`（单行规范化 JSON，签名是占位值）。
> 本契约对应方案一原文 §3.2 / §3.3 / §9；发布流程见 P0-06。

## 1. 用途与位置

每次 GitHub Release 必须附带 `update-manifest.json`，客户端只通过稳定地址拉取：

    https://github.com/<owner>/<repo>/releases/latest/download/update-manifest.json

当前仓库的规范地址（2026-11-15 实测：旧地址 301 到它）：

    https://github.com/PaperLibrary-Developers/PaperLibrary

客户端**不调用 `api.github.com`**（避开匿名 60 次/小时限制）。清单给出完整包的下载 URL、
字节数与 SHA-256；客户端**先验签清单，再信任其中的 URL、版本与哈希**。

## 2. 字段（schema=1，顺序即规范化顺序）

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `schema` | int | 必须为 `1` | 清单结构版本；扩展只能新增 schema=2，不得在 1 里加字段 |
| `product` | string | 必须为 `"PaperLibrary"` | 产品标识，拒绝未知产品 |
| `channel` | string | 必须为 `"stable"` | 渠道；灰度/分渠道属于后续扩展 |
| `version` | string | `x.y.z` 语义化版本 | 更新比较用（semver），如 `1.6.0` |
| `tag` | string | 必须等于 `v<major>.<minor>` | 发布标签/界面显示用，如 `v1.6`（由 `version` 推导） |
| `publishedAt` | string | UTC ISO8601 `YYYY-MM-DDTHH:MM:SSZ` | 发布时间 |
| `releaseNotesUrl` | string | https + `github.com` + 路径含 `/releases/` | 更新说明页面 |
| `minWindows` | string | `x.y.z` 形式 | 最低 Windows 版本；**取值待确认**（样例用原文示例 `10.0.17763`，不代表最终决定） |
| `arch` | string | 必须为 `"x64"` | 架构 |
| `assets.full.url` | string | https + `github.com` + 路径以 `/<owner>/<repo>/releases/` 开头 | 完整包下载地址（按版本 pin，不用 latest） |
| `assets.full.size` | int | 正整数（字节） | 完整包大小，下载后按它校验 |
| `assets.full.sha256` | string | 64 位小写十六进制 | 完整包 SHA-256，下载后必须一致 |
| `signature` | string | base64，解码后 64 字节 | Ed25519 签名，覆盖第 3 节的规范化 JSON |

顶层**不允许未知字段**（schema 冻结）；`assets` 目前只定义 `full`。

## 3. 规范化 JSON（签名的唯一输入）

签名的输入是“去掉 `signature` 后”的规范化字节，规则必须可逐字节复现：

1. 字段顺序固定为第 2 节表格顺序；`assets.full` 的顺序固定为 `url`、`size`、`sha256`。
2. UTF-8 编码、无 BOM、**无多余空白**：分隔符用 `,` 与 `:`，不缩进、不换行、无结尾换行。
3. 非 ASCII 字符不转义（`ensure_ascii=False`）。
4. `signature` 不参与签名；完整产物把 `signature` 放在最后。

样例的签名覆盖字节（与 `shared/fixtures/update-manifest.example.json` 对应，可复算）：

    {"schema":1,"product":"PaperLibrary","channel":"stable","version":"1.6.0","tag":"v1.6","publishedAt":"2026-11-15T00:12:52Z","releaseNotesUrl":"https://github.com/PaperLibrary-Developers/PaperLibrary/releases/tag/v1.6","minWindows":"10.0.17763","arch":"x64","assets":{"full":{"url":"https://github.com/PaperLibrary-Developers/PaperLibrary/releases/download/v1.6/PaperLibrary.v1.6.Nexus.zip","size":45625893,"sha256":"0952a533ddee752941161803939beea77e9e5a6a72dcdf26540b893ecc28ee70"}}}

发布产物就是这个规范化字节串（单行 JSON，无结尾换行），用户可直接对文件重新规范化后验签。
同一个清单重复签名/序列化必须得到相同字节（由 `test/test_update_manifest.py` 断言）。

签名工具（`tools/update_manifest.py sign`）的输入允许**省略** `signature`（待签清单）；
输出一定是含 signature 的规范化字节。验签时必须先做结构校验再验签，且签名覆盖除
`signature` 外的全部字段——任何字段（含 size/sha256/URL/版本）被改动都会验签失败。

## 4. 校验规则（任一不满足 → 明确报错、不下载、不安装）

- 清单超过 **256 KB**（原文 §4.2 响应体上限）→ 拒绝；
- 不是合法 UTF-8 / JSON / 顶层不是对象 → 拒绝；
- 缺字段、未知字段、字段类型错误 → 拒绝；
- 未知 `schema` / `product` / `channel` / `arch` → 拒绝；
- `version` 非 `x.y.z`、`tag` 与 `version` 不匹配 → 拒绝；
- `publishedAt` / `minWindows` 格式错误 → 拒绝；
- URL 非 https、主机不在允许列表、带用户名/密码、不是 GitHub Release 地址 → 拒绝；
- `size` 非正整数、`sha256` 非 64 位小写十六进制 → 拒绝；
- `signature` 缺失/不是 base64/解码后不是 64 字节 → 拒绝；
- 验签失败 → 拒绝（签名实现与公钥见 P0-03）。

## 5. 下载重定向的允许主机（P0-04 实测）

清单里的资产 URL 固定为 `github.com`（可能 301 到规范仓库名，仍是 `github.com`）。
实际跟随下载时实测链条（2026-11-15，v1.6 资产）：

    github.com (301 → 规范 owner/repo)
    → github.com (302)
    → release-assets.githubusercontent.com (200)

因此下载阶段的重定向允许列表至少包含 `github.com`、`release-assets.githubusercontent.com`；
原文示例里的 `objects.githubusercontent.com` 本次未观测到。**最终列表仍列为待确认项**（需在下载实现前复核）。

## 6. 不做的事

- 不在清单里放任何脚本/命令/可执行参数；客户端不执行清单中的任意内容。
- 不放私有 token/PAT；不使用私有仓库鉴权下载。
- 不在清单里放用户数据、API Key、本机路径。
- 不允许降级（比较逻辑在阶段 1 的 `/api/update/check` 实现，本契约只定义版本字段）。
