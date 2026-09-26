"""Command line entry point: ``python3 -m logpipe ...``."""

import argparse
import json
import sys

from .config import ALLOWED_FORMATS, ConfigError, load_config
from .core import run_pipeline
from .legacy import legacy_config, run_legacy

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_CONFIG = 2
EXIT_ROW_FAILURES = 3


def build_arg_parser():
    parser = argparse.ArgumentParser(
        prog="logpipe",
        description="Config-driven log line converter (Python stdlib only).",
    )
    parser.add_argument(
        "inputs",
        nargs="*",
        help="input log files, processed in order (default: stdin)",
    )
    parser.add_argument("--rules", help="path to the rules config (JSON)")
    parser.add_argument(
        "--format",
        choices=ALLOWED_FORMATS,
        help="override the output format declared in the config",
    )
    parser.add_argument("--output", help="output file (default: stdout)")
    parser.add_argument(
        "--errors", help="file for failed lines + reasons (default: stderr)"
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the rules config and exit",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with code 3 if any line fails conversion",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="legacy fixed-split mode: split on a fixed delimiter into "
        "positional string columns col1..colN, emit JSON Lines",
    )
    parser.add_argument(
        "--legacy-delimiter",
        default="|",
        help="delimiter for legacy mode (default: '|')",
    )
    parser.add_argument(
        "--legacy-fields",
        type=int,
        default=0,
        metavar="N",
        help="field count, required by --dump-legacy-config",
    )
    parser.add_argument(
        "--dump-legacy-config",
        action="store_true",
        help="print a rules config equivalent to legacy mode and exit; "
        "use it as the starting point for migration",
    )
    return parser


def _iter_inputs(paths):
    if not paths:
        yield from sys.stdin
        return
    for path in paths:
        with open(path, "r", encoding="utf-8") as fh:
            yield from fh


def _run_config_mode(args, config):
    out_stream = (
        open(args.output, "w", encoding="utf-8", newline="")
        if args.output
        else sys.stdout
    )
    err_stream = (
        open(args.errors, "w", encoding="utf-8") if args.errors else sys.stderr
    )
    try:
        stats = run_pipeline(
            _iter_inputs(args.inputs), config, out_stream, err_stream
        )
    finally:
        if args.output:
            out_stream.close()
        if args.errors:
            err_stream.close()
    print(
        f"summary: total={stats.total} ok={stats.ok} failed={stats.failed}",
        file=sys.stderr,
    )
    if args.strict and stats.failed:
        return EXIT_ROW_FAILURES
    return EXIT_OK


def _run_legacy_mode(args):
    out_stream = (
        open(args.output, "w", encoding="utf-8", newline="")
        if args.output
        else sys.stdout
    )
    try:
        total = run_legacy(
            _iter_inputs(args.inputs), args.legacy_delimiter, out_stream
        )
    finally:
        if args.output:
            out_stream.close()
    print(f"summary: total={total} ok={total} failed=0", file=sys.stderr)
    return EXIT_OK


def main(argv=None):
    args = build_arg_parser().parse_args(argv)

    if args.dump_legacy_config:
        if args.legacy_fields < 1:
            print(
                "error: --dump-legacy-config requires --legacy-fields N",
                file=sys.stderr,
            )
            return EXIT_USAGE
        config = legacy_config(args.legacy_fields, args.legacy_delimiter)
        print(json.dumps(config, indent=2, ensure_ascii=False))
        return EXIT_OK

    if args.legacy:
        return _run_legacy_mode(args)

    if not args.rules:
        print("error: --rules is required (or use --legacy)", file=sys.stderr)
        return EXIT_USAGE
    try:
        config = load_config(args.rules)
    except ConfigError as exc:
        print(f"invalid config in {args.rules}:", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return EXIT_CONFIG
    except OSError as exc:
        print(f"error: cannot read config: {exc}", file=sys.stderr)
        return EXIT_USAGE

    if args.format:
        config.out_format = args.format

    if args.check:
        print(
            f"config OK: {len(config.fields)} fields, "
            f"output format {config.out_format!r}"
        )
        return EXIT_OK

    return _run_config_mode(args, config)


if __name__ == "__main__":
    sys.exit(main())
