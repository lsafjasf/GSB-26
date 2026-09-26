"""命令行入口：python -m logpipe ..."""
from __future__ import annotations

import argparse
import sys
from typing import Optional, TextIO

from .config import ConfigError, OutputTemplate, load_output, load_rules
from .formatters import format_error, format_record, write_header
from .legacy import run_legacy
from .parser import ParseError, parse_line


def build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="logpipe",
        description="声明式日志解析管道：按配置切分字段并输出 jsonl/csv。",
    )
    ap.add_argument("--rules", help="解析规则配置（JSON）。缺省时走旧固定切分模式")
    ap.add_argument("--output", help="输出模板配置（JSON）；缺省输出全部字段、jsonl 格式")
    ap.add_argument("--legacy", action="store_true",
                    help="强制使用旧固定切分模式（等价于不传 --rules）")
    ap.add_argument("--input", help="输入文件，缺省读 stdin")
    ap.add_argument("--errors", help="失败行输出文件，缺省写 stderr")
    ap.add_argument("--strict", action="store_true",
                    help="存在失败行时以退出码 1 结束（默认仍为 0）")
    return ap


def _open(path: Optional[str], mode: str, default: TextIO) -> tuple[TextIO, bool]:
    if path is None:
        return default, False
    return open(path, mode, encoding="utf-8", newline=""), True


def run_configured(fin, out: TextIO, ferr: TextIO, rules_path: str,
                   output_path: Optional[str]) -> int:
    """新模式主流程。返回失败行数；单行失败不中断整批。"""
    rules = load_rules(rules_path)
    if output_path:
        template = load_output(output_path, rules)
    else:
        template = OutputTemplate(format="jsonl",
                                  fields=[f.name for f in rules.fields])
    write_header(out, template)
    failed = 0
    for line_no, line in enumerate(fin, 1):
        try:
            record = parse_line(line, rules)
        except ParseError as exc:
            failed += 1
            ferr.write(format_error(line_no, line, str(exc)) + "\n")
            continue
        out.write(format_record(record, template) + "\n")
    return failed


def main(argv: Optional[list[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if args.rules and args.legacy:
        print("logpipe: --rules 与 --legacy 互斥", file=sys.stderr)
        return 2
    if args.output and not args.rules:
        print("logpipe: --output 需要与 --rules 一起使用", file=sys.stderr)
        return 2

    fin, close_in = _open(args.input, "r", sys.stdin)
    ferr, close_err = _open(args.errors, "w", sys.stderr)
    try:
        try:
            if args.legacy or not args.rules:
                failed = run_legacy(fin, sys.stdout, ferr)
            else:
                failed = run_configured(fin, sys.stdout, ferr, args.rules, args.output)
        except ConfigError as exc:
            print(f"logpipe: 配置非法: {exc}", file=sys.stderr)
            return 2
        print(f"logpipe: failed={failed}", file=sys.stderr)
        return 1 if (args.strict and failed) else 0
    finally:
        if close_in:
            fin.close()
        if close_err:
            ferr.close()


if __name__ == "__main__":
    raise SystemExit(main())
