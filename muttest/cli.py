"""Command line interface: python -m muttest ..."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .mutator import collect_mutants
from .report import render_json, render_text
from .runner import run_suite


def _load_equivalents(path):
    if not path:
        return {}
    with open(path) as fh:
        return json.load(fh)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="muttest",
        description="A standard-library-only mutation testing framework.")
    parser.add_argument("module", help="python module under test")
    parser.add_argument("tests", help="unittest test module for it")
    parser.add_argument("--equivalents", metavar="FILE",
                        help="JSON file {mutant_id: reason} with manually "
                             "confirmed equivalent mutants")
    parser.add_argument("--timeout", type=float, default=5.0,
                        help="per-mutant timeout in seconds (default 5)")
    parser.add_argument("--jobs", type=int, default=max(1, os.cpu_count() or 1),
                        help="parallel worker processes (default: cpu count)")
    parser.add_argument("--cache", metavar="FILE", default=".muttest-cache.json",
                        help="incremental result cache (default "
                             ".muttest-cache.json)")
    parser.add_argument("--no-cache", action="store_true",
                        help="disable the incremental cache")
    parser.add_argument("--list", action="store_true",
                        help="only list mutants (ids needed for the "
                             "equivalents file), do not run tests")
    parser.add_argument("--json", metavar="FILE", help="write JSON report")
    parser.add_argument("--report", metavar="FILE", help="write text report")
    args = parser.parse_args(argv)

    if args.list:
        with open(args.module) as fh:
            mutants = collect_mutants(fh.read())
        for m in mutants:
            print(f"{m.id:<38} line {m.lineno:<4} {m.detail}")
        print(f"{len(mutants)} mutants")
        return 0

    summary = run_suite(
        args.module, args.tests,
        timeout=args.timeout,
        jobs=args.jobs,
        cache_path=None if args.no_cache else args.cache,
        equivalents=_load_equivalents(args.equivalents),
    )
    text = render_text(summary, args.module, args.tests)
    print(text)
    if args.report:
        with open(args.report, "w") as fh:
            fh.write(text + "\n")
    if args.json:
        with open(args.json, "w") as fh:
            fh.write(render_json(summary, args.module, args.tests) + "\n")
    # exit code 1 if any non-equivalent mutant survived: useful for CI gates
    return 1 if any(r.status == "survived" and not r.equivalent
                    for r in summary.results) else 0


if __name__ == "__main__":
    sys.exit(main())
