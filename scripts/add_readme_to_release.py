# -*- coding: utf-8 -*-
"""add_readme_to_release.py — 把新手使用说明写入便携版目录，并重新打包 zip"""
import os
import re
import shutil
import sys

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
DIST_NAME = "个人文献库_便携版"
DIST = os.path.join(DESKTOP, DIST_NAME)
ZIP = os.path.join(DESKTOP, DIST_NAME + ".zip")
MD = os.path.join(APP_DIR, "scripts", "README_portable.md")

CSS = """
:root { --ink:#1f2933; --muted:#5b6770; --line:#e2e6ea; --brand:#2f6f5e; --bg:#fbfaf7; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font: 15.5px/1.85 -apple-system, "Segoe UI", "Microsoft YaHei", "PingFang SC", sans-serif; }
.wrap { max-width: 880px; margin: 0 auto; padding: 46px 28px 90px; }
h1 { font-size: 30px; margin: 0 0 6px; letter-spacing:-0.02em; }
h1 + p, h1 + blockquote { color: var(--muted); }
h2 { font-size: 21px; margin: 42px 0 12px; padding-bottom: 8px; border-bottom: 2px solid var(--line); }
h3 { font-size: 17px; margin: 26px 0 8px; color: var(--brand); }
p { margin: 10px 0; }
ul, ol { margin: 10px 0 10px 22px; padding: 0; }
li { margin: 5px 0; }
code { background:#eef1f4; padding:1.5px 6px; border-radius:4px; font-size:13.5px;
  font-family: ui-monospace, Consolas, monospace; }
pre { background:#20272e; color:#e6edf3; padding:14px 16px; border-radius:10px;
  overflow-x:auto; font-size:13px; line-height:1.65; }
pre code { background:none; padding:0; color:inherit; }
table { border-collapse: collapse; width:100%; margin:14px 0; font-size:14.5px; }
th, td { border:1px solid var(--line); padding:8px 11px; text-align:left; vertical-align:top; }
th { background:#f1f4f2; font-weight:600; }
blockquote { margin:14px 0; padding:10px 16px; background:#fff7e6; border-left:4px solid #e0a800;
  border-radius:0 8px 8px 0; color:#5c4a12; }
blockquote p { margin:4px 0; }
hr { border:none; border-top:1px solid var(--line); margin:34px 0; }
strong { font-weight:650; }
a { color: var(--brand); }
.toc { background:#fff; border:1px solid var(--line); border-radius:10px; padding:14px 20px; margin:22px 0 8px; }
.toc b { display:block; margin-bottom:6px; color:var(--muted); font-size:13px; letter-spacing:.06em; }
.toc a { display:inline-block; margin:3px 14px 3px 0; text-decoration:none; font-size:14px; }
"""


def inline(text: str) -> str:
    text = (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def md_to_html(md: str) -> str:
    lines = md.split("\n")
    out, i = [], 0
    in_list = None
    in_code = False
    code_buf = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    while i < len(lines):
        raw = lines[i]
        line = raw.rstrip()

        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>" + "\n".join(code_buf) + "</code></pre>")
                code_buf, in_code = [], False
            else:
                close_list()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))
            i += 1
            continue

        if not line.strip():
            close_list()
            i += 1
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            close_list()
            level = len(m.group(1))
            out.append(f"<h{level}>{inline(m.group(2))}</h{level}>")
            i += 1
            continue

        if line.strip() in ("---", "***", "___"):
            close_list()
            out.append("<hr>")
            i += 1
            continue

        if line.startswith(">"):
            close_list()
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append("<blockquote>" + "".join(f"<p>{inline(x)}</p>" for x in buf if x) + "</blockquote>")
            continue

        if line.lstrip().startswith(("- ", "* ")):
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{inline(line.lstrip()[2:])}</li>")
            i += 1
            continue

        if re.match(r"^\d+\.\s", line.lstrip()):
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            item = re.sub(r"^\d+\.\s", "", line.lstrip())
            out.append("<li>" + inline(item) + "</li>")
            i += 1
            continue

        if line.lstrip().startswith("|"):
            close_list()
            rows = []
            while i < len(lines) and lines[i].lstrip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            html = ["<table>"]
            for ri, row in enumerate(rows):
                cells = [c.strip() for c in row.strip("|").split("|")]
                if ri == 1 and all(set(c) <= set("-: ") for c in cells):
                    continue
                tag = "th" if ri == 0 else "td"
                html.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
            html.append("</table>")
            out.append("".join(html))
            continue

        close_list()
        out.append(f"<p>{inline(line)}</p>")
        i += 1

    close_list()
    if in_code:
        out.append("<pre><code>" + "\n".join(code_buf) + "</code></pre>")
    return "\n".join(out)


def main():
    with open(MD, encoding="utf-8") as f:
        md = f.read()

    body = md_to_html(md)
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>个人文献库 · 使用说明</title>
<style>{CSS}</style></head>
<body><div class="wrap">{body}</div></body></html>"""

    os.makedirs(DIST, exist_ok=True)
    with open(os.path.join(DIST, "使用说明.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(DIST, "使用说明.txt"), "w", encoding="utf-8") as f:
        f.write(md)
    print("说明文件已写入:", DIST)

    # 重新打包
    if os.path.exists(ZIP):
        os.remove(ZIP)
    shutil.make_archive(ZIP[:-4], "zip", DESKTOP, DIST_NAME)
    print("zip 体积: %.0f MB" % (os.path.getsize(ZIP) / 1024 / 1024))


if __name__ == "__main__":
    main()
