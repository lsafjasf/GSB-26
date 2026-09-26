"""命令行入口：python3 -m typechecker <file.dsl> [...]"""
import sys

from .infer import Checker
from .parser import ParseError, parse_program


def main(argv) -> int:
    if not argv:
        print("用法: python3 -m typechecker <文件.dsl> [...]", file=sys.stderr)
        return 2
    exit_code = 0
    for path in argv:
        with open(path, "r", encoding="utf-8") as fh:
            src = fh.read()
        print(f"== {path} ==")
        try:
            program = parse_program(src)
        except ParseError as exc:
            print(f"error: 解析失败: {exc}")
            exit_code = 1
            continue
        result = Checker().check(program)
        for name, ty in result.def_types.items():
            print(f"  {name} : {ty}")
        for ty in result.expr_types:
            print(f"  <表达式> : {ty}")
        for diag in [*result.errors, *result.warnings, *result.infos]:
            print(diag.render())
        if result.errors:
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
