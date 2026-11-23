"""把 SI 附件抽成可供总结与问答读取的文本。"""
from __future__ import annotations

import codecs
import csv
import re
from io import StringIO
from pathlib import Path
from xml.etree import ElementTree
from zipfile import ZipFile

from paperlib.adapters.doc_text import extract_doc_text


TEXT_LIMIT = 5 * 1024 * 1024
OFFICE_LIMIT = 32 * 1024 * 1024
TEXT_EXTENSIONS = (".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".tex", ".bib")
WORD_NAMESPACE = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_WORD_NAMESPACE = "http://purl.oclc.org/ooxml/wordprocessingml/main"
MATH_TEXT_TAGS = {
    "{http://schemas.openxmlformats.org/officeDocument/2006/math}t",
    "{http://purl.oclc.org/ooxml/officeDocument/math}t",
}


def _read_small_file(path, limit, message):
    with path.open("rb") as source:
        data = source.read(limit + 1)
    if len(data) > limit:
        raise ValueError(message)
    return data


def _read_plain_text(path):
    data = _read_small_file(path, TEXT_LIMIT, "文本文件超过 5MB")
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        text = data.decode("utf-32")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        text = data.decode("utf-16")
    elif data.startswith(codecs.BOM_UTF8):
        text = data.decode("utf-8-sig")
    else:
        for encoding in ("utf-8", "gb18030", "cp1252"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise ValueError("无法识别文本编码")
    if re.search(r"[\x00-\x08\x0e-\x1f]", text):
        raise ValueError("文件含有非文本字符")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _check_xml_size(entries):
    if sum(entry.file_size for entry in entries) > OFFICE_LIMIT:
        raise ValueError("Office 文档的 XML 超过 32MB")


def _word_xml_text(source):
    parts = []
    for _, element in ElementTree.iterparse(source, events=("end",)):
        namespace, _, tag = element.tag.partition("}")
        if element.tag in MATH_TEXT_TAGS:
            parts.append(element.text or "")
        elif namespace[1:] in (WORD_NAMESPACE, STRICT_WORD_NAMESPACE):
            if tag == "t":
                parts.append(element.text or "")
            elif tag == "tc":
                # 单元格末尾的段落换行换成列分隔符。
                if parts and parts[-1] == "\n":
                    parts.pop()
                parts.append("\t")
            elif tag == "tr":
                if parts and parts[-1] == "\t":
                    parts.pop()
                parts.append("\n")
            elif tag in ("p", "br", "cr"):
                parts.append("\n")
            elif tag == "tab":
                parts.append("\t")
        element.clear()
    return "".join(parts).strip("\n")


def _read_docx(path):
    with ZipFile(path) as archive:
        entries = [archive.getinfo("word/document.xml")]
        names = set(archive.namelist())
        entries.extend(archive.getinfo(name) for name in ("word/footnotes.xml", "word/endnotes.xml") if name in names)
        _check_xml_size(entries)
        parts = []
        for entry in entries:
            with archive.open(entry) as source:
                parts.append(_word_xml_text(source))
        return "\n\n".join(part for part in parts if part.strip())


def _read_rtf(path):
    from striprtf.striprtf import rtf_to_text

    data = _read_small_file(path, TEXT_LIMIT, "RTF 文件超过 5MB")
    if not data.lstrip().startswith(b"{\\rtf"):
        raise ValueError("文件不是有效的 RTF")
    text = rtf_to_text(data.decode("latin-1"), errors="replace")
    # RTF 的 Unicode 控制字以 UTF-16 单元存储，合并补充平面的代理对。
    return text.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def _read_doc(path):
    with path.open("rb") as source:
        head = source.read(8)
    if head.lstrip().startswith(b"{\\rtf"):
        return _read_rtf(path)
    if head.startswith(b"PK\x03\x04"):
        return _read_docx(path)
    data = _read_small_file(path, OFFICE_LIMIT, "DOC 文件超过 32MB")
    return extract_doc_text(data)


def _read_xlsx(path):
    from openpyxl import load_workbook

    with ZipFile(path) as archive:
        _check_xml_size(entry for entry in archive.infolist() if entry.filename.endswith((".xml", ".rels")))
    output = StringIO()
    writer = csv.writer(output, delimiter="\t", lineterminator="\n")
    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in workbook.worksheets:
            # 某些导出工具写错 dimension，使用实际单元格范围。
            sheet.reset_dimensions()
            started = False
            for row in sheet.values:
                if all(value is None for value in row):
                    continue
                if not started:
                    writer.writerow(["工作表：" + sheet.title])
                    started = True
                writer.writerow(row)
                if output.tell() > OFFICE_LIMIT:
                    raise ValueError("工作簿的抽取文本超过 32M 字符")
    finally:
        workbook.close()
    return output.getvalue().rstrip("\n")


EXTRACTORS = {ext: ("text", _read_plain_text) for ext in TEXT_EXTENSIONS}
EXTRACTORS.update({
    ".docx": ("docx", _read_docx),
    ".doc": ("doc", _read_doc),
    ".rtf": ("rtf", _read_rtf),
    ".xlsx": ("xlsx", _read_xlsx),
})


def extract_attachment_text(path, extension, extract_pdf):
    ext = str(extension or "").lower()
    kind = "pdf" if ext == ".pdf" else "other"
    try:
        if ext == ".pdf":
            text = str(extract_pdf(path).get("text") or "").strip()
            if len(text) < 20:
                return {"ok": False, "error": "这个 PDF 提取不到文字（可能是扫描件）", "kind": kind}
        elif ext in EXTRACTORS:
            kind, read = EXTRACTORS[ext]
            text = read(Path(path))
        else:
            return {"ok": False, "error": "这种格式暂不抽取文字（" + ext + "），只作为附件保存", "kind": kind}
        if not text.strip():
            return {"ok": False, "error": "文件没有可提取的文字", "kind": kind}
    except ImportError:
        return {"ok": False, "error": "缺少抽取依赖，请使用完整发布包或安装 requirements.txt", "kind": kind}
    except Exception as exc:  # 单个附件解析失败时仍允许保存原件
        return {"ok": False, "error": "抽取失败：" + str(exc), "kind": kind}
    return {"ok": True, "text": text, "kind": kind}
