"""命令行入口：python3 -m dslc [--optimize] [--run] [--set k=v] file.dsl"""

import argparse
import json
import sys

from .compiler import compile_file
from .errors import CompileError
from .interpreter import execute


def parse_inputs(pairs):
    inputs = {}
    for pair in pairs:
        key, _, raw = pair.partition("=")
        try:
            inputs[key] = json.loads(raw)
        except json.JSONDecodeError:
            inputs[key] = raw
    return inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description="配置 DSL 编译器")
    parser.add_argument("file", help="DSL 配置文件")
    parser.add_argument("--optimize", action="store_true", help="输出优化后的计划")
    parser.add_argument("--run", action="store_true", help="解释执行并打印步骤轨迹")
    parser.add_argument("--set", dest="overrides", action="append", default=[],
                        metavar="k=v", help="覆盖参数（值按 JSON 解析，失败则按字符串）")
    args = parser.parse_args(argv)

    try:
        plan = compile_file(args.file, optimize=args.optimize)
    except CompileError as error:
        print(f"compile error: {error}", file=sys.stderr)
        return 1

    inputs = parse_inputs(args.overrides)
    if args.run:
        print(json.dumps(execute(plan, inputs), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
