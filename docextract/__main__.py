"""命令行入口：python -m docextract <文件...> [-o 输出.json]"""

import argparse
import json
import sys

from .extractor import extract_file


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="docextract",
        description="提取 Python 源码中函数/类的文档注释，输出结构化 JSON")
    parser.add_argument("files", nargs="+", help="待提取的 .py 文件")
    parser.add_argument("-o", "--output", help="输出 JSON 文件（默认 stdout）")
    parser.add_argument("--indent", type=int, default=2)
    args = parser.parse_args(argv)

    result = [extract_file(path) for path in args.files]
    payload = result[0] if len(result) == 1 else result
    text = json.dumps(payload, ensure_ascii=False, indent=args.indent)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    else:
        sys.stdout.write(text + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
