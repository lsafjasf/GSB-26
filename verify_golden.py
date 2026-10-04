"""对拍：并行结果 vs 串行基准（golden 文件），逐行逐值比对。

对每个查询、每个并行度：
  1. 与 golden 文件（串行基准）比对
  2. 与现场串行结果比对
全部通过才退出码 0。

运行: python3 verify_golden.py
"""

import csv
import os
import sys

from qengine import Schema, list_shards, run_parallel, run_serial
from qengine.queries import ALL_QUERIES, EVENTS_COLS

HERE = os.path.dirname(os.path.abspath(__file__))
IN_DIR = os.path.join(HERE, "data", "golden", "input")
OUT_DIR = os.path.join(HERE, "data", "golden", "expected")

WORKERS = [1, 2, 3, 4, 8]   # 含"并行度 > 分片数"（4 分片 vs 8 workers）


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, list):
        return "|".join(_fmt(x) for x in v)
    return str(v)


def load_expected(path):
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]


def to_csv_rows(rows, cols):
    return [[_fmt(r[c]) for c in cols] for r in rows]


def main():
    schema = Schema(EVENTS_COLS)
    shards = list_shards(IN_DIR)
    print(f"shards: {[os.path.basename(p) for p in shards]}")
    failures = 0
    for name, plan in ALL_QUERIES.items():
        cols = plan.output_columns()
        exp_cols, exp_rows = load_expected(os.path.join(OUT_DIR, f"{name}.csv"))
        assert exp_cols == cols, f"{name}: header mismatch {exp_cols} vs {cols}"

        serial_rows = to_csv_rows(run_serial(shards, schema, plan), cols)
        ok = serial_rows == exp_rows
        print(f"[{name}] serial vs golden      : {'PASS' if ok else 'FAIL'} "
              f"({len(serial_rows)} rows)")
        failures += not ok

        for w in WORKERS:
            got = to_csv_rows(run_parallel(shards, schema, plan, w), cols)
            ok = got == exp_rows
            print(f"[{name}] parallel w={w:<2} vs golden: "
                  f"{'PASS' if ok else 'FAIL'} ({len(got)} rows)")
            if not ok:
                failures += 1
                for i, (a, b) in enumerate(zip(got, exp_rows)):
                    if a != b:
                        print(f"    first diff row {i}: got={a} exp={b}")
                        break
    if failures:
        print(f"\nFAILED: {failures} mismatches")
        sys.exit(1)
    print("\nALL PASS: parallel == serial == golden, 逐行逐值一致")


if __name__ == "__main__":
    main()
