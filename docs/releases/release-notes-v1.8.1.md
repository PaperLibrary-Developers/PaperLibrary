# PaperLibrary v1.8.1 更新说明

发布日期：2026-11-17
版本：1.8.1（tag v1.8.1，Windows 10.0.17763 及以上，x64）

## 本次更新

这是一个修复版本，解决在部分 Windows 机器上“检查更新”报 SSL 证书校验失败的问题。

### 修复的问题

- 修复更新检查提示：
  [SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get issuer certificate。
- 根因：包内 Python 3.11 / OpenSSL 3.0 默认未启用交叉签名根证书的 partial-chain 行为；
  GitHub 的 Sectigo 证书链在 Windows 根证书库的 USERTrust ECC 交叉签名锚点处无法终止。
  这不是证书库缺失、代理或更新源配置错误。
- 修复方式：为更新检查/下载请求增加专用 HTTPS 上下文，启用
  VERIFY_X509_PARTIAL_CHAIN，同时保持 CERT_REQUIRED 和主机名校验，未关闭任何证书校验。

### 新增/变更

- 新增 paperlib/update/tls.py：更新请求的统一 HTTPS 上下文。
- paperlib/update/fetch.py、paperlib/update/download.py 使用该上下文；
  清单检查与更新包下载行为保持一致。
- 新增 test/test_update_tls.py：确保校验模式为 CERT_REQUIRED、主机名校验开启，
  并包含 partial-chain flag。

### 验证

- 全量测试：718 tests OK（1 skip）
- TLS/更新相关测试：71 tests OK
- 同一台机器使用包内 Python 复测：
  - 关闭 partial-chain：复现 unable to get issuer certificate
  - 开启 partial-chain：HTTP 200，更新检查正常返回“已是最新版本”

## 注意事项

- 本版本为补丁更新，更新机制、数据备份和自动下载默认关等行为保持不变。
- 更新会保留 data/；仍不会静默安装，下载后需用户点击“立即安装”。
- 需要 Windows 10.0.17763 或更高版本、x64；桌面窗口依赖 WebView2。
- 本版本未做 Authenticode 签名，SmartScreen 可能提示；请从官方 Release 下载并核对 SHA256SUMS。
- 当前正在运行的旧进程仍加载旧代码；安装/重启到 v1.8.1 后修复生效。
