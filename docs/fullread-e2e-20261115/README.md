# 全文读取修复 · 真实文献验收记录（2026-11-15）

测试数据：`本机测试目录（路径略）`

- 正文：Angew. Chem. Int. Ed. 2023, e202216690，11 页，抽取 51,206 字
- 补充材料（SI）：6 个 checkCIF PDF，抽取合计 386,070 字
  - 1a_planar 14,206 字；1a_twisted 18,402 字；H4G_o_Box 59,118 字
  - H5G_o_Box 68,134 字；H7G2_o_Box 56,519 字；H8G2_p_Box 169,691 字
- 合计送入材料：437,276 字；DIRECT_LIMIT=40,000，故按 14 段 map-reduce 处理。

## 1. 提取完整性

按“去掉所有空白字符后逐字比较”核对抽取文本与 PyMuPDF 直读文本：

| 文件 | 归一化字符数（直读/库内） | 结果 |
|---|---|---|
| 正文 11 页 | 42,924 / 42,887 | 0.9991，内容一致（行序/断词修复差异） |
| SI 1a_planar | 12,159 / 12,159 | 完全一致（ratio 1.0） |
| SI 1a_twisted | 15,672 / 15,672 | 完全一致 |
| SI H4G_o_Box | 50,945 / 50,945 | 完全一致 |
| SI H5G_o_Box | 58,663 / 58,663 | 完全一致 |
| SI H7G2_o_Box | 48,435 / 48,435 | 完全一致 |
| SI H8G2_p_Box | 145,992 / 145,992 | 完全一致 |

所有 SI 的末页内容（checkCIF 末尾 “Datablock …-ellipsoid plot”）均在库内文本结尾出现，没有只取前几页。

## 2. AI 总结（真实 DeepSeek API，同步接口）

请求：`POST /api/ai/summarize-sync {"paperId":"…","save":true}`，耗时 26.9 秒。

返回的覆盖信息：

```json
{
  "mode": "map-reduce",
  "bodyChars": 51206,
  "siChars": 386070,
  "totalChars": 437276,
  "attachCount": 6,
  "chunks": 14,
  "processedChunks": 14,
  "missing": []
}
```

总结正文 1,679 字，包含旧版 12,000 字节选根本看不到的晚期正文/SI 细节，例如：
NMR 滴定 4:1、H4G（单斜 P21/c）、H5G（C2/c）、H8G2（三斜 P-1，100 K，Cu Kα 1.54184 Å）等。
未再出现“正文节选被截断/仅依据摘要”的自我声明。

## 3. 与文献对话（真实 API + UI）

| 类型 | 问题 | 回答要点 | 结果 |
|---|---|---|---|
| 正文晚期（约 2.3 万字符处） | 1a/o-Box 的 NMR 滴定中，游离 o-Box 亚甲基质子完全消失时的比例？ | 4:1（正文） | 正确 |
| 只存在于 SI（H4G） | SI 中 H4G_o_Box 的 R1、wR2？ | R1 = 0.1923（0.19）；wR2 = 0.3665 | 正确 |
| 只存在于 SI（H8G2） | SI 中 H8G2_p_Box 的 wR2、R1？ | wR2 = 0.5022（130063）；R1 = 0.2600（82808） | 正确 |
| 文献未提供 | Cu2O 光催化产率？ | “资料里没有……正文与 SI 均未涉及 Cu2O 光催化” | 未编造 |

H8G2 是第 6 个 SI；旧实现 SI 合计上限 12,000 字（约只能覆盖前 3 个文件的开头），根本读不到该文件，新实现 14 段全部覆盖。

## 4. 截图

- `summary-fullread.png`：AI 总结面板，底部显示“已完整读入正文 51206 字 + SI 386070 字（6 个文件，分 14 段）· 刚刚生成”
- `chat-si.png`：浏览器中提问 H8G2 SI，回答 wR2=0.5022 / R1=0.2600，状态“已基于正文与 SI 全文回答”
- `chat-body.png`：浏览器中提问正文晚期 NMR 4:1，回答正确

## 5. 命令

```bash
python -m unittest discover -s test          # 434 tests OK
python tools/contract_check.py               # PASS 59 / FAIL 0 / SKIP 10
python tools/e2e.py                          # 37 steps, 8 步为既有差异（AI 预设顺序、folder 空字段）
```

## 6. 仍存在的限制

- 分段大小固定为 48,000 字符/段（`paperlib/domain/fullread.py` 的 `CHUNK_CHARS`），按 64k 上下文模型设计；若模型上下文更小，需调小该常量，否则单段请求会被模型接口拒绝（会明确报错，不会静默截断）。
- 只对带文本层的 PDF/TXT 等有用；扫描件仍需先 OCR（识图识别全文）。没有文本层的 SI 会在 `fullRead.missing` 里列出并在界面提示“未提取到文字”，不会被说成已读入。
- 本次验收使用 DeepSeek（deepseek-chat）真实 API 和上述 7 个 PDF；未测试其他厂商模型/超长 20 万页文档。
- 改动未提交，仅落盘。
