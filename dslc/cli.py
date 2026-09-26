"""Command line interface: check / compile / run / differential / bench."""

import argparse
import itertools
import json
import sys
import time

from .ast_nodes import Bin, Lit, Not, Unresolved
from .compiler import build_plan, compile_plan
from .errors import DSLError, DSLErrorList
from .interp import (InputError, effectful_sequence, execute,
                     traces_equivalent)
from .lexer import tokenize
from .parser import parse_config
from .validate import validate


def read_config(path):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return parse_config(tokenize(text))


def report_errors(exc):
    if isinstance(exc, DSLErrorList):
        for err in exc.errors:
            print(err, file=sys.stderr)
    else:
        print(exc, file=sys.stderr)


def expr_text(e):
    if isinstance(e, Lit):
        return json.dumps(e.value)
    if isinstance(e, Unresolved):
        return e.name
    if isinstance(e, Not):
        return "(not {})".format(expr_text(e.operand))
    if isinstance(e, Bin):
        return "({} {} {})".format(expr_text(e.left), e.op, expr_text(e.right))
    return "?"


def plan_jsonable(plan):
    steps = {}
    for name, s in plan["steps"].items():
        entry = {
            "run": s["run"],
            "needs": s["needs"],
            "conds": s["conds"],
            "when": expr_text(s["when"]) if s["when"] is not None else None,
        }
        if s["branch"]:
            entry["branch"] = {
                "on": expr_text(s["branch"]["on"]),
                "cases": [[json.dumps(l), t] for l, t in s["branch"]["cases"]],
                "else": s["branch"]["else"],
            }
        steps[name] = entry
    return {
        "params": {
            n: dict({"type": str(p["type"])},
                    **({"default": p["default"]} if "default" in p else {}))
            for n, p in plan["params"].items()
        },
        "steps": steps,
        "stages": plan["stages"],
    }


def print_plan(plan, title):
    n_steps = len(plan["steps"])
    widest = max((len(st) for st in plan["stages"]), default=0)
    print("{}: {} steps, {} stages, max stage width {}".format(
        title, n_steps, len(plan["stages"]), widest))
    for i, stage in enumerate(plan["stages"]):
        print("  stage {:>3}: {}".format(i, ", ".join(stage)))


def cmd_check(args):
    try:
        config = read_config(args.file)
        validate(config)
    except (DSLError, DSLErrorList) as e:
        report_errors(e)
        return 1
    print("OK: {} params, {} steps".format(
        len(config.params), len(config.steps)))
    return 0


def cmd_compile(args):
    try:
        config = read_config(args.file)
        plan = compile_plan(config, optimize=not args.no_optimize)
    except (DSLError, DSLErrorList) as e:
        report_errors(e)
        return 1
    if args.json:
        print(json.dumps(plan_jsonable(plan), indent=2))
    else:
        print_plan(plan, "optimized plan" if not args.no_optimize
                   else "unoptimized plan")
    return 0


def parse_input_value(s):
    if s == "true":
        return True
    if s == "false":
        return False
    try:
        return int(s)
    except ValueError:
        return s


def parse_inputs(text):
    inputs = {}
    if not text:
        return inputs
    for item in text.split(","):
        k, _, v = item.partition("=")
        inputs[k.strip()] = parse_input_value(v.strip())
    return inputs


def cmd_run(args):
    try:
        config = read_config(args.file)
        plan = compile_plan(config, optimize=not args.no_optimize)
        inputs = parse_inputs(args.input)
        trace = execute(plan, inputs)
    except (DSLError, DSLErrorList) as e:
        report_errors(e)
        return 1
    except InputError as e:
        print("input error: {}".format(e), file=sys.stderr)
        return 1
    for i, rec in enumerate(trace.stages):
        for name, did in rec:
            print("stage {:>3}  {}  {}".format(
                i, "RUN " if did else "SKIP", name))
    seq = effectful_sequence(plan, trace)
    print("effectful trace: " + (" -> ".join(seq) if seq else "(empty)"))
    return 0


