"""零依赖 Word 97–2003（.doc / MS-CFB + MS-DOC）纯文本抽取。

直接解析 OLE2 复合文档（MS-CFB）中的 WordDocument 与 0Table/1Table 流，
按 MS-DOC FIB + Piece Table（Clx/PlcPcd）还原正文、表格、脚注与尾注纯文本：
- 仅使用 Python 标准库（struct / re），不依赖 msdoc2docx 等重型排版转换包；
- 内存直读、免落盘临时文件，同时彻底避免第三方包中 VML 图形模块触发安全软件启发式误报。
"""
from __future__ import annotations

import re
import struct

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
MAX_STREAM_BYTES = 64 * 1024 * 1024

_CONTROL_STRIP_RE = re.compile(r"[\x00-\x06\x08\x0e-\x1f]")


class _OleReader:
    """最小只读 MS-CFB（OLE2）流读取器，带链环检测与体积上限。"""

    def __init__(self, data: bytes):
        if len(data) < 512 or data[:8] != OLE_MAGIC:
            raise ValueError("文件不是有效的 OLE2 Word .doc 文档")
        self.data = data
        self.sector_shift, self.mini_shift = struct.unpack_from("<HH", data, 0x1E)
        if self.sector_shift not in (9, 12) or self.mini_shift != 6:
            raise ValueError("不支持的 OLE2 扇区规格")
        self.sector_size = 1 << self.sector_shift
        self.mini_size = 1 << self.mini_shift

        (
            self.num_fat_sectors,
            self.dir_start,
            _txn,
            self.mini_cutoff,
            self.mini_fat_start,
            self.num_mini_fat_sectors,
            self.difat_start,
            self.num_difat_sectors,
        ) = struct.unpack_from("<IIIIIIII", data, 0x2C)

        self.fat = self._load_fat()
        self.entries = self._load_directory()
        self.mini_stream = b""
        self.mini_fat: list[int] = []
        root = self.entries.get("__root__")
        if root is not None and root[1] > 0 and root[0] not in (ENDOFCHAIN, FREESECT):
            self.mini_stream = self._read_fat_chain(root[0], root[1])
            if self.num_mini_fat_sectors > 0 and self.mini_fat_start not in (ENDOFCHAIN, FREESECT):
                raw_mfat = self._read_fat_chain(self.mini_fat_start, self.num_mini_fat_sectors * self.sector_size)
                count = len(raw_mfat) // 4
                self.mini_fat = list(struct.unpack_from(f"<{count}I", raw_mfat, 0))

    def _sector_slice(self, sid: int) -> bytes:
        offset = (sid + 1) * self.sector_size
        if sid < 0 or offset < 0 or offset + self.sector_size > len(self.data):
            raise ValueError("OLE2 扇区越界")
        return self.data[offset:offset + self.sector_size]

    def _load_fat(self) -> list[int]:
        difat = [sid for sid in struct.unpack_from("<109I", self.data, 0x4C) if sid not in (FREESECT, ENDOFCHAIN)]
        next_difat = self.difat_start
        visited_difat: set[int] = set()
        entries_per_sector = self.sector_size // 4
        while (
            next_difat not in (FREESECT, ENDOFCHAIN)
            and len(visited_difat) < self.num_difat_sectors
            and next_difat not in visited_difat
        ):
            visited_difat.add(next_difat)
            sec = self._sector_slice(next_difat)
            vals = struct.unpack_from(f"<{entries_per_sector}I", sec, 0)
            for sid in vals[:-1]:
                if sid not in (FREESECT, ENDOFCHAIN):
                    difat.append(sid)
            next_difat = vals[-1]

        fat: list[int] = []
        for fat_sid in difat[: self.num_fat_sectors or len(difat)]:
            sec = self._sector_slice(fat_sid)
            fat.extend(struct.unpack_from(f"<{entries_per_sector}I", sec, 0))
        return fat

    def _read_fat_chain(self, start: int, max_size: int) -> bytes:
        if max_size > MAX_STREAM_BYTES:
            raise ValueError("OLE2 流体积超出限制")
        chunks: list[bytes] = []
        sid = start
        visited: set[int] = set()
        total = 0
        while sid not in (ENDOFCHAIN, FREESECT):
            if sid in visited or sid >= len(self.fat):
                raise ValueError("OLE2 FAT 链损坏")
            visited.add(sid)
            chunk = self._sector_slice(sid)
            chunks.append(chunk)
            total += len(chunk)
            if total >= max_size:
                break
            sid = self.fat[sid]
        return b"".join(chunks)[:max_size]

    def _read_mini_chain(self, start: int, size: int) -> bytes:
        if size > MAX_STREAM_BYTES:
            raise ValueError("OLE2 Mini 流体积超出限制")
        chunks: list[bytes] = []
        sid = start
        visited: set[int] = set()
        total = 0
        while sid not in (ENDOFCHAIN, FREESECT):
            if sid in visited or sid >= len(self.mini_fat):
                raise ValueError("OLE2 MiniFAT 链损坏")
            visited.add(sid)
            off = sid * self.mini_size
            if off + self.mini_size > len(self.mini_stream):
                raise ValueError("OLE2 Mini 流越界")
            chunks.append(self.mini_stream[off:off + self.mini_size])
            total += self.mini_size
            if total >= size:
                break
            sid = self.mini_fat[sid]
        return b"".join(chunks)[:size]

    def _load_directory(self) -> dict[str, tuple[int, int]]:
        raw = self._read_fat_chain(self.dir_start, MAX_STREAM_BYTES)
        entries: dict[str, tuple[int, int]] = {}
        for off in range(0, len(raw) - 127, 128):
            entry = raw[off:off + 128]
            name_len = struct.unpack_from("<H", entry, 0x40)[0]
            obj_type = entry[0x42]
            if obj_type not in (2, 5) or name_len < 2 or name_len > 64:
                continue
            name = entry[:name_len].decode("utf-16-le", "ignore").rstrip("\x00")
            start_sid = struct.unpack_from("<I", entry, 0x74)[0]
            size = struct.unpack_from("<Q", entry, 0x78)[0]
            if self.sector_shift == 9:
                size &= 0xFFFFFFFF
            if obj_type == 5:
                entries["__root__"] = (start_sid, size)
            elif name:
                entries[name] = (start_sid, size)
        return entries

    def read_stream(self, name: str) -> bytes | None:
        meta = self.entries.get(name)
        if meta is None:
            return None
        start_sid, size = meta
        if size == 0:
            return b""
        if size < self.mini_cutoff:
            return self._read_mini_chain(start_sid, size)
        return self._read_fat_chain(start_sid, size)


