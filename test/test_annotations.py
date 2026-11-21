"""PDF 批注端点（/api/papers/:id/annotations）的行为测试。

覆盖点（这些是前端最容易踩、后端必须兜住的）：
  · 空库读出来是空数组，不是 404 / null；
  · 坐标一律夹到 0~1：前端算出来的负值 / 超界值不该让标记飞到页外；
  · 认不出来的数据（类型不对、没有 id、高亮没有矩形、便签没有落点）跳过，不整单失败；
  · 重复 id 只留第一条；颜色认不出来退回默认黄，老的键名折算成色值；
  · 整表覆盖是原子的：PUT 之后 GET 读到的就是刚写的那一份；
  · 入参形状不对回 400（items 不是数组 / 超上限 / 不是 JSON 对象）。

复用 test_api_smoke 的进程内服务夹具（真 HTTP，覆盖路由表 + JSON 编码）。
"""
from __future__ import annotations

import json
import unittest
from urllib.parse import quote

from test.test_api_smoke import ServerFixture

PATH = "/api/papers/%s/annotations"


def highlight(ident, **extra):
    item = {
        "id": ident,
        "type": "highlight",
        "page": 2,
        "color": "yellow",
        "rects": [{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.02}],
        "quote": "  a highlighted   sentence  ",
        "note": "",
        "createdAt": "2026-01-01T00:00:00.000Z",
    }
    item.update(extra)
    return item


def note(ident, **extra):
    item = {
        "id": ident,
        "type": "note",
        "page": 3,
        "color": "blue",
        "rects": [],
        "point": {"x": 0.5, "y": 0.6},
        "quote": "",
        "note": "第一行\n第二行",
        "createdAt": "2026-01-01T00:00:00.000Z",
    }
    item.update(extra)
    return item


class AnnotationsApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fx = ServerFixture()
        status, data = cls.fx.json("POST", "/api/papers", {"title": "批注测试文献", "year": 2025})
        assert status == 201, (status, data)
        cls.paper_id = data["paper"]["id"]

    @classmethod
    def tearDownClass(cls):
        cls.fx.stop()

    def put(self, items):
        return self.fx.json("PUT", PATH % self.paper_id, {"items": items})

    def get(self):
        return self.fx.json("GET", PATH % self.paper_id)

    def test_01_empty_then_roundtrip(self):
        status, data = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(data["items"], [])
        self.assertEqual(data["updatedAt"], "")

        status, data = self.put([highlight("h1"), note("n1")])
        self.assertEqual(status, 200, data)
        self.assertEqual([i["id"] for i in data["items"]], ["h1", "n1"])
        self.assertTrue(data["updatedAt"])

        status, data = self.get()
        self.assertEqual(status, 200)
        self.assertEqual(len(data["items"]), 2)
        types = {i["id"]: i["type"] for i in data["items"]}
        self.assertEqual(types, {"h1": "highlight", "n1": "note"})
        saved = {i["id"]: i for i in data["items"]}
        # 引文折成单行；便签正文保留换行
        self.assertEqual(saved["h1"]["quote"], "a highlighted sentence")
        self.assertEqual(saved["n1"]["note"], "第一行\n第二行")
        # 高亮不存 point（省 payload），便签保留落点
        self.assertIsNone(saved["h1"]["point"])
        self.assertEqual(saved["n1"]["point"], {"x": 0.5, "y": 0.6})

    def test_02_clamps_and_filters(self):
        bad = [
            highlight("c1", rects=[{"x": -0.4, "y": 1.7, "w": 2, "h": -1}]),   # 宽高非法 → 整条丢
            highlight("c2", rects=[{"x": -0.4, "y": 1.7, "w": 0.5, "h": 0.1}], color="purple"),
            note("c3", point=None, rects=[]),                                   # 便签没落点 → 丢
            {"id": "c4", "type": "sticker", "page": 1},                         # 类型不认识 → 丢
            {"type": "highlight", "page": 1, "rects": [{"x": 0, "y": 0, "w": 1, "h": 1}]},  # 没 id → 丢
            "not an object",
        ]
        status, data = self.put(bad + [highlight("c2")])                        # 重复 id：只留第一条
        self.assertEqual(status, 200, data)
        ids = [i["id"] for i in data["items"]]
        self.assertEqual(ids, ["c2"])

        rect = data["items"][0]["rects"][0]
        self.assertAlmostEqual(rect["x"], 0.0)      # -0.4 → 0
        self.assertAlmostEqual(rect["y"], 1.0)      # 1.7 → 1
        self.assertAlmostEqual(rect["w"], 0.5)
        self.assertEqual(data["items"][0]["color"], "#f2c433")   # purple → 默认黄

    def test_02b_colors_accept_hex_and_legacy_keys(self):
        """调色盘加了自定义颜色：六位 / 三位 hex 都要原样存住，老键名折算成色值。"""
        status, data = self.put([
            highlight("hx", color="#12AB34"),
            highlight("h3", color="#abc"),
            note("nk", color="green"),
            highlight("hb", color="nonsense"),
        ])
        self.assertEqual(status, 200, data)
        colors = {i["id"]: i["color"] for i in data["items"]}
        self.assertEqual(colors, {
            "hx": "#12ab34",   # 大写规整成小写
            "h3": "#aabbcc",   # 三位展开成六位
            "nk": "#3f9e77",   # 老键名 green
            "hb": "#f2c433",   # 认不出来 → 默认黄
        })

    def test_03_replaces_whole_set(self):
        self.put([highlight("a"), highlight("b")])
        status, data = self.put([highlight("b")])
        self.assertEqual(status, 200)
        self.assertEqual([i["id"] for i in data["items"]], ["b"])
        self.assertEqual([i["id"] for i in self.get()[1]["items"]], ["b"])

    def test_04_rejects_bad_shapes(self):
        status, data = self.fx.json("PUT", PATH % self.paper_id, {"items": "nope"})
        self.assertEqual(status, 400)
        self.assertIn("items", data["error"])

        status, data = self.fx.json("PUT", PATH % self.paper_id, [1, 2, 3])
        self.assertEqual(status, 400)

        status, data = self.fx.json("PUT", PATH % self.paper_id,
                                    {"items": [highlight("x%d" % i) for i in range(801)]})
        self.assertEqual(status, 400)
        self.assertIn("批注太多", data["error"])

    def test_05_unknown_paper(self):
        status, data = self.fx.json("GET", PATH % "no-such-paper")
        self.assertEqual(status, 404)
        status, data = self.fx.json("PUT", PATH % "no-such-paper", {"items": []})
        self.assertEqual(status, 404)

    def test_06_method_not_allowed(self):
        status, data = self.fx.json("POST", PATH % self.paper_id, {"items": []})
        self.assertEqual(status, 404)       # 路由表里没有 POST 这一条（与其它端点一致）

    def test_attachment_pdf_annotations_are_isolated_and_follow_rename(self):
        status, data = self.fx.json("POST", "/api/papers", {"title": "Attachment annotation fixture"})
        self.assertEqual(status, 201)
        paper_id = data["paper"]["id"]
        attachments = "/api/papers/" + paper_id + "/attachments"

        def upload(name, data):
            status, _, payload = self.fx.call("POST", attachments, raw=(data, {"X-Filename": quote(name)}))
            self.assertEqual(status, 201, payload)
            return json.loads(payload)["attachment"]["file"]

        def annotation_path(file):
            return attachments + "/" + quote(file, safe="") + "/annotations"

        first = upload("方法.pdf", b"%PDF-1.4\n% first fixture")
        second = upload("figures.pdf", b"%PDF-1.4\n% second fixture")
        table = upload("data.csv", b"sample,value\n1,2\n")
        self.assertEqual(self.fx.call("GET", attachments + "/" + quote(first), headers={"Range": "bytes=0-4"}),
                         (206, "application/pdf", b"%PDF-"))
        self.assertEqual(self.fx.json("PUT", PATH % paper_id, {"items": [highlight("main")]})[0], 200)
        self.assertEqual(self.fx.json("PUT", annotation_path(first), {"items": [note("si-note")]})[0], 200)
        for path, expected in ((PATH % paper_id, ["main"]), (annotation_path(first), ["si-note"]),
                               (annotation_path(second), [])):
            status, data = self.fx.json("GET", path)
            self.assertEqual(status, 200)
            self.assertEqual([item["id"] for item in data["items"]], expected)
        self.assertEqual(self.fx.json("PUT", annotation_path(table), {"items": []})[0], 400)

        self.assertEqual(self.fx.json("PATCH", "/api/papers/" + paper_id, {"title": "Renamed fixture"})[0], 200)
        status, result = self.fx.json("POST", "/api/papers/tidy-pdf-names", {})
        self.assertEqual(status, 200)
        next_file = next(row["next"] for row in result["result"]["attachments"]["results"] if row["old"] == first)
        self.assertNotEqual(first, next_file)
        self.assertEqual(self.fx.json("GET", annotation_path(first))[0], 404)
        status, data = self.fx.json("GET", annotation_path(next_file))
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in data["items"]], ["si-note"])
        self.assertEqual(self.fx.json("DELETE", attachments + "/" + quote(next_file))[0], 200)
        self.assertEqual(self.fx.json("GET", annotation_path(next_file))[0], 404)
        self.assertEqual(self.fx.json("PUT", annotation_path(next_file), {"items": [note("old")]})[0], 404)


if __name__ == "__main__":
    unittest.main()
