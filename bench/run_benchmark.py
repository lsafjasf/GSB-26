#!/usr/bin/env python3
"""性能基准：对合成代码库执行提取并报告耗时。

用法：python3 bench/run_benchmark.py [源码文件] [重复次数]
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from docxtract import extract  # noqa: E402


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "bench" / "bench_src.py")
    repeat = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    source = Path(path).read_text(encoding="utf-8")
    lines = source.count("\n") + 1

    # 预热一次
    result = extract(source, path)

    timings = []
    for _ in range(repeat):
        start = time.perf_counter()
        result = extract(source, path)
        timings.append(time.perf_counter() - start)

    best = min(timings)
    stats = result["stats"]
    print("文件:           {}".format(path))
    print("代码行数:       {:,}".format(lines))
    print("提取条目数:     {:,}".format(stats["entries"]))
    print("注释条数:       {:,}".format(stats["comments"]))
    print("重复次数:       {}".format(repeat))
    print("各次耗时(秒):   {}".format(", ".join("{:.3f}".format(t) for t in timings)))
    print("最优耗时(秒):   {:.3f}".format(best))
    print("吞吐(行/秒):    {:,.0f}".format(lines / best))
    print("吞吐(条目/秒):  {:,.0f}".format(stats["entries"] / best))


if __name__ == "__main__":
    main()