def cmd_differential(args):
    try:
        config = read_config(args.file)
        plan_l = compile_plan(config, optimize=False)
        plan_o = compile_plan(config, optimize=True)
    except (DSLError, DSLErrorList) as e:
        report_errors(e)
        return 1
    names = list(plan_l["params"])
    space = []
    for n in names:
        t = plan_l["params"][n]["type"]
        if t.kind == "enum":
            space.append(list(t.values))
        elif t.kind == "bool":
            space.append([False, True])
        elif t.kind == "int":
            space.append([0, 1, 2])
        else:
            space.append(["x"])
    combos = list(itertools.product(*space)) if names else [()]
    combos = combos[: args.max]
    failures = 0
    for combo in combos:
        inputs = dict(zip(names, combo))
        tl = execute(plan_l, inputs)
        to = execute(plan_o, inputs)
        ok, why = traces_equivalent(plan_l, tl, plan_o, to)
        if not ok:
            failures += 1
            print("MISMATCH at {}: {}".format(inputs, why))
    print("differential: {} input combinations, {} equivalent, "
          "{} mismatches".format(len(combos), len(combos) - failures,
                                 failures))
    return 1 if failures else 0


def gen_chain(n):
    lines = []
    for i in range(n):
        needs = "  needs: s{}\n".format(i - 1) if i else ""
        lines.append("step s{} {{\n{}  run: \"step {}\"\n}}".format(
            i, needs, i))
    return "\n".join(lines)


def gen_wide(n):
    lines = ["step w{} {{\n  run: \"step {}\"\n}}".format(i, i)
             for i in range(n)]
    join_needs = ", ".join("w{}".format(i) for i in range(n))
    lines.append("step join {{\n  needs: {}\n  run: \"join\"\n}}".format(
        join_needs))
    return "\n".join(lines)


def gen_mixed(n):
    lines = ["param env: enum(a, b, c) = a", "param flag: bool = true"]
    for i in range(n):
        body = []
        if i > 0:
            body.append("needs: s{}".format(i - 1))
        if i % 3 == 1:
            body.append('run: "work {}"'.format(i))
        if i % 7 == 3:
            body.append("when: flag")
        if i % 5 == 4 and i + 2 < n:
            body.append("branch on env {{\n"
                        "    case a -> s{}\n"
                        "    case b -> s{}\n"
                        "    else -> skip\n"
                        "  }}".format(i + 1, i + 2))
        lines.append("step s{} {{\n  ".format(i) + "\n  ".join(body) + "\n}")
    return "\n".join(lines)


def cmd_bench(args):
    n = args.steps
    configs = [("deep-chain", gen_chain(n)),
               ("wide-parallel", gen_wide(n)),
               ("mixed", gen_mixed(n))]
    header = "{:<14}{:>7}{:>10}{:>13}{:>10}{:>13}{:>10}".format(
        "config", "steps", "parse_ms", "validate_ms", "build_ms",
        "optimize_ms", "total_ms")
    print(header)
    for label, text in configs:
        t0 = time.perf_counter()
        config = parse_config(tokenize(text))
        t1 = time.perf_counter()
        validate(config)
        t2 = time.perf_counter()
        plan = build_plan(config, optimize=False)
        t3 = time.perf_counter()
        plan_o = build_plan(config, optimize=True)
        t4 = time.perf_counter()
        print("{:<14}{:>7}{:>10.1f}{:>13.1f}{:>10.1f}{:>13.1f}{:>10.1f}".format(
            label, len(plan["steps"]),
            (t1 - t0) * 1e3, (t2 - t1) * 1e3, (t3 - t2) * 1e3,
            (t4 - t3) * 1e3, (t4 - t0) * 1e3))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="dslc", description="Config DSL compiler (stdlib only)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="parse and statically validate")
    p.add_argument("file")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("compile", help="compile to an execution plan")
    p.add_argument("file")
    p.add_argument("--no-optimize", action="store_true")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_compile)

    p = sub.add_parser("run", help="execute a plan with the interpreter")
    p.add_argument("file")
    p.add_argument("--no-optimize", action="store_true")
    p.add_argument("--input", default="",
                   help="comma separated k=v, e.g. env=prod,replicas=3")
    p.set_defaults(fn=cmd_run)

    p = sub.add_parser("differential",
                       help="run both plans over input combinations and "
                            "check trace equivalence")
    p.add_argument("file")
    p.add_argument("--max", type=int, default=1000,
                   help="max input combinations")
    p.set_defaults(fn=cmd_differential)

    p = sub.add_parser("bench", help="compile-time benchmark")
    p.add_argument("--steps", type=int, default=3000)
    p.set_defaults(fn=cmd_bench)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
