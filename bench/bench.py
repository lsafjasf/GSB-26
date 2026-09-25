"""对生成的语料库计时提取，输出性能数据。"""

import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docextract import extract_file  # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parent / "corpus"


def main():
    files = sorted(CORPUS.glob("*.py"))
    total_lines = 0
    total_entries = 0
    start = time.perf_counter()
    for path in files:
        result = extract_file(path)
        total_entries += len(result["entries"])
    elapsed = time.perf_counter() - start
    for path in files:
        total_lines += sum(1 for _ in open(path, encoding="utf-8"))
    report = {
        "files": len(files),
        "lines": total_lines,
        "entries": total_entries,
        "seconds": round(elapsed, 3),
        "lines_per_second": int(total_lines / elapsed),
        "python": sys.version.split()[0],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    out = pathlib.Path(__file__).resolve().parent / "bench_result.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")


if __name__ == "__main__":
    main()
