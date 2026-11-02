# -*- coding: utf-8 -*-
# pdf_index 与 thumbs 的测试：缓存命中/失效、缩略图生成与尺寸。
import os
import shutil
import sys
import tempfile
import time

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)
import pdf_index as PI
import thumbs as TH

passed = failed = 0


def check(name, cond, extra=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  [OK]   {name}")
    else:
        failed += 1
        print(f"  [FAIL] {name} {extra}")


tmp = tempfile.mkdtemp(prefix="lvidx_")
try:
    libdir = os.path.join(tmp, "lib")
    os.makedirs(libdir)
    os.makedirs(os.path.join(tmp, "data"))

    def mkpdf(name, body=b"%PDF-1.4 hello"):
        p = os.path.join(libdir, name)
        with open(p, "wb") as f:
            f.write(body)
        return {"full_path": p, "filename": os.path.basename(p), "folder": "x"}

    infos = [mkpdf(f"p{i}.pdf", b"%PDF-1.4 " + bytes([i]) * 500) for i in range(5)]
    library = set()

    print("=== 1) 首次（冷）：全部要算哈希 ===")
    n, total, st = PI.count_new(infos, tmp, lambda pid: pid in library)
    check("总数正确", total == 5, total)
    check("冷启动全部现算", st["hashed"] == 5, st)
    check("缓存命中 0", st["cached"] == 0, st)
    check("待导入 = 5", n == 5, n)
    check("索引已落盘", os.path.isfile(os.path.join(tmp, "data", ".pdf_index.json")))

    print("\n=== 2) 第二次（热）：应全部命中缓存 ===")
    n2, _, st2 = PI.count_new(infos, tmp, lambda pid: pid in library)
    check("热启动全部命中", st2["cached"] == 5, st2)
    check("热启动不再算哈希", st2["hashed"] == 0, st2)
    check("结果一致", n2 == 5, n2)

    print("\n=== 3) 入库后待导入应为 0 ===")
    ids = [e["id"] for e in PI.hash_pdfs(infos, tmp)[0]]
    library = set(ids)
    n3, _, st3 = PI.count_new(infos, tmp, lambda pid: pid in library)
    check("已入库则待导入 0", n3 == 0, n3)
    check("仍然全部命中缓存", st3["cached"] == 5, st3)

    print("\n=== 4) 文件被改动 → 只重算那一个 ===")
    time.sleep(0.01)
    with open(infos[2]["full_path"], "wb") as f:
        f.write(b"%PDF-1.4 CHANGED" + b"x" * 600)
    library = set(ids) - {ids[2]}          # 内容变了 → 哈希也变 → 变成「待导入」
    n4, _, st4 = PI.count_new(infos, tmp, lambda pid: pid in library)
    check("只有 1 个重算", st4["hashed"] == 1, st4)
    check("其余 4 个命中", st4["cached"] == 4, st4)
    check("改动的那个被算作待导入", n4 == 1, n4)

    print("\n=== 5) 新增文件 → 只算新的 ===")
    infos2 = infos + [mkpdf("new.pdf", b"%PDF-1.4 newone" + b"y" * 300)]
    n5, total5, st5 = PI.count_new(infos2, tmp, lambda pid: pid in set(ids))
    check("总数 +1", total5 == 6, total5)
    check("只算新增的 1 个", st5["hashed"] == 1, st5)
    check("其余 5 个命中", st5["cached"] == 5, st5)

    print("\n=== 6) 删除文件 → 索引不留尸体 ===")
    os.remove(infos[0]["full_path"])
    n6, total6, st6 = PI.count_new(infos2[1:], tmp, lambda pid: pid in set(ids))
    idx = PI.load_index(tmp)
    check("总数减 1", total6 == 5, total6)
    check("被删文件的条目已清除", os.path.abspath(infos[0]["full_path"]) not in idx,
          list(idx.keys())[:2])
    check("失败计数为 0（文件是主动移除而非读失败）", st6["failed"] == 0, st6)

    print("\n=== 7) 损坏/不可读文件不影响其它 ===")
    bad = os.path.join(libdir, "unreadable.pdf")
    with open(bad, "wb") as f:
        f.write(b"%PDF")
    os.chmod(bad, 0o000) if os.name != "nt" else None
    infos3 = infos2[1:] + [{"full_path": os.path.join(libdir, "nope.pdf"),
                            "filename": "nope.pdf", "folder": "x"}]
    n7, total7, st7 = PI.count_new(infos3, tmp, lambda pid: True)
    check("不存在的文件被跳过而不是报错", st7["failed"] >= 1, st7)

    print("\n=== 8) 缩略图 ===")
    try:
        from PIL import Image
        # 造一张大图（非目标比例，验证裁剪）
        src = os.path.join(tmp, "big.png")
        big = Image.new("RGB", (2000, 1000), (200, 30, 30))
        big.save(src)
        t1 = TH.make_thumb(src, tmp)
        check("生成了缩略图", bool(t1) and os.path.isfile(t1), t1)
        with Image.open(t1) as im:
            check("尺寸符合预期", im.size == TH.THUMB_SIZE, im.size)
        check("缩略图比原图小很多", os.path.getsize(t1) < os.path.getsize(src) / 5,
              f"{os.path.getsize(t1)} vs {os.path.getsize(src)}")
        t1b = TH.make_thumb(src, tmp)
        check("第二次命中缓存（同路径）", t1b == t1, (t1b, t1))
        # 改图后应重新生成
        time.sleep(0.01)
        Image.new("RGB", (900, 1200), (30, 30, 200)).save(src)
        t1c = TH.make_thumb(src, tmp)
        check("原图变了则重新生成", t1c != t1, (t1c, t1))
        stt = TH.cache_stats(tmp)
        check("缓存统计到文件", stt["files"] >= 2, stt)
        check("清缓存生效", TH.clear_cache(tmp) >= 2)
        check("清完为空", TH.cache_stats(tmp)["files"] == 0)
        check("不存在的源返回 None", TH.make_thumb(os.path.join(tmp, "no.png"), tmp) is None)
    except ImportError:
        check("Pillow 可用", False, "未安装 Pillow")

finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"\n通过 {passed} 项，失败 {failed} 项")
sys.exit(1 if failed else 0)
