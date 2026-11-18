"""Measure storage startup and one-card edits on a repeatable synthetic library."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path


def seed(conn, count):
    titles = [(str(i), "Fixture %05d" % i) for i in range(count)]
    data = json.dumps({"summary": "Fixture text. " * 40})
    with conn:
        conn.executemany("INSERT INTO papers(id,title,data) VALUES (?,?,?)",
                         ((paper_id, title, data) for paper_id, title in titles))
        conn.execute("INSERT INTO tags(name) VALUES ('demo')")
        conn.executemany("INSERT INTO paper_tags(paper_id,tag) VALUES (?, 'demo')",
                         ((paper_id,) for paper_id, _ in titles))
        conn.executemany("INSERT INTO attachments(paper_id,file) VALUES (?, 'supplement.txt')",
                         ((paper_id,) for paper_id, _ in titles))
        conn.executemany("INSERT INTO chat_messages(paper_id,role,content) VALUES (?,?,?)",
                         ((paper_id, role, ("%s %d. " % (role, turn)) * 10)
                          for paper_id, _ in titles for turn in range(4) for role in ("user", "assistant")))


def measure(work, samples):
    elapsed = []
    for _ in range(samples):
        start = time.perf_counter()
        work()
        elapsed.append((time.perf_counter() - start) * 1000)
    return round(statistics.median(elapsed), 2)


def sql_count(conn, work, operations):
    count = 0

    def trace(statement):
        nonlocal count
        if statement.split(maxsplit=1)[0].upper() in operations:
            count += 1

    conn.set_trace_callback(trace)
    try:
        work()
    finally:
        conn.set_trace_callback(None)
    return count


def run(size, samples, Store):
    with tempfile.TemporaryDirectory(prefix="pl-library-bench-") as directory:
        store = Store(directory)
        try:
            conn = store.repo.conn
            # Give each revision the same sorter settings and avoid host temp-file I/O.
            conn.execute("PRAGMA temp_store=MEMORY")
            seed(conn, size)
            load_sql = sql_count(conn, store.load, {"SELECT"})
            load_ms = measure(store.load, samples)
            paper_id = str(size // 2)

            def edit():
                rating = store.find_paper(paper_id)["rating"]
                store.patch_paper(paper_id, {"rating": 5 if rating == 1 else 1})

            edit_sql = sql_count(conn, edit, {"INSERT", "DELETE", "UPDATE", "REPLACE"})
            return {"cards": size, "loadSelects": load_sql, "loadMs": load_ms,
                    "editWrites": edit_sql, "editMs": measure(edit, samples)}
        finally:
            store.repo.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000])
    parser.add_argument("--samples", type=int, default=7)
    args = parser.parse_args()
    if args.samples < 1 or any(size < 1 for size in args.sizes):
        parser.error("--sizes and --samples must be positive")

    sys.path.insert(0, str(args.root.resolve()))
    from paperlib.data.store import Store

    print(json.dumps({"samples": args.samples, "results": [run(size, args.samples, Store) for size in args.sizes]},
                     indent=2))


if __name__ == "__main__":
    main()
