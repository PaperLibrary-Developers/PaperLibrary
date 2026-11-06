"""纯函数 JS 对照测试（移植自 Sci-lib）：用 node 直接跑 JS 模块，逐例比较输出。

**不手写期望值**：所有期望都由 Sci-lib 的 JS 实现当场跑出来。
依赖 node（v24 可用）与 Sci-lib 仓库；路径取环境变量 SCILIB_PATH，默认同级 ../Sci-lib。
缺失时 skip，不影响其他用例（其余 test_pure_* 是纯 Python 断言）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shared.pure import abstract as ab  # noqa: E402
from shared.pure import abstract_extract as axe  # noqa: E402
from shared.pure import boolean_query as bq  # noqa: E402
from shared.pure import passages as pg  # noqa: E402
from shared.pure import text_repair as tr  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SCILIB = Path(os.environ.get("SCILIB_PATH") or (ROOT.parent / "Sci-lib"))
PURE_DIR = SCILIB / "shared" / "pure"

# 通用 runner：读 stdin 的 JSON（每个元素是一组实参），把结果 JSON 打回来
RUNNER = (
    "const fs=require('fs');"
    "const mod=require(process.argv[1]);"
    "const name=process.argv[2];"
    "const payload=JSON.parse(fs.readFileSync(0,'utf8'));"
    "const revive=function(v){"
    "  if(Array.isArray(v)){return v.map(revive);}"
    "  if(v&&typeof v==='object'){"
    "    if(Object.prototype.hasOwnProperty.call(v,'__set__')){return new Set(v.__set__);}"
    "    const o={};const ks=Object.keys(v);"
    "    for(let i=0;i<ks.length;i++){o[ks[i]]=revive(v[ks[i]]);}"
    "    return o;"
    "  }"
    "  return v;"
    "};"
    "const out=payload.map(function(args){"
    "  const r=mod[name].apply(null,args.map(revive));"
    "  if(r instanceof Set){return Array.from(r).sort();}"
    "  return r===undefined?null:r;"
    "});"
    "process.stdout.write(JSON.stringify(out));"
)


def _run_node(script, payload, module_file=None, extra=None):
    argv = ["node", "-e", script]
    if module_file is not None:
        argv.append(str(module_file))
    if extra is not None:
        argv.append(str(extra))
    proc = subprocess.run(
        argv,
        input=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError("node 执行失败: " + proc.stderr.decode("utf-8", "replace"))
    text = proc.stdout.decode("utf-8")
    return json.loads(text) if text else None


def js(module_name, fn_name, arg_tuples):
    """在 JS 里调用 module[fn] 若干次，返回结果列表（JSON 归一化后）。"""
    return _run_node(RUNNER, arg_tuples, PURE_DIR / (module_name + ".js"), fn_name)


def _norm(value):
    """Python 值 → JS 侧同构的 JSON 值（用于比较）。"""
    return json.loads(json.dumps(value, ensure_ascii=False))


class NodeParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("没有 node，跳过 JS 对照测试")
        if not PURE_DIR.is_dir():
            raise unittest.SkipTest("没找到 Sci-lib（可用 SCILIB_PATH 指定）：" + str(PURE_DIR))


QUERIES = [
    "cucurbituril host",
    '"molecular capsule" AND host',
    "a AND b OR NOT c",
    "(a OR b) AND c",
    "a && b",
    "a || b",
    "!a",
    "a 与 b 或 非 c",
    "“分子胶囊” 与 host",
    "title:cucurbituril",
    'title:"molecular capsule" author:Nitschke AND year:2013-2018',
    "year:2013",
    "year:2013-2018",
    "year:2013-",
    "year:-2018",
    "doi:10.1021/jacs.9b01234",
    "fulltext:binding note:重要 keyword:CB[8]",
    "cucurbituril NOT cyclodextrin",
    "a OR",
    "",
    "   ",
    "((a",
    "a AND NOT (b OR c)",
    "摘要:主客体",
    "http://example.com",
    "-",
    "A and b",
]


class BooleanQueryParity(NodeParity):
    def test_tokenize(self):
        expected = js("boolean-query", "tokenize", [[q] for q in QUERIES])
        actual = [bq.tokenize(q) for q in QUERIES]
        self.assertEqual(expected, _norm(actual))

    def test_parse_bundle(self):
        expected = js("boolean-query", "parse", [[q] for q in QUERIES])
        actual = [bq.parse_query(q) for q in QUERIES]
        self.assertEqual(expected, _norm(actual))

    def test_matches(self):
        pairs = [
            ("a AND b", "a b"), ("a AND b", "a c"), ("a OR b", "b"), ("a OR b", "c"),
            ("a NOT b", "a c"), ("a NOT b", "a b"), ("(a OR b) AND c", "b c"),
            ("(a OR b) AND c", "b d"), ("a OR (b AND c)", "a d"),
            ('"host guest"', "host  guest"), ('"host guest"', "host and guest"),
            ("cucurbituril", "A cucurbituril-based host"), ("", "anything"),
            ("分子胶囊", "这是分子胶囊的论文"), ("摘要:主客体", "摘要:主客体化学"),
        ]
        expected = js("boolean-query", "matches", [[q, text] for q, text in pairs])
        actual = [bq.matches(text, q) for q, text in pairs]
        self.assertEqual(expected, actual)

    def test_year_hit(self):
        pairs = [("2015", "2015"), ("2015", "2016"), ("2015", "2013-2018"), ("2020", "2013-2018"),
                 ("2020", "2013-"), ("2010", "-2018"), ("", "2015"), ("2013–2018", "2015"),
                 ("2015", "abc"), ("1999, 2020", "2013-2018")]
        expected = js("boolean-query", "yearHit", [[a, b] for a, b in pairs])
        actual = [bq.year_hit(a, b) for a, b in pairs]
        self.assertEqual(expected, actual)

    def test_query_level_helpers(self):
        names = ["hasBoolean", "hasFieldSyntax", "needsEvaluator"]
        expected = [js("boolean-query", name, [[q] for q in QUERIES]) for name in names]
        actual = [
            [bq.has_boolean(q) for q in QUERIES],
            [bq.has_field_syntax(q) for q in QUERIES],
            [bq.needs_evaluator(q) for q in QUERIES],
        ]
        self.assertEqual(expected, actual)

    def test_normalize_field(self):
        names = ["title", "venue", "authors", "摘要", "不存在的字段", "DOI", "fulltext", ""]
        expected = js("boolean-query", "normalizeField", [[n] for n in names])
        actual = [bq.normalize_field(n) for n in names]
        self.assertEqual(expected, actual)

    def test_plain_query_and_arxiv(self):
        queries = ["title:cucurbituril AND NOT cyclodextrin", "a AND b", "a NOT b",
                   "a OR b", "title:capsule year:2013-2018", "AND OR NOT", '"phrase one" x']
        self.assertEqual(
            js("boolean-query", "plainQuery", [[q] for q in queries]),
            [bq.plain_query(q) for q in queries],
        )
        self.assertEqual(
            js("boolean-query", "toArxivQuery", [[q] for q in queries]),
            [bq.to_arxiv_query(q) for q in queries],
        )

    def test_supports_boolean(self):
        sources = ["openalex", "europepmc", "arxiv", "crossref", "semanticscholar", "", None]
        expected = js("boolean-query", "supportsBoolean", [[s] for s in sources])
        self.assertEqual(expected, [bq.supports_boolean(s) for s in sources])

    def test_filter_items(self):
        script = (
            "const fs=require('fs');"
            "const mod=require(process.argv[1]);"
            "const p=JSON.parse(fs.readFileSync(0,'utf8'));"
            "const out=p.map(function(c){"
            "  return mod.filterItems(c.items,c.query,"
            "    function(it){return String(it.text==null?'':it.text);},"
            "    {fieldText:function(it,f){return String((it.fields||{})[f]==null?'':it.fields[f]);}});"
            "});"
            "process.stdout.write(JSON.stringify(out));"
        )
        cases = [
            {"query": "plain keywords", "items": [{"text": "a", "fields": {}}, {"text": "b", "fields": {}}]},
            {"query": "a AND c", "items": [{"text": "a c", "fields": {}}, {"text": "b", "fields": {}}]},
            {"query": "title:capsule", "items": [{"text": "x", "fields": {"title": "molecular capsule"}},
                                                 {"text": "capsule", "fields": {"title": "other"}}]},
            {"query": "year:2013-2018", "items": [{"text": "x", "fields": {"year": "2015"}},
                                                  {"text": "y", "fields": {"year": "2020"}}]},
        ]
        expected = _run_node(script, cases, PURE_DIR / "boolean-query.js")
        actual = [
            bq.filter_items(
                c["items"], c["query"], lambda it: it["text"],
                {"field_text": lambda it, f: (it.get("fields") or {}).get(f) or ""},
            )
            for c in cases
        ]
        self.assertEqual(expected, _norm(actual))

    def test_evaluate_with_field_text(self):
        script = (
            "const fs=require('fs');"
            "const mod=require(process.argv[1]);"
            "const p=JSON.parse(fs.readFileSync(0,'utf8'));"
            "const out=p.map(function(c){"
            "  const parsed=mod.parse(c.query);"
            "  if(!parsed.ast){return true;}"
            "  return mod.evaluate(parsed.ast,function(){return String(c.text==null?'':c.text);},"
            "    {fieldText:function(f){return String((c.fields||{})[f]==null?'':c.fields[f]);}});"
            "});"
            "process.stdout.write(JSON.stringify(out));"
        )
        cases = [
            {"query": "title:capsule", "text": "", "fields": {"title": "molecular capsule"}},
            {"query": "title:capsule", "text": "capsule", "fields": {"title": "other"}},
            {"query": "author:nitschke AND year:2015", "text": "", "fields": {"author": "Nitschke J.", "year": "2015"}},
            {"query": "a OR title:b", "text": "a", "fields": {"title": "zzz"}},
            {"query": "", "text": "whatever", "fields": {}},
        ]
        expected = _run_node(script, cases, PURE_DIR / "boolean-query.js")
        actual = []
        for c in cases:
            ast = bq.parse(c["query"])
            if not ast:
                actual.append(True)
                continue
            actual.append(bq.evaluate(
                ast, lambda: c["text"],
                {"field_text": lambda f, _c=c: (_c.get("fields") or {}).get(f) or ""},
            ))
        self.assertEqual(expected, actual)


class TextRepairParity(NodeParity):
    SAMPLES = [
        "e ffi cient", "de fi ne", "classi fi cation", "the fi nding", "bene fi t", "we fl ow",
        "hello world", "", None, "o ffi cial e ffi ciency", "ﬁ nding fl ow",
        "well - defined", "16213 - 16219", "sp -rich", "a—b", "a – b", "a - b",
        "  a   b  ", "a , b .", "( a )", "a  ,  b",
    ]

    def test_ligatures_and_tidy(self):
        for name, fn in (("repairLigatures", tr.repair_ligatures),
                         ("tidyDashes", tr.tidy_dashes),
                         ("tidySpacing", tr.tidy_spacing)):
            expected = js("text-repair", name, [[s] for s in self.SAMPLES])
            self.assertEqual(expected, [fn(s) for s in self.SAMPLES], name)

    def test_known_divergence_start_ligature(self):
        """已知（刻意）分歧：JS 的 '$1$3$4' 只有 3 个捕获组，$4 会当字面量输出，
        于是「词首连字」被写成 "a$4rt"（丢连字 + 塞入 $4）。Python 版按意图接成真词。
        这里把两边都钉住：JS 一侧确认缺陷仍在，Python 一侧确认输出是干净单词。"""
        samples = ["st art", "fi ne", "fl ow"]
        js_out = js("text-repair", "repairLigatures", [[s] for s in samples])
        py_out = [tr.repair_ligatures(s) for s in samples]
        self.assertTrue(all("$4" in item for item in js_out), js_out)
        self.assertEqual(py_out, ["start", "fine", "flow"])

    def test_build_word_set(self):
        samples = ["The quick brown fox jumps", "low lower lowest", "", "Cucurbit[8]uril chemistry"]
        expected = js("text-repair", "buildWordSet", [[s] for s in samples])
        self.assertEqual(expected, [sorted(tr.build_word_set(s)) for s in samples])

    def test_join_hyphen_breaks(self):
        cases = [
            ("con- tainer molecules", ["container"]),
            ("hydrogen- bonded", ["container"]),
            ("con- tainer", []),
            ("well- defined and con- tainer", ["container", "defined"]),
        ]
        expected = js("text-repair", "joinHyphenBreaks", [[t, {"__set__": v}] for t, v in cases])
        actual = [tr.join_hyphen_breaks(t, set(v)) for t, v in cases]
        self.assertEqual(expected, actual)

    def test_repair_text(self):
        cases = [("e ffi cient  hydrogen - bonded", None),
                 ("e ffi cient  hydrogen - bonded", ["container"]),
                 ("con- tainer  well - defined", ["container"]),
                 ("", None)]
        expected = js("text-repair", "repairText", [[t, ({"__set__": v} if v else v) if v else None] for t, v in cases])
        actual = [tr.repair_text(t, set(v) if v else None) for t, v in cases]
        self.assertEqual(expected, actual)


LONG_TEXT = (
    "Section S-1. General information and materials. " + "The host guest chemistry was studied in detail. " * 30
    + "Figure S - 55 shows the titration data for the molecular capsule. "
    + "Further discussion of the binding constant and its concentration dependence follows. " * 30
    + "Finally, Table GP-2 lists the yields for all compounds prepared in this work. "
    + "Supporting information is available on request from the authors. " * 10
)


class PassagesParity(NodeParity):
    def _report(self, cases):
        expected = js("passages", "pickPassages", [[t, q, o] for t, q, o in cases])
        return expected, [pg.pick_passages_report(t, q, o) for t, q, o in cases]

    def test_pick_passages_report(self):
        cases = [
            (LONG_TEXT, "What does Figure S55 show about the molecular capsule?", None),
            (LONG_TEXT, "What does Figure S55 show about the molecular capsule?",
             {"budget": 2500, "headChars": 900, "radius": 400}),
            (LONG_TEXT, "S-55 titration", {"budget": 1800, "headChars": 600, "tailChars": 400, "radius": 300}),
            (LONG_TEXT, "Table GP-2 yields", {"budget": 3000, "headChars": 1000, "tailChars": 0}),
            ("short text", "any question", None),
            ("", "any question", None),
            (LONG_TEXT, "分子胶囊 的 滴定 数据", {"budget": 2000, "headChars": 500}),
        ]
        expected, actual = self._report(cases)
        self.assertEqual(expected, _norm(actual))

    def test_find_hits_and_windows(self):
        questions = ["What does Figure S55 show?", "S − 55 titration data", "molecular capsule",
                     "分子胶囊", ""]
        expected = js("passages", "findHits", [[LONG_TEXT, q, 60] for q in questions])
        actual = [pg.find_hits(LONG_TEXT, q, 60) for q in questions]
        self.assertEqual(expected, _norm(actual))

        hits_cases = [[0, 100, 300, 5000], [], [10, 20, 30]]
        expected = js("passages", "mergeWindows", [[hits, 700] for hits in hits_cases])
        self.assertEqual(expected, [pg.merge_windows(h, 700) for h in hits_cases])


class AbstractParity(NodeParity):
    def test_clean_source_abstract(self):
        samples = [
            "<jats:p>This is a real abstract about cucurbituril chemistry and host guest binding. " * 3 + "</jats:p>",
            "ADVERTISEMENT RETURN TO ISSUE Share on Facebook Download PDF",
            "Abstract: " + "A long enough abstract text describing the binding of guests. " * 5,
            "", None, "Short abstract.",
            "Cite this: JACS. Publication Date: 2019. " + "text " * 40,
        ]
        expected = js("abstract", "cleanSourceAbstract", [[s] for s in samples])
        self.assertEqual(expected, [ab.clean_source_abstract(s) for s in samples])

    def test_looks_like_chrome(self):
        samples = ["", "   ", "ADVERTISEMENT", "Downloaded via ETH Zurich. Altmetric.",
                   "A normal abstract about chemistry.", "RETURN TO ISSUE something"]
        expected = js("abstract", "looksLikeChrome", [[s] for s in samples])
        self.assertEqual(expected, [ab.looks_like_chrome(s) for s in samples])

    def test_choose_abstract(self):
        long_text = "Binding of the guest inside the cavity was confirmed by NMR. " * 6
        short_text = "Short note."
        cases = [
            {"crossref": long_text},
            {"crossref": short_text, "pdf": {"text": long_text, "method": "heading"}},
            {"crossref": short_text, "pdf": {"text": short_text, "method": "position"}, "openalex": long_text},
            {"crossref": short_text},
            {"pdf": {"text": short_text, "method": "position"}},
            {},
            {"crossref": "ADVERTISEMENT RETURN TO ISSUE", "openalex": long_text},
            {"crossref": None, "pdf": None, "openalex": None},
        ]
        expected = js("abstract", "chooseAbstract", [[c] for c in cases])
        self.assertEqual(expected, _norm([ab.choose_abstract(c) for c in cases]))

    def test_describe_source(self):
        pairs = [("crossref", None), ("openalex", None), ("pdf", "heading"), ("pdf", "position"),
                 ("", None), (None, None)]
        expected = js("abstract", "describeSource", [[a, b] for a, b in pairs])
        self.assertEqual(expected, [ab.describe_source(a, b) for a, b in pairs])


HEADING_LINES = [
    "Journal of the American Chemical Society",
    "Cucurbituril-Based Molecular Capsules",
    "John Smith, Anna Lee and Jonathan R. Nitschke",
    "Department of Chemistry, University of Cambridge",
    "ABSTRACT: The binding of guests inside the cavity of cucurbit[8]uril was studied by NMR spectroscopy "
    "and isothermal titration calorimetry. The association constants reach 10^9 M-1 in water.",
    "1 Introduction",
    "Host guest chemistry is a broad field.",
]
POSITION_LINES = [
    "nature chemistry",
    "Article",
    "https://doi.org/10.1038/s41557-019-0123-4",
    "Supramolecular capsules assembled from cucurbituril",
    "Anna Lee, John Smith",
    "Department of Chemistry, University of Cambridge, UK",
    "The self-assembly of molecular capsules from cucurbit[8]uril and viologen guests was investigated in water. "
    "We find that the capsule forms quantitatively at millimolar concentrations.",
    "Results and discussion",
    "The binding constant was determined by titration.",
]


class AbstractExtractParity(NodeParity):
    def test_extract_abstract_from_lines(self):
        cases = [
            (HEADING_LINES, {"title": "Cucurbituril-Based Molecular Capsules"}),
            (POSITION_LINES, {"title": "Supramolecular capsules assembled from cucurbituril"}),
            (POSITION_LINES, None),
            ([], None),
            (["just one line"], None),
        ]
        expected = js("abstract-extract", "extractAbstractFromLines", [[lines, opts] for lines, opts in cases])
        actual = [axe.extract_abstract_from_lines(lines, opts) for lines, opts in cases]
        self.assertEqual(expected, _norm(actual))

    def test_clean_abstract_and_trim(self):
        samples = ["e ffi cient catalyst was used. Downloaded via ETH Zurich. https://doi.org/10.1/x",
                   "S C Cs were observed", "", None]
        expected = js("abstract-extract", "cleanAbstract", [[s, None] for s in samples])
        self.assertEqual(expected, [axe.clean_abstract(s) for s in samples])

        long_text = "First sentence here. Second sentence goes on. Third sentence is longer than needed."
        cases = [(long_text, 30), (long_text, 500), ("", 10), (long_text, 62)]
        expected = js("abstract-extract", "trimToLimit", [[t, n] for t, n in cases])
        self.assertEqual(expected, [axe.trim_to_limit(t, n) for t, n in cases])
