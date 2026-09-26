"""Benchmark: convert a multi-megabyte HTML document and report timing.

Run:  python3 bench/benchmark.py [--size-mb 5] [--runs 3]
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from richtext2text import convert  # noqa: E402

PARA = ("<p>Retrieval and summarisation pipelines need plain text, but "
        "naive tag stripping destroys paragraph, list and table "
        "structure. This paragraph provides realistic prose bulk with "
        "<b>inline</b> <i>markup</i> and a "
        '<a href="https://example.com/ref">reference link</a>.</p>\n')

LIST = ("<ul><li>first item with some text</li>"
        "<li>second item<ul><li>nested child one</li>"
        "<li>nested child two</li></ul></li>"
        "<li>third item</li></ul>\n")

OLIST = ("<ol><li>step one</li><li>step two</li>"
         "<li>step three</li></ol>\n")

TABLE = ("<table><tr><th>Column A</th><th>Column B</th>"
         "<th>Column C</th></tr>"
         "<tr><td>value 1</td><td>value 2</td><td>value 3</td></tr>"
         "<tr><td>value 4</td><td>value 5</td><td>value 6</td></tr>"
         "</table>\n")

CODE = ("<pre>def transform(node):\n"
        "    return [render(child) for child in node.children]\n"
        "</pre>\n")

QUOTE = "<blockquote><p>A quoted passage worth keeping.</p></blockquote>\n"
HEADING = "<h2>Section heading</h2>\n"

BLOCKS = [PARA, LIST, TABLE, PARA, CODE, OLIST, QUOTE, HEADING]


def build_html(target_bytes: int) -> str:
    parts = ["<html><head><title>bench</title></head><body>\n"]
    size = len(parts[0])
    i = 0
    while size < target_bytes:
        block = BLOCKS[i % len(BLOCKS)]
        parts.append(block)
        size += len(block)
        i += 1
    parts.append("</body></html>")
    return "".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--size-mb", type=float, default=5.0)
    ap.add_argument("--runs", type=int, default=3)
    args = ap.parse_args()

    html = build_html(int(args.size_mb * 1024 * 1024))
    size_mb = len(html.encode("utf-8")) / (1024 * 1024)
    print(f"input size: {size_mb:.2f} MiB, runs: {args.runs}")

    times = []
    out_len = 0
    for _ in range(args.runs):
        start = time.perf_counter()
        out = convert(html)
        times.append(time.perf_counter() - start)
        out_len = len(out)

    best = min(times)
    mean = sum(times) / len(times)
    print(f"output size: {out_len / (1024 * 1024):.2f} MiB")
    print(f"best: {best:.3f}s  mean: {mean:.3f}s  "
          f"throughput(best): {size_mb / best:.1f} MiB/s")
    print("all times:", " ".join(f"{t:.3f}s" for t in times))


if __name__ == "__main__":
    main()