def _strip_fields(text: str) -> str:
    """按 Word 域控制符规则（0x13 域开始、0x14 分隔、0x15 结束）去掉指令、保留显示值。"""
    if "\x13" not in text:
        return text
    out: list[str] = []
    # 栈元素：False=正在域指令区（丢弃），True=已进入域结果区（保留）
    stack: list[bool] = []
    for ch in text:
        if ch == "\x13":
            stack.append(False)
        elif ch == "\x14":
            if stack:
                stack[-1] = True
        elif ch == "\x15":
            if stack:
                stack.pop()
        else:
            if not stack or all(stack):
                out.append(ch)
    return "".join(out)


def _clean_word_text(raw: str) -> str:
    text = _strip_fields(raw)
    # Word 表格：单元格以 \x07 结束，行尾紧跟第二个 \x07（\x07\x07）
    text = text.replace("\x07\x07", "\n")
    text = text.replace("\x07", "\t")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\x0b", "\n").replace("\x0c", "\n")
    text = _CONTROL_STRIP_RE.sub("", text)
    # 清理表格单元格内末尾换行紧挨着制表符的空白，以及行首尾多余空行
    lines = [line.rstrip("\t ") for line in text.split("\n")]
    return "\n".join(lines).strip("\n")


def _extract_pieces(word_doc: bytes, table_stream: bytes, fc_clx: int, lcb_clx: int) -> str:
    if lcb_clx <= 0 or fc_clx + lcb_clx > len(table_stream):
        raise ValueError("Word .doc 缺少有效的 Piece Table（Clx）")
    clx = table_stream[fc_clx:fc_clx + lcb_clx]
    pos = 0
    plc = None
    while pos < len(clx):
        clxt = clx[pos]
        pos += 1
        if clxt == 0x01:
            if pos + 2 > len(clx):
                raise ValueError("Word .doc Prc 结构截断")
            cb_grpprl = struct.unpack_from("<H", clx, pos)[0]
            pos += 2 + cb_grpprl
        elif clxt == 0x02:
            if pos + 4 > len(clx):
                raise ValueError("Word .doc Pcdt 长度截断")
            lcb = struct.unpack_from("<I", clx, pos)[0]
            pos += 4
            if pos + lcb > len(clx) or lcb < 4 or (lcb - 4) % 12 != 0:
                raise ValueError("Word .doc PlcPcd 长度非法")
            plc = clx[pos:pos + lcb]
            break
        else:
            raise ValueError("Word .doc Clx 标记异常")
    if plc is None:
        raise ValueError("Word .doc 未找到 PlcPcd 文本表")

    count = (len(plc) - 4) // 12
    cps = struct.unpack_from(f"<{count + 1}I", plc, 0)
    pcd_base = 4 * (count + 1)
    parts: list[str] = []
    for idx in range(count):
        cp_len = cps[idx + 1] - cps[idx]
        if cp_len <= 0:
            continue
        fc_raw = struct.unpack_from("<I", plc, pcd_base + idx * 8 + 2)[0]
        if fc_raw & 0x40000000:
            byte_off = (fc_raw & ~0x40000000) >> 1
            chunk = word_doc[byte_off:byte_off + cp_len]
            parts.append(chunk.decode("cp1252", "replace"))
        else:
            byte_off = fc_raw
            chunk = word_doc[byte_off:byte_off + cp_len * 2]
            parts.append(chunk.decode("utf-16-le", "replace"))
    return "".join(parts)


