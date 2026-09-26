"""命令行入口：python3 -m callgraph.cli <file.py>"""

import sys

from .cycles import find_cycles
from .graph import CallGraph
from .report import format_report


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv:
        print("用法: python3 -m callgraph.cli <python源文件>")
        return 2
    with open(argv[0], "r", encoding="utf-8") as fh:
        source = fh.read()
    graph = CallGraph.from_source(source)
    cycles = find_cycles(graph)
    print(format_report(graph, cycles))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
