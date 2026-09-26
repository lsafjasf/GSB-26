"""Benchmark: validate a 1,000,000-row CSV file. Usage: python3 bench.py"""

import os
import platform
import sys
import tempfile
import time

from csvcheck import Field, Validator

SCHEMA = [
    Field("id", "int"),
    Field("name", "str", min_length=1, max_length=20),
    Field("email", "str", pattern=r"^[^@\s]+@[^@\s]+$"),
    Field("age", "int", min_value=0, max_value=150),
    Field("role", "enum", choices=["admin", "user", "guest"]),
]
ROWS = 1_000_000


def generate(path, error_rate):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("id,name,email,age,role\n")
        for i in range(ROWS):
            if i % 20 < error_rate * 20:          # deterministic bad rows
                fh.write(f"{i},,not-an-email,999,alien\n")
            else:
                fh.write(f"{i},user{i},u{i}@example.com,25,user\n")


def run_case(path, **kw):
    validator = Validator(SCHEMA, **kw)
    start = time.perf_counter()
    result = validator.validate_file(path)
    elapsed = time.perf_counter() - start
    return result, elapsed


def main():
    tmp = tempfile.mkdtemp()
    print(f"python {platform.python_version()} | {platform.platform()}")
    print(f"rows per file: {ROWS:,}\n")

    cases = [("clean file (0% errors)", 0.0, {}),
             ("dirty file (5% bad rows)", 0.05, {}),
             ("dirty file, max_errors=100", 0.05, {"max_errors": 100})]
    for label, rate, kw in cases:
        path = os.path.join(tmp, f"bench_{rate}.csv")
        gen_start = time.perf_counter()
        generate(path, rate)
        size_mb = os.path.getsize(path) / 1e6
        result, elapsed = run_case(path, **kw)
        print(f"{label}")
        print(f"  file size      : {size_mb:.1f} MB")
        print(f"  rows processed : {result.rows_processed:,}")
        print(f"  total errors   : {result.total_errors:,} (truncated={result.truncated})")
        print(f"  validate time  : {elapsed:.2f}s  ({ROWS / elapsed:,.0f} rows/s)\n")
        os.remove(path)


if __name__ == "__main__":
    main()
