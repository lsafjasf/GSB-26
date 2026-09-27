"""命令行入口：python3 -m docxtract <源码文件> [-o 输出.json]"""

from __future__ import annotations

import argparse
import json
import sys

from .core import extract_file


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="docxtract", description="从 Python 源码提取定义与文档注释")
    parser.add_argument("source", help="Python 源码文件路径")
    parser.add_argument("-o", "--output", help="输出 JSON 文件（默认打印到 stdout）")
    parser.add_argument("--indent", type=int, default=2, help="JSON 缩进（默认 2）")
    args = parser.parse_args(argv)

    result = extract_file(args.source)
    text = json.dumps(result, ensure_ascii=False, indent=args.indent)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("written: {} ({} entries, {} comments)".format(
            args.output, result["stats"]["entries"], result["stats"]["comments"]))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