def extract_doc_text(data: bytes) -> str:
    """从 Word 97–2003 二进制 .doc 字节流提取纯文本（含正文、表格、脚注与尾注）。"""
    ole = _OleReader(data)
    word_doc = ole.read_stream("WordDocument")
    if not word_doc or len(word_doc) < 0x9A:
        raise ValueError("文件不是有效的 Word .doc（缺少 WordDocument 流）")

    w_ident, _n_fib = struct.unpack_from("<HH", word_doc, 0x00)
    if w_ident != 0xA5EC:
        raise ValueError("不支持的 Word .doc 版本标识")

    flags = struct.unpack_from("<H", word_doc, 0x0A)[0]
    if flags & 0x0100:
        raise ValueError("暂不支持加密或受密码保护的 Word .doc 文档")

    table_name = "1Table" if (flags & 0x0200) else "0Table"
    table_stream = ole.read_stream(table_name)
    if not table_stream:
        raise ValueError(f"Word .doc 缺少 {table_name} 流")

    # 动态定位 FibRgLw 与 FibRgFcLcb（兼容 Word 97/2000/2002/2003/2007+）
    csw = struct.unpack_from("<H", word_doc, 0x20)[0]
    cslw_off = 0x22 + 2 * csw
    if cslw_off + 2 > len(word_doc):
        raise ValueError("Word .doc FIB 头截断")
    cslw = struct.unpack_from("<H", word_doc, cslw_off)[0]
    rg_lw_off = cslw_off + 2
    cb_rg_fc_off = rg_lw_off + 4 * cslw
    if cslw < 10 or cb_rg_fc_off + 2 > len(word_doc):
        raise ValueError("Word .doc FibRgLw 结构不完整")

    ccp_text, ccp_ftn, ccp_hdd, ccp_mcr, ccp_atn, ccp_edn = struct.unpack_from("<6i", word_doc, rg_lw_off + 12)
    cb_rg_fc = struct.unpack_from("<H", word_doc, cb_rg_fc_off)[0]
    rg_fc_off = cb_rg_fc_off + 2
    if cb_rg_fc < 34 or rg_fc_off + 34 * 8 > len(word_doc):
        raise ValueError("Word .doc FibRgFcLcb 缺少 Clx 偏移")

    fc_clx, lcb_clx = struct.unpack_from("<II", word_doc, rg_fc_off + 33 * 8)
    full_text = _extract_pieces(word_doc, table_stream, fc_clx, lcb_clx)

    segments: list[str] = []
    if ccp_text > 0:
        main_body = _clean_word_text(full_text[:ccp_text])
        if main_body:
            segments.append(main_body)
    if ccp_ftn > 0:
        ftn = _clean_word_text(full_text[ccp_text:ccp_text + ccp_ftn])
        if ftn:
            segments.append(ftn)
    edn_start = ccp_text + max(0, ccp_ftn) + max(0, ccp_hdd) + max(0, ccp_mcr) + max(0, ccp_atn)
    if ccp_edn > 0 and edn_start < len(full_text):
        edn = _clean_word_text(full_text[edn_start:edn_start + ccp_edn])
        if edn:
            segments.append(edn)

    if not segments and full_text:
        fallback = _clean_word_text(full_text)
        if fallback:
            segments.append(fallback)
    return "\n\n".join(segments)
